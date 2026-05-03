from __future__ import annotations

from metacompressor.tests.conftest import _large_tests_enabled


def test_large_tests_only_enabled_for_exact_one(monkeypatch):
    monkeypatch.setenv("RUN_LARGE_TESTS", "1")
    assert _large_tests_enabled() is True


def test_large_tests_disabled_for_falsey_strings(monkeypatch):
    for value in ["0", "false", "False", ""]:
        monkeypatch.setenv("RUN_LARGE_TESTS", value)
        assert _large_tests_enabled() is False


def test_large_tests_disabled_when_unset(monkeypatch):
    monkeypatch.delenv("RUN_LARGE_TESTS", raising=False)
    assert _large_tests_enabled() is False
