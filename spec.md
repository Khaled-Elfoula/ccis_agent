# spec.md — CCIS Support Agent

Course: SWE 485, King Saud University · Author: Khaled Elfoula
Companion file: `agent.md`. Part 1 of it is the runtime system prompt (between the `SYSTEM_PROMPT_START` / `SYSTEM_PROMPT_END` markers); Part 2 is developer notes. This file says **what to build and how it fits together**. If they disagree, `agent.md` wins on behavior and this file wins on structure.

## 1. Goal

A Telegram chat agent for the College of Computer and Information Sciences (CCIS) that answers college questions, shows free slots on faculty schedules, and hands off complex cases with a contact and an email template (Arabic or English). WhatsApp comes later through the same core.

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
5. Free-slot lookup on a faculty member's schedule (read-only, never books)
6. Complex-case hand-off: contact + email template (ar/en)

**Out (v1)**
- Booking or sending requests on the user's behalf
- Anything needing student login or records
- WhatsApp (v2, adapter only)
- Voice, images, file uploads
- Vector search / embeddings

## 4. Architecture

```
            ┌───────────── scheduled ─────────────┐
 Web pages ─► scrapers/ ─► clean + normalize ─► SQLite (FTS5)
                                                   ▲
 Telegram ─► bot/telegram.py ─► agent/loop.py ─► agent/tools.py
   user   ◄──────────────────── reply ◄─────────────┘
```

- **Channel adapter** (`bot/`): receives text, passes `(chat_id, text)` to the agent, sends back the reply. No business logic.
- **Agent loop** (`agent/loop.py`): calls the model with the system prompt block from `agent.md` and the tool list, runs requested tools, stops per the stop rules.
- **Tools** (`agent/tools.py`): read from SQLite only. Nothing is scraped while a user waits.
- **Scrapers** (`scrapers/`): one module per source, writing normalized rows to SQLite on a schedule.

## 5. Stack

Python 3.11+, `python-telegram-bot` (long polling), Google Gemini API through the `google-genai` Python SDK with function calling (model name set in `MODEL`; pick a current Flash-class model from Google AI Studio), `httpx` + `BeautifulSoup`, `rapidfuzz` (name matching), Playwright (Edugate form, and any page that needs JavaScript), SQLite with FTS5, APScheduler, `python-dotenv`, `pytest`.

## 6. Project layout

```
ccis-agent/
├── agent.md                 # Part 1 = system prompt, Part 2 = dev notes
├── spec.md
├── .env.example
├── requirements.txt
├── main.py                  # starts bot + scheduler
├── bot/telegram.py
├── agent/
│   ├── loop.py
│   ├── tools.py
│   ├── prompts.py           # extracts the marked block from agent.md, fills {today}
│   └── arabic.py            # normalization helpers
├── scrapers/
│   ├── base.py              # fetch, robots.txt, rate limit, cache
│   ├── sources.py           # source registry (id, url, refresh)
│   └── <one file per source>.py
├── db/
│   ├── schema.sql
│   └── ccis.sqlite          # generated, git-ignored
├── data/
│   └── sample_schedules.json   # fallback if schedules can't be scraped
└── tests/
    ├── test_tools.py
    ├── test_arabic.py
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

CREATE TABLE contacts (
  id INTEGER PRIMARY KEY,
  name TEXT, name_norm TEXT,
  role TEXT,                 -- advisor | faculty | office | department
  email TEXT,
  source_url TEXT NOT NULL,
  scraped_at TEXT NOT NULL
);

CREATE TABLE faculty (          -- one row per person; username is the join key
  id INTEGER PRIMARY KEY,
  username TEXT UNIQUE NOT NULL,   -- from faculty.ksu.edu.sa/ar/<username>
  name_ar TEXT, name_en TEXT, name_norm TEXT,
  rank TEXT, department TEXT,     -- CS | IT | CE | SE | IS
  building TEXT, room TEXT,       -- CCIS list shows "31|2115" = building|room
  email TEXT, phone TEXT,
  profile_url TEXT, list_url TEXT,
  scraped_at TEXT NOT NULL
);

CREATE TABLE office_hours (       -- from faculty.ksu.edu.sa profile "الساعات المكتبية"
  faculty_id INTEGER REFERENCES faculty(id),
  day TEXT, start TEXT, end TEXT,
  source_url TEXT NOT NULL, scraped_at TEXT NOT NULL
);

CREATE TABLE sections (           -- from Edugate guest timetable
  term TEXT, course_code TEXT, section TEXT,
  instructor_raw TEXT,            -- name exactly as Edugate shows it
  faculty_id INTEGER REFERENCES faculty(id),   -- NULL until matched
  match_score REAL,
  day TEXT, start TEXT, end TEXT, room TEXT,
  scraped_at TEXT NOT NULL
);

CREATE TABLE calendar_events (
  id INTEGER PRIMARY KEY,
  term TEXT,
  title TEXT,
  start_date TEXT,
  end_date TEXT,
  source_url TEXT NOT NULL,
  scraped_at TEXT NOT NULL
);
```

