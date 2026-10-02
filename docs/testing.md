# Testing

This document describes the test strategy, what is covered, how to run
everything, the end-to-end smoke-test harness and its checks, and an honest
statement of what is **not** covered.

## 1. Test strategy

| Level | Tool | Location | Requires |
|---|---|---|---|
| Unit | pytest | `backend/tests/unit/` | Nothing external (pure functions) |
| Integration / API | pytest + httpx ASGI transport | `backend/tests/` | PostgreSQL, Redis (Qdrant for semantic paths) |
| End-to-end | custom smoke harness | `scripts/smoke_test.py` | The running backend + all three services |
| Frontend | Vitest + Testing Library | `frontend/` | **Separate workstream** — not covered by this backend suite |

There is no separate browser-driven E2E suite. The smoke test drives the real
HTTP API end-to-end, which is the E2E layer for this project.

## 2. Unit tests

Located in `backend/tests/unit/`. They are pure and fast.

### `test_chunker.py`

Covers the heading-aware chunker:

* chunks are produced and non-empty;
* indices are sequential (`[c.index for c in chunks] == range(len(chunks))`);
* the heading breadcrumb is prefixed to the embedded text
  (`"JVM 学习笔记 > 垃圾回收"` is present and the chunk starts with it);
* the size target is respected, with the documented 2× slack for a single
  oversized sentence;
* overlap preserves content across consecutive chunks;
* empty and whitespace-only input return `[]`;
* a non-positive `chunk_size` raises `ValueError`;
* `overlap` is clamped to half the chunk size;
* `token_estimate` is populated and `char_count == len(content)`;
* a single oversized sentence is hard-split.

### `test_heuristic_provider.py`

Covers the deterministic offline engine:

* tokenisation handles CJK and Latin and drops stop-words;
* sentence splitting handles Chinese and English punctuation;
* embeddings are deterministic, L2-normalised, fixed at 384 dimensions;
* related text scores higher cosine similarity than unrelated text;
* empty embedding input returns no vectors;
* the `task` marker routes to the right algorithm (`document_summary`,
  `knowledge_extraction`, `task_planner`, `rag_answer`);
* knowledge extraction returns all expected keys and finds the real TODO line;
* the planner produces phases whose tasks have titles, completion criteria,
  integer estimates and valid priorities;
* the RAG answer uses only supplied context and is honest when there is none;
* usage is reported and `total == prompt + completion`;
* the agent policy starts with `knowledge_search`, advances through the tool
  sequence, and terminates when all tools are used;
* `supports_tool_calling` is true.

### `test_retry.py`

Covers the bounded retry helper:

* success without retry;
* retries then succeeds (attempt count asserted);
* gives up after `max_attempts`;
* non-retryable errors fail fast (one attempt);
* backoff is exponential and capped (1, 2, 4, 8, capped at 10);
* `max_elapsed` bounds total time;
* the `on_retry` callback receives the attempt number;
* cancellation is not retried;
* `chunked()` splits evenly and rejects size ≤ 0.

### `test_security_and_files.py`

Covers security helpers, file storage and parsers:

* bcrypt is salted per hash and verifiable; a corrupt hash fails safely; long
  passwords are truncated, not crashing;
* JWT round-trip; invalid and expired tokens raise `AuthenticationError`
  (`token_expired`);
* filename sanitisation blocks traversal, backslashes, absolute paths, NUL bytes
  and drive colons, keeps the extension, and handles Windows reserved names;
* extension allow-list accepts `.md/.markdown/.txt/.pdf` and rejects
  `.exe/.sh/.zip/.php` and extension-less names;
* Content-Type mismatch is rejected; `application/octet-stream` and a missing
  Content-Type are accepted;
* storage-root containment accepts inside paths and rejects outside paths;
  `delete_file` refuses to delete outside the root;
* Markdown and text parsers extract titles and headings; the text parser falls
  back to `gb18030`;
* empty files, unsupported extensions, corrupt PDFs and PDFs with no extractable
  text raise `DocumentParseError` / `UnsupportedMediaTypeError`;
