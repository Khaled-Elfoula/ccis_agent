# spec.md — CCIS Support Agent

Course: SWE 485, King Saud University · Author: Khaled Elfoula

Companion file: `agent.md`. Part 1 of it is the runtime system prompt (between the `SYSTEM_PROMPT_START` / `SYSTEM_PROMPT_END` markers); Part 2 is developer notes. This file says **what to build and how it fits together**. If they disagree, `agent.md` wins on behavior and this file wins on structure.

## 1. Goal

A Telegram chat agent for the College of Computer and Information Sciences (CCIS) that answers college questions, gives dates from the university's academic calendar, reports a faculty member's published office hours, and hands off complex cases with a contact and an email template (Arabic or English). WhatsApp comes later through the same core.

It is an **agent**: for each message it decides whether to answer, look something up, ask one question, or hand off, and it decides when to stop.

## 2. Users

- Students and visitors asking about programs, admission, deadlines, advisors.
- Language: Arabic and English.
- No login. No real student data is used or stored.

## 3. Scope

**In (v1)**
1. Program and department info
2. Admission requirements and steps
3. Academic calendar dates and deadlines
4. Advisor / department contact lookup
5. A faculty member's published office hours (read-only, never books, no estimates)
6. Complex-case hand-off: contact + email template (ar/en)

**Out (v1)**
- Booking or sending requests on the user's behalf
- Anything needing student login or records
- Timetable scraping, free-slot estimates or anything that computes availability
- WhatsApp (v2, adapter only)
- Voice, images, file uploads
- Vector search / embeddings

## 4. Architecture

```
              ┌──────── run by hand ─────────┐
Web pages ─► scrapers/ ─► clean + normalize ─► SQLite (FTS5)
                                                  ▲
Telegram ─► bot/telegram.py ─► agent/loop.py ─► agent/tools.py
  user   ◄──────────────────── reply ◄─────────────┘
```

- **Channel adapter** (`bot/`): receives text, passes `(chat_id, text)` to the agent, sends back the reply. No business logic.
- **Agent loop** (`agent/loop.py`): calls the model with the system prompt block from `agent.md` and the tool list, runs requested tools, stops per the stop rules.
- **Tools** (`agent/tools.py`): read from SQLite only. Nothing is scraped while a user waits.
- **Scrapers** (`scrapers/`): one module per source, writing normalized rows to SQLite. They run by hand (`python -m scrapers.run --all`), then the refreshed database is committed. The bot has no in-process scheduler.

## 5. Stack

Python 3.11+, `python-telegram-bot` (long polling), Google Gemini API through the `google-genai` Python SDK with function calling (model name set in `MODEL`; pick a current Flash-class model from Google AI Studio), `httpx` + `BeautifulSoup`, `rapidfuzz` (name matching), SQLite with FTS5, `python-dotenv`, `pytest`.

## 6. Project layout

```
ccis-agent/
├── agent.md                 # Part 1 = system prompt, Part 2 = dev notes
├── spec.md
├── README.md
├── .env.example
├── requirements.txt
├── main.py                  # starts the Telegram bot and the Render health server
├── bot/telegram.py
├── agent/
│   ├── loop.py
│   ├── tools.py
│   ├── prompts.py           # extracts the marked block from agent.md, fills {today}
│   ├── names.py             # Arabic-aware name matching
│   └── arabic.py            # normalization helpers
├── scrapers/
│   ├── base.py              # fetch, robots.txt, rate limit, cache
│   ├── sources.py           # source registry (id, url, refresh)
│   ├── ccis.py              # college pages
│   ├── faculty.py           # faculty list pages
│   ├── profiles.py          # faculty profile pages
│   ├── calendar.py          # academic calendar page
│   ├── deadlines.py         # student academic movements page
│   ├── catalog.py           # SE course catalog page
│   └── run.py               # the refresh command
├── db/
│   ├── schema.sql
│   └── ccis.sqlite          # generated, committed after a refresh
├── data/
│   └── name_overrides.json
├── scripts/
│   ├── prepare_deploy.py    # checkpoint the DB, print its size and the git commands
│   └── report_*.py          # data reports
└── tests/
    ├── test_tools.py
    ├── test_calendar.py
    ├── test_deadlines.py
    ├── test_catalog.py
    ├── test_arabic.py
    ├── test_names.py
    ├── test_hardening.py
    └── test_acceptance.py
```

