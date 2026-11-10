"""Tests for loading the runtime system prompt out of agent.md."""

from datetime import date

import pytest

from agent.prompts import PromptError, load_system_prompt


def test_prompt_is_only_the_marked_block():
    prompt = load_system_prompt(today=date(2026, 11, 10))
    assert prompt.startswith("You are the CCIS Support Agent")
    assert "Today's date: 2026-11-10" in prompt
    assert "{today}" not in prompt
    assert "SYSTEM_PROMPT_START" not in prompt
    assert "Part 2" not in prompt
    assert "Developer notes" not in prompt
    assert "spec.md" not in prompt


def test_missing_marker_fails_loudly(tmp_path):
    broken = tmp_path / "agent.md"
    broken.write_text("# no markers here\n", encoding="utf-8")
    with pytest.raises(PromptError):
        load_system_prompt(broken)


def test_missing_file_fails_loudly(tmp_path):
    with pytest.raises(PromptError):
        load_system_prompt(tmp_path / "absent.md")
