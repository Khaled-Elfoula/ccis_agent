"""Tests for the degree-programme listing tool."""

import pytest

import db as database
from agent.tools import execute_tool, list_programs

PROGRAMS = [
    ("بكالوريوس", "CS", "بكالوريوس العلوم في علوم الحاسب", None),
    ("بكالوريوس", "CE", "بكالوريوس العلوم في هندسة الحاسب", None),
    (
        "ماجستير",
        "CS",
        "درجة الماجستير في علوم الحاسب خيار الرسالة",
        "شروط القبول: بكالوريوس علوم الحاسب",
    ),
    (
        "ماجستير",
        "مشترك",
        "درجة الماجستير في مجال الأمن السيبراني",
        "شروط القبول: بكالوريوس في أحد التخصصات",
    ),
    ("دكتوراه", "CS", "درجة الدكتوراه في علوم الحاسب", "شروط القبول: ماجستير في التخصص"),
]


@pytest.fixture()
def programs_db(tmp_path):
    """Create a temporary database with five programmes."""
    path = tmp_path / "programs.sqlite"
    conn = database.init_db(path)
    conn.executemany(
        "INSERT INTO programs (degree, department, name_ar, requirements_ar, source_url, scraped_at)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        [
            (
                degree,
                department,
                name,
                requirements,
                "https://ccis.ksu.edu.sa/ar/programs",
                "2026-11-16T10:00:00+00:00",
            )
            for degree, department, name, requirements in PROGRAMS
        ],
    )
    conn.commit()
    conn.close()
    return path


def test_lists_every_program(programs_db):
    rows = list_programs(db_path=programs_db)
    assert isinstance(rows, list)
    assert len(rows) == len(PROGRAMS)
    assert {row["name"] for row in rows} == {name for _, _, name, _ in PROGRAMS}


def test_filters_by_level_and_department(programs_db):
    rows = list_programs(level="master", department="CS", db_path=programs_db)
    assert [row["name"] for row in rows] == ["درجة الماجستير في علوم الحاسب خيار الرسالة"]
    shared = list_programs(level="master", department="shared", db_path=programs_db)
    assert [row["department"] for row in shared] == ["مشترك"]
    arabic = list_programs(level="ماجستير", department="علوم الحاسب", db_path=programs_db)
    assert [row["name"] for row in arabic] == [rows[0]["name"]]
    lowercase = list_programs(department="cs", db_path=programs_db)
    assert {row["department"] for row in lowercase} == {"CS"}


def test_requirements_are_opt_in(programs_db):
    rows = list_programs(db_path=programs_db)
    assert all("requirements" not in row for row in rows)
    rows = list_programs(level="phd", include_requirements=True, db_path=programs_db)
    assert rows[0]["requirements"].startswith("شروط القبول")


def test_rejects_an_unknown_filter(programs_db):
    assert list_programs(level="diploma", db_path=programs_db) == {"error": "unknown level"}
    assert list_programs(department="math", db_path=programs_db) == {"error": "unknown department"}


def test_execute_tool_routes_list_programs(programs_db, monkeypatch):
    import config

    monkeypatch.setattr(config, "DB_PATH", programs_db)
    rows = execute_tool("list_programs", {"level": "bachelor"})
    assert len(rows) == 2
    assert all(row["degree"] == "بكالوريوس" for row in rows)