Timetables are stored per term in `sections` (from Edugate or snapshots). `data/sample_schedules.json` (fake data) is a demo fallback only.

**Arabic normalization** (applied to indexed text and to queries): remove tashkeel and tatweel, unify أ إ آ → ا, ى → ي, ة → ه, normalize Arabic-Indic digits to ASCII.

## 8. Tools

All tools return JSON. Every factual result includes `source_url` and `scraped_at`. Errors return `{"error": "..."}` and never raise into the loop.

| Tool | Input | Output |
|---|---|---|
| `search_kb` | `query: str`, `lang?: "ar"\|"en"`, `limit?: int=5` | `[{text, title, source_url, scraped_at}]` ranked by FTS5 |
| `get_calendar` | `term?: str`, `topic?: str` | `[{title, start_date, end_date, source_url, scraped_at}]` |
| `find_contact` | `person_or_role: str` | `[{name, rank, department, building, room, email, profile_url, source_url, scraped_at}]` |
| `get_free_slots` | `faculty_name: str`, `day?: str` | `{faculty, basis, slots: [{day, start, end}], source_url, scraped_at}` where `basis` is `office_hours` or `estimated_from_timetable`; or `{candidates: [...]}` if the name is ambiguous |
| `draft_email` | `to: str`, `case_summary: str`, `lang: "ar"\|"en"` | `{subject, body}` with `[Your name]`, `[Your ID]` placeholders |
| `handoff` | `reason: str`, `contact: str`, `lang: "ar"\|"en"` | `{message}` containing reason, contact, and template |

**Faculty identity.** The join key is the `username` in the faculty profile URL. CCIS list pages link to profiles in several shapes (`fac.ksu.edu.sa/<u>`, `faculty.ksu.edu.sa/ar/<u>`, `.../en/<u>`, `.../ar/<u>/cv/123`); extract it with `(?:fac|faculty)\.ksu\.edu\.sa/(?:(?:ar|en)/)?([A-Za-z0-9_.-]+)`. Edugate shows only a display name, so it must be matched to a `faculty` row (below).

**Name matching** (CCIS names are not always accurate, and Edugate spells them differently). Normalize both sides: remove titles (د. / أ.د. / أ. / Dr / Prof), tashkeel, and unify alef/ya/ta-marbuta. Score with `rapidfuzz.fuzz.token_set_ratio`. Accept automatically only if the score is >= 85 **and** the first and last name tokens both match **and** the runner-up is at least 5 points lower. Otherwise return up to 3 `candidates` and let the agent ask the user to pick. Save accepted matches in `sections.faculty_id` / `match_score`, and allow manual fixes in `data/name_overrides.json` (`{"edugate name": "username"}`) which always win.

**Free-slot logic** (read-only, never books):
1. If the faculty member has rows in `office_hours`, return them with `basis = "office_hours"`.
2. Otherwise compute from the timetable: take the working window (Sunday–Thursday, `WORK_DAY_START`–`WORK_DAY_END`), subtract that person's `sections` rows for the current term, and return gaps of at least 30 minutes with `basis = "estimated_from_timetable"`.
3. If neither exists, return `{"error": "no schedule data"}` and the agent hands off.

An estimate only means "not teaching then". The agent must say so and suggest confirming by email.

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

## 10. Telegram behavior

- `/start`: short greeting in Arabic and English, one line on what the agent can do.
- `/reset`: clears conversation memory.
- `/help`: lists the six capabilities.
- Any text message goes to the agent. Show the "typing" action while working.
- Replies are plain text, split if longer than 4000 characters.
- Non-text messages: reply that only text is supported.
- Basic rate limit: 10 messages per minute per chat.

## 11. Scraping rules

- Only fetch URLs listed in `scrapers/sources.py`.
- Respect `robots.txt`; use a clear User-Agent with a contact email.
- About 1 request per second per host; cache raw HTML on disk and use `If-Modified-Since` / ETag where possible.
- Parse defensively. If a selector fails, log which source and selector, keep the old data, and do not wipe the table.
- Store `scraped_at` on everything.

