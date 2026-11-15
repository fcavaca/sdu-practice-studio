"""Builds the practice website data from the course materials.

Run from the 1_Exam_Studio folder:
    python builder/build_site.py

Outputs into site/:
    questions.json        question bank, exams and answer keys (computed from the real files)
    data/*.sqlite.gz      the course databases, gzipped, run in the browser with sql.js
    downloads/            the datasets students download for the Excel questions
Every SQL reference query is executed here against the real database; the result fingerprint
(row count, control totals, hashes) is stored so the browser can check a student's query.
"""
import gzip
import json
import math
import os
import re
import shutil
import sqlite3
import sys
from pathlib import Path

import numpy as np
import openpyxl
import pandas as pd
import statsmodels.formula.api as smf

sys.stdout.reconfigure(encoding="utf-8")
HERE = Path(__file__).resolve().parents[1]
MAT = HERE / "Materials"
SITE = HERE / "site"
DATA = SITE / "data"
DL = SITE / "downloads"
MAX_PART = 15 * 1000 * 1000  # GitHub web upload limit is 25 MB per file

DBS = {
    "chinook": MAT / "SQLs/PwC/Chinook_Sqlite.sqlite",
    "songs": MAT / "SQLs/cs50/songs/songs.db",
    "movies": MAT / "SQLs/cs50/movies/movies.db",
    "pcards": MAT / "SQLs/Pcards/pcards.db",
}
DATASETS = {
    "film": {"name": "NYC film permits", "kind": "excel", "files": ["NYC_Film_Permits.csv"]},
    "codes": {"name": "Plant codes (ETL case 2)", "kind": "excel", "files": ["ETL_Case2_plant_codes.xlsx"]},
    "journal": {"name": "Journal entries (ETL case 4)", "kind": "excel", "files": ["ETL_Case4_journal_entries.xlsx"]},
    "payroll": {"name": "IntegrateCo payroll 2015/2016", "kind": "excel",
                "files": ["IntegrateCo_2015payroll.csv", "IntegrateCo_2016payroll.csv", "IntegrateCo_jobcodes.csv", "IntegrateCo_locations.csv"]},
    "restaurant": {"name": "Restaurant sales (regression)", "kind": "excel", "files": ["Restaurant_data.xlsx"]},
    "chinook": {"name": "Chinook", "kind": "sql", "files": ["chinook.sqlite"]},
    "songs": {"name": "Songs (CS50)", "kind": "sql", "files": ["songs.sqlite"]},
    "movies": {"name": "Movies (CS50)", "kind": "sql", "files": []},
    "pcards": {"name": "Purchasing cards (OSU)", "kind": "sql", "files": []},
}


# ---------- fingerprint: must match normCell/fingerprint in site/app.js exactly ----------
def fnv(s: str) -> int:
    h = 0x811C9DC5
    for b in s.encode("utf-8"):
        h ^= b
        h = (h * 0x01000193) & 0xFFFFFFFF
    return h


def norm_cell(v) -> str:
    if v is None:
        return "∅"
    if isinstance(v, (int, float)):
        return "n" + str(int(math.floor(v * 100 + 0.5)))
    if isinstance(v, bytes):
        return "b" + str(len(v))
    return "s" + str(v).strip()


def fingerprint(cols, rows):
    rs = ["\x1f".join(norm_cell(c) for c in r) for r in rows]
    hs = [fnv(r) for r in rs]
    sums = []
    for j in range(len(cols)):
        nums = [r[j] for r in rows if isinstance(r[j], (int, float))]
        sums.append(int(sum(math.floor(v * 100 + 0.5) for v in nums)) if nums else None)
    return {
        "ncols": len(cols),
        "nrows": len(rows),
        "hash": sum(hs) % (2 ** 32),
        "ohash": fnv("\x1e".join(rs)),
        "sums": sums,
        "columns": list(cols),
        "rows": [list(r) for r in rows[:50]] if len(rows) <= 50 else None,
        "preview": [list(r) for r in rows[:8]],
    }


# ---------- question bank ----------
QUESTIONS = []


def sql_q(qid, ds, prompt, sql, skills, ordered=False, minutes=3, note=None):
    QUESTIONS.append({"id": qid, "subject": "SQL", "dataset": ds, "prompt": prompt, "skills": skills,
                      "check": "result", "ref_sql": sql.strip(), "ordered": ordered, "minutes": minutes, "note": note})


def val_q(qid, subject, ds, prompt, answers, skills, solution, minutes=3, source=None, model=None):
    QUESTIONS.append({"id": qid, "subject": subject, "dataset": ds, "prompt": prompt, "skills": skills,
                      "check": "value", "answers": answers, "solution": solution, "minutes": minutes,
                      "source": source, "model": model})


def num(label, value, tol, unit=""):
    return {"label": label, "type": "number", "value": round(float(value), 6), "tol": tol, "unit": unit}


def pct(label, value, tol):
    return {"label": label, "type": "percent", "value": float(value), "tol": tol}


def text(label, *accepted):
    return {"label": label, "type": "text", "accepted": list(accepted)}


def choice(label, options, correct):
    return {"label": label, "type": "choice", "options": options, "value": correct}


def dt(label, y, mo, d, h, mi):
    return {"label": label, "type": "datetime", "parts": [y, mo, d, h, mi]}


