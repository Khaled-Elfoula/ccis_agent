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