**Source registry** (fill the URLs before building the scrapers)

| Source ID | Covers | URL | Refresh | Status |
|---|---|---|---|---|
| `ccis_home` | College overview, departments, student resources | https://ccis.ksu.edu.sa/ar | weekly | verified (Drupal, static HTML) |
| `ccis_about` | College in brief | https://ccis.ksu.edu.sa/ar/aboutcollege | monthly | listed in nav, not yet opened |
| `ccis_contact` | College contact | https://ccis.ksu.edu.sa/ar/contact-us | monthly | listed in nav, not yet opened |
| `ccis_programs_bsc` / `_masters` / `_phd` | Programs | https://ccis.ksu.edu.sa/ar/bsc , /ar/masters , /ar/phd | weekly | listed in nav, not yet opened |
| `ccis_transfer` | Transfer to the college | https://ccis.ksu.edu.sa/ar/acceptance-and-transfer | weekly | listed in nav, not yet opened |
| `ccis_regulations` | Forms and regulations | https://ccis.ksu.edu.sa/ar/ccis-regulations | monthly | listed in nav, not yet opened |
| `ccis_affairs_agency` | Educational and Academic Affairs agency | https://ccis.ksu.edu.sa/ar/student-affairs-agency-mail | monthly | listed in nav, not yet opened |
| `fac_list_se` | SE faculty: name, rank, office (building\|room), phone, profile link | https://ccis.ksu.edu.sa/ar/se/se-faculty-and-staff | weekly | **verified** |
| `fac_list_cs` | CS faculty list | https://cs.ksu.edu.sa/ar/cs/faculty-and-staff | weekly | from nav, different host, not yet opened |
| `fac_list_it` | IT faculty list | https://ccis.ksu.edu.sa/ar/it/it-faculty-and-staff | weekly | from nav, not yet opened |
| `fac_list_ce` | CE faculty list | https://ccis.ksu.edu.sa/ar/ce/faculty-and-staff | weekly | from nav, not yet opened |
| `fac_list_is` | IS faculty list | https://ccis.ksu.edu.sa/is/is-faculty-and-staff | weekly | from nav, not yet opened |
| `fac_profile` | Per-person: **email**, office, office hours | https://faculty.ksu.edu.sa/ar/<username> | weekly | email and office **verified**; office-hours content not yet seen |
| `edugate_timetable` | Sections: course, time, room, instructor | https://edugate.ksu.edu.sa/ksu/ui/guest/timetable/index/scheduleCoursesIndex.faces (the search form; the older `mainScheduleTreeCoursesIndex.faces` link in the menu leads to it) | per term | form **verified**; results/details not yet seen (see 11.1) |
| `ksu_calendar` | Academic calendar | https://dar.ksu.edu.sa/ar/current (linked from the Edugate guest menu as "التقويم الجامعي") | daily | URL found, page not yet opened |

Allowed hosts: `ccis.ksu.edu.sa`, `cs.ksu.edu.sa`, `fac.ksu.edu.sa`, `faculty.ksu.edu.sa`, `edugate.ksu.edu.sa`, `dar.ksu.edu.sa`.

**Findings from checking the live pages**
- On the CCIS list pages the **email is an image** (`email_6.png`), not text. Take the email from the faculty profile page, which shows it as text. Do not guess emails from a username pattern.
- Office is written `building|room` (for example `31|2115`); some rows are incomplete (`31|`). The profile page shows only a room number. Prefer the CCIS list when it is complete, otherwise the profile, and keep `scraped_at`. The two pages can disagree.
- Profile pages have an office-hours tab (`#working_hours`). In the one profile checked, no hours text came through, so it may be empty or loaded by JavaScript. Check a profile that has hours before writing that parser.
- Profile fetches can time out. Use retries with backoff.
- Link shapes vary a lot (see Faculty identity in section 8).

### 11.1 Edugate guest timetable

Status: the search form page **opens without login** (`scheduleCoursesIndex.faces`). Results and details only appear after pressing the search button (JavaScript postback), so I have not seen them yet. A different Edugate URL was refused by my fetch tool because of robots rules, so read https://edugate.ksu.edu.sa/robots.txt yourself before automating anything.

**What the form page shows**
- Term shown on the page: الفصل الأول 1448/1449 (parse it and store it as `term`; do not hard-code it).
- Filters: academic degree, campus (المقر), college, department, specialization.
- Results columns: course code, course name, schedule type, section type, section status.
- Per-section details panel with: section times (اوقات الشعبة: day, time), final exam (day, time, date), colleges and departments holding the section, and **المحاضر (lecturer)**.
- Message on first load: results appear only after pressing search.

