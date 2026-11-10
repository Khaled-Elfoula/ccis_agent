"""Fill the knowledge base: python -m scrapers.run [--only ID] [--force]."""

import argparse
import logging
from datetime import datetime, timezone

import config
import db as database
from agent.arabic import search_form
from scrapers import ccis
from scrapers.base import ScrapeError, fetch
from scrapers.sources import SOURCES, SOURCES_BY_ID

logger = logging.getLogger("scrapers.run")


def store_page(conn, source_id: str, url: str, parsed: ccis.ParsedPage) -> int:
    """Replace a page and its chunks in one transaction; return the chunk count."""
    scraped_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with conn:
        row = conn.execute("SELECT id FROM pages WHERE url = ?", (url,)).fetchone()
        if row is None:
            page_id = conn.execute(
                "INSERT INTO pages (source_id, url, title, lang, scraped_at) VALUES (?, ?, ?, ?, ?)",
                (source_id, url, parsed.title, parsed.lang, scraped_at),
            ).lastrowid
        else:
            page_id = row["id"]
            conn.execute(
                "UPDATE pages SET source_id = ?, title = ?, lang = ?, scraped_at = ? WHERE id = ?",
                (source_id, parsed.title, parsed.lang, scraped_at, page_id),
            )
            conn.execute("DELETE FROM chunks WHERE page_id = ?", (page_id,))
        conn.executemany(
            "INSERT INTO chunks (page_id, text, text_norm) VALUES (?, ?, ?)",
            [(page_id, chunk, search_form(chunk)) for chunk in parsed.chunks],
        )
    return len(parsed.chunks)


def run(only: set[str] | None = None, force: bool = False) -> int:
    """Scrape the selected sources and return the number of failures."""
    conn = database.init_db(config.DB_PATH)
    failures = 0
    try:
        for source in SOURCES:
            if only and source.id not in only:
                continue
            try:
                fetched = fetch(source.id, source.url, force=force)
                parsed = ccis.parse(fetched.html)
                count = store_page(conn, source.id, source.url, parsed)
            except (ScrapeError, ccis.ParseError) as exc:
                logger.error("%s: %s (existing data kept)", source.id, exc)
                failures += 1
                continue
            note = " (not modified)" if fetched.not_modified else ""
            print(f"{source.id:22} {count:3} chunks  {parsed.title}{note}")
    finally:
        conn.close()
    return failures


def main() -> None:
    """Parse arguments, run the scrape and exit non-zero on any failure."""
    parser = argparse.ArgumentParser(description="Scrape the CCIS pages into SQLite.")
    parser.add_argument("--only", help="Comma-separated source ids to scrape")
    parser.add_argument("--force", action="store_true", help="Ignore ETag/Last-Modified")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    only = {part.strip() for part in args.only.split(",") if part.strip()} if args.only else None
    unknown = (only or set()) - set(SOURCES_BY_ID)
    if unknown:
        raise SystemExit(f"unknown source id(s): {', '.join(sorted(unknown))}")
    raise SystemExit(1 if run(only, args.force) else 0)


if __name__ == "__main__":
    main()
