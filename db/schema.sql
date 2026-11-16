CREATE TABLE IF NOT EXISTS pages (
  id INTEGER PRIMARY KEY,
  source_id TEXT NOT NULL,
  url TEXT NOT NULL,
  title TEXT,
  lang TEXT,
  scraped_at TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS pages_url_idx ON pages(url);

CREATE TABLE IF NOT EXISTS chunks (
  id INTEGER PRIMARY KEY,
  page_id INTEGER REFERENCES pages(id),
  text TEXT NOT NULL,
  text_norm TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS chunks_page_idx ON chunks(page_id);

CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
  text_norm,
  content='chunks',
  content_rowid='id'
);

CREATE TRIGGER IF NOT EXISTS chunks_ai AFTER INSERT ON chunks BEGIN
  INSERT INTO chunks_fts(rowid, text_norm) VALUES (new.id, new.text_norm);
END;

CREATE TRIGGER IF NOT EXISTS chunks_ad AFTER DELETE ON chunks BEGIN
  INSERT INTO chunks_fts(chunks_fts, rowid, text_norm) VALUES ('delete', old.id, old.text_norm);
END;

CREATE TRIGGER IF NOT EXISTS chunks_au AFTER UPDATE ON chunks BEGIN
  INSERT INTO chunks_fts(chunks_fts, rowid, text_norm) VALUES ('delete', old.id, old.text_norm);
  INSERT INTO chunks_fts(rowid, text_norm) VALUES (new.id, new.text_norm);
END;

CREATE TABLE IF NOT EXISTS faculty (
  id INTEGER PRIMARY KEY,
  username TEXT UNIQUE NOT NULL,
  name_ar TEXT,
  name_en TEXT,
  name_norm TEXT,
  rank TEXT,
  department TEXT,
  building TEXT,
  room TEXT,
  email TEXT,
  email_source TEXT,         -- 'scraped' | 'derived' | 'derived_unverified'
  phone TEXT,
  profile_url TEXT,
  list_url TEXT,
  scraped_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS faculty_username_idx ON faculty(username);

CREATE TABLE IF NOT EXISTS office_hours (
  id INTEGER PRIMARY KEY,
  faculty_id INTEGER REFERENCES faculty(id) ON DELETE CASCADE,
  day TEXT,
  start TEXT,
  end TEXT,
  source_url TEXT NOT NULL,
  scraped_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS office_hours_faculty_idx ON office_hours(faculty_id);

CREATE TABLE IF NOT EXISTS calendar_events (
  id INTEGER PRIMARY KEY,
  academic_year TEXT NOT NULL,   -- from the page title, e.g. "2026-2027"
  term TEXT,                     -- semester header as the page writes it
  title TEXT NOT NULL,
  weekday TEXT,                  -- as the page writes it
  date_gregorian TEXT NOT NULL,   -- ISO YYYY-MM-DD
  date_hijri TEXT,               -- YYYY-MM-DD, e.g. "1448-03-10"
  source_url TEXT NOT NULL,
  scraped_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS calendar_events_date_idx ON calendar_events(date_gregorian);
CREATE TABLE IF NOT EXISTS academic_deadlines (
  id INTEGER PRIMARY KEY,
  academic_year TEXT NOT NULL,   -- from the page heading, e.g. "2026-2027"
  term TEXT,                     -- term heading as the page writes it
  title TEXT NOT NULL,           -- the movement, as the page writes it
  kind TEXT NOT NULL,            -- 'action' | 'holiday' | 'semester_start' | 'exam'
  start_date TEXT,               -- ISO YYYY-MM-DD, null when the row carries no date
  end_date TEXT,                 -- ISO YYYY-MM-DD, null for a one-date row
  start_weekday TEXT,            -- as the page writes it
  end_weekday TEXT,              -- as the page writes it
  note TEXT,                     -- the * footnote, or the row's own text when it has no dates
  page_updated TEXT,             -- the page's "آخر تحديث" line
  source_url TEXT NOT NULL,
  scraped_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS academic_deadlines_start_idx ON academic_deadlines(start_date);

CREATE TABLE IF NOT EXISTS courses (
  id INTEGER PRIMARY KEY,
  code_display TEXT NOT NULL,            -- as the page writes it, e.g. "هاب٣١٢"
  code_norm TEXT NOT NULL,               -- ASCII digits, no space, e.g. "هاب312"
  number INTEGER,                        -- 312
  level TEXT,                            -- 'bachelor' | 'graduate'
  name_ar TEXT,                          -- null when the page lists no name
  hours_text TEXT,                       -- raw, e.g. "3(3+0+1)"
  credit_hours INTEGER,                  -- 3 in "3(3+0+1)"
  lecture_hours INTEGER,                 -- 3 in "3(3+0+1)"
  lab_hours INTEGER,                     -- 0 in "3(3+0+1)"
  tutorial_hours INTEGER,                -- 1 in "3(3+0+1)"
  prerequisites_text TEXT,               -- raw, e.g. "هاب٢١١ + عال١١٣" or "95 ساعة"
  prerequisite_codes TEXT,               -- JSON list of code_norm
  prerequisite_min_credit_hours INTEGER, -- 95 for "95 ساعة", else null
  corequisites_text TEXT,                -- raw
  corequisite_codes TEXT,                -- JSON list of code_norm
  description_ar TEXT,
  name_en TEXT,                          -- null until the English page is scraped (C5)
  description_en TEXT,                   -- null until the English page is scraped (C5)
  source_url TEXT NOT NULL,
  scraped_at TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS courses_code_idx ON courses(code_norm);
CREATE INDEX IF NOT EXISTS courses_number_idx ON courses(number);

CREATE TABLE IF NOT EXISTS programs (
  id INTEGER PRIMARY KEY,
  degree TEXT NOT NULL,                 -- 'بكالوريوس' | 'ماجستير' | 'دكتوراه'
  department TEXT,                       -- 'CS' | 'CE' | 'IT' | 'SE' | 'IS' | 'مشترك'
  name_ar TEXT NOT NULL,                 -- as the page writes it
  name_en TEXT,                          -- null until an English page is scraped
  requirements_ar TEXT,                  -- admission conditions shown under the programme
  source_url TEXT NOT NULL,
  scraped_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS programs_source_idx ON programs(source_url);
CREATE INDEX IF NOT EXISTS programs_degree_idx ON programs(degree);