def build_chinook():
    C = "chinook"
    off = "official course answer (PWC answers.docx)"
    val_q("SQL-CH-01", "SQL", C, "How many columns does the Customer table have?",
          [num("Columns", 13, 0)], ["schema"], "PRAGMA table_info(Customer); returns one row per column (13).", 2, off)
    val_q("SQL-CH-02", "SQL", C, "If you select only the first 5 rows of the Customer table, what is the Country in the last (5th) row?",
          [text("Country", "Czech Republic")], ["select", "limit"], "SELECT Country FROM Customer LIMIT 5; the 5th row is Czech Republic.", 2, off)
    val_q("SQL-CH-03", "SQL", C, "If you sort the Customer table on LastName, what is the last name in the third row?",
          [text("LastName", "Bernard")], ["order by"], "SELECT LastName FROM Customer ORDER BY LastName LIMIT 3;", 2, off)
    sql_q("SQL-CH-04", C, "How many invoices have totals greater than $20? Return one number.",
          "SELECT COUNT(*) FROM Invoice WHERE Total > 20", ["where", "count"], minutes=2)
    sql_q("SQL-CH-05", C, "How many invoices have totals greater than $10 and less than $20? Return one number.",
          "SELECT COUNT(*) FROM Invoice WHERE Total > 10 AND Total < 20", ["where", "and"], minutes=2)
    sql_q("SQL-CH-06", C, "How many invoices are billed to Brazil, Argentina or Chile? Return one number.",
          "SELECT COUNT(*) FROM Invoice WHERE BillingCountry IN ('Brazil','Argentina','Chile')", ["in"], minutes=2)
    sql_q("SQL-CH-07", C, "How many invoices have a billing country that starts with “C”? Return one number.",
          "SELECT COUNT(*) FROM Invoice WHERE BillingCountry LIKE 'C%'", ["like"], minutes=2)
    sql_q("SQL-CH-08", C, "How many customers list their company? Return one number.",
          "SELECT COUNT(Company) FROM Customer", ["null handling"], minutes=2)
    val_q("SQL-CH-09", "SQL", C, "How many employees were born on a Sunday?",
          [num("Employees", 2, 0)], ["dates", "strftime"], "SELECT COUNT(*) FROM Employee WHERE strftime('%w', BirthDate) = '0';", 3, off)
    val_q("SQL-CH-10", "SQL", C, "How old (in years) was the oldest employee when they were hired? One decimal is enough.",
          [num("Age at hire (years)", 55.5, 0.3)], ["dates", "julianday"],
          "SELECT MAX((julianday(HireDate) - julianday(BirthDate)) / 365.25) FROM Employee; ≈ 55.5 years.", 3, off)
    sql_q("SQL-CH-11", C, "List billing countries by total invoice value, largest first (ties alphabetically). Columns: BillingCountry, total value.",
          "SELECT BillingCountry, SUM(Total) FROM Invoice GROUP BY BillingCountry ORDER BY SUM(Total) DESC, BillingCountry",
          ["group by", "order by"], ordered=True)
    sql_q("SQL-CH-12", C, "How many distinct unit prices appear on invoice lines? Return one number.",
          "SELECT COUNT(DISTINCT UnitPrice) FROM InvoiceLine", ["distinct"], minutes=2)
    sql_q("SQL-CH-13", C, "How many invoice lines (downloads) have there been at each unit price? Columns: UnitPrice, number of lines.",
          "SELECT UnitPrice, COUNT(*) FROM InvoiceLine GROUP BY UnitPrice", ["group by"])
    sql_q("SQL-CH-14", C, "What is the most popular genre by units sold (Quantity)? Columns: genre name, units. One row.",
          "SELECT g.Name, SUM(il.Quantity) FROM InvoiceLine il JOIN Track t ON t.TrackId = il.TrackId JOIN Genre g ON g.GenreId = t.GenreId GROUP BY g.GenreId ORDER BY 2 DESC LIMIT 1",
          ["join", "group by", "limit"], minutes=4)
    for k, ctry, thr, cnt in [("A", "Brazil", 38, 10), ("B", "Germany", 38, 15)]:
        sql_q(f"SQL-CH-2{k}1", C, f"Show invoice IDs and totals billed to {ctry}, largest totals first (ties by InvoiceId). Columns: InvoiceId, Total.",
              f"SELECT InvoiceId, Total FROM Invoice WHERE BillingCountry = '{ctry}' ORDER BY Total DESC, InvoiceId", ["filter", "sort"], ordered=True, minutes=2)
        sql_q(f"SQL-CH-2{k}2", C, f"Find the number and total value of invoices billed to {ctry}. Columns: count, total.",
              f"SELECT COUNT(*), SUM(Total) FROM Invoice WHERE BillingCountry = '{ctry}'", ["filter", "aggregate"], minutes=2)
        sql_q(f"SQL-CH-2{k}3", C, f"Show customer first name, last name, invoice ID and total for invoices billed to {ctry}. Columns: FirstName, LastName, InvoiceId, Total.",
              f"SELECT c.FirstName, c.LastName, i.InvoiceId, i.Total FROM Invoice i JOIN Customer c ON c.CustomerId = i.CustomerId WHERE i.BillingCountry = '{ctry}'",
              ["join", "filter"])
        sql_q(f"SQL-CH-2{k}4", C, f"Show customers and their total invoice value for invoices billed to {ctry}, only customers with totals above {thr}. Columns: FirstName, LastName, total.",
              f"SELECT c.FirstName, c.LastName, SUM(i.Total) FROM Invoice i JOIN Customer c ON c.CustomerId = i.CustomerId WHERE i.BillingCountry = '{ctry}' GROUP BY c.CustomerId HAVING SUM(i.Total) > {thr}",
              ["join", "group by", "having"], minutes=4)
        sql_q(f"SQL-CH-2{k}5", C, f"Show total units (Quantity) purchased by genre on invoices billed to {ctry}. Columns: genre name, units.",
              f"SELECT g.Name, SUM(il.Quantity) FROM Invoice i JOIN InvoiceLine il ON il.InvoiceId = i.InvoiceId JOIN Track t ON t.TrackId = il.TrackId JOIN Genre g ON g.GenreId = t.GenreId WHERE i.BillingCountry = '{ctry}' GROUP BY g.GenreId",
              ["join", "group by"], minutes=5)
        sql_q(f"SQL-CH-2{k}6", C, f"Show billing countries with at least {cnt} invoices, with invoice count and total value. Columns: BillingCountry, count, total.",
              f"SELECT BillingCountry, COUNT(*), SUM(Total) FROM Invoice GROUP BY BillingCountry HAVING COUNT(*) >= {cnt}",
              ["group by", "having"])


