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
from app.models.task import Task, TaskDependency, TaskDocument, TaskTag
from app.models.user import User

__all__ = [  # noqa: RUF022 - grouped by domain layer, which is more readable here
    "Base",
    "TimestampMixin",
    "UUIDPrimaryKeyMixin",
    "utcnow",
    # domain
    "User",
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
    "ContextSnapshot",
    "MemoryCandidate",
    "MemoryEntry",
    "UserPreference",
    "UserProfile",
    "ToolAuditLog",
    "ToolCall",
    "AIRunSource",
    # infra
    "Job",
    "ActivityEvent",
]