## 7. Data model (SQLite)

```sql
CREATE TABLE pages (
  id INTEGER PRIMARY KEY,
  source_id TEXT NOT NULL,
  url TEXT NOT NULL,
  title TEXT,
  lang TEXT,                 -- 'ar' | 'en'
  scraped_at TEXT NOT NULL   -- ISO 8601
);

CREATE TABLE chunks (        -- searchable passages
  id INTEGER PRIMARY KEY,
  page_id INTEGER REFERENCES pages(id),
  text TEXT NOT NULL,
  text_norm TEXT NOT NULL    -- Arabic-normalized
);
CREATE VIRTUAL TABLE chunks_fts USING fts5(text_norm, content='chunks', content_rowid='id');

CREATE TABLE faculty (          -- one row per person; username is the join key
  id INTEGER PRIMARY KEY,
  username TEXT UNIQUE NOT NULL,   -- from faculty.ksu.edu.sa/ar/<username>
  name_ar TEXT, name_en TEXT, name_norm TEXT,
  rank TEXT, department TEXT,     -- CS | IT | CE | SE | IS
  building TEXT, room TEXT,       -- CCIS list shows "31|2115" = building|room
  email TEXT,
  email_source TEXT,              -- 'scraped' | 'derived' | 'derived_unverified'
  phone TEXT,
  profile_url TEXT, list_url TEXT,
  scraped_at TEXT NOT NULL
);

CREATE TABLE office_hours (       -- from faculty.ksu.edu.sa profile "الساعات المكتبية"
  faculty_id INTEGER REFERENCES faculty(id),
  day TEXT, start TEXT, end TEXT,
  source_url TEXT NOT NULL, scraped_at TEXT NOT NULL
);

CREATE TABLE calendar_events (    -- from dar.ksu.edu.sa/ar/current
  id INTEGER PRIMARY KEY,
  academic_year TEXT NOT NULL,    -- from the page title, e.g. "2026-2027"
  term TEXT,                      -- semester header as the page writes it
  title TEXT NOT NULL,
  weekday TEXT,                   -- as the page writes it
  date_gregorian TEXT NOT NULL,   -- ISO YYYY-MM-DD
  date_hijri TEXT,                -- YYYY-MM-DD, e.g. "1448-03-10"
  source_url TEXT NOT NULL,
  scraped_at TEXT NOT NULL
);

CREATE TABLE academic_deadlines (   -- from dar.ksu.edu.sa/ar/academictable
  id INTEGER PRIMARY KEY,
  academic_year TEXT NOT NULL,      -- "2026-2027", from the page heading
  term TEXT,                        -- the term heading as the page writes it
  title TEXT NOT NULL,              -- the movement, as the page writes it
  kind TEXT NOT NULL,               -- 'action' | 'holiday' | 'semester_start' | 'exam'
  start_date TEXT,                  -- ISO YYYY-MM-DD; null when the row carries no date
  end_date TEXT,                    -- ISO YYYY-MM-DD; null for a one-date row
  start_weekday TEXT, end_weekday TEXT,   -- as the page writes them
  note TEXT,                        -- the * footnote, or the row's own text when it has no dates
  page_updated TEXT,                -- the page's "آخر تحديث" line
  source_url TEXT NOT NULL, scraped_at TEXT NOT NULL
);

CREATE TABLE courses (              -- from ccis.ksu.edu.sa/ar/se/se-course-catalog
  id INTEGER PRIMARY KEY,
  code_display TEXT NOT NULL,       -- as written, e.g. "هاب٣١٢"
  code_norm TEXT NOT NULL,          -- ASCII digits, no space, e.g. "هاب312"
  number INTEGER, level TEXT,       -- 'bachelor' (2xx-4xx) | 'graduate' (5xx-7xx)
  name_ar TEXT,                     -- null when the page lists no name
  hours_text TEXT,                  -- raw, e.g. "3(3+0+1)"
  credit_hours INTEGER, lecture_hours INTEGER, lab_hours INTEGER, tutorial_hours INTEGER,
  prerequisites_text TEXT, prerequisite_codes TEXT,          -- JSON list of code_norm
  prerequisite_min_credit_hours INTEGER,                     -- 95 for "95 ساعة"
  corequisites_text TEXT, corequisite_codes TEXT,            -- JSON list of code_norm
  description_ar TEXT,
  name_en TEXT, description_en TEXT,  -- null until the English page is scraped
  source_url TEXT NOT NULL, scraped_at TEXT NOT NULL
);

CREATE TABLE programs (             -- from the three CCIS program pages
  id INTEGER PRIMARY KEY,
  degree TEXT NOT NULL,             -- 'بكالوريوس' | 'ماجستير' | 'دكتوراه'
  department TEXT,                   -- CS | CE | IT | SE | IS | 'مشترك'
  name_ar TEXT NOT NULL,             -- as the page writes it
  name_en TEXT,                      -- null until an English page is scraped
  requirements_ar TEXT,              -- admission conditions shown under the programme
  source_url TEXT NOT NULL, scraped_at TEXT NOT NULL
);
```

