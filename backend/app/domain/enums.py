"""Domain enumerations shared by models, schemas and services."""

from __future__ import annotations

from enum import StrEnum


class DocumentStatus(StrEnum):
    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class EmbeddingStatus(StrEnum):
    PENDING = "PENDING"
    EMBEDDED = "EMBEDDED"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"


class TaskStatus(StrEnum):
    TODO = "TODO"
    IN_PROGRESS = "IN_PROGRESS"
    BLOCKED = "BLOCKED"
    DONE = "DONE"
    CANCELLED = "CANCELLED"


class TaskPriority(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    URGENT = "URGENT"


class TaskSource(StrEnum):
    MANUAL = "MANUAL"
    PLANNER = "PLANNER"
    AGENT = "AGENT"


class ProjectStatus(StrEnum):
    ACTIVE = "ACTIVE"
    ARCHIVED = "ARCHIVED"


class RunStatus(StrEnum):
    """Terminal vocabulary for an AI run.

    `SUCCEEDED` means the run **completed and its result verified**. It is not a synonym
    for "the model said something". `INCOMPLETE` and `INTERRUPTED` exist because the
    previous set had no way to say "this stopped without finishing": an agent that hit
    `max_steps`, detected a loop or got an empty response was written as `SUCCEEDED`
    because it had produced explanatory text, which made "stopped" indistinguishable from
    "done" in the API, the database and the UI.
    """

    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    #: Ran to a stop that is not a failure and not a completion — step ceiling, loop
    #: guard, empty model response, or an unverified deliverable.
    INCOMPLETE = "INCOMPLETE"
    #: The process died mid-run and the outcome cannot be determined from the record.
    INTERRUPTED = "INTERRUPTED"
    FAILED = "FAILED"
    TIMEOUT = "TIMEOUT"
    CANCELLED = "CANCELLED"

    @property
    def is_terminal(self) -> bool:
        return self is not RunStatus.RUNNING

    @property
    def is_success(self) -> bool:
        return self is RunStatus.SUCCEEDED


class RunType(StrEnum):
    RAG_ANSWER = "RAG_ANSWER"
    DOCUMENT_SUMMARY = "DOCUMENT_SUMMARY"
    KNOWLEDGE_EXTRACTION = "KNOWLEDGE_EXTRACTION"
    TASK_PLANNER = "TASK_PLANNER"
    AGENT = "AGENT"
    NOTE_GENERATION = "NOTE_GENERATION"
    INTERVIEW_GENERATOR = "INTERVIEW_GENERATOR"
    STUDY_PLAN = "STUDY_PLAN"
    EMBEDDING = "EMBEDDING"
    QUERY_REWRITE = "QUERY_REWRITE"


class JobStatus(StrEnum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"


class JobType(StrEnum):
    INGEST_DOCUMENT = "INGEST_DOCUMENT"
    EMBED_DOCUMENT = "EMBED_DOCUMENT"
    REINDEX_KNOWLEDGE_BASE = "REINDEX_KNOWLEDGE_BASE"


class ConceptSource(StrEnum):
    AI = "AI"
    MANUAL = "MANUAL"


class AIProviderKind(StrEnum):
    OPENAI_COMPATIBLE = "openai_compatible"
    OLLAMA = "ollama"
    OFFLINE = "offline"


class SearchMode(StrEnum):
    KEYWORD = "keyword"
    SEMANTIC = "semantic"
    HYBRID = "hybrid"
