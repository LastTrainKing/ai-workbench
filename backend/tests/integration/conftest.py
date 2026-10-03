"""Integration-test configuration.

Every test in this directory talks to real infrastructure, so the schema fixture
is pulled in automatically here. ``tests/unit`` deliberately does **not** do this
— those tests must run on a machine with no PostgreSQL, Redis or Qdrant.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _integration_needs_database(_clean_state: None) -> None:
    """Ensure the test schema exists and is empty for every integration test.

    Some integration tests open their own session with ``session_scope()`` rather
    than taking the ``session`` fixture, so an explicit dependency is required to
    guarantee the tables have been created.
    """
    return None