The calendar keeps **one row per date**: `بداية ...` and `نهاية ...` are separate rows and no date range is computed or stored. The academic table keeps one row per movement, with its start and end dates as the page gives them. Both parsers check each weekday against its Gregorian date and flag a mismatch; neither corrects one.

`courses` hours follow the page's `credit(lecture+lab+tutorial)` form: `3(3+0+1)` is 3 credit hours, 3 lecture hours, 0 lab hours and 1 tutorial hour a week. Prerequisites are course codes joined by `+`, or a credit-hour requirement such as `95 ساعة`, which is stored as `prerequisite_min_credit_hours` rather than as a code. Every anomaly the parser meets (an empty name, an hours text it cannot read, a duplicate code, a missing label) is reported and left unfixed.

`programs` keeps one row per degree programme on the three program pages. The parser reads the department heading that precedes each programme (`CS`, `CE`, `IT`, `SE`, `IS`, or `مشترك` for the shared cybersecurity programme) and attaches the admission conditions under `شروط القبول` to the programmes of that section. The bachelor page has no admission-conditions block, so its `requirements_ar` is null. A failed parse keeps the previously stored rows.

**Arabic normalization** (applied to indexed text and to queries): remove tashkeel and tatweel, unify أ إ آ → ا, ى → ي, ة → ه, fold the Persian letter forms ھ ی ک to ه ي ك, and normalize Arabic-Indic digits to ASCII.

## 8. Tools

All tools return JSON. `search_kb`, `get_calendar`, `find_course` and `list_programs` include `source_url` and `scraped_at`; `find_contact` and `get_office_hours` return contact details without that metadata. Errors return `{"error": "..."}` and never raise into the loop. A result whose `scraped_at` is older than `STALE_AFTER_DAYS` also carries `stale: true` and a `stale_note` with its date, so the agent can say the information may have changed.

