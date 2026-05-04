from __future__ import annotations

import os

import pytest


def _large_tests_enabled() -> bool:
    return os.getenv("RUN_LARGE_TESTS") == "1"


def pytest_collection_modifyitems(
    config: pytest.Config, items: list[pytest.Item]
) -> None:
    if _large_tests_enabled():
        return
    skip_large = pytest.mark.skip(reason="Skipping large tests; set RUN_LARGE_TESTS=1")
    for item in items:
        if "large" in item.keywords:
            item.add_marker(skip_large)
