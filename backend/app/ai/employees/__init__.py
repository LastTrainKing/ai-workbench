"""The employee layer: identity, role contracts, persona guard, permission narrowing.

This package is deliberately small and free of I/O. It answers four questions and nothing
else:

* ``templates`` — what a role starts as (§6.2/§6.3 of the brief)
* ``persona_guard`` — what a persona may say without arguing with its own rules
* ``policy`` — how an employee's permissions narrow the user's, and never widen them

Persistence and orchestration live in ``app.services.employee_service``; the SQL lives in
``app.repositories.employee_repo``. Keeping the rules here means they can be tested without
a database, which is what ``backend/tests/unit/test_employee_*.py`` does.
"""

from __future__ import annotations

from app.ai.employees.persona_guard import PersonaReview, review_persona
from app.ai.employees.policy import narrow_policy, tool_allowed
from app.ai.employees.templates import (
    DEFAULT_ROLE_ID,
    TEMPLATES,
    TEMPLATES_BY_ID,
    RoleTemplate,
    get_template,
    validate_tools,
)

__all__ = [
    "DEFAULT_ROLE_ID",
    "TEMPLATES",
    "TEMPLATES_BY_ID",
    "PersonaReview",
    "RoleTemplate",
    "get_template",
    "narrow_policy",
    "review_persona",
    "tool_allowed",
    "validate_tools",
]
