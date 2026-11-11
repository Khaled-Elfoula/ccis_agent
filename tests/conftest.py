"""Make the project root importable when pytest runs from anywhere."""

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import agent.tools as tools_module  # noqa: E402
import db as database  # noqa: E402

FACULTY_ROWS = [
    ("withhours", "سعد عبدالله الأحمدي", "Saad Abdullah AlAhmadi", "CS", "31", "2179", "salahmadi@ksu.edu.sa"),
    ("sampleonly", "سامي بن صالح بن أحمد الوكيل", "Sami Alwakeel", "CE", None, "2206", "swakeel@ksu.edu.sa"),
    ("nodata", "محمد شميم حسين", None, "SE", "31", "2115", None),
    ("dup1", "محمد عبدالله السالم", None, "CS", "31", "2200", "msalem@ksu.edu.sa"),
    ("dup2", "محمد سعد السالم", None, "CS", "31", "2201", "msalem2@ksu.edu.sa"),
]

SAMPLE_SCHEDULES = {
    "_note": "demo sample data",
    "version": 1,
    "doctors": {
        "sampleonly": {
            "busy": [{"day": "Sunday", "start": "09:00", "end": "11:00", "label": "demo"}]
        },
        "withhours": {
            "busy": [{"day": "Sunday", "start": "08:00", "end": "16:00", "label": "demo"}]
        },
    },
}


@pytest.fixture()
def faculty_db(tmp_path, monkeypatch):
    """A temporary database with five faculty rows and office hours for one person."""
    path = tmp_path / "faculty.sqlite"
    conn = database.init_db(path)
    conn.executemany(
        "INSERT INTO faculty (username, name_ar, name_en, rank, department, building, room, email,"
        " profile_url, list_url, scraped_at) VALUES (?, ?, ?, 'أستاذ', ?, ?, ?, ?, ?, ?, ?)",
        [
            (
                username,
                name_ar,
                name_en,
                department,
                building,
                room,
                email,
                f"https://faculty.ksu.edu.sa/ar/{username}",
                f"https://ccis.ksu.edu.sa/ar/{department.lower()}/faculty-and-staff",
                "2026-11-10T10:00:00+00:00",
            )
            for username, name_ar, name_en, department, building, room, email in FACULTY_ROWS
        ],
    )
    faculty_id = conn.execute(
        "SELECT id FROM faculty WHERE username = 'withhours'"
    ).fetchone()["id"]
    conn.executemany(
        "INSERT INTO office_hours (faculty_id, day, start, end, source_url, scraped_at)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        [
            (
                faculty_id,
                day,
                start,
                end,
                "https://faculty.ksu.edu.sa/ar/withhours",
                "2026-11-10T10:00:00+00:00",
            )
            for day, start, end in (("Sunday", "10:00", "11:00"), ("Tuesday", "12:00", "13:00"))
        ],
    )
    conn.commit()
    conn.close()
    monkeypatch.setattr(tools_module, "load_overrides", lambda: {})
    return path


@pytest.fixture()
def sample_file(tmp_path):
    """A temporary demo schedule file with two doctors."""
    path = tmp_path / "sample_schedules.json"
    path.write_text(json.dumps(SAMPLE_SCHEDULES), encoding="utf-8")
    return path
