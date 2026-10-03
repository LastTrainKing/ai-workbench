"""API-test configuration.

API tests drive the FastAPI application, which needs the database and Redis, so
the schema fixture is pulled in automatically for this directory.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _api_needs_database(_clean_state: None) -> None:
    """Ensure the test schema exists and is empty for every API test."""
    return None
