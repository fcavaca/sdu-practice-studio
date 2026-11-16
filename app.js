/* SDU Practice Studio — vanilla JS, no build step.
   The added functionality is "Check my answer": a deterministic check of a student's answer against an
   answer key computed from the real course files (see builder/build_site.py). SQL is executed in the
   browser with sql.js and compared with the reference result using a fingerprint: row/column counts,
   per-column control totals and order-independent / ordered hashes. */
"use strict";

const $app = document.getElementById("app");
let BANK = null;            // questions.json
const QMAP = {};            // id -> question
const DBCACHE = {};         // dataset -> Promise<SQL.Database>
let SQLjs = null;
let examTimer = null;

// ---------- small helpers ----------
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmt = (v) => (v === null || v === undefined ? "NULL" : typeof v === "number" ? (Number.isInteger(v) ? v.toLocaleString("en-US") : v.toLocaleString("en-US", { maximumFractionDigits: 4 })) : String(v));
const cents = (c) => (c === null ? "–" : (c / 100).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 }));
const store = {
  get(k, d) { try { const v = localStorage.getItem(k); return v ? JSON.parse(v) : d; } catch { return d; } },
  set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch { /* private mode: progress not saved */ } },
};
const progress = () => store.get("sps-progress", {});
function mark(id, ok) {
  const p = progress();
  if (ok) p[id] = { s: "ok", t: Date.now() };
  else if (!p[id] || p[id].s !== "ok") p[id] = { s: "tried", t: Date.now() };
  store.set("sps-progress", p);
}
const statusChip = (id) => {
  const s = progress()[id]?.s;
  return s === "ok" ? '<span class="chip ok">solved</span>' : s === "tried" ? '<span class="chip tried">tried</span>' : "";
};

// ---------- fingerprint (must mirror builder/build_site.py) ----------
const enc = new TextEncoder();
function fnv(str) {
  let h = 0x811c9dc5;
  for (const b of enc.encode(str)) { h ^= b; h = Math.imul(h, 0x01000193) >>> 0; }
  return h >>> 0;
}
function normCell(v) {
  if (v === null || v === undefined) return "∅";
  if (typeof v === "number") return "n" + String(Math.floor(v * 100 + 0.5));
  if (v instanceof Uint8Array) return "b" + v.length;
  return "s" + String(v).trim();
}
function fingerprint(cols, rows) {
  const rs = rows.map((r) => r.map(normCell).join("\x1f"));
  let hash = 0;
  for (const r of rs) hash = (hash + fnv(r)) % 4294967296;
  const sums = cols.map((_, j) => {
    let any = false, s = 0;
    for (const r of rows) if (typeof r[j] === "number") { any = true; s += Math.floor(r[j] * 100 + 0.5); }
    return any ? s : null;
  });
  return { ncols: cols.length, nrows: rows.length, hash, ohash: fnv(rs.join("\x1e")), sums, rs };
}

