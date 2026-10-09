# Changelog

All notable changes to this project are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and this
project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [2.2.0] — 2026-10-09

Release-freeze round. Every entry below is a defect that was confirmed against the source
and is covered by a regression test that fails on the previous code. The full table,
including what is still open, is `RELEASE-FREEZE-ISSUES.md`.

### Security

- **Logout no longer fails open.** `is_token_revoked` read Redis, and a Redis outage made
  every logged-out token valid again. Revocations are recorded in a new `revoked_tokens`
  table (with a migration) and Redis is a fast path in front of it; the check answers
  revoked / not_revoked / storage_unavailable, and a request is refused in the third case.
- **Passwords are no longer truncated at 72 bytes.** The old code applied bcrypt's limit
  itself, so two passwords agreeing on their first 72 bytes were one password and a
  multibyte password could be cut mid-character. The scheme is versioned and pre-hashes the
  whole password; existing hashes still verify and are upgraded on the next login.
- **Production refuses placeholder keys.** The validator compared against one exact string
  and the length, so the 47-character placeholder that actually shipped in `.env.example`
  passed. It now rejects placeholder text, short values and degenerate patterns.
- **A fresh install is a production install.** The installer copied `.env.example` to
  `.env`, which selects `APP_ENV=dev` and seeds `demo / demo1234`. The launcher now writes
  a production file with a per-install random key, and never overwrites an existing one.
- **Skill discovery had no domain check.** `_authorise` treated a missing policy as
  "allowed", and `POST /skills/discover` never passed one. A missing policy is now a
  denial, and redirects are re-authorised at every hop instead of followed blindly.
- **One account could change another's AI provider.** Provider, model, base URL and API key
  lived in one shared file. They are now layered per account, and a request can only write
  its own layer. Base URLs are validated (https for remote, no embedded credentials).
- **Qdrant had no authentication and CORS was on.** The launcher now generates and enforces
  an API key that the backend reads from the same file, and CORS is off.
- **docker-compose published everything on every interface** with a default PostgreSQL
  password and an unauthenticated Redis. The data services are no longer published; the API
  and frontend bind to loopback; the credentials are required.

### Fixed

- **A stopped agent is no longer recorded as a success.** `max_steps`, `loop_detected` and
  `empty_response` produced explanatory text, and the truthiness of that text was the whole
  success test. New terminal states `INCOMPLETE` and `INTERRUPTED` say what happened, and a
  delivery task's success is verified against the file it claims to have produced.
- **A crashed job is no longer retried forever.** `requeue_stale` ignored
  `attempts`/`max_attempts`. An exhausted job is now terminal `FAILED`, and its document is
  marked failed with it.
- **RAG grounding is honest.** One valid citation marked the whole answer grounded. Every
  claim-bearing sentence must now cite a real source, and an ungrounded answer says so.

### Changed

- Single version source: `VERSION` at the repository root, propagated by
  `scripts/sync_version.py`. The shell, installer, backend and frontend had reported four
  different versions.
- `pytest` no longer carries a duplicate `-q`, which was suppressing the summary.

## [1.1.0] — 2026-10-03

Real DeepSeek integration plus a runtime provider control surface. Verified against the
live DeepSeek API: **60/60 integration checks**, **75/75 end-to-end assertions**, and a
full RAG → planner → agent chain executed by `deepseek-flash`.

### Added

- **DeepSeek provider** (`app/ai/providers/deepseek.py`) — reuses the OpenAI-compatible
  transport and adds DeepSeek's defaults, live model discovery via `GET /models`,
  precise error translation, and handling of the reasoning-model contract.
  Default model is **`deepseek-flash` (DeepSeek V4.1 Flash)**.
- **Provider registry** (`app/ai/providers/registry.py`) — a declarative catalogue of
  DeepSeek / OpenAI-Compatible / Ollama / Offline with labels, kinds, default models,
  docs links and per-provider model lists. Adding a vendor is a two-step change.
- **Runtime AI configuration** (`app/ai/runtime_config.py`) — provider, model, base URL,
  API key, temperature, max tokens and timeout are resolvable at runtime with the
  precedence **runtime override → `.env` → default**. Overrides persist to
  `.runtime/ai-config.json` (git-ignored, local only) and take effect on the next AI
  call without a restart.