| Tool | Input | Output |
|---|---|---|
| `search_kb` | `query: str`, `lang?: "ar"\|"en"`, `limit?: int=5` | `[{text, title, source_url, scraped_at}]` ranked by FTS5 |
| `list_programs` | `level?: "bachelor"\|"master"\|"phd"`, `department?: "CS"\|"CE"\|"IT"\|"SE"\|"IS"\|"shared"`, `include_requirements?: bool=false` | `[{degree, department, name, source_url, scraped_at, requirements?}]`, every matching programme |
| `get_calendar` | `topic?: str`, `term?: str`, `upcoming_only?: bool` | `[{title, term, academic_year, kind, start_date, end_date, weekdays, date_hijri, note, status, days_left_until_end?, days_until_start?, source_url, scraped_at}]`, from `calendar_events` and `academic_deadlines` together |
| `find_course` | `query: str`, `level?: "bachelor"\|"graduate"` | one exact code: `{code, code_display, name, level, hours{credit, lecture, lab, tutorial, text}, prerequisites{text, codes, min_credit_hours}, corequisites{text, codes}, description, unlocks[], source_url, scraped_at}`; several matches: `[{code, name, credit_hours, description, source_url, scraped_at}]` (max 5) |
| `list_courses` | `level?: "bachelor"\|"graduate"` | `[{code, name, credit_hours, level}]` |
| `find_contact` | `person_or_role: str` | `[{name, rank, department, building, room, email, profile_url}]` |
| `get_office_hours` | `faculty_name: str`, `day?: str` | `{faculty: {name, rank, department, building, room, email, profile_url}, office_hours: [{day, start, end}]}`; or `{no_office_hours: true, faculty, email, office, profile_url}`; or `{status: "candidates, not confirmed", ...}` when the name is ambiguous |
| `draft_email` | `to: str`, `case_summary: str`, `lang: "ar"\|"en"` | `{subject, body}` with `[Your name]`, `[Your ID]` placeholders |
| `handoff` | `reason: str`, `contact: str`, `lang: "ar"\|"en"` | `{message}` containing reason, contact and template |

**Faculty identity.** The join key is the `username` in the faculty profile URL. CCIS list pages link to profiles in several shapes (`fac.ksu.edu.sa/<u>`, `faculty.ksu.edu.sa/ar/<u>`, `.../en/<u>`, `.../ar/<u>/cv/123`); extract it with `(?:fac|faculty)\.ksu\.edu\.sa/(?:(?:ar|en)/)?([A-Za-z0-9_.-]+)`.

**Name matching** (CCIS names are not always accurate). Normalize both sides: remove titles (د. / أ.د. / أ. / Dr / Prof), tashkeel, articles, and unify alef/ya/ta-marbuta. Score with `rapidfuzz.fuzz.token_set_ratio`. Accept automatically only if the score is >= 85 **and** the first and last name tokens both match **and** the runner-up is at least 5 points lower. Below 50 nothing matches. In between, return up to 3 `candidates` marked "candidates, not confirmed" and let the agent ask the user to pick. `data/name_overrides.json` (`{"name": "username"}`) always wins.

**Office hours** (read-only, never books):
1. If the faculty member has rows in `office_hours`, return them, optionally filtered to one day, without `source_url` or `scraped_at`. Rows whose times cannot be read (bad `HH:MM`, or end not after start) are not reported; `unreadable_rows` counts them.
2. If the person has no rows, return `{"no_office_hours": true}` with their email, office and `profile_url`, so the agent can suggest writing to them.
3. Nothing is estimated from a timetable, and no free time is computed.

**Calendar and academic deadlines.** `get_calendar` is one tool over two tables: `calendar_events` (single dates) and `academic_deadlines` (start and end windows). It returns one shape for both and filters by topic and term with the same Arabic normalization as search (so `الحذف` matches `حذف`, and `الفصل الأول` matches the full term name). `TOPIC_SYNONYMS` in `agent/tools.py` maps what students say to the rows they mean: a course withdrawal (`اعتذار`, `انسحاب من مقرر`, `حذف مقرر`) reaches `الاعتذار عن مقرر دراسي` and `الحذف والإضافة`; `انسحاب من الجامعة` reaches `الانسحاب من الجامعة`; and a bare "withdraw" reaches both, because they are different movements. `status` (`past` | `open_now` | `upcoming`), `days_left_until_end` and `days_until_start` are computed in code; the model never does date arithmetic. A row with no date (the make-up exam request) has `status: null` and its text in `note`. If nothing matches, the tool returns an error and the agent says the date is not on the university calendar. Results are capped at 8, open rows first.

**Courses.** `find_course` reads a code written in any order or script (`هاب312`, `312 هاب`, `هاب ٣١٢`, `hab 312`) or words from a name or description, and `list_courses` lists the catalog by level. One exact code returns the full record, with `unlocks` (the courses that list this code as a prerequisite) computed at query time. A null `name` means the catalog lists none: the agent says so and gives the description rather than inventing a name.