def build_songs():
    S = "songs"
    sql_q("SQL-SO-01", S, "List the names and energy of all songs. Columns: name, energy.", "SELECT name, energy FROM songs", ["select"], minutes=1)
    sql_q("SQL-SO-02", S, "List the names of all songs in decreasing order of tempo. Column: name.", "SELECT name FROM songs ORDER BY tempo DESC", ["order by"], ordered=True, minutes=1)
    sql_q("SQL-SO-03", S, "List the names of the top 3 longest songs, longest first. Column: name.", "SELECT name FROM songs ORDER BY duration_ms DESC LIMIT 3", ["order by", "limit"], ordered=True, minutes=1)
    sql_q("SQL-SO-04", S, "List the names of songs with danceability, energy and valence all greater than 0.6. Column: name.",
          "SELECT name FROM songs WHERE danceability > 0.6 AND energy > 0.6 AND valence > 0.6", ["where"], minutes=2)
    sql_q("SQL-SO-05", S, "Return the average tempo of all songs. One number.", "SELECT AVG(tempo) FROM songs", ["avg"], minutes=1)
    sql_q("SQL-SO-06", S, "List the names of songs by Drake, without assuming Drake's artist_id. Column: name.",
          "SELECT name FROM songs WHERE artist_id = (SELECT id FROM artists WHERE name = 'Drake')", ["subquery"], minutes=2)
    sql_q("SQL-SO-07", S, "Return the average energy of songs by Post Malone. One number.",
          "SELECT AVG(energy) FROM songs WHERE artist_id = (SELECT id FROM artists WHERE name = 'Post Malone')", ["subquery", "avg"], minutes=2)
    sql_q("SQL-SO-08", S, "List the names of songs that feature other artists (“feat.” in the name). Column: name.",
          "SELECT name FROM songs WHERE name LIKE '%feat.%'", ["like"], minutes=1)


def build_movies():
    M = "movies"
    note = "Large database (about 42 MB download, loaded once). Queries can take a few seconds."
    sql_q("SQL-MO-01", M, "List the titles of all movies released in 2007. Column: title.", "SELECT title FROM movies WHERE year = 2007", ["where"], minutes=2, note=note)
    sql_q("SQL-MO-02", M, "What is Johnny Depp's birth year? One number.", "SELECT birth FROM people WHERE name = 'Johnny Depp'", ["where"], minutes=1, note=note)
    sql_q("SQL-MO-03", M, "List the titles of all movies released in 2017 or later, in alphabetical order. Column: title.",
          "SELECT title FROM movies WHERE year >= 2017 ORDER BY title", ["order by"], ordered=True, minutes=2, note=note)
    sql_q("SQL-MO-04", M, "How many movies have an IMDb rating greater than 9.0? One number.",
          "SELECT COUNT(*) FROM ratings WHERE rating > 9.0", ["count"], minutes=1, note=note)
    sql_q("SQL-MO-05", M, "List the titles and release years of all Toy Story movies (titles starting with “Toy Story”), in chronological order. Columns: title, year.",
          "SELECT title, year FROM movies WHERE title LIKE 'Toy Story%' ORDER BY year", ["like", "order by"], ordered=True, minutes=2, note=note)
    sql_q("SQL-MO-06", M, "What is the average rating of all movies released in 2013? One number.",
          "SELECT AVG(r.rating) FROM ratings r JOIN movies m ON m.id = r.movie_id WHERE m.year = 2013", ["join", "avg"], minutes=3, note=note)
    sql_q("SQL-MO-07", M, "List movies released in 2011 with their ratings, highest rating first; equal ratings alphabetically by title. Leave out unrated movies. Columns: title, rating.",
          "SELECT m.title, r.rating FROM movies m JOIN ratings r ON r.movie_id = m.id WHERE m.year = 2011 ORDER BY r.rating DESC, m.title",
          ["join", "order by"], ordered=True, minutes=4, note=note)
    sql_q("SQL-MO-08", M, "List the names of all people who starred in Harry Potter and the Sorcerer's Stone. Column: name.",
          "SELECT p.name FROM people p JOIN stars s ON s.person_id = p.id JOIN movies m ON m.id = s.movie_id WHERE m.title = 'Harry Potter and the Sorcerer''s Stone'",
          ["join"], minutes=4, note=note)
    sql_q("SQL-MO-09", M, "List the names of all people who starred in a movie released in 2003 (each person once). Column: name. (Order is not checked.)",
          "SELECT name FROM people WHERE id IN (SELECT s.person_id FROM stars s JOIN movies m ON m.id = s.movie_id WHERE m.year = 2003)",
          ["subquery", "distinct"], minutes=5, note=note)
    sql_q("SQL-MO-10", M, "List the names of all people who directed a movie rated at least 8.0 (each person once). Column: name.",
          "SELECT name FROM people WHERE id IN (SELECT d.person_id FROM directors d JOIN ratings r ON r.movie_id = d.movie_id WHERE r.rating >= 8.0)",
          ["subquery", "join"], minutes=5, note=note)
    sql_q("SQL-MO-11", M, "List the titles of the five highest-rated movies starring Helena Bonham Carter, highest first; equal ratings alphabetically by title. Column: title.",
          "SELECT m.title FROM movies m JOIN stars s ON s.movie_id = m.id JOIN people p ON p.id = s.person_id JOIN ratings r ON r.movie_id = m.id WHERE p.name = 'Helena Bonham Carter' ORDER BY r.rating DESC, m.title LIMIT 5",
          ["join", "order by", "limit"], ordered=True, minutes=4, note=note)
    sql_q("SQL-MO-12", M, "List the titles of all movies starring both Johnny Depp and Helena Bonham Carter. Column: title.",
          "SELECT title FROM movies WHERE id IN (SELECT movie_id FROM stars WHERE person_id = (SELECT id FROM people WHERE name = 'Johnny Depp')) AND id IN (SELECT movie_id FROM stars WHERE person_id = (SELECT id FROM people WHERE name = 'Helena Bonham Carter'))",
          ["subquery", "intersect"], minutes=5, note=note)
    sql_q("SQL-MO-13", M, "List the names of all people who starred in a movie with Kevin Bacon (the one born in 1958), excluding him. Each person once. Column: name.",
          "SELECT name FROM people WHERE id IN (SELECT person_id FROM stars WHERE movie_id IN (SELECT movie_id FROM stars WHERE person_id = (SELECT id FROM people WHERE name = 'Kevin Bacon' AND birth = 1958))) AND id <> (SELECT id FROM people WHERE name = 'Kevin Bacon' AND birth = 1958)",
          ["nested subqueries"], minutes=6, note=note)