- **AI provider API** (`/api/v1/ai-config`) — 8 endpoints: read the effective
  configuration, list providers, list models (`live=true` asks the vendor), read health,
  patch the configuration, clear overrides, toggle a provider, and run a real
  connection test that returns latency and token usage.
- **Settings → AI Provider UI** — a working control panel: current effective provider
  banner with an explicit degradation warning, provider cards with enable switches and
  configured/active badges, dynamic model picker per provider with a custom-model
  escape hatch, base URL, write-only API key field, temperature / max-tokens / timeout,
  "apply", "reset to .env", and a "test connection" button. The tab is now the default
  view of Settings.
- **DeepSeek-aware error taxonomy** — `ai_insufficient_balance` (HTTP 402, not
  retryable), `ai_model_not_found`, and `ai_reasoning_budget_exhausted`.
- **Agent run provenance** — `knowledge_search` now records the chunks it retrieved as
  `ai_run_sources` on the agent run, so an agent run can answer "which documents did
  this use?" exactly like a RAG run, and the knowledge graph gains those edges.
- **Live integration tests** (`tests/integration/test_deepseek_live.py`) — real API
  calls that **skip automatically** with a stated reason when no key is configured.
  The key is read from the environment and never referenced in test source.
- **Verification harness** (`scripts/verify_deepseek.py`) — 11 groups / 60 checks
  covering connectivity, RAG, document analysis, planner, agent, the AI run trace,
  provider switching, bad key, unreachable host, retry, fallback and honest labelling.

### Fixed

Five real defects, all found by running against the live API rather than a mock:

- **JSON mode was rejected with HTTP 400.** DeepSeek refuses
  `response_format={"type":"json_object"}` unless the prompt literally contains the word
  "json". The provider now satisfies the requirement in one place instead of making
  every prompt author remember a vendor quirk.
- **`reasoning_content` was not echoed back.** `deepseek-flash` is a reasoning model and
  rejects a tool-loop follow-up that omits the previous assistant turn's chain of
  thought ("The `reasoning_content` in the thinking mode must be passed back"). The
  value now travels on `ChatMessage` / `ChatResult` and is replayed by the agent.
- **Truncated JSON lost the whole plan.** A long structured answer cut off by the token
  ceiling used to fail the entire request. The parser now recovers the complete part by
  closing open structures and trimming back to the last valid boundary, bounded so a
  pathological input cannot spin.
- **A 1-token connection probe reported a false failure** for a healthy reasoning
  endpoint, because the whole allowance went on the chain of thought. The probe budget
  is now 256 tokens.
- **`str(None)` made an empty API key look configured.** The resolver returned the
  string `"None"` when every candidate was empty, which silently selected a cloud
  provider with no credentials. The fallback now returns the default value itself.
- **Task plans could ship without completion criteria.** Models occasionally omit the
  field even though the prompt requires it. The plan normaliser now enforces the
  contract and logs the under-delivery rather than shipping an empty criterion.
- **Pure unit tests required a database.** The test suite's schema fixture was
  `autouse`, so `pytest tests/unit` failed on a machine with no PostgreSQL — contradicting
  the README. The fixtures are now opt-in, and all 246 unit tests run with no services.

### Changed

- `AI_MAX_TOKENS` default raised from 2048 to **8192** — reasoning models need room for
  the chain of thought before the answer, and the old value silently produced empty
  responses.
- The agent loop's per-step budget is now `max(1024, min(ai_max_tokens, 3000))`.
- Provider error messages now include the vendor's own explanation verbatim. A 400 used
  to surface as "unexpected HTTP 400" and nothing else, which made the two defects above
  invisible.
- `AI_PROVIDER` defaults to `deepseek`; `DEEPSEEK_API_KEY` / `DEEPSEEK_BASE_URL` /
  `DEEPSEEK_MODEL` have their own environment namespace.
- API keys are masked as `sk-****(len=35)` — the prefix and length only, never the tail,
  because these values end up in screenshots and bug reports.

### Security

- The API key lives only in `.env` (git-ignored) or `.runtime/ai-config.json`
  (git-ignored, excluded from the delivery archive). Verified: it appears in **no Git
  commit**, no tracked file, no log line, no screenshot and no archive entry.
- Reads never return the key — only `api_key_set` and a mask.

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

[1.1.0]: https://github.com/your-username/ai-workbench/releases/tag/v1.1.0
[1.0.0]: https://github.com/your-username/ai-workbench/releases/tag/v1.0.0