**Programmes.** `list_programs` reads the `programs` table, one row per programme from the bachelor, master and PhD pages. It returns every matching programme with its degree, department, name and source, and filters by `level` (`bachelor`, `master`, `phd`) and `department` (`CS`, `CE`, `IT`, `SE`, `IS`, `shared`). Admission conditions are returned only when `include_requirements` is true. A question about what the college offers, or about one department's or level's programmes, must use this tool rather than `search_kb`; the agent lists every row the tool returns, never just one.

**Email provenance (internal only).** `faculty.email_source` records where each address came from: `scraped` (read off the profile page), `derived` (built as `username@ksu.edu.sa` from the username, not confirmed on the page), `derived_unverified` (the same, but the profile page returned 403/404). The column serves the refresh logic only: a scraped email always wins over a stored derived one, and a profile fetch that finds no email keeps the stored address and its label. No tool returns the label, no prompt mentions it and no answer says where an email came from; the tools return the email alone.

## 9. Agent loop

```
history = last N messages for this chat_id (in memory, N = 10)
steps = 0
loop:
    response = model(system=system_prompt, tools=TOOLS, messages=history)
    if response has no tool calls: return response text
    if steps >= MAX_TOOL_CALLS (4): force a final answer or hand-off
    for each tool call: run it, append result to history
    steps += 1
```

- Stop rules and behavior come from the system prompt block in `agent.md`. Only that block is sent to the model, never the rest of `agent.md` or `spec.md`.
- Repeat guard: reject a call with identical tool and arguments to an earlier one in the same turn.
- Tool results are wrapped as data. Scraped text is never treated as instructions.
- Per-chat history is in memory only and cleared on restart or `/reset`.
- Each turn logs the chat id, the time, the intent and the tools called — never the message text.

## 10. Telegram behavior

- `/start`: short greeting in Arabic and English, one line on what the agent can do.
- `/reset`: clears conversation memory.
- `/help`: lists the six capabilities.
- Any text message goes to the agent. Show the "typing" action while working.
- Replies are plain text, split if longer than 4000 characters.
- Non-text messages: reply that only text is supported.
- Basic rate limit: 10 messages per minute per chat.
- If both the main and the fallback model fail, reply with one short bilingual apology.

## 11. Scraping rules

- Only fetch URLs listed in `scrapers/sources.py`.
- Respect `robots.txt`; use a clear User-Agent with a contact email.
- About 1 request per second per host; cache raw HTML on disk and use `If-Modified-Since` / ETag where possible.
- Parse defensively. If a selector fails, log which source and selector, keep the old data, and do not wipe the table. A calendar or page parse failure leaves the stored rows untouched, and an empty office-hours parse keeps the stored rows.
- Store `scraped_at` on every scraped row, even when a tool returns contact details without it.

**Source registry**

| Source ID | Covers | URL | Refresh | Status |
|---|---|---|---|---|
| `ccis_home` | College overview, departments, student resources | https://ccis.ksu.edu.sa/ar | weekly | verified (Drupal, static HTML) |
| `ccis_about` | College in brief | https://ccis.ksu.edu.sa/ar/aboutcollege | monthly | listed in nav |
| `ccis_contact` | College contact | https://ccis.ksu.edu.sa/ar/contact-us | monthly | listed in nav |
| `ccis_programs_bsc` / `_masters` / `_phd` | Programs | https://ccis.ksu.edu.sa/ar/bsc , /ar/masters , /ar/phd | weekly | listed in nav |
| `ccis_transfer` | Transfer to the college | https://ccis.ksu.edu.sa/ar/acceptance-and-transfer | weekly | listed in nav |
| `ccis_regulations` | Forms and regulations | https://ccis.ksu.edu.sa/ar/ccis-regulations | monthly | listed in nav |
| `ccis_affairs_agency` | Educational and Academic Affairs agency | https://ccis.ksu.edu.sa/ar/student-affairs-agency-mail | monthly | listed in nav |
| `fac_list_se` | SE faculty: name, rank, office (building\|room), phone, profile link | https://ccis.ksu.edu.sa/ar/se/se-faculty-and-staff | weekly | **verified** |
| `fac_list_cs` | CS faculty list | https://cs.ksu.edu.sa/ar/cs/faculty-and-staff | weekly | **verified** |
| `fac_list_it` | IT faculty list | https://ccis.ksu.edu.sa/ar/it/it-faculty-and-staff | weekly | **verified** |
| `fac_list_ce` | CE faculty list | https://ccis.ksu.edu.sa/ar/ce/faculty-and-staff | weekly | **verified** |
| `fac_list_is` | IS faculty list | https://ccis.ksu.edu.sa/is/is-faculty-and-staff | weekly | **verified** |
| `fac_profile` | Per-person: **email**, office, office hours | https://faculty.ksu.edu.sa/ar/<username> | weekly | email, office and office hours **verified** |
| `ksu_calendar` | Academic calendar | https://dar.ksu.edu.sa/ar/current | daily | **verified** (saved copy in `tests/fixtures/calendar/`) |
| `ksu_academic_table` | Student academic movements: add/drop, withdrawals, excuses, transfer, exemption, exams, holidays | https://dar.ksu.edu.sa/ar/academictable | weekly | **verified** (saved copy in `tests/fixtures/deadlines/`) |
| `ccis_se_catalog` | SE course catalog: code, name, hours, prerequisites, co-requisites, description | https://ccis.ksu.edu.sa/ar/se/se-course-catalog | weekly | **verified** (saved copy in `tests/fixtures/catalog/`) |