def build_pcards():
    P = "pcards"
    note = "Large database (about 22 MB download, loaded once)."
    sql_q("SQL-PC-01", P, "What is the total number of P-card transactions in the database? One number.", "SELECT COUNT(*) FROM pcards", ["count"], minutes=1, note=note)
    sql_q("SQL-PC-02", P, "How many transactions are for $5,000 or more? One number.", "SELECT COUNT(*) FROM pcards WHERE Amount >= 5000", ["where"], minutes=1, note=note)
    sql_q("SQL-PC-03", P, "What is the total dollar amount of returns (negative amounts) from January to March 2014, inclusive? Use the Year and Month columns. One number.",
          "SELECT SUM(Amount) FROM pcards WHERE Amount < 0 AND Year = 2014 AND Month BETWEEN 1 AND 3", ["where", "sum"], minutes=3, note=note)
    sql_q("SQL-PC-04", P, "How many transactions are there in each year, most recent year first? Columns: Year, count.",
          "SELECT Year, COUNT(*) FROM pcards GROUP BY Year ORDER BY Year DESC", ["group by", "order by"], ordered=True, minutes=2, note=note)
    sql_q("SQL-PC-05", P, "List cardholders (FullName) with more than 10 transactions in 2014, with their number of transactions. Columns: FullName, count. (Order is not checked.)",
          "SELECT FullName, COUNT(*) FROM pcards WHERE Year = 2014 GROUP BY FullName HAVING COUNT(*) > 10", ["group by", "having"], minutes=3, note=note)
    sql_q("SQL-PC-06", P, "In 2014, which cardholders returned more than $1,000 of goods in total? Show the name, the total returned (as stored, a negative number) and the number of returns. Columns: FullName, total, count.",
          "SELECT FullName, SUM(Amount), COUNT(*) FROM pcards WHERE Year = 2014 AND Amount < 0 GROUP BY FullName HAVING SUM(Amount) < -1000",
          ["where", "group by", "having"], minutes=4, note=note)
    sql_q("SQL-PC-07", P, "OSU did not pay 4.5% state sales tax. For each year, show total spent and the tax saved (4.5% of total), most recent year first. Columns: Year, total, tax saved.",
          "SELECT Year, SUM(Amount), SUM(Amount) * 0.045 FROM pcards GROUP BY Year ORDER BY Year DESC", ["group by", "calculated field"], ordered=True, minutes=3, note=note)
    sql_q("SQL-PC-08", P, "Show 2014 transactions over $3,000 at vendors with “amazon” anywhere in the name, smallest amount first (ties by FullName). Columns: Vendor, FullName, Amount, Year, Month.",
          "SELECT Vendor, FullName, Amount, Year, Month FROM pcards WHERE Vendor LIKE '%amazon%' AND Year = 2014 AND Amount > 3000 ORDER BY Amount, FullName",
          ["like", "order by"], ordered=True, minutes=3, note=note)
    sql_q("SQL-PC-09", P, "Risk-rank 2014 returns: returns of $1,000 or more (in absolute value) are High, $500 to under $1,000 Medium, under $500 Low. Show the number of returns in each risk class. Columns: risk class (High/Medium/Low), count.",
          "SELECT CASE WHEN -Amount >= 1000 THEN 'High' WHEN -Amount >= 500 THEN 'Medium' ELSE 'Low' END, COUNT(*) FROM pcards WHERE Year = 2014 AND Amount < 0 GROUP BY 1",
          ["case when", "group by"], minutes=4, note=note)