* the real sample PDF parses with 3 pages and contains "CAP Theorem";
* whitespace normalisation collapses runs correctly;
* language detection returns `zh` / `en` / `unknown`;
* a streaming upload over the limit raises `PayloadTooLargeError` and leaves no
  partial file; an empty upload raises `StorageError`.

## 3. Integration fixtures

`backend/tests/conftest.py` provides:

* a dedicated database `aiworkbench_test` (created on demand) and Redis DB index
  1, so tests never touch development data;
* environment variables set **before** `app` is imported (the settings object and
  engine are created at import time), forcing `AI_PROVIDER=offline`,
  `EMBEDDING_PROVIDER=offline`, `WORKER_ENABLED=false`, `CACHE_ENABLED=true` and
  `APP_ENV=test`;
* session-scoped schema creation (`drop_all` + `create_all`) and per-test
  truncation of every table plus a Redis `flushdb`;
* fixtures: `session`, `client` (httpx over ASGI), `user_token`,
  `auth_headers`, `knowledge_base`, `sample_markdown`, `settings_override`.

Forcing the offline engine means the suite needs no API key and produces stable,
reproducible output.

## 4. End-to-end smoke test

`scripts/smoke_test.py` drives the **real HTTP API** against a running backend and
asserts the whole product loop works: upload → parse → chunk → embed → retrieve →
answer → plan → agent → trace, plus the failure paths.

```powershell
.venv\Scripts\python.exe scripts\smoke_test.py --base-url http://127.0.0.1:8000
```

Exit code 0 means every check passed; 2 means the backend was unreachable.

### Structure

The harness is organised into **16 numbered groups**. On a full run it executes
**75 assertions** (`report.check`). The static call sites are fewer than 75
because several checks live inside loops (four uploads, four ingestion checks,
three search modes), and a few checks are conditional (register fallback,
"knowledge base id present", "sample exists") and do not run on the happy path.
On a passing run the counter reads `75/75 checks passed`.

| Group | Topic | Representative assertions |
|---|---|---|
| [1] | health & dependencies | `GET /health`; PostgreSQL, Redis, Qdrant reachable; prints the AI provider in use |
| [2] | authentication | login/register; access token; `GET /auth/me`; unauthenticated request rejected (401); wrong password rejected (401) |
| [3] | project & knowledge base | create project; create KB; duplicate KB name rejected (409) |
| [4] | document ingestion input | upload 4 samples; duplicate upload detected; unsupported type rejected (415); oversized upload rejected (413) |
| [5] | background ingestion pipeline | each uploaded document reaches `COMPLETED` with `chunks > 0` and `embedded == chunks` |
| [6] | search | keyword / semantic / hybrid each return hits; hybrid exposes both score components; a second identical search is served from cache |
| [7] | RAG | `POST /chat/rag`; non-empty answer; citations returned; every citation points at a real chunk; `run_id` recorded; unknown KB rejected (404) |
| [8] | AI task planner | `POST /planner/plan`; plan has phases; every task has a completion criterion; tasks persisted; task tree is hierarchical |
| [9] | tool-calling agent | `POST /agents/run`; at least one tool executed; final answer produced; real entities created; tool catalogue exposed (≥ 6) |
| [10] | AI document analysis | `POST /documents/{id}/analyze`; summary produced; note persisted |
| [11] | AI execution trace | `GET /ai-runs`; runs carry prompt version + token usage; trace exposes retrieval metadata, cited sources and prompt name/version; metrics aggregate |
| [12] | prompt versioning | templates listed (≥ 8); detail has versions; new version created (201); reset to built-in (200) |
| [13] | dashboard, graph & stats | task stats; knowledge numbers from the DB; AI stats; activity feed; provider status; graph nodes/edges; graph links knowledge to tasks |
| [14] | error handling | missing resource 404; invalid UUID 422; empty task title 422; invalid enum 422 |
| [15] | task workflow | dependency created; completing a blocked task rejected (409); circular dependency rejected (409); task completes after its dependency; knowledge linked to task |
| [16] | cascade cleanup | KB deleted; deleted KB is gone; documents cascade with the KB |

### Fixtures

The smoke test uploads the four files in `samples/`:

