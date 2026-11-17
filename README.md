# CCIS Support Agent

A Telegram assistant for the College of Computer and Information Sciences (CCIS) at King Saud University.
It answers questions about programs and admission from the college's own pages, gives dates from the
university academic calendar, looks up a faculty member's contact details and published office hours, and
hands off complex cases with a contact and an email template in Arabic or English.

It is read-only: it never books, reserves, confirms or sends anything, and every fact it gives comes from a
stored page with a source or profile link.

## What the agent does

- **College info / programs** ✓ — departments, degree programs, admission requirements and steps, transfer
  and regulations, answered from the stored college pages (`search_kb`).
- **Academic calendar and important dates** ✓ — add/drop, withdrawals, postponing, excuses, exams and
  holidays, each with its status and a source link.
- **Courses** ✓ — **SWE only**: the SE department catalog, 82 courses (`هاب` / SWE codes), credit hours,
  prerequisites, co-requisites and description.
- **Doctor info** ✓ — name, rank, department, email, office (building + room, e.g. `31|2115`), profile link,
  and published office hours when they exist; otherwise it says none are published and gives the email.
  Phone is stored in the database but not returned by any tool.
- **Email template** ✓ — Arabic or English, with `[Your name]` / `[Your ID]` placeholders, and a contact for
  the hand-off.

Two things that are easy to leave out: general college questions (departments, forms, regulations) are
answered too, and petitions, exceptions and appeals are handed off to the right contact instead of being
decided. And it never books, sends, or invents a date or an email.

## Setup

Python 3.11+.

```bash
pip install -r requirements.txt
cp .env.example .env      # then fill in the three required values
```

| Setting | Meaning |
|---|---|
| `TELEGRAM_BOT_TOKEN` | Bot token from BotFather |
| `GEMINI_API_KEY` | Google AI Studio key |
| `MODEL` | A current Gemini Flash-class model name |
| `MODEL_FALLBACKS` | Models tried in order when the main one fails; defaults to `gemini-3.5-flash-lite`, `gemini-3.8-flash`, `gemini-3.6-flash`, `Gemini 4 Hydrogen` |
| `MAX_TOOL_CALLS` | Tool calls per user message, default 4 |
| `HISTORY_MESSAGES` | Messages kept per chat in memory, default 10 |
| `STALE_AFTER_DAYS` | After this many days a result is flagged as possibly changed, default 30 |
| `DB_PATH` | Optional; defaults to `db/ccis.sqlite` |

## Refresh the data

The database is filled by hand, never by the running bot:

```bash
python -m scrapers.run --all        # every source below plus row counts
python -m scrapers.run --calendar   # only the academic calendar
python -m scrapers.run --deadlines  # only the academic-movements table
python -m scrapers.run --catalog    # only the SE course catalog
python -m scrapers.run --programs   # only the three degree-program pages
python -m scrapers.run --faculty    # faculty lists and profiles (slow; respects throttling)
```

`--all` prints the row count of each table. Scrapers obey `robots.txt`, wait at least one second between
requests to the same host, cache the raw HTML under `db/cache/`, and keep the old rows when a page fails to
fetch or parse.

## Run the bot

```bash
python main.py
```

`main.py` starts long polling and, on Render, a small health server (`PORT`) plus a self-ping when
`RENDER_EXTERNAL_URL` is set. Nothing is scheduled inside the process.

## Deploy

The database is committed on purpose so a deployment ships with data:

```bash
python scripts/prepare_deploy.py     # checkpoint the WAL, show the size, print the git commands
```

It prints the exact `git add -f`, `git commit` and `git push` commands; it does not run them. Never commit
`.env`.

## Test

```bash
python -m pytest -q
```

The suite runs offline: the model is replaced by a scripted fake, and every scraper test uses saved HTML
fixtures. The calendar, academic-movements and course-catalog parsers are tested against
`tests/fixtures/calendar/current.html`, `tests/fixtures/deadlines/academic_table.html` and
`tests/fixtures/catalog/se_course_catalog.html`.

## Layout

| Path | What it holds |
|---|---|
| `agent/loop.py` | The tool-calling loop, stop rules, retries and the fallback model chain |
| `agent/tools.py` | `search_kb`, `list_programs`, `get_calendar`, `find_contact`, `get_office_hours`, `find_course`, `list_courses`, `draft_email`, `handoff` |
| `agent/prompts.py` | Loads the system prompt from the marked block in `agent.md` |
| `agent/names.py`, `agent/arabic.py` | Arabic normalization and faculty name matching |
| `bot/telegram.py` | Telegram adapter, rate limit, reply splitting, turn logging |
| `scrapers/` | One module per source plus the refresh command |
| `db/schema.sql` | `pages`, `chunks`, `faculty`, `office_hours`, `calendar_events`, `academic_deadlines`, `courses`, `programs` |
| `scripts/` | Data reports, email filling and `prepare_deploy.py` |
| `spec.md`, `agent.md` | The specification and the runtime system prompt |

## Data notes

- Only the URLs in `scrapers/sources.py` are fetched, all on allowed KSU hosts.
- Emails come from the faculty directory and profile pages. The internal `email_source` column records how
  each address was obtained and is used only when refreshing data; no tool returns it and no answer mentions it.
- Office hours are the doctor's published rows. The agent never estimates availability and never computes free
  time.
- The academic calendar is stored one row per date; `بداية` and `نهاية` are separate rows and no range is
  computed. The student academic movements (add/drop, withdrawals, excuses, transfer, exemption, exams,
  holidays) come from the academic-movements page and are stored as start and end windows. `get_calendar`
  reads both and computes the status and the days left in code; the model never does date arithmetic.
- The SE course catalog is stored one row per course. Hours follow the page's `credit(lecture+lab+tutorial)`
  form, prerequisites are codes or a credit-hour requirement, and the anomalies the page contains (an empty
  name, an hours text in words, a code row without a label) are reported by the refresh command and left
  unfixed.