def build_film():
    F = "film"
    off = "official course answer (solutions.xlsx)"
    sol = "Use the course solution approach: import the CSV, then formulas/pivot tables."
    qs = [
        ("01", "How many records does the table have?", [num("Records", 33502, 0)], ["import"], "COUNTA on EventID (or the row count after import)."),
        ("02", "How many columns does the table have?", [num("Columns", 14, 0)], ["import"], "Count the header fields."),
        ("03", "How many records have a missing value for ZipCode?", [num("Records", 11, 0)], ["blanks"], "COUNTBLANK on ZipCode."),
        ("04", "How many records have an invalid value for ZipCode?", [num("Records", 2, 0)], ["data quality"], "Filter ZipCode for values that are not 5-digit codes."),
        ("05", "Which column has the same value for every record? Give the exact column name.", [text("Column", "EventAgency")], ["data quality"], "A pivot or COUNTA(UNIQUE()) per column shows EventAgency has one value."),
        ("06", "What is the oldest EnteredOn value? Give the complete date and time.", [dt("Oldest EnteredOn", 2011, 12, 7, 16, 38)], ["dates"], "MIN(EnteredOn), formatted as date and time."),
        ("07", "What is the newest EnteredOn value? Give the complete date and time.", [dt("Newest EnteredOn", 2018, 2, 9, 12, 46)], ["dates"], "MAX(EnteredOn)."),
        ("08", "How many different event types are there?", [num("Event types", 4, 0)], ["pivot"], "Pivot table on EventType."),
        ("09", "What is the most common event type?", [text("Event type", "Shooting Permit")], ["pivot"], "Pivot table on EventType, sort by count."),
        ("10", "What is the most common Category of shooting permit?", [text("Category", "Television")], ["pivot", "filter"], "Pivot on Category, filtered to Shooting Permit."),
        ("11", "How many shooting permits were granted for student films?", [num("Permits", 193, 0)], ["countifs"], "COUNTIFS(EventType,\"Shooting Permit\",Category,\"Student\")."),
        ("12", "What percentage of shooting permits required approval by more than one Community Board?", [pct("Share", 0.0784, 0.0006)], ["text functions"], "A comma in CommunityBoard(s) means more than one board."),
        ("13", "In what year did the total number of shoots in Brooklyn outnumber shoots in Manhattan?", [num("Year", 2017, 0)], ["pivot", "dates"], "Pivot: YEAR(StartDateTime) by Borough, shooting permits only."),
        ("14", "What percentage of shoots in Manhattan take place on or near Broadway? Round to the nearest percent.", [pct("Share", 0.20, 0.006)], ["text search"], "ISNUMBER(SEARCH(\"BROADWAY\",ParkingHeld)) for Manhattan shooting permits."),
        ("15", "How many shooting permit applications since 2013 were submitted from a foreign country?", [num("Applications", 7, 0)], ["filter"], "Filter Country ≠ United States of America and EnteredOn ≥ 2013."),
        ("16", "What is the average duration of a shooting permit, in hours, rounded to the nearest half hour?", [num("Hours", 17, 0.26)], ["date arithmetic"], "AVERAGE((EndDateTime − StartDateTime) × 24)."),
        ("17", "What was the duration of the shortest shoot, in minutes?", [num("Minutes", 1, 0)], ["date arithmetic"], "MIN((End − Start) × 1440)."),
        ("18", "What is the average lead time in days between EnteredOn and StartDateTime for shooting permits? Round to the nearest day.", [num("Days", 5, 0.5)], ["date arithmetic"], "AVERAGE(StartDateTime − EnteredOn)."),
        ("19", "What is the most common day of the week for a shooting permit to start? Give the full day name.", [text("Day", "Thursday")], ["dates", "TEXT()"], "TEXT(StartDateTime,\"dddd\") then a pivot."),
        ("20", "What percentage of shooting permits include shooting at least part of a Friday? Two decimals.", [pct("Share", 0.2365, 0.00006)], ["dates"], "Check whether any day between start and end date is a Friday."),
    ]
    for n, p, a, sk, s in qs:
        val_q(f"XL-FP-{n}", "Excel", F, p, a, sk, s, 3, off)


def build_payroll():
    I = MAT / "Excels/3. IntegrateCo Assignment"
    p15 = pd.read_csv(I / "Analytics_mindset_case_studies_IntegrateCo_2015payroll.csv", sep="|")
    p16 = pd.read_csv(I / "Analytics_mindset_case_studies_IntegrateCo_2016payroll.csv", sep="|")
    p15["Pay_Date"] = pd.to_datetime(p15["Pay_Date"], format="%m/%d/%Y")
    assert p15.Pay_Date.max().month <= 6
    w15 = p15.Reg_Pay.sum() + p15.OT_Pay.sum()
    w16 = p16.Gross_Wages.sum()
    c15 = p15[["Reg_Pay", "OT_Pay", "Futa", "Suta", "Fica_Medc", "Work_Comp"]].sum().sum()
    c16 = p16[["Gross_Wages", "SocSec_Medicare", "FUTA", "SUTA", "Work_Comp"]].sum().sum()
    assert abs((w16 - w15) - 266000) < 1000, "should reproduce the CFO's ~$266,000"
    D = "payroll"
    src = "computed from the course files (2015: pipe-delimited, Jan–Jun; 2016: Jan–Jun)"
    val_q("XL-PY-01", "Excel", D, "Import both pipe-delimited payroll files. How many data rows does each file have?",
          [num("2015 rows", len(p15), 0), num("2016 rows", len(p16), 0)], ["import", "delimiters"],
          "Data → From Text/CSV, delimiter |. Count the rows below the header.", 3, src)
    val_q("XL-PY-02", "Excel", D, "What were total gross wages in the first six months of each year? (2015 = Reg_Pay + OT_Pay; 2016 = Gross_Wages.) Then give the increase.",
          [num("2015 gross wages", w15, 1), num("2016 gross wages", w16, 1), num("Increase", w16 - w15, 1)],
          ["sum", "mapping fields"], "The 2015 system splits regular and overtime pay; the 2016 system reports one Gross_Wages field. The increase reconciles to the CFO's ≈ $266,000.", 5, src)
    val_q("XL-PY-03", "Excel", D, "What was total payroll cost including employer taxes and workers' comp? (2015: Reg_Pay, OT_Pay, Futa, Suta, Fica_Medc, Work_Comp; 2016: Gross_Wages, SocSec_Medicare, FUTA, SUTA, Work_Comp.)",
          [num("2015 total cost", c15, 1), num("2016 total cost", c16, 1)], ["sum", "mapping fields"],
          "Map each 2015 field to its 2016 equivalent and sum.", 5, src)
    val_q("XL-PY-04", "Excel", D, "How many different employees were paid in each period? (2015: Emp_No; 2016: Employee_Number.)",
          [num("2015 employees", p15.Emp_No.nunique(), 0), num("2016 employees", p16.Employee_Number.nunique(), 0)],
          ["unique count"], "COUNTA(UNIQUE(range)) or a pivot table with distinct count.", 4, src)
    val_q("XL-PY-05", "Excel", D, "How many regular hours were worked in each period? (2015: Reg_Hrs; 2016: Hours.)",
          [num("2015 regular hours", p15.Reg_Hrs.sum(), 0.5), num("2016 regular hours", p16.Hours.sum(), 0.5)],
          ["sum"], "More hours with fewer employees: part of the explanation for higher payroll.", 3, src)