The `.faces` extension means JavaServer Faces: server-side form state (`javax.faces.ViewState`) and postbacks. A plain GET cannot reach results.

Plan, in order of preference:
1. **If robots/terms allow automation:** a Playwright script that selects college (علوم الحاسب) and department (for example هندسة البرمجيات), enters a course code, presses search, opens details for each section, and writes `sections` rows. Run it per term (or nightly during registration), never while a user waits. Loop over department course codes, not over students.
2. **If automation is not allowed or breaks:** snapshot mode. A person saves the results and details pages for the needed courses into `data/edugate_snapshots/<term>/*.html`, and the parser reads those files. Same parser, no live access. Ask the university for permission or an export in parallel.
3. **Fallback for demos:** `data/sample_schedules.json` with fake data.

Parser requirements: extract course code, section, days, start/end, room, and the lecturer name exactly as shown (`instructor_raw`); save raw HTML fixtures under `tests/fixtures/edugate/` and test the parser against them; never overwrite good data when a parse fails.

Build order for this part: (a) get one saved results page and one details page, (b) write the parser against those, (c) only then decide between 1 and 2.

## 12. Configuration

`.env`:
```
TELEGRAM_BOT_TOKEN=
GEMINI_API_KEY=
MODEL=            # a current Gemini Flash-class model name from Google AI Studio
MAX_TOOL_CALLS=4
HISTORY_MESSAGES=10
WORK_DAY_START=08:00
WORK_DAY_END=16:00
STALE_AFTER_DAYS=30
```

Never commit `.env` or `ccis.sqlite`.

## 13. Milestones

| # | Slice | Done when |
|---|---|---|
| M1 | Skeleton + Telegram echo | Bot replies to any text; `.env` loading works |
| M2 | One scraper + `search_kb` | A question about one page is answered from SQLite with a source link |
| M3 | Agent loop + stop rules | Model calls `search_kb`, answers, and stops; 4-call limit enforced |
| M4 | Arabic support | Arabic questions match Arabic pages; reply language follows the user |
| M5 | Calendar + contacts | `get_calendar` and `find_contact` work with sources |
| M6 | Faculty identity + free slots | Faculty list + profile scrapers fill `faculty`; Edugate parser (snapshots first) fills `sections`; names match with `rapidfuzz`; `get_free_slots` returns office hours or an estimate with the right `basis` |
| M7 | Hand-off + templates | Petition-type request returns reason, contact and ar/en template |
| M8 | Scheduler + hardening | Re-scrape runs on schedule; failures don't erase data; rate limit works |
| M9 | Eval pass | All acceptance tests below pass |

Each slice is committed separately and tested before the next one starts.

## 14. Acceptance tests

1. An in-scope English question gets a correct answer with a source link.
2. An Arabic question gets an Arabic answer.
3. "When is Dr. X free on Sunday?" returns slots, says whether they are office hours or an estimate from the class timetable, and states nothing was booked.
4. "Book me an appointment" is not booked; the agent offers free slots and the advisor's email.
5. "I want to withdraw after the deadline" produces a hand-off with contact and a template in the requested language.
6. A question with no source returns "I couldn't find this" plus a contact; no invented facts.
7. Data older than `STALE_AFTER_DAYS` carries a "may have changed" note.
8. A prompt-injection sentence planted in a scraped page is ignored.
9. An unanswerable question ends within 4 tool calls.
10. If a user types a student ID or password, the agent tells them not to share it and does not repeat it.
11. A broken selector in one scraper leaves existing data intact.

## 15. Risks and open questions

| Risk / question | Mitigation |
|---|---|
| Edugate is a JSF app and may disallow automation | Check robots.txt, Playwright per term, snapshot mode, or ask the university for permission or an export |
| Estimated slots are wrong (doctor busy with meetings) | Label as estimate, suggest emailing, never present as confirmed |
| Edugate instructor name does not match the CCIS name | `rapidfuzz` matching with thresholds, candidates list, manual `name_overrides.json` |
| CCIS list shows email as an image | Take email from the faculty profile page |
| Site layout changes break scrapers | Keep old data, log failures, test selectors against saved HTML fixtures |
| Arabic search misses due to spelling variants | Normalization plus tests in `test_arabic.py` |
| Source pages disagree | Prefer the more recent `scraped_at`; otherwise hand off |
| Does the doctor expect request sending? | Proposal text says no; confirm with the doctor |
| `ksu_calendar` page structure | URL known; page not yet opened |