| File | Type |
|---|---|
| `java-backend-notes.md` | Markdown (9 headings) |
| `rag-notes.md` | Markdown (8 headings) |
| `software-engineering-checklist.txt` | plain text |
| `distributed-systems.pdf` | PDF (3 pages) |

### Verified results (2026-10-03)

The harness was run against a live backend on the development machine:

| Result | Value |
|---|---|
| Checks passed | **75/75** |
| PostgreSQL | 16.4 reachable |
| Redis | reachable |
| Qdrant | 1.12.4 reachable |
| Documents ingested | 8 / 7 / 1 / 3 chunks respectively (19 chunks total) |
| Embedding success | 100% (every chunk embedded) |
| AI provider in use | **offline engine** (`heuristic-extractive-v1`) — no cloud key was configured |

Because the offline engine was in use, the answer, plan and extraction content is
extractive and deterministic; that is the expected behaviour with
`AI_API_KEY` empty, and it is reported honestly in `GET /health/deps` and on every
`ai_runs` row. See [../docs/thesis/09-系统测试.md](thesis/09-系统测试.md) for the
full test chapter and the table of defects found and fixed.

## 5. What is NOT covered

Be explicit about the gaps:

* **No frontend tests in this suite.** The frontend is a separate workstream under
  `frontend/` (Vitest + Testing Library); its tests are not run by this backend
  suite.
* **No load / performance testing.** No throughput, concurrency or memory
  benchmarks were run. Latency is measured per request and per AI run at runtime,
  but no load profile exists.
* **No security scanning.** No SAST/DAST tooling, dependency-vulnerability scan or
  penetration test was performed.
* **No cross-encoder reranker evaluation.** No retrieval-quality metric (recall@k,
  MRR, nDCG) was computed against a labelled set; retrieval quality is asserted
  only by "returns hits" and "scores present".
* **No OCR path.** Scanned PDFs are out of scope and fail loudly; there is no test
  because there is no feature.
* **Cloud providers are not tested in CI.** The suite forces the offline engine;
  the OpenAI-compatible and Ollama providers have no automated tests. Their
  behaviour was exercised manually only when a key/daemon was available.
* **No migration tests.** `backend/alembic/versions/` is empty, so no upgrade/downgrade
  path is tested.
* **No multi-worker stress test.** `FOR UPDATE SKIP LOCKED` claiming is exercised
  by the single in-process worker, not by several concurrent worker processes.
* **No chaos / fault-injection tests.** Degradation paths (Redis down, Qdrant down)
  are implemented and logged, but not automatically tested.
* **Windows-only verification.** The scripts and the native runtimes were verified
  on Windows; Linux/macOS have not been tested, and Docker was not used.

## 6. Running everything

```powershell
# 1. Start the infrastructure
powershell -ExecutionPolicy Bypass -File scripts\infra-start.ps1

# 2. Unit tests (no external services needed for the pure ones)
cd backend
..\.venv\Scripts\python.exe -m pytest tests/unit -q

# 3. Full test suite (needs PostgreSQL + Redis; Qdrant for semantic paths)
..\.venv\Scripts\python.exe -m pytest -q

# 4. Start the app and run the end-to-end smoke test
cd ..
powershell -ExecutionPolicy Bypass -File scripts\dev.ps1 start
.venv\Scripts\python.exe scripts\smoke_test.py
```

Lint and type checks (configured in `backend/pyproject.toml`):

```powershell
cd backend
..\.venv\Scripts\python.exe -m ruff check app tests
..\.venv\Scripts\python.exe -m ruff format --check app tests
..\.venv\Scripts\python.exe -m mypy app
```

Ruff selects `E, W, F, I, B, UP, C4, SIM, RUF`. mypy is focused on the AI/domain
layers; `app.repositories.*` is excluded because SQLAlchemy's async typing is
noisy without the plugin.

## 7. Related documents

* [deployment.md](deployment.md) — how to bring the stack up first
* [../docs/thesis/09-系统测试.md](thesis/09-系统测试.md) — the Chinese test chapter
* [api.md](api.md) — the endpoints the smoke test exercises