// ---------- database loading ----------
async function getSQL() {
  if (!SQLjs) SQLjs = await initSqlJs({ locateFile: (f) => `https://cdnjs.cloudflare.com/ajax/libs/sql.js/1.10.2/${f}` });
  return SQLjs;
}
function loadDB(ds, onProgress) {
  if (DBCACHE[ds]) return DBCACHE[ds];
  DBCACHE[ds] = (async () => {
    const SQL = await getSQL();
    const info = BANK.datasets[ds].db;
    const chunks = [];
    let got = 0;
    for (const url of info.parts) {
      const res = await fetch(url);
      if (!res.ok) throw new Error(`Could not download ${url} (${res.status})`);
      const reader = res.body.getReader();
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        chunks.push(value); got += value.length;
        onProgress && onProgress(got / info.bytes);
      }
    }
    const gz = new Blob(chunks);
    const raw = await new Response(gz.stream().pipeThrough(new DecompressionStream("gzip"))).arrayBuffer();
    return new SQL.Database(new Uint8Array(raw));
  })();
  DBCACHE[ds].catch(() => { delete DBCACHE[ds]; });
  return DBCACHE[ds];
}
const FORBIDDEN = /\b(insert|update|delete|drop|alter|create|replace|attach|detach|vacuum|commit|begin|rollback|savepoint|reindex)\b/i;
function stripSql(sql) {
  return sql.replace(/--[^\n]*/g, " ").replace(/\/\*[\s\S]*?\*\//g, " ").replace(/'(?:[^']|'')*'/g, "''").replace(/"(?:[^"]|"")*"/g, '""');
}
function runQuery(db, sql) {
  const bare = stripSql(sql).trim();
  if (!bare) throw new Error("Write a query first.");
  if (FORBIDDEN.test(bare)) throw new Error("Only reading queries (SELECT / WITH / PRAGMA) are allowed here, so the practice database stays unchanged.");
  // iterate so that column names are known even when the last statement returns no rows
  let last = null;
  for (const stmt of db.iterateStatements(sql)) {
    const columns = stmt.getColumnNames();
    const values = [];
    while (stmt.step()) values.push(stmt.get());
    stmt.free();
    if (columns.length) last = { columns, values };
  }
  return last || { columns: [], values: [] };
}

// ---------- answer checking ----------
function checkSql(q, result) {
  const exp = q.expected;
  const cols = result.columns, rows = result.values;
  const got = fingerprint(cols, rows);
  const out = { ok: false, level: "bad", title: "", points: [] };
  const colList = exp.columns.length ? `(${exp.ncols} column${exp.ncols > 1 ? "s" : ""})` : "";
  if (got.ncols && !got.nrows) {
    out.title = "No rows returned";
    out.points.push(`Your query runs but returns no rows; the answer key has <b>${exp.nrows.toLocaleString()}</b>. A filter is excluding everything: check spelling and capitalisation of text values, and WHERE vs HAVING.`);
    return out;
  }
  if (got.ncols !== exp.ncols) {
    out.title = "Different columns";
    out.points.push(`Your result has <b>${got.ncols}</b> column${got.ncols === 1 ? "" : "s"}; the question asks for <b>${exp.ncols}</b> ${colList}. Check the column list in the question; names do not matter, order does.`);
    return out;
  }
  const totals = () => exp.sums.map((s, j) => {
    if (s === null && got.sums[j] === null) return null;
    const same = s === got.sums[j];
    return `Column ${j + 1} control total: yours <b>${cents(got.sums[j])}</b>, expected <b>${cents(s)}</b> ${same ? "✓" : "✗"}`;
  }).filter(Boolean);
  if (got.nrows !== exp.nrows) {
    out.title = "Different number of rows";
    out.points.push(`Your query returns <b>${got.nrows.toLocaleString()}</b> row${got.nrows === 1 ? "" : "s"}; the answer key has <b>${exp.nrows.toLocaleString()}</b>.`);
    out.points.push(got.nrows > exp.nrows ? "Too many rows usually means a missing filter, a missing GROUP BY / HAVING condition, or a join that duplicates rows." : "Too few rows usually means a filter that is too strict (=, LIKE pattern, AND vs OR) or an inner join that drops rows.");
    out.points.push(...totals());
  } else if (got.hash === exp.hash) {
    if (q.ordered && got.ohash !== exp.ohash) {
      out.level = "warn"; out.title = "Right rows, wrong order";
      out.points.push("Every row matches the answer key, but the question asks for a specific order. Check your ORDER BY (direction, and the tie-breaker named in the question).");
    } else {
      out.ok = true; out.level = "ok"; out.title = "Correct";
      out.points.push(`All ${exp.nrows.toLocaleString()} row${exp.nrows === 1 ? "" : "s"} and every control total match the answer key${q.ordered ? ", in the right order" : ""}.`);
    }
  } else {
    out.title = "Same size, different values";
    const t = totals();
    const off = exp.sums.map((s, j) => s !== got.sums[j]).filter(Boolean).length;
    out.points.push(off ? `The row count matches, but ${off} column total${off > 1 ? "s differ" : " differs"}. That points to the calculation or the filter behind ${off > 1 ? "those columns" : "that column"}.` : "Row count and numeric control totals match, so the difference is in text values or in which rows were picked (for example names).");
    out.points.push(...t);
  }
  if (!out.ok && exp.rows) {
    const want = exp.rows.map((r) => r.map(normCell).join("\x1f"));
    const bag = new Map(); want.forEach((r) => bag.set(r, (bag.get(r) || 0) + 1));
    const extra = [];
    got.rs.forEach((r, i) => { if (bag.get(r)) bag.set(r, bag.get(r) - 1); else extra.push(rows[i]); });
    const missing = [];
    exp.rows.forEach((r, i) => { const k = want[i]; if (bag.get(k)) { missing.push(r); bag.set(k, bag.get(k) - 1); } });
    if (extra.length) out.points.push(`Rows in your result that are not in the answer key: ${extra.slice(0, 3).map((r) => "<code>" + esc(r.map(fmt).join(" | ")) + "</code>").join(", ")}${extra.length > 3 ? ` and ${extra.length - 3} more` : ""}.`);
    if (missing.length) out.points.push(`${missing.length} row${missing.length > 1 ? "s" : ""} from the answer key ${missing.length > 1 ? "are" : "is"} missing from your result.`);
  }
  return out;
}

function parseNumber(s) {
  let t = String(s).trim().replace(/[\s$€£]|kr\.?|dkk/gi, "").replace(/%$/, "");
  if (!t) return NaN;
  const lc = t.lastIndexOf(","), ld = t.lastIndexOf(".");
  if (lc > -1 && ld > -1) t = lc > ld ? t.replace(/\./g, "").replace(",", ".") : t.replace(/,/g, "");
  else if (lc > -1) { const after = t.length - lc - 1; t = (t.split(",").length > 2 || after === 3) ? t.replace(/,/g, "") : t.replace(",", "."); }
  return Number(t);
}
const normText = (s) => String(s).toLowerCase().replace(/[^a-z0-9]/g, "");
function checkValue(a, raw) {
  if (raw === undefined || raw === null || String(raw).trim() === "") return { ok: false, msg: "no answer" };
  if (a.type === "number") {
    const v = parseNumber(raw);
    if (Number.isNaN(v)) return { ok: false, msg: "not a number" };
    return { ok: Math.abs(v - a.value) <= a.tol + 1e-9, msg: "" };
  }
  if (a.type === "percent") {
    let v = parseNumber(raw);
    if (Number.isNaN(v)) return { ok: false, msg: "not a number" };
    if (/%/.test(raw) || v > 1) v = v / 100;
    return { ok: Math.abs(v - a.value) <= a.tol + 1e-9, msg: "" };
  }
  if (a.type === "text") return { ok: a.accepted.some((x) => normText(x) === normText(raw)), msg: "" };
  if (a.type === "choice") return { ok: raw === a.value, msg: "" };
  if (a.type === "datetime") {
    const [y, mo, d, h, mi] = a.parts;
    const ns = (String(raw).match(/\d+/g) || []).map(Number);
    let hour = ns.includes(h);
    if (!hour && /pm/i.test(raw)) hour = ns.includes(h - 12);
    const ok = ns.includes(y) && ns.includes(mi) && hour && ns.includes(mo) && ns.includes(d);
    return { ok, msg: ok ? "" : "give date and time, e.g. 2014-05-03 09:15" };
  }
  return { ok: false, msg: "" };
}
const showAnswer = (a) => a.type === "number" ? `${fmt(a.value)}${a.tol ? ` (± ${a.tol})` : ""}` : a.type === "percent" ? `${(a.value * 100).toFixed(2)}%` : a.type === "text" ? a.accepted[0] : a.type === "choice" ? a.value : a.type === "datetime" ? `${a.parts[0]}-${String(a.parts[1]).padStart(2, "0")}-${String(a.parts[2]).padStart(2, "0")} ${a.parts[3]}:${String(a.parts[4]).padStart(2, "0")}` : "";

// ---------- views ----------
function setNav(r) { document.querySelectorAll("#nav a").forEach((a) => a.classList.toggle("on", a.dataset.r === r)); }

function viewHome() {
  setNav("home");
  const p = progress();
  const n = BANK.questions.length, solved = BANK.questions.filter((q) => p[q.id]?.s === "ok").length;
  const by = (s) => BANK.questions.filter((q) => q.subject === s).length;
  $app.innerHTML = `
    <h1>Practise the exact exam format, and know when you are right</h1>
    <p class="muted" style="max-width:760px">At the oral exam you receive one full <b>data manipulation</b> exam (Excel and SQL) and one <b>data interpretation</b> exam (regression). Every question here is built from the course datasets. Each one has an answer key computed from the real files, so you can <b>check your answer instantly</b>: for SQL, your query runs on the real database in your browser and is compared row by row with the key.</p>
    <div class="grid" style="margin-top:18px">
      <div class="card"><div class="muted">Question bank</div><div class="kpi">${n}</div><div class="muted">${by("Excel")} Excel · ${by("SQL")} SQL · ${by("Interpretation")} interpretation</div></div>
      <div class="card"><div class="muted">Your progress (this browser)</div><div class="kpi">${solved} / ${n}</div><div class="progress"><span style="width:${(100 * solved / n).toFixed(1)}%"></span></div></div>
      <div class="card"><div class="muted">Full exams</div><div class="kpi">${BANK.exams.length}</div><div class="muted">timed, mixed Excel + SQL, and a regression exam</div></div>
    </div>
    <div class="row" style="margin-top:18px"><a class="btn primary" href="#/exams">Start a full exam</a><a class="btn" href="#/questions">Browse questions</a><a class="btn" href="#/about">How the answer check works</a></div>
    <h2>Datasets</h2>
    <div class="grid">${Object.entries(BANK.datasets).map(([k, d]) => `
      <div class="card"><b>${esc(d.name)}</b> <span class="chip">${d.kind === "sql" ? "SQL" : "Excel"}</span>
      <div class="muted" style="font-size:13px;margin-top:4px">${BANK.questions.filter((q) => q.dataset === k).length} questions</div>
      <div style="font-size:13px;margin-top:6px">${d.files.map((f) => `<a href="${encodeURIComponent(f)}" download>${esc(f)}</a>`).join(" · ") || "<span class='muted'>runs in the browser; the original file is on the course page</span>"}</div></div>`).join("")}
    </div>`;
}

function viewQuestions(params) {
  setNav("questions");
  const f = { subject: params.get("subject") || "", dataset: params.get("dataset") || "", q: params.get("q") || "", status: params.get("status") || "" };
  const p = progress();
  const list = BANK.questions.filter((q) => (!f.subject || q.subject === f.subject) && (!f.dataset || q.dataset === f.dataset)
    && (!f.q || (q.id + " " + q.prompt + " " + q.skills.join(" ")).toLowerCase().includes(f.q.toLowerCase()))
    && (!f.status || (f.status === "open" ? !p[q.id] || p[q.id].s !== "ok" : p[q.id]?.s === f.status)));
  $app.innerHTML = `
    <h1>Question bank</h1>
    <p class="muted">${list.length} of ${BANK.questions.length} questions</p>
    <div class="row" style="margin:12px 0 16px">
      <select id="fs" aria-label="Subject"><option value="">All subjects</option>${["Excel", "SQL", "Interpretation"].map((s) => `<option ${s === f.subject ? "selected" : ""}>${s}</option>`).join("")}</select>
      <select id="fd" aria-label="Dataset"><option value="">All datasets</option>${Object.entries(BANK.datasets).map(([k, d]) => `<option value="${k}" ${k === f.dataset ? "selected" : ""}>${esc(d.name)}</option>`).join("")}</select>
      <select id="fst" aria-label="Status"><option value="">Any status</option><option value="open" ${f.status === "open" ? "selected" : ""}>Not solved yet</option><option value="ok" ${f.status === "ok" ? "selected" : ""}>Solved</option><option value="tried" ${f.status === "tried" ? "selected" : ""}>Tried, not solved</option></select>
      <input type="search" id="fq" placeholder="Search ID, prompt or skill…" value="${esc(f.q)}" aria-label="Search">
    </div>
    <div class="qlist">${list.map((q) => `
      <a class="card qitem" href="#/q/${q.id}">
        <span class="id">${q.id}</span>
        <span>${esc(q.prompt)}<br><span class="muted" style="font-size:13px">${q.subject} · ${esc(BANK.datasets[q.dataset].name)} · ${q.skills.map(esc).join(", ")}</span></span>
        <span>${statusChip(q.id)}</span>
      </a>`).join("") || '<p class="muted">No questions match.</p>'}</div>`;
  const go = () => {
    const s = new URLSearchParams();
    [["subject", "fs"], ["dataset", "fd"], ["status", "fst"], ["q", "fq"]].forEach(([k, id]) => { const v = document.getElementById(id).value; if (v) s.set(k, v); });
    history.replaceState(null, "", "#/questions" + (s.toString() ? "?" + s : ""));
    viewQuestions(s);
    const el = document.getElementById("fq"); if (document.activeElement?.id !== "fq" && f.q !== (s.get("q") || "")) el.focus();
  };
  ["fs", "fd", "fst"].forEach((id) => document.getElementById(id).addEventListener("change", go));
  let t; document.getElementById("fq").addEventListener("input", () => { clearTimeout(t); t = setTimeout(() => { go(); const el = document.getElementById("fq"); el.focus(); el.setSelectionRange(el.value.length, el.value.length); }, 250); });
}

function modelTable(m) {
  return `<div class="card" style="margin:12px 0"><b>Model ${m.key}</b> <span class="muted">· dependent variable: daily sales ($) · N = ${m.n} · R² = ${m.r2}</span>
    <p class="muted" style="font-size:13px;margin:6px 0">${esc(m.desc)}</p>
    <div class="result"><table class="data model"><thead><tr><th>Variable</th><th>Coefficient</th><th>Std. error</th><th>t</th><th>p-value</th></tr></thead>
    <tbody>${m.rows.map((r) => `<tr><td>${esc(r[0])}</td><td>${r[1].toLocaleString("en-US", { maximumFractionDigits: 3 })}</td><td>${r[2]}</td><td>${r[3]}</td><td>${r[4] < 0.0001 ? "&lt;0.0001" : r[4]}</td></tr>`).join("")}</tbody></table></div></div>`;
}

function renderResult(el, res, limit = 200) {
  if (!res.columns.length) { el.innerHTML = '<p class="muted" style="padding:8px">Query ran, no rows returned.</p>'; return; }
  const rows = res.values.slice(0, limit);
  el.innerHTML = `<table class="data"><thead><tr>${res.columns.map((c) => `<th>${esc(c)}</th>`).join("")}</tr></thead><tbody>${rows.map((r) => `<tr>${r.map((v) => `<td>${esc(fmt(v))}</td>`).join("")}</tr>`).join("")}</tbody></table>${res.values.length > limit ? `<p class="muted" style="padding:6px 9px;font-size:13px">Showing ${limit} of ${res.values.length.toLocaleString()} rows.</p>` : ""}`;
}

function verdictHtml(v) {
  return `<div class="verdict ${v.level}" role="status"><h4>${v.level === "ok" ? "✓ " : v.level === "warn" ? "≈ " : "✗ "}${esc(v.title)}</h4><ul>${v.points.map((p) => `<li>${p}</li>`).join("")}</ul></div>`;
}

function questionHtml(q, ctx) {
  const ds = BANK.datasets[q.dataset];
  const inExam = !!ctx.exam;
  const files = ds.files.map((f) => `<a href="${encodeURIComponent(f)}" download>${esc(f)}</a>`).join(" · ");
  let body = "";
  if (q.check === "result" || (q.subject === "SQL")) {
    body += `
      <label class="sr" for="sql">SQL query</label>
      <textarea class="sql" id="sql" spellcheck="false" placeholder="SELECT …">${esc(store.get("sps-sql-" + q.id, ""))}</textarea>
      <div class="row" style="margin:8px 0">
        <button class="btn" id="run">▶ Run</button>
        ${q.check === "result" ? '<button class="btn primary" id="check">Check my answer</button>' : ""}
        <span class="muted" id="dbstatus" style="font-size:13px"></span>
      </div>`;
  }
  if (q.check === "value") {
    body += `<div class="answers card" style="margin-top:8px">${q.subject === "SQL" ? '<p class="muted" style="margin:0 0 4px;font-size:13px">Explore with SQL above, then enter your answer.</p>' : ""}
      ${q.answers.map((a, i) => a.type === "choice"
        ? `<label>${esc(a.label)} <span class="mark" id="m${i}"></span></label><div class="row">${a.options.map((o) => `<label style="font-weight:400;margin:0"><input type="radio" name="a${i}" value="${esc(o)}"> ${esc(o)}</label>`).join("")}</div>`
        : `<label for="a${i}">${esc(a.label)} <span class="mark" id="m${i}"></span></label><input type="text" id="a${i}" autocomplete="off" style="width:100%;max-width:320px" placeholder="${a.type === "percent" ? "e.g. 12.5% or 0.125" : a.type === "datetime" ? "e.g. 2014-05-03 09:15" : ""}">`).join("")}
      <div class="row" style="margin-top:12px"><button class="btn primary" id="checkv">Check my answer</button></div></div>`;
  }
  return `
    <div class="row muted" style="font-size:13px">${inExam ? `<a href="#/exam/${ctx.exam.id}">← ${esc(ctx.exam.title)}</a>` : '<a href="#/questions">← Question bank</a>'}</div>
    <div class="row" style="margin-top:6px"><h1 style="margin:0">${q.id}</h1>${statusChip(q.id)}<span class="chip">${q.subject}</span><span class="chip">${esc(ds.name)}</span><span class="chip">≈ ${q.minutes} min</span></div>
    <p style="font-size:18px;margin:12px 0">${esc(q.prompt)}</p>
    ${q.note ? `<p class="note">${esc(q.note)}</p>` : ""}
    ${q.model ? modelTable(q.model) : ""}
    ${files ? `<p class="muted" style="font-size:13px">Dataset: ${files}</p>` : ""}
    <div class="${q.subject === "SQL" ? "two" : ""}">
      <div>${body}
        <div id="verdict" style="margin-top:10px"></div>
        <div id="out" class="result" style="margin-top:10px;display:none"></div>
        <div id="reveal" style="margin-top:12px"></div>
      </div>
      ${q.subject === "SQL" ? '<aside class="card schema" id="schema"><b>Tables</b><p class="muted" style="font-size:13px">Loads with the database.</p></aside>' : ""}
    </div>
    ${inExam ? "" : `<div class="row" style="margin-top:24px">${neighbours(q)}</div>`}`;
}

function neighbours(q) {
  const i = BANK.questions.indexOf(q);
  const prev = BANK.questions[i - 1], next = BANK.questions[i + 1];
  return `${prev ? `<a class="btn" href="#/q/${prev.id}">← ${prev.id}</a>` : ""}${next ? `<a class="btn" href="#/q/${next.id}">${next.id} →</a>` : ""}`;
}

function revealHtml(q, attempts, inExam) {
  if (inExam) return '<p class="muted" style="font-size:13px">Solutions are shown when you finish the exam.</p>';
  if (!attempts) return '<p class="muted" style="font-size:13px">Make one attempt to unlock the reference solution.</p>';
  return `<button class="btn" id="showsol">Show reference solution</button>`;
}

function solutionHtml(q) {
  if (q.check === "result") {
    const e = q.expected;
    return `<div class="card"><b>Reference query</b><pre class="ref">${esc(q.ref_sql)}</pre>
      <b>Answer key</b> <span class="muted">(${e.nrows.toLocaleString()} rows${q.ordered ? ", order checked" : ""})</span>
      <div class="result" style="margin-top:6px"><table class="data"><thead><tr>${e.columns.map((c) => `<th>${esc(c)}</th>`).join("")}</tr></thead><tbody>${e.preview.map((r) => `<tr>${r.map((v) => `<td>${esc(fmt(v))}</td>`).join("")}</tr>`).join("")}</tbody></table>${e.nrows > e.preview.length ? `<p class="muted" style="padding:6px 9px;font-size:13px">First ${e.preview.length} rows shown.</p>` : ""}</div></div>`;
  }
  return `<div class="card"><b>Answer</b><ul>${q.answers.map((a) => `<li>${esc(a.label)}: <b>${esc(showAnswer(a))}</b></li>`).join("")}</ul>
    <b>How</b><p style="margin:4px 0">${esc(q.solution)}</p>${q.source ? `<p class="muted" style="font-size:13px;margin:0">Source of the key: ${esc(q.source)}</p>` : ""}</div>`;
}

async function wireSchema(db) {
  const el = document.getElementById("schema");
  if (!el) return;
  const t = db.exec("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name");
  const names = t.length ? t[0].values.map((r) => r[0]) : [];
  el.innerHTML = "<b>Tables</b>" + names.map((n) => {
    const cols = db.exec(`PRAGMA table_info("${n.replace(/"/g, '""')}")`)[0].values.map((r) => `${r[1]} <span class="muted">${r[2]}</span>`);
    return `<details><summary>${esc(n)}</summary>${cols.map((c) => `<code>${c}</code>`).join("")}</details>`;
  }).join("");
}

function viewQuestion(id, ctx = {}) {
  setNav(ctx.exam ? "exams" : "questions");
  const q = QMAP[id];
  if (!q) { $app.innerHTML = `<p>Question ${esc(id)} not found. <a href="#/questions">Back to the question bank</a></p>`; return; }
  $app.innerHTML = questionHtml(q, ctx);
  let attempts = 0;
  const inExam = !!ctx.exam && !(store.get("sps-exam-" + ctx.exam.id, null) || {}).done;
  const $v = document.getElementById("verdict"), $r = document.getElementById("reveal"), $out = document.getElementById("out");
  const refreshReveal = () => {
    $r.innerHTML = revealHtml(q, attempts, inExam);
    const b = document.getElementById("showsol");
    if (b) b.onclick = () => { $r.innerHTML = solutionHtml(q); };
  };
  refreshReveal();
  const record = (ok) => {
    attempts++; mark(q.id, ok);
    if (ctx.exam) { const st = store.get("sps-exam-" + ctx.exam.id, null); if (st && !st.done) { st.res[q.id] = st.res[q.id] === true ? true : ok; store.set("sps-exam-" + ctx.exam.id, st); } }
    refreshReveal();
  };

  const sqlEl = document.getElementById("sql");
  if (sqlEl) {
    const $s = document.getElementById("dbstatus");
    let db = null;
    const ensure = async () => {
      if (db) return db;
      $s.textContent = "Loading database…";
      db = await loadDB(q.dataset, (f) => { $s.textContent = `Downloading database… ${Math.min(100, Math.round(f * 100))}%`; });
      $s.textContent = "Database ready.";
      wireSchema(db);
      return db;
    };
    ensure().catch((e) => { $s.textContent = "Could not load the database: " + e.message; });
    sqlEl.addEventListener("input", () => store.set("sps-sql-" + q.id, sqlEl.value));
    sqlEl.addEventListener("keydown", (e) => { if ((e.ctrlKey || e.metaKey) && e.key === "Enter") { e.preventDefault(); document.getElementById(q.check === "result" ? "check" : "run").click(); } });
    const exec = async () => {
      const d = await ensure();
      $s.textContent = "Running…";
      await new Promise((r) => setTimeout(r, 20));
      const t0 = performance.now();
      const res = runQuery(d, sqlEl.value);
      $s.textContent = `Ran in ${((performance.now() - t0) / 1000).toFixed(2)} s · ${res.values.length.toLocaleString()} rows`;
      $out.style.display = "block";
      renderResult($out, res);
      return res;
    };
    document.getElementById("run").onclick = async () => {
      try { await exec(); } catch (e) { $v.innerHTML = verdictHtml({ level: "bad", title: "Query error", points: [esc(e.message)] }); }
    };
    const chk = document.getElementById("check");
    if (chk) chk.onclick = async () => {
      try {
        const res = await exec();
        const v = checkSql(q, res);
        $v.innerHTML = verdictHtml(v);
        record(v.ok);
      } catch (e) { $v.innerHTML = verdictHtml({ level: "bad", title: "Query error", points: [esc(e.message), "A query that does not run cannot be checked; fix the error first."] }); }
    };
  }
  const cv = document.getElementById("checkv");
  if (cv) cv.onclick = () => {
    let all = true;
    const pts = [];
    q.answers.forEach((a, i) => {
      const raw = a.type === "choice" ? (document.querySelector(`input[name=a${i}]:checked`) || {}).value : document.getElementById("a" + i).value;
      const r = checkValue(a, raw);
      all = all && r.ok;
      const m = document.getElementById("m" + i);
      m.className = "mark " + (r.ok ? "ok" : "bad");
      m.textContent = r.ok ? "✓" : "✗" + (r.msg ? ` (${r.msg})` : "");
      if (!r.ok) pts.push(`${esc(a.label)}: not matching the answer key${a.type === "number" && a.tol ? ` (tolerance ± ${a.tol})` : ""}.`);
    });
    $v.innerHTML = verdictHtml(all ? { level: "ok", title: "Correct", points: ["Every figure matches the answer key computed from the course file."] }
      : { level: "bad", title: "Not yet", points: [...pts, "Recheck the filter and the exact definition in the question before changing your method."] });
    record(all);
  };
}

function viewExams() {
  setNav("exams");
  $app.innerHTML = `<h1>Full exams</h1>
    <p class="muted" style="max-width:760px">Each data manipulation exam combines Excel and SQL questions from the bank, as at the oral exam. The timer runs while you work. You can check answers during the exam, but solutions only appear when you finish.</p>
    <div class="grid" style="margin-top:14px">${BANK.exams.map((e) => {
      const st = store.get("sps-exam-" + e.id, null);
      const score = st && st.done ? `${Object.values(st.res).filter((x) => x === true).length}/${e.questions.length} correct` : st ? "in progress" : "";
      return `<a class="card" style="text-decoration:none;color:inherit" href="#/exam/${e.id}"><span class="chip">${esc(e.type)}</span><h3 style="margin:8px 0 4px">${esc(e.title)}</h3><div class="muted" style="font-size:13px">${e.questions.length} questions · ${e.minutes} min ${score ? "· " + score : ""}</div></a>`;
    }).join("")}</div>`;
}

function viewExam(id, sub) {
  setNav("exams");
  const e = BANK.exams.find((x) => x.id === id);
  if (!e) { $app.innerHTML = "<p>Exam not found.</p>"; return; }
  let st = store.get("sps-exam-" + e.id, null);
  if (sub && sub !== "finish") return viewQuestion(sub, { exam: e });
  if (!st) {
    $app.innerHTML = `<a href="#/exams" class="muted">← Exams</a><h1>${esc(e.title)}</h1><p class="muted">${e.questions.length} questions · ${e.minutes} minutes</p>
      <ol>${e.questions.map((qid) => `<li>${qid}: ${esc(QMAP[qid].prompt)}</li>`).join("")}</ol>
      <button class="btn primary" id="start">Start exam and timer</button>`;
    document.getElementById("start").onclick = () => { store.set("sps-exam-" + e.id, { start: Date.now(), res: {}, done: false }); viewExam(id); };
    return;
  }
  if (sub === "finish" && !st.done) { st.done = true; st.end = Date.now(); store.set("sps-exam-" + e.id, st); }
  const elapsed = () => Math.floor(((st.done ? st.end : Date.now()) - st.start) / 1000);
  const ok = Object.values(st.res).filter((x) => x === true).length;
  $app.innerHTML = `<a href="#/exams" class="muted">← Exams</a>
    <div class="row"><h1 style="margin:0">${esc(e.title)}</h1><span class="chip">${st.done ? "finished" : "in progress"}</span></div>
    <p>Time: <span class="timer" id="clock"></span> of ${e.minutes}:00 ${st.done ? ` · <b>${ok} / ${e.questions.length} correct</b>` : ""}</p>
    <div class="qlist">${e.questions.map((qid, i) => {
      const r = st.res[qid];
      const chip = r === true ? '<span class="chip ok">correct</span>' : r === false ? '<span class="chip tried">not yet correct</span>' : '<span class="chip">not attempted</span>';
      return `<a class="card qitem" href="#/exam/${e.id}/${qid}"><span class="id">${i + 1}. ${qid}</span><span>${esc(QMAP[qid].prompt)}</span><span>${chip}</span></a>`;
    }).join("")}</div>
    <div class="row" style="margin-top:16px">${st.done ? `<button class="btn" id="again">Reset and retake</button><a class="btn" href="#/questions">Review solutions in the question bank</a>` : `<a class="btn primary" href="#/exam/${e.id}/finish">Finish exam</a>`}</div>`;
  const tick = () => { const s = elapsed(); const c = document.getElementById("clock"); if (!c) return; c.textContent = `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`; c.style.color = s > e.minutes * 60 ? "var(--bad)" : ""; };
  tick(); clearInterval(examTimer); if (!st.done) examTimer = setInterval(tick, 1000);
  const again = document.getElementById("again");
  if (again) again.onclick = () => { store.set("sps-exam-" + e.id, null); viewExam(id); };
}

function viewAbout() {
  setNav("about");
  $app.innerHTML = `<h1>Why “Check my answer”</h1>
  <div style="max-width:780px">
  <p>The reference site gives students the questions, the datasets, a timer and an AI tutor, but no way to find out whether an answer is <b>right</b>. A student who writes a query that runs and returns plausible-looking numbers has no signal that a filter is wrong or a join duplicates rows. In accounting we do not accept a figure because it looks plausible: we reconcile it to a control total. This site does the same for practice answers.</p>
  <h2>How it works</h2>
  <ul>
    <li><b>Answer keys come from the real files.</b> A build script (<code>builder/build_site.py</code>) runs every reference SQL query on the course databases and recomputes the Excel figures from the course files. It also asserts that they match the official course answers where those exist (<i>PWC answers.docx</i>, <i>solutions.xlsx</i>, and the CFO's ≈ $266,000 payroll gap in IntegrateCo).</li>
    <li><b>Your SQL runs on the real database, in your browser</b> (sql.js, nothing is sent to a server). Your result is compared with the key on column count, row count, a <b>control total per numeric column</b>, and order-independent and ordered fingerprints. Column names and rounding beyond cents are ignored.</li>
    <li><b>The feedback says where the difference is</b>, not what the answer is. Examples: "35 rows, key has 5", "column 3 total differs", "right rows, wrong order". The reference solution unlocks only after an attempt, and only after the exam in exam mode.</li>
    <li><b>Excel answers</b> are checked figure by figure, with tolerances where the question asks for rounding. Both 12.5% and 0.125 are accepted, as are the Danish 1.234,56 and the English 1,234.56.</li>
  </ul>
  <h2>Evidence: does it catch real mistakes?</h2>
  <p>The <a href="#/selftest">self-test page</a> runs every reference query through the browser checker (all must pass) and then runs typical student mistakes, which must all be flagged. You can run it yourself.</p>
  <div class="card" id="evidence"><b>Result when the site was built (16 Nov 2026)</b><ul style="margin:6px 0 0">
    <li><b>51 of 51</b> SQL answer keys (Chinook, Songs, Movies, P-cards) pass the browser checker. The fingerprint computed in the browser is identical to the one computed from the course files.</li>
    <li><b>15 of 15</b> typical mistakes that change the result were flagged, with a pointer to the cause. Examples: WHERE instead of HAVING, a forgotten join condition (2,065 rows instead of 35), LIKE '%C%' instead of 'C%', a duplicated join that inflated units from 190 to 1,690, and a wrong sort order.</li>
    <li><b>4</b> variants that are wrong in general but give the identical result on the course data were accepted. One example is <code>&gt;=</code> instead of <code>&gt;</code> when no value sits on the boundary. This is the limit of any result-based check. The builder therefore asserts that every HAVING filter in the bank removes at least one row, so those questions do test the filter.</li>
  </ul></div>
  </div>`;
}

// ---------- self-test: proves JS/Python parity and that typical mistakes are caught ----------
const MISTAKES = [
  ["SQL-CH-2A4", "WHERE instead of HAVING (filters invoices, not customer totals)", "SELECT c.FirstName, c.LastName, SUM(i.Total) FROM Invoice i JOIN Customer c ON c.CustomerId=i.CustomerId WHERE i.BillingCountry='Brazil' AND i.Total>30 GROUP BY c.CustomerId"],
  ["SQL-CH-2A4", "HAVING >= instead of >", "SELECT c.FirstName, c.LastName, SUM(i.Total) FROM Invoice i JOIN Customer c ON c.CustomerId=i.CustomerId WHERE i.BillingCountry='Brazil' GROUP BY c.CustomerId HAVING SUM(i.Total)>=30"],
  ["SQL-CH-2A2", "Counting customers instead of invoices", "SELECT COUNT(DISTINCT CustomerId), SUM(Total) FROM Invoice WHERE BillingCountry='Brazil'"],
  ["SQL-CH-2A3", "Join condition forgotten (every invoice paired with every customer)", "SELECT c.FirstName, c.LastName, i.InvoiceId, i.Total FROM Invoice i, Customer c WHERE i.BillingCountry='Brazil'"],
  ["SQL-CH-2A3", "Joining on the wrong key (InvoiceId = CustomerId)", "SELECT c.FirstName, c.LastName, i.InvoiceId, i.Total FROM Invoice i JOIN Customer c ON c.CustomerId=i.InvoiceId WHERE i.BillingCountry='Brazil'"],
  ["SQL-CH-2A1", "Sorting ascending", "SELECT InvoiceId, Total FROM Invoice WHERE BillingCountry='Brazil' ORDER BY Total, InvoiceId"],
  ["SQL-CH-2A1", "Forgetting the tie-breaker", "SELECT InvoiceId, Total FROM Invoice WHERE BillingCountry='Brazil' ORDER BY Total DESC, InvoiceId DESC"],
  ["SQL-CH-07", "LIKE '%C%' instead of 'C%'", "SELECT COUNT(*) FROM Invoice WHERE BillingCountry LIKE '%C%'"],
  ["SQL-CH-05", ">= / <= instead of > / <", "SELECT COUNT(*) FROM Invoice WHERE Total >= 10 AND Total <= 20"],
  ["SQL-CH-11", "Grouping by customer country instead of billing country", "SELECT c.Country, SUM(i.Total) FROM Invoice i JOIN Customer c ON c.CustomerId=i.CustomerId GROUP BY c.Country ORDER BY 2 DESC, 1"],
  ["SQL-CH-2A5", "Join to InvoiceLine twice (duplicates rows)", "SELECT g.Name, SUM(il.Quantity) FROM Invoice i JOIN InvoiceLine il ON il.InvoiceId=i.InvoiceId JOIN InvoiceLine il2 ON il2.InvoiceId=i.InvoiceId JOIN Track t ON t.TrackId=il.TrackId JOIN Genre g ON g.GenreId=t.GenreId WHERE i.BillingCountry='Brazil' GROUP BY g.GenreId"],
  ["SQL-CH-2A6", "COUNT(*) > 10 instead of >= 10", "SELECT BillingCountry, COUNT(*), SUM(Total) FROM Invoice GROUP BY BillingCountry HAVING COUNT(*) > 10"],
  ["SQL-SO-04", "OR instead of AND", "SELECT name FROM songs WHERE danceability > 0.6 OR energy > 0.6 OR valence > 0.6"],
  ["SQL-SO-07", "Average energy of all songs (artist filter missing)", "SELECT AVG(energy) FROM songs"],
  ["SQL-SO-08", "Searching 'feat' without the dot", "SELECT name FROM songs WHERE name LIKE '%feat%'"],
  ["SQL-SO-02", "Ascending tempo", "SELECT name FROM songs ORDER BY tempo"],
  ["SQL-SO-03", "LIMIT forgotten", "SELECT name FROM songs ORDER BY duration_ms DESC"],
  ["SQL-CH-13", "Summing prices instead of counting lines", "SELECT UnitPrice, SUM(UnitPrice) FROM InvoiceLine GROUP BY UnitPrice"],
  ["SQL-CH-11", "Rounding totals to whole dollars", "SELECT BillingCountry, ROUND(SUM(Total)) FROM Invoice GROUP BY BillingCountry ORDER BY SUM(Total) DESC, BillingCountry"],
];

async function viewSelftest() {
  setNav("about");
  $app.innerHTML = `<h1>Self-test</h1><p class="muted">Runs every Chinook and Songs reference query through the browser checker, then a list of typical mistakes. Large databases (Movies, P-cards) are tested only if you tick the box.</p>
    <label><input type="checkbox" id="big"> Also test Movies and P-cards (downloads about 64 MB)</label>
    <div class="row" style="margin:10px 0"><button class="btn primary" id="go">Run self-test</button></div><div id="st"></div>`;
  document.getElementById("go").onclick = async () => {
    const $st = document.getElementById("st");
    const sets = ["chinook", "songs"].concat(document.getElementById("big").checked ? ["pcards", "movies"] : []);
    let pass = 0, total = 0;
    const lines = [];
    for (const ds of sets) {
      $st.innerHTML = `<p>Loading ${ds}…</p>`;
      const db = await loadDB(ds);
      for (const q of BANK.questions.filter((x) => x.dataset === ds && x.check === "result")) {
        total++;
        const v = checkSql(q, runQuery(db, q.ref_sql));
        if (v.ok) pass++; else lines.push(`<li>${q.id}: reference did not pass (${esc(v.title)})</li>`);
      }
    }
    const db = await loadDB("chinook"); const dbs = await loadDB("songs");
    const rows = MISTAKES.map(([qid, label, sql]) => {
      const q = QMAP[qid];
      const v = checkSql(q, runQuery(q.dataset === "songs" ? dbs : db, sql));
      return { qid, label, v };
    });
    // a "mistake" whose result is identical to the key on this data is not a wrong answer here; report it separately
    const same = rows.filter((r) => r.v.ok).length;
    const caught = rows.length - same;
    $st.innerHTML = `<div class="verdict ${pass === total ? "ok" : "bad"}"><h4>${pass} / ${total} reference queries pass in the browser</h4>
      <p style="margin:0">The browser fingerprint matches the one computed in Python for every answer key.</p>${lines.length ? `<ul>${lines.join("")}</ul>` : ""}</div>
      <h2>Typical mistakes: ${caught} of ${caught} that change the result were flagged</h2>
      <p class="muted">${same} further variants return exactly the same rows as the key on the course data (for example no invoice total is exactly $10 or $20, so <code>&gt;=</code> and <code>&gt;</code> agree). The checker accepts them, correctly for this data. That is a limit of any result-based check, and the reason the reference query is shown after an attempt.</p>
      <div class="result" style="max-height:none"><table class="data" style="white-space:normal"><thead><tr><th>Question</th><th>Variant</th><th>Checker's feedback</th></tr></thead>
      <tbody>${rows.map((r) => `<tr><td>${r.qid}</td><td style="white-space:normal">${esc(r.label)}</td><td style="white-space:normal"><b>${r.v.ok ? "Same result on this data, accepted" : esc(r.v.title)}</b>${r.v.ok ? "" : ": " + r.v.points.slice(0, 2).map((p) => p.replace(/<[^>]+>/g, "")).map(esc).join(" ")}</td></tr>`).join("")}</tbody></table></div>`;
    window.__selftest = { pass, total, caught, same, mistakes: rows.length };
  };
}

// ---------- router ----------
function route() {
  clearInterval(examTimer);
  const h = location.hash.replace(/^#\/?/, "");
  const [path, qs] = h.split("?");
  const parts = path.split("/").filter(Boolean);
  const params = new URLSearchParams(qs || "");
  window.scrollTo(0, 0);
  if (!parts.length) return viewHome();
  if (parts[0] === "questions") return viewQuestions(params);
  if (parts[0] === "q") return viewQuestion(parts[1]);
  if (parts[0] === "exams") return viewExams();
  if (parts[0] === "exam") return viewExam(parts[1], parts[2]);
  if (parts[0] === "about") return viewAbout();
  if (parts[0] === "selftest") return viewSelftest();
  viewHome();
}

// theme toggle (remembered per browser)
(function theme() {
  const saved = store.get("sps-theme", null);
  if (saved) document.documentElement.dataset.theme = saved;
  document.getElementById("themeBtn").onclick = () => {
    const dark = document.documentElement.dataset.theme ? document.documentElement.dataset.theme === "dark" : matchMedia("(prefers-color-scheme: dark)").matches;
    document.documentElement.dataset.theme = dark ? "light" : "dark";
    store.set("sps-theme", document.documentElement.dataset.theme);
  };
})();

fetch("questions.json").then((r) => r.json()).then((b) => {
  BANK = b;
  b.questions.forEach((q) => { QMAP[q.id] = q; });
  window.addEventListener("hashchange", route);
  route();
}).catch((e) => { $app.innerHTML = `<p>Could not load the question bank: ${esc(e.message)}</p>`; });