def build_codes():
    df = pd.read_excel(MAT / "Excels/2. ETL Assignment/data/case 2/Analytics_mindset_case_studies_ETL_Case2_Excel.xlsx", sheet_name="Case 2")
    parts = df.EmployeeCode.str.extract(r"^([A-Z]{3})(\d+)-(\d+)([A-Z])$")
    assert parts.notna().all().all(), "unexpected code format"
    df["Location"], df["EmpID"], df["Plant"], df["Period"] = parts[0], parts[1], parts[2].astype(int), parts[3]
    loc_counts = df.Location.value_counts()
    D = "codes"
    src = "computed from the course file (ETL case 2)"
    top = loc_counts.index[0]
    val_q("XL-PC-01", "Excel", D, "EmployeeCode combines Location (3 letters), EmpID, Plant ID and Pay Period, for example JAP1080-19M. Split it. Which location has the most employees, and how many?",
          [text("Location", top), num("Employees", int(loc_counts.iloc[0]), 0)], ["text-to-columns", "LEFT/MID/RIGHT"],
          "Text to Columns on '-', then LEFT(…,3) for the location; pivot on Location.", 5, src)
    val_q("XL-PC-02", "Excel", D, "How many employees are paid weekly (Pay Period W) and how many monthly (M)?",
          [num("Weekly (W)", int((df.Period == "W").sum()), 0), num("Monthly (M)", int((df.Period == "M").sum()), 0)],
          ["RIGHT", "countif"], "RIGHT(EmployeeCode,1) then COUNTIF.", 4, src)
    df["LocPlant"] = df.Location + df.Plant.astype(str)
    val_q("XL-PC-03", "Excel", D, "Build a LocationPlantID (location + plant number, for example JAP19). How many different location-plant combinations are there?",
          [num("Combinations", df.LocPlant.nunique(), 0)], ["concatenate", "unique"], "=Location&PlantID, then COUNTA(UNIQUE()).", 5, src)
    val_q("XL-PC-04", "Excel", D, "How many employees work at plant 21 in AUS?",
          [num("Employees", int(((df.Location == "AUS") & (df.Plant == 21)).sum()), 0)], ["countifs"], "COUNTIFS(Location,\"AUS\",PlantID,21).", 3, src)


def build_journal():
    x = MAT / "Excels/2. ETL Assignment/data/case 4/Analytics_mindset_case_studies_ETL_Case4_Excel.xlsx"
    je = pd.read_excel(x, sheet_name="JELineItems")
    coa = pd.read_excel(x, sheet_name="ChartOfAccounts")
    bu = pd.read_excel(x, sheet_name="BusinessUnits")
    je["GLAccountNumber"] = je.GLAccountNumber.astype(str)
    coa["GLAccountNumber"] = coa.GLAccountNumber.astype(str)
    m = je.merge(coa, on="GLAccountNumber", how="left").merge(bu, on="BusinessUnitID", how="left")
    unmatched = int(m.AccountType.isna().sum())
    bal = je.groupby("JENumber").Amount.sum().round(2)
    D = "journal"
    src = "computed from the course file (ETL case 4)"
    val_q("XL-JE-01", "Excel", D, "How many journal-entry line items and how many distinct journal entries (JENumber) are there?",
          [num("Line items", len(je), 0), num("Journal entries", je.JENumber.nunique(), 0)], ["count", "unique"], "COUNTA and COUNTA(UNIQUE(JENumber)).", 3, src)
    val_q("XL-JE-02", "Excel", D, "What are total debits and total credits? (Use the Debit and Credit columns.)",
          [num("Total debits", je.Debit.sum(), 0.01), num("Total credits", je.Credit.sum(), 0.01)], ["sum", "control totals"],
          "SUM(Debit) and SUM(Credit). Credits are stored as negatives; debits + credits should be 0.", 3, src)
    val_q("XL-JE-03", "Excel", D, "How many journal entries do not balance (their Amounts do not sum to zero)?",
          [num("Unbalanced entries", int((bal != 0).sum()), 0)], ["sumif", "pivot", "audit"], "Pivot: JENumber rows, sum of Amount; count non-zero totals.", 5, src)
    exp_food = m[(m.AccountType == "Expenses") & (m.BusinessUnit == "FOOD SERVICE")].Amount.sum()
    val_q("XL-JE-04", "Excel", D, "Join the chart of accounts and business units to the line items. What is the net Amount posted to Expenses accounts for the FOOD SERVICE business unit?",
          [num("Net expenses, FOOD SERVICE", exp_food, 0.01)], ["xlookup", "sumifs"],
          f"XLOOKUP AccountType from ChartOfAccounts and BusinessUnit from BusinessUnits, then SUMIFS. ({unmatched} line items have no matching account.)", 6, src)
    val_q("XL-JE-05", "Excel", D, "How many line items have a GLAccountNumber that does not exist in the chart of accounts?",
          [num("Unmatched line items", unmatched, 0)], ["xlookup", "data quality"], "XLOOKUP with an \"not found\" result, then COUNTIF.", 4, src)


