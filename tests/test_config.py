"""Tests for reading the model settings from the environment."""

import config


def test_models_reads_a_comma_separated_list(monkeypatch):
    monkeypatch.setenv("TEST_MODELS", " a , b ,, a ")
    assert config._models("TEST_MODELS") == ["a", "b"]


def test_models_uses_the_default_when_the_setting_is_unset(monkeypatch):
    monkeypatch.delenv("TEST_MODELS", raising=False)
    assert config._models("TEST_MODELS", ("x", "y")) == ["x", "y"]


def test_the_chain_prefers_the_new_setting(monkeypatch):
    monkeypatch.setenv("MODEL_FALLBACKS", "gemini-a, gemini-b")
    monkeypatch.setenv("MODEL_FALLBACK", "old-model")
    assert config._model_chain() == ["gemini-a", "gemini-b"]


def test_the_chain_still_reads_the_older_single_setting(monkeypatch):
    monkeypatch.delenv("MODEL_FALLBACKS", raising=False)
    monkeypatch.setenv("MODEL_FALLBACK", "old-model")
    assert config._model_chain() == ["old-model"]


def test_the_chain_defaults_to_the_four_models_in_order(monkeypatch):
    monkeypatch.delenv("MODEL_FALLBACKS", raising=False)
    monkeypatch.delenv("MODEL_FALLBACK", raising=False)
    assert config._model_chain() == [
        "gemini-3.5-flash-lite",
        "gemini-3.8-flash",
        "gemini-3.6-flash",
        "Gemini 4 Hydrogen",
    ]
