"""Tests for scripts/prepare_deploy.py."""

import sqlite3

import config
from scripts.prepare_deploy import checkpoint, git_commands, main, size_mb


def test_checkpoint_folds_the_log_into_the_file(tmp_path):
    path = tmp_path / "wal.sqlite"
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("CREATE TABLE t (x INTEGER)")
    conn.execute("INSERT INTO t VALUES (1)")
    conn.commit()
    conn.close()

    busy, log_frames, checkpointed = checkpoint(path)
    assert busy == 0
    assert log_frames >= 0 and checkpointed >= 0

    conn = sqlite3.connect(path)
    assert conn.execute("SELECT COUNT(*) FROM t").fetchone()[0] == 1
    conn.close()
    assert size_mb(path) > 0


def test_git_commands_use_a_root_relative_path_inside_the_project():
    commands = git_commands(config.ROOT / "db" / "ccis.sqlite")
    assert commands[0] == "git add -f db/ccis.sqlite"
    assert commands[1] == 'git commit -m "refresh data"'
    assert commands[2] == "git push"


def test_git_commands_keep_a_path_outside_the_project(tmp_path):
    outside = tmp_path / "elsewhere.sqlite"
    assert git_commands(outside)[0] == f"git add -f {outside.as_posix()}"


def test_a_missing_database_says_how_to_build_it(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "missing.sqlite")
    assert main() == 1
    output = capsys.readouterr().out
    assert "database not found" in output
    assert "scrapers.run --all" in output