Allowed hosts: `ccis.ksu.edu.sa`, `cs.ksu.edu.sa`, `fac.ksu.edu.sa`, `faculty.ksu.edu.sa`, `dar.ksu.edu.sa`.

**Findings from checking the live pages**
- On the CCIS list pages the **email is an image** (`email_6.png`), not text. Take the email from the faculty profile page, which shows it as text. Do not guess emails from a username pattern.
- Office is written `building|room` (for example `31|2115`); some rows are incomplete (`31|`). The profile page shows only a room number. Prefer the CCIS list when it is complete, otherwise the profile, and keep `scraped_at`.
- Profile pages have an office-hours tab (`#working_hours`); the hours table is under `section.working_hours` and 50 people currently have rows.
- Profile fetches can time out. Use retries with backoff and stop after repeated throttling, keeping the old data.
- Link shapes vary a lot (see Faculty identity above).
- The calendar page is one table per term, four terms on the page (first semester, second semester, summer, and the next academic year), 22 dated rows in the saved copy. Each weekday matches its Gregorian date. It has no drop/add or withdrawal rows.
- The academic-movements page is three tables, one per term, each inside a `<details>` whose `<summary>` names the term, 49 movements in the saved copy. Each row is الحركة الأكاديمية | البداية | النهاية, and each date cell holds the weekday and the date separated by a line break. A single-cell bold row marks a semester start. `تقديم طلب اختبار نهائي بديل` carries text instead of dates, so it is stored with no dates and its text as the note. The `*` row (أسبوع التعزيز الأكاديمي) takes the footnote under its table as its note. The page's last-update line is `أغسطس 6, 2026`. Every weekday matches its Gregorian date.
- The SE catalog is 82 two-column tables, one per course, all 2-cell rows. Codes and hours use Arabic-Indic digits; the graduate rows use the Persian letter forms ھ ی ک. The level is the number's first digit: 2-4 is bachelor, 5-7 is graduate, so هاب٦٠٣ (6xx) and شامل٧٠٠ (7xx) are both graduate. `هاب٣١٤` has an empty name, `هاب٦٠٣`'s code row has no label, and `هاب٦٩٩` gives its hours as `وحدة دراسية واحدة`. All three are reported as anomalies and left unfixed. Prerequisites are codes joined by `+`, a credit-hour requirement (`95 ساعة`), or empty.

## 12. Configuration

`.env`:
```
TELEGRAM_BOT_TOKEN=
GEMINI_API_KEY=
MODEL=            # a current Gemini Flash-class model name from Google AI Studio
MODEL_FALLBACK=
MAX_TOOL_CALLS=4
HISTORY_MESSAGES=10
STALE_AFTER_DAYS=30
DB_PATH=          # optional; defaults to db/ccis.sqlite
```

