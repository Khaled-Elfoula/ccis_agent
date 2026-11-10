"""Gemini function declarations and their SQLite-backed implementations."""

import sqlite3
from pathlib import Path

from google.genai import types

import config
import db as database
from agent.arabic import fts_query

MAX_LIMIT = 20
DEFAULT_LIMIT = 5
MAX_PASSAGE_CHARS = 1500


def search_kb(
    query: str,
    lang: str | None = None,
    limit: int = DEFAULT_LIMIT,
    db_path: str | Path | None = None,
) -> list[dict] | dict:
    """Search indexed CCIS pages with FTS5 and return passages with their sources."""
    if not query or not query.strip():
        return {"error": "empty query"}
    match = fts_query(query)
    if not match:
        return {"error": "query has no searchable words"}
    try:
        size = max(1, min(int(limit), MAX_LIMIT))
    except (TypeError, ValueError):
        size = DEFAULT_LIMIT

    sql = [
        "SELECT c.text, p.title, p.url AS source_url, p.scraped_at, bm25(chunks_fts) AS rank",
        "FROM chunks_fts",
        "JOIN chunks c ON c.id = chunks_fts.rowid",
        "JOIN pages p ON p.id = c.page_id",
        "WHERE chunks_fts MATCH ?",
    ]
    params: list[object] = [match]
    if lang in ("ar", "en"):
        sql.append("AND p.lang = ?")
        params.append(lang)
    sql.append("ORDER BY rank LIMIT ?")
    params.append(size)

    try:
        conn = database.connect(db_path or config.DB_PATH)
    except sqlite3.Error as exc:
        return {"error": f"knowledge base unavailable: {exc}"}
    try:
        rows = conn.execute(" ".join(sql), params).fetchall()
    except sqlite3.Error as exc:
        return {"error": f"search failed: {exc}"}
    finally:
        conn.close()

    if not rows:
        return {"error": "no results found"}
    return [
        {
            "text": row["text"][:MAX_PASSAGE_CHARS],
            "title": row["title"],
            "source_url": row["source_url"],
            "scraped_at": row["scraped_at"],
        }
        for row in rows
    ]


SEARCH_KB_DECLARATION = types.FunctionDeclaration(
    name="search_kb",
    description=(
        "Search the College of Computer and Information Sciences knowledge base: college overview and "
        "departments, bachelor/master/PhD programs, admission requirements, transfer conditions, forms "
        "and regulations, and the college's contact details and academic affairs agency. "
        "Use it for any factual question about the college before answering; do not answer such questions "
        "from memory. Queries may be Arabic or English. "
        "Returns a ranked list of passages, each with the passage text, the page title, the source_url to "
        "cite and the scraped_at date to check for staleness. "
        "Returns {\"error\": ...} when nothing matches, which means you must not invent an answer."
    ),
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={
            "query": types.Schema(
                type=types.Type.STRING,
                description="What to look for, as a few keywords or a short phrase in the user's language.",
            ),
            "lang": types.Schema(
                type=types.Type.STRING,
                enum=["ar", "en"],
                description="Restrict results to Arabic or English pages. Omit to search all pages.",
            ),
            "limit": types.Schema(
                type=types.Type.INTEGER,
                description=f"How many passages to return, 1 to {MAX_LIMIT}. Default {DEFAULT_LIMIT}.",
            ),
        },
        required=["query"],
    ),
)

TOOLS = [types.Tool(function_declarations=[SEARCH_KB_DECLARATION])]
HANDLERS = {"search_kb": search_kb}


def execute_tool(name: str, arguments: dict | None) -> list | dict:
    """Run a tool by name, returning JSON-ready data and never raising."""
    handler = HANDLERS.get(name)
    if handler is None:
        return {"error": f"unknown tool: {name}"}
    try:
        return handler(**(arguments or {}))
    except TypeError as exc:
        return {"error": f"bad arguments for {name}: {exc}"}
    except Exception as exc:  # a failing tool must never break the agent loop
        return {"error": f"{name} failed: {exc}"}