def build_regression():
    x = MAT / "Excels/4. Restaurant/data.xlsm"
    sales = pd.read_excel(x, sheet_name="salesData")
    weather = pd.read_excel(x, sheet_name="weatherData")
    games = pd.read_excel(x, sheet_name="gameData")
    d = sales.groupby("date").agg(sales=("netTotal", "sum"), sales364=("netTotal_364", "sum")).reset_index()
    d = d.merge(weather[["date", "tmax"]], on="date", how="left").merge(games[["date", "gameDay"]], on="date", how="left")
    d["gameDay"] = d.gameDay.fillna(0).astype(int)
    d = d.dropna(subset=["tmax"])
    d = d[d.sales364 > 0]
    models = {
        "A": smf.ols("sales ~ tmax", d).fit(),
        "B": smf.ols("sales ~ gameDay", d).fit(),
        "C": smf.ols("sales ~ sales364 * gameDay", d).fit(),
    }

    def table(res):
        rows = []
        for name in res.params.index:
            rows.append([name.replace("Intercept", "Intercept").replace("sales364:gameDay", "sales364 × gameDay"),
                         round(res.params[name], 3), round(res.bse[name], 3), round(res.tvalues[name], 2), round(res.pvalues[name], 4)])
        return {"rows": rows, "r2": round(res.rsquared, 3), "n": int(res.nobs)}

    tabs = {k: table(v) for k, v in models.items()}
    # predictions use the coefficients as displayed (3 decimals), which is what students calculate with
    A, B, C = (models[k].params.round(3) for k in "ABC")
    pA, pB, pC = models["A"].pvalues, models["B"].pvalues, models["C"].pvalues
    D = "restaurant"
    src = "regression run on the course file (daily sales = sum of netTotal; merged with weatherData and gameData)"
    desc = "Daily sales of Bob's restaurant (sum of netTotal per day) regressed on daily maximum temperature (tmax, °F), a game-day dummy (gameDay = 1 on game days) and sales on the same day one year earlier (sales364)."

    def pred(params, **xs):
        return params["Intercept"] + sum(params[k] * v for k, v in xs.items())

    sig = lambda p: "Yes" if p < 0.05 else "No"
    val_q("DI-RE-01", "Interpretation", D, "Model A: using the output, predict daily sales on a day with a maximum temperature of 80 °F.",
          [num("Predicted sales ($)", pred(A, tmax=80), 1)], ["prediction"], "Intercept + coefficient × 80.", 3, src, {"key": "A", "desc": desc, **tabs["A"]})
    val_q("DI-RE-02", "Interpretation", D, "Model A: is the effect of temperature on sales statistically significant at the 5% level?",
          [choice("Significant at 5%?", ["Yes", "No"], sig(pA["tmax"]))], ["p-value"], "Compare the p-value of tmax with 0.05.", 2, src, {"key": "A", "desc": desc, **tabs["A"]})
    val_q("DI-RE-03", "Interpretation", D, "Model B: by how many dollars do average sales on game days differ from other days? (Use a minus sign if lower.)",
          [num("Difference ($)", B["gameDay"], 1)], ["dummy variables"], "The gameDay coefficient is the average difference.", 2, src, {"key": "B", "desc": desc, **tabs["B"]})
    val_q("DI-RE-04", "Interpretation", D, "Model B: can you conclude, at the 5% level, that game days affect sales?",
          [choice("Significant at 5%?", ["Yes", "No"], sig(pB["gameDay"]))], ["p-value"], "Compare the p-value of gameDay with 0.05.", 2, src, {"key": "B", "desc": desc, **tabs["B"]})
    y364 = round(float(d.sales364.median()), -1)
    val_q("DI-RE-05", "Interpretation", D, f"Model C: predict sales on a game day when sales on the same day last year were ${y364:,.0f}.",
          [num("Predicted sales ($)", pred(C, sales364=y364, gameDay=1, **{"sales364:gameDay": y364}), 3)], ["interaction", "prediction"],
          "Intercept + b1·sales364 + b2·1 + b3·(sales364 × 1).", 4, src, {"key": "C", "desc": desc, **tabs["C"]})
    slope_game = C["sales364"] + C["sales364:gameDay"]
    val_q("DI-RE-06", "Interpretation", D, "Model C: on game days, how much do current sales change for each extra $1 of last year's sales? (Slope on game days.)",
          [num("Slope on game days", slope_game, 0.01)], ["interaction"], "b(sales364) + b(sales364 × gameDay).", 3, src, {"key": "C", "desc": desc, **tabs["C"]})


# ---------- run SQL references ----------
TIE_CHECKS = {
    # ordered questions: query that must return 0 if the ORDER BY keys never tie between different rows
    "SQL-SO-02": ("songs", "SELECT COUNT(*) FROM (SELECT tempo FROM songs GROUP BY tempo HAVING COUNT(*) > 1)"),
    "SQL-SO-03": ("songs", "SELECT COUNT(*) FROM (SELECT duration_ms FROM songs GROUP BY duration_ms HAVING COUNT(*) > 1 AND duration_ms >= (SELECT duration_ms FROM songs ORDER BY duration_ms DESC LIMIT 1 OFFSET 3))"),
    "SQL-PC-08": ("pcards", "SELECT COUNT(*) FROM (SELECT Amount, FullName FROM pcards WHERE Vendor LIKE '%amazon%' AND Year=2014 AND Amount>3000 GROUP BY Amount, FullName HAVING COUNT(DISTINCT Vendor||Month)>1)"),
}


def run_sql():
    conns = {k: sqlite3.connect(v) for k, v in DBS.items()}
    for q in QUESTIONS:
        if q["check"] != "result":
            continue
        cur = conns[q["dataset"]].execute(q["ref_sql"])
        cols = [c[0] for c in cur.description]
        rows = cur.fetchall()
        assert rows, f"{q['id']} returned no rows"
        q["expected"] = fingerprint(cols, rows)
        print(f"{q['id']:<12} rows={len(rows):<6} cols={len(cols)}")
    for q in QUESTIONS:  # a HAVING filter must actually remove rows, otherwise the question cannot test it
        if q["check"] == "result" and " HAVING " in q["ref_sql"]:
            loose = re.sub(r" HAVING .*?(?= ORDER BY|$)", "", q["ref_sql"])
            n_loose = len(conns[q["dataset"]].execute(loose).fetchall())
            assert n_loose > q["expected"]["nrows"], f"{q['id']}: HAVING removes nothing"
    for qid, (db, sql) in TIE_CHECKS.items():
        ties = conns[db].execute(sql).fetchone()[0]
        print(f"tie check {qid}: {ties}")
        if ties:
            for q in QUESTIONS:
                if q["id"] == qid:
                    q["ordered"] = False
                    q["prompt"] += " (Order is not checked because of ties.)"
    # self-checks against the official course answers
    ch = {q["id"]: q["expected"]["preview"][0][0] for q in QUESTIONS if q["dataset"] == "chinook" and q["check"] == "result" and q["expected"]["nrows"] == 1}
    for qid, want in {"SQL-CH-04": 4, "SQL-CH-05": 60, "SQL-CH-06": 49, "SQL-CH-07": 77, "SQL-CH-08": 10, "SQL-CH-12": 2}.items():
        assert ch[qid] == want, (qid, ch[qid], want)
    top = [q for q in QUESTIONS if q["id"] == "SQL-CH-14"][0]["expected"]["preview"][0][0]
    assert top == "Rock", top
    c = conns["chinook"]
    assert len(c.execute("PRAGMA table_info(Customer)").fetchall()) == 13
    assert c.execute("SELECT Country FROM Customer LIMIT 5").fetchall()[-1][0] == "Czech Republic"
    assert c.execute("SELECT LastName FROM Customer ORDER BY LastName LIMIT 3").fetchall()[-1][0] == "Bernard"
    assert c.execute("SELECT COUNT(*) FROM Employee WHERE strftime('%w', BirthDate)='0'").fetchone()[0] == 2
    age = c.execute("SELECT MAX((julianday(HireDate)-julianday(BirthDate))/365.25) FROM Employee").fetchone()[0]
    assert abs(age - 55.5) < 0.3, age
    print("Chinook answers match the official course answers.")


