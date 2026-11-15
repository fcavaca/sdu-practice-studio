# SDU Practice Studio

Practice exams for *Data Analytics in Accounting* (SDU), with instant, deterministic answer checking against the real course datasets.

**Live site:** https://fcavaca.github.io/sdu-practice-studio/

## What it contains
- **96 questions**, built only from the course materials:
  - Excel: NYC film permits, IntegrateCo payroll, plant codes (ETL case 2), journal entries (ETL case 4).
  - SQL: Chinook, Songs, Movies, OSU purchasing cards.
  - Data interpretation: restaurant sales regressions.
- **9 timed full exams:** 8 data manipulation exams (Excel + SQL) and 1 regression exam.
- **Check my answer** (the added functionality):
  - SQL runs on the real database in the browser (sql.js) and is compared with the answer key on row count, column count, a control total per numeric column, and ordered and unordered fingerprints.
  - Excel figures are checked with tolerances.
  - The feedback says where the difference is. The solution unlocks after an attempt.
- **Self-test** (`#/selftest`): runs every reference query and a list of typical mistakes through the checker.

## Files
| Path | What |
|---|---|
| `index.html`, `app.js` | The whole site (no build step, no server) |
| `questions.json` | Question bank, exams and answer keys |
| `*.sqlite.gz`, `*.sqlite.gz.partN` | Course databases, gzipped. Movies and P-cards are split into parts of 15 MB or less for GitHub's web upload; the page joins the parts again. |
| `*.csv`, `*.xlsx`, `chinook.sqlite`, `songs.sqlite` | Datasets students download for the Excel questions (solution sheets removed) |
| `build_site.py` | Builds `questions.json` and the database files from the course materials, and checks the keys against the official course answers |

All files sit in the repository root because GitHub's web upload does not keep folders.

## Rebuilding
From the project folder that holds `Materials/`: `python builder/build_site.py`. Requires pandas, openpyxl and statsmodels.
