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
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    TIMEOUT = "TIMEOUT"
    CANCELLED = "CANCELLED"


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