def verify_film():
    p = MAT / "Excels/1. PWC Excel Assignment/NYC_Film_Permits.txt"
    f = pd.read_csv(p, dtype=str)
    assert len(f) == 33502 and f.shape[1] == 14
    assert f.ZipCode.isna().sum() == 11
    sp = f[f.EventType == "Shooting Permit"]
    assert (sp.Category == "Student").sum() == 193
    print("NYC film permit answers spot-checked against the data.")


def copy_data():
    DATA.mkdir(parents=True, exist_ok=True)
    DL.mkdir(parents=True, exist_ok=True)
    manifest = {}
    for k, src in DBS.items():
        raw = src.read_bytes()
        z = gzip.compress(raw, 6)
        parts = [z[i:i + MAX_PART] for i in range(0, len(z), MAX_PART)]
        names = []
        for i, part in enumerate(parts):
            name = f"{k}.sqlite.gz" if len(parts) == 1 else f"{k}.sqlite.gz.part{i + 1}"
            (DATA / name).write_bytes(part)
            names.append(f"data/{name}")
        manifest[k] = {"parts": names, "bytes": len(z)}
        print(f"{k}: {len(raw) / 1e6:.1f} MB -> {len(z) / 1e6:.1f} MB in {len(parts)} file(s)")
    shutil.copy(DBS["chinook"], DL / "chinook.sqlite")
    shutil.copy(DBS["songs"], DL / "songs.sqlite")
    shutil.copy(MAT / "Excels/1. PWC Excel Assignment/NYC_Film_Permits.txt", DL / "NYC_Film_Permits.csv")
    I = MAT / "Excels/3. IntegrateCo Assignment"
    for n in ["2015payroll", "2016payroll", "jobcodes", "locations"]:
        shutil.copy(I / f"Analytics_mindset_case_studies_IntegrateCo_{n}.csv", DL / f"IntegrateCo_{n}.csv")
    # data-only copies (no solution sheets, no macros)
    with pd.ExcelWriter(DL / "ETL_Case2_plant_codes.xlsx") as w:
        pd.read_excel(MAT / "Excels/2. ETL Assignment/data/case 2/Analytics_mindset_case_studies_ETL_Case2_Excel.xlsx", sheet_name="Case 2").to_excel(w, sheet_name="Case 2", index=False)
    x4 = MAT / "Excels/2. ETL Assignment/data/case 4/Analytics_mindset_case_studies_ETL_Case4_Excel.xlsx"
    with pd.ExcelWriter(DL / "ETL_Case4_journal_entries.xlsx") as w:
        for s in ["JELineItems", "BusinessUnits", "ChartOfAccounts", "PreparerInfo", "Source"]:
            pd.read_excel(x4, sheet_name=s).to_excel(w, sheet_name=s, index=False)
    xr = MAT / "Excels/4. Restaurant/data.xlsm"
    with pd.ExcelWriter(DL / "Restaurant_data.xlsx") as w:
        for s in pd.ExcelFile(xr).sheet_names:
            pd.read_excel(xr, sheet_name=s).to_excel(w, sheet_name=s, index=False)
    return manifest


def make_exams():
    by = lambda prefix: [q["id"] for q in QUESTIONS if q["id"].startswith(prefix)]
    excel_sets = [("NYC film permits", by("XL-FP-")), ("IntegrateCo payroll", by("XL-PY-")), ("Plant codes", by("XL-PC-")), ("Journal entries", by("XL-JE-"))]
    sql_sets = [("Chinook", by("SQL-CH-")), ("Purchasing cards", by("SQL-PC-")), ("Songs", by("SQL-SO-")), ("Movies", by("SQL-MO-"))]
    exams = []
    rng = np.random.default_rng(2026)
    n = 1
    for xi, (xn, xq) in enumerate(excel_sets):
        for si, (sn, sq) in enumerate(sql_sets):
            if (xi + si) % 2:  # 8 balanced combinations
                continue
            pick_x = sorted(rng.choice(xq, size=min(3, len(xq)), replace=False).tolist())
            pick_s = sorted(rng.choice(sq, size=min(3, len(sq)), replace=False).tolist())
            exams.append({"id": f"DM-{n:02d}", "type": "Data manipulation", "title": f"Exam {n}: {xn} + {sn}",
                          "questions": pick_x + pick_s, "minutes": 20})
            n += 1
    exams.append({"id": "DI-01", "type": "Data interpretation", "title": "Regression exam: restaurant sales",
                  "questions": by("DI-RE-"), "minutes": 20})
    return exams


def main():
    build_film(); build_payroll(); build_codes(); build_journal()
    build_chinook(); build_pcards(); build_songs(); build_movies()
    build_regression()
    verify_film()
    run_sql()
    manifest = copy_data()
    out = {
        "generated": pd.Timestamp.now().strftime("%Y-%m-%d"),
        "datasets": {k: {**v, "db": manifest.get(k)} for k, v in DATASETS.items()},
        "questions": QUESTIONS,
        "exams": make_exams(),
    }
    (SITE / "questions.json").write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":"), default=float), encoding="utf-8")
    print(f"{len(QUESTIONS)} questions, {len(out['exams'])} exams written to site/questions.json")


if __name__ == "__main__":
    main()
