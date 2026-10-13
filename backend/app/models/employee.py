"""The employee: a persona that outlives a conversation.

**Why a table and not a system prompt.** Everything else in this product is already an
entity with a row — a project, a task, a memory, a deliverable. An "employee" that existed
only as a string prepended to each request could not be granted a permission, could not own
a deliverable, could not be paused, and could not be told apart from a different employee
in the run history. The prompt says a persona is a thing the user manages; a thing the user
manages has an identity.

**Why the fields are grouped the way they are.** §6.1 of the brief lists eleven concepts.
They fall into four groups, and the grouping is the point:

* *Who they are* — ``name``, ``role_id``, ``avatar``, ``mission``, ``persona``. Displayed.
* *What they may touch* — ``knowledge_scope``, ``memory_scope``, ``permission_profile``.
  **Enforced.** These are read by the permission service before a tool runs, not by the
  window before it draws a button.
* *What they may use* — ``skills``, ``model_policy``. Resolved against the real registries,
  so a skill named here that no tool backs is a validation error rather than a claim.
* *Where they are* — ``status``, ``version``, ``created_from_template``.

``persona`` is deliberately **not** free rein. It is text that shapes tone. The system
prompt, the permission policy and the audit log sit outside it and cannot be reached from
it — see ``app/ai/employees/persona_guard.py``, which rejects persona text that tries.

JSONB rather than child tables for the four scope lists: they are small, always read
whole, never queried by element, and change as a unit when the employee is edited. A join
table would add three tables and buy nothing.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

#: Where an employee is in its life. The first three are "can take work", the last three
#: are not, and the UI colours them differently for that reason rather than for decoration.
EMPLOYEE_STATUSES: tuple[str, ...] = (
    "idle",  # ready, nothing running
    "working",  # a run is in flight
    "waiting_approval",  # a run is stopped at a confirmation
    "waiting_user",  # the employee asked a question and is blocked on the answer
    "paused",  # the user paused it
    "failed",  # the last run ended badly and has not been retried
    "archived",  # retired; kept so its deliverables still have an owner
)

ACTIVE_EMPLOYEE_STATUSES: frozenset[str] = frozenset({"idle", "working", "waiting_approval"})


class Employee(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """One employee. Owned by exactly one user, always."""

    __tablename__ = "employees"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )

    # ---------------------------------------------------------------- who they are
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    #: Which role template this was created from. Kept after creation because it explains
    #: where the defaults came from; not a foreign key, because a template is code.
    role_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    #: A short label for the UI. Free text, not a file path — the window decides how to
    #: draw it, and a path here would make the row depend on a file that can be deleted.
    avatar: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    mission: Mapped[str] = mapped_column(Text, nullable=False, default="")
    #: Tone and working style. Cannot override the system prompt — see persona_guard.
    persona: Mapped[str] = mapped_column(Text, nullable=False, default="")

    # ---------------------------------------------------------------- what they touch
    #: List of responsibilities, each a short sentence. Displayed in the contract.
    responsibilities: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    #: What this employee does *not* do. Stated explicitly because "not in the list" and
    #: "explicitly out of scope" read very differently to a user deciding what to ask for.
    out_of_scope: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    #: Who and what it serves.
    service_scope: Mapped[str] = mapped_column(Text, nullable=False, default="")
    #: Knowledge bases and project ids it may read. Empty list = none, not "all".
    knowledge_scope: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    #: Which memory scopes it may read and write.
    memory_scope: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    #: Permission overrides for this employee: level, mode, per-tool decisions.
    #: The effective policy is ``user policy`` narrowed by ``this`` — never widened.
    permission_profile: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)

    # ---------------------------------------------------------------- what they may use
    #: Tool names. Validated against the real registry on write.
    skills: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    #: Which logical model role to use for which kind of work.
    model_policy: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)

    # ---------------------------------------------------------------- where they are
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="idle", index=True)
    #: Bumped on every edit. Lets a run record which version of the employee ran it, which
    #: is the difference between "the employee changed" and "the employee misbehaved".
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_from_template: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    #: Set when the user retires it. Archived employees stay because their deliverables and
    #: their run history need an owner; deleting the row would orphan both.
    archived: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, index=True)

    __table_args__ = (
        UniqueConstraint("user_id", "name", name="uq_employees_user_id_name"),
        CheckConstraint(
            "status IN ('idle','working','waiting_approval','waiting_user','paused','failed','archived')",
            name="employees_status_valid",
        ),
        Index("ix_employees_user_id_archived", "user_id", "archived"),
    )

    @property
    def active(self) -> bool:
        """Whether this employee can currently be given work."""
        return not self.archived and self.status in ACTIVE_EMPLOYEE_STATUSES

    def __repr__(self) -> str:  # pragma: no cover - debugging helper
        return f"<Employee {self.name!r} role={self.role_id!r} status={self.status}>"