Never commit `.env`. The database is committed on purpose after a refresh.

## 13. Milestones

| # | Slice | Done when |
|---|---|---|
| M1 | Skeleton + Telegram echo | Bot replies to any text; `.env` loading works |
| M2 | One scraper + `search_kb` | A question about one page is answered from SQLite with a source link |
| M3 | Agent loop + stop rules | Model calls `search_kb`, answers, and stops; 4-call limit enforced |
| M4 | Arabic support | Arabic questions match Arabic pages; reply language follows the user |
| M5 | Calendar + contacts | `get_calendar` and `find_contact` work with sources |
| M5.1 | Deadlines + course catalog | `get_calendar` also answers academic movements from `academic_deadlines` with a code-computed status; `find_course` and `list_courses` answer from `courses`; both pages are saved as fixtures and parsed in tests |
| M6 | Faculty identity + office hours | Faculty list + profile scrapers fill `faculty` and `office_hours`; names match with `rapidfuzz`; `get_office_hours` returns the published rows or says there are none |
| M6.1 | Degree programmes | The three program pages fill `programs`; `list_programs` returns every bachelor, master and PhD programme by level and department, with admission conditions on request |
| M7 | Hand-off + templates | Petition-type request returns reason, contact and ar/en template |
| M8 | Hardening + deploy | Failures keep old data; stale results are flagged; rate limit works; `scrapers.run --all` refreshes and prints counts; `scripts/prepare_deploy.py` prepares the database |
| M9 | Eval pass | All acceptance tests below pass |

Each slice is committed separately and tested before the next one starts.

## 14. Acceptance tests

1. An in-scope English question gets a correct answer with a source link.
2. An Arabic question gets an Arabic answer.
3. "When is Dr. X free on Sunday?" returns the doctor's published office hours with their profile link, or says no hours are published, and states nothing was booked.
4. "Book me an appointment" is not booked; the agent offers the published hours or the advisor's email.
5. "I want to withdraw after the deadline" produces a hand-off with contact and a template in the requested language.
6. A question with no source returns "I couldn't find this" plus a contact; no invented facts.
7. Data older than `STALE_AFTER_DAYS` carries a "may have changed" note.
8. A prompt-injection sentence planted in a scraped page is ignored.
9. An unanswerable question ends within 4 tool calls.
10. If a user types a student ID or password, the agent tells them not to share it and does not repeat it.
11. A broken selector in one scraper leaves existing data intact.
12. A date question is answered from `calendar_events` or `academic_deadlines` with a link, or the agent says it is not on the university calendar page.
13. "Can I still drop a course?" gives the status first ("open now until Saturday 28 November 2026") from the tool, and a bare "withdraw" is answered as two different things: a course withdrawal and a withdrawal from the university.
14. "What are the hours of هاب٤٤٤?" answers 2 credit hours, 0 lecture, 4 lab and 1 tutorial, with no date arithmetic and no invented name for هاب٣١٤.
15. "What programs does the college offer?" returns every programme in `programs`, grouped by degree and department, not just one.

## 15. Risks and open questions

| Risk / question | Mitigation |
|---|---|
| The calendar page changes layout or moves | Parser raises on a missing table and the stored rows are kept; saved copy in `tests/fixtures/calendar/` |
| The calendar has no drop/add or withdrawal rows | Those movements come from `academic_deadlines`; a topic that matches neither table returns an error and the agent says so instead of guessing |
| The catalog page changes layout | The parser finds labels by text, reports anomalies instead of fixing them, and raises on a page with no course; saved copy in `tests/fixtures/catalog/` |
| Office hours are stale or a doctor removed them | Stored `scraped_at` plus the stale flag; an empty parse keeps the stored rows |
| CCIS list shows email as an image | Take email from the faculty profile page |
| Site layout changes break scrapers | Keep old data, log failures, test selectors against saved HTML fixtures |
| Arabic search misses due to spelling variants | Normalization plus tests in `test_arabic.py` |
| Source pages disagree | Prefer the more recent `scraped_at`; otherwise hand off |
| Does the doctor expect request sending? | Proposal text says no; confirm with the doctor |
