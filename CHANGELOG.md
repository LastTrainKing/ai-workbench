# Changelog

All notable changes to this project are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and this
project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.0.0] — 2026-10-03

First complete, verified release. Every item below was executed and checked on the
development machine (Windows 11, native PostgreSQL 16.4 / Redis 5.0.14 / Qdrant 1.12.4).

### Added

**Knowledge**
- Markdown / TXT / PDF ingestion with extension and MIME validation, streaming size
  limits and content-hash duplicate detection
- Heading-aware recursive chunker with sentence-boundary packing, overlap and a
  heading breadcrumb prefixed to each chunk's embedded text
- Asynchronous ingestion pipeline (parse → normalise → chunk → embed → Qdrant) with
  per-stage progress, retries and a persisted failure reason
- Qdrant adapter with lazy collection creation at the embedding provider's real
  dimension, payload indexes and tenant-scoped filtering

**Retrieval & RAG**
- Keyword, semantic and hybrid search; hybrid fusion is a min-max normalised weighted
  sum with the dense and sparse scores returned separately for explainability
- Lexical reranker (query-term coverage, bigram phrase hit, heading match, length prior)
- RAG pipeline: query rewrite → retrieval → context assembly → generation → citation
  extraction, with the retrieval metadata and sources persisted on the AI run
- Citation-enforced prompt; an empty knowledge base produces an honest "nothing found"
  answer instead of a hallucination

**Tasks & planning**
- Goal → phase → task → subtask planner with estimates, priorities, dependencies and
  verifiable completion criteria
- Task tree with cycle-safe dependencies, status transition rules, automatic
  unblocking of children, and a `task ↔ document` knowledge link
- 7-day study-plan generation

**AI engineering**
- `LLMProvider` / `EmbeddingProvider` abstraction with three real implementations:
  OpenAI-compatible, Ollama (fully local) and a deterministic offline engine
- Automatic, *reported* degradation to the offline engine when no cloud key is present
- Versioned prompt registry with a full version history, activation and rollback
- Tool-calling agent: 11 typed tools, bounded ReAct loop, duplicate-call detection,
  write-operation cap, and a database-reachable only through tools
- AI execution tracing: prompt name **and version**, retrieved sources, tool calls,
  tokens, latency, retries, cost estimate and classified errors
- Bounded retry with exponential backoff, jitter and a wall-clock budget
- Structured-output parsing tolerant of fenced or prose-wrapped JSON

**Platform**
- 22-table relational schema with Alembic migrations (verified up and down)
- 49 REST paths / 65 operations under `/api/v1` with a uniform envelope, pagination
  and a typed error taxonomy
- JWT authentication with bcrypt hashing and per-user data isolation
- Redis caching for search, prompts, stats and embeddings, with namespace-version
  invalidation and graceful degradation
- Database-backed worker using `SELECT … FOR UPDATE SKIP LOCKED`, bounded retries,
  stale-job recovery and a job-level timeout
- Dashboard statistics computed entirely from the database — no seeded demo data
- Knowledge graph projection over projects, knowledge bases, documents, concepts,
  tasks, notes and AI runs
- React 18 + TypeScript + Vite + Tailwind frontend: 18 pages, three-column dark-first
  developer-tool shell, `Ctrl+K` command palette, Markdown editor with live preview,
  force-directed graph, and loading / empty / error / skeleton states throughout
- Native project-scoped runtime management (`fetch-runtime`, `infra-start/stop/status`,
  `dev`) plus `start.bat` / `stop.bat` / `start.sh` / `stop.sh`
- `docker-compose.yml` and Dockerfiles for all services (**provided but not executed**
  on the development machine — see the warning in that file)
- Test suite: 270 backend tests (194 unit / 51 integration / 25 API) at 80.4% coverage,
  114 frontend tests, and a 75-assertion end-to-end smoke test
- Documentation set: architecture, database, API, AI, RAG, agent, deployment, testing,
  security, development, plus a 10-chapter thesis support pack

### Fixed

Ten real defects found during verification; each now has a regression test.

- **Worker event-loop starvation** — the main loop returned without awaiting whenever
  every slot was busy, freezing the entire process including the HTTP API
- **Reserved logging key** — `extra={"message": …}` raised `KeyError` *inside the
  exception handler*, turning a clean 409 into a 500; fixed at the call site and with
  a central guard
- **`MissingGreenlet` on serialisation** — server-generated `updated_at` was expired
  after flush and lazily reloaded; fixed with `eager_defaults`
- **Wrong entity from generic repository helpers** — cross-entity queries selected
  `self.model`; replaced with explicit `list_of` / `get_one_of` / `count_of`
- **Stale service imports in agent tools** — write tools silently returned `ok: false`
  after a module merge; fixed and guarded by an import-integrity test
- **Self-deadlock when recording a failure** — a second session waited on a row lock
  held by the doomed transaction; the transaction is now rolled back first
- **Tokenizer string expansion** — `extend(run)` emitted single CJK characters and
  flooded the embedding with noise
- **Duplicate index name** — column `index=True` plus an explicit `Index` broke
  `create_all` with `DuplicateTableError`
- **Over-aggressive payload sanitiser** — real task plans were replaced with
  `"<max-depth>"`; the nesting cap was raised above any structure the product produces
- **Fragile context discovery** — the offline engine could answer from the prompt's
  own instructions; templates now wrap material in explicit `<document>` /
  `<context>` tags

### Security

- Extension allow-list with MIME consistency checking and a streaming size limit
- UUID-generated stored filenames (no user-controlled paths) and storage-root
  containment checks
- JWT (HS256) with bcrypt password hashing and constant-time credential comparison
- Parameterised queries only; LIKE wildcards escaped in user-supplied search input
- CORS allow-list, secrets via git-ignored `.env`, secret redaction in logs

### Known limitations

- No OCR: a scanned PDF without a text layer fails with a clear message
- The reranker is lexical; no cross-encoder model is shipped
- AI responses are not streamed token by token
- `docker-compose.yml` is provided but was **not** executed on the development machine
  (no Docker, unreachable Docker Hub)
- No rate limiting, refresh-token rotation, audit log or upload virus scanning
- Single-node deployment; no horizontal scaling story beyond running more workers

[1.0.0]: https://github.com/your-username/ai-workbench/releases/tag/v1.0.0
