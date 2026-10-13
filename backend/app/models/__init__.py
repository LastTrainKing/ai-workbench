"""SQLAlchemy model registry.

Importing this package makes every mapper available to Alembic autogenerate and
to ``Base.metadata.create_all`` in tests.
"""

from app.models.ai import (
    AgentRun,
    AIRun,
    AIRunSource,
    Prompt,
    PromptVersion,
    ToolAuditLog,
    ToolCall,
)
from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin, utcnow
from app.models.context import (
    ContextSnapshot,
    MemoryCandidate,
    MemoryEntry,
    UserPreference,
    UserProfile,
)
from app.models.conversation import Conversation, Message, MessageKind, MessageRole
from app.models.delivery import BuildRun, BuildStatus, Deliverable
from app.models.employee import ACTIVE_EMPLOYEE_STATUSES, EMPLOYEE_STATUSES, Employee
from app.models.job import ActivityEvent, Job
from app.models.knowledge import (
    Concept,
    Document,
    DocumentChunk,
    DocumentConcept,
    DocumentTag,
    KnowledgeBase,
    Tag,
)
from app.models.note import Note
from app.models.project import Project
from app.models.skill import InstalledSkill
from app.models.task import Task, TaskDependency, TaskDocument, TaskTag
from app.models.user import RevokedToken, User

__all__ = [  # noqa: RUF022 - grouped by domain layer, which is more readable here
    "Base",
    "TimestampMixin",
    "UUIDPrimaryKeyMixin",
    "utcnow",
    # domain
    "User",
    "RevokedToken",
    "InstalledSkill",
    "Project",
    "KnowledgeBase",
    "Document",
    "DocumentChunk",
    "Tag",
    "DocumentTag",
    "Concept",
    "DocumentConcept",
    "Task",
    "TaskDependency",
    "TaskTag",
    "TaskDocument",
    "Note",
    # ai
    "Prompt",
    "PromptVersion",
    "AIRun",
    "AgentRun",
    "BuildRun",
    "BuildStatus",
    "ContextSnapshot",
    "Conversation",
    "Deliverable",
    "Employee",
    "EMPLOYEE_STATUSES",
    "ACTIVE_EMPLOYEE_STATUSES",
    "MemoryCandidate",
    "MemoryEntry",
    "Message",
    "MessageKind",
    "MessageRole",
    "UserPreference",
    "UserProfile",
    "ToolAuditLog",
    "ToolCall",
    "AIRunSource",
    # infra
    "Job",
    "ActivityEvent",
]
