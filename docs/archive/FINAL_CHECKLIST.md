# Final checklist

Verification performed on **2026-10-03** on the development machine:

- Windows 11 Home (Chinese), AMD Ryzen 7 7435H (8 cores), 15.7 GB RAM
- Python 3.13.14 · Node 22.22.2 · npm 10.9.7
- PostgreSQL **16.4** · Redis **5.0.14.1** · Qdrant **1.12.4** — all running as native,
  project-scoped processes started by `scripts/infra-start.ps1`
- Docker: **not installed**, and the Docker Hub registry endpoint is unreachable from
  this machine (verified by direct HTTP probe). Anything Docker-related is therefore
  marked as provided-but-unverified, not as passing.
- AI provider in use during verification: the **offline deterministic engine**
  (`AI_PROVIDER=openai_compatible` with an empty `AI_API_KEY` degrades to it and
  reports `degraded: true`). No cloud key was configured, so no hosted model was
  exercised. The OpenAI-compatible and Ollama code paths are implemented and
  reachable by configuration, but were **not** run against a live vendor.

Legend: **PASS** = executed and observed. **FAIL** = executed and did not meet the
requirement. **N/A** = cannot be verified in this environment, with the reason given.

---

## Infrastructure

| # | Item | Result | Evidence |
|---|---|---|---|
| 1 | Frontend runs | **PASS** | `npm run dev` serves `http://127.0.0.1:5173` (HTTP 200); `npm run build` succeeds; 18 pages render with real data (screenshots) |
| 2 | Backend runs | **PASS** | `python -m app.cli serve` serves `http://127.0.0.1:8000`; `GET /api/v1/health` → `{"status":"ok"}` |
| 3 | PostgreSQL runs | **PASS** | `SELECT version()` → `PostgreSQL 16.4`; 22 tables created; `alembic check` → no pending operations |
| 4 | Redis runs | **PASS** | `redis-cli ping` → `PONG`; cache hit/miss counters observable via `GET /metrics` |
| 5 | Qdrant runs | **PASS** | `GET /collections` → 200; collection created on first embed with the detected dimension (384) |
| 6 | Docker runs | **N/A** | Docker Desktop is not installed and `registry-1.docker.io` is unreachable (probe returned HTTP 000 / timeout). `docker-compose.yml`, `backend/Dockerfile`, `frontend/Dockerfile`, `worker/Dockerfile` are provided and reviewed but **were never built or started**. The warning is stated at the top of the compose file and in `docs/deployment.md`. |
| 7 | One-click start | **PASS** | `start.bat` (native path) verified end to end; `scripts/fetch-runtime.ps1`, `infra-start.ps1`, `infra-status.ps1`, `infra-stop.ps1`, `dev.ps1` all executed. `start.sh`/`stop.sh` are provided but were not executed (the verified environment is Windows). |

## Knowledge ingestion

| # | Item | Result | Evidence |
|---|---|---|---|
| 8 | Document upload | **PASS** | 4 files accepted with HTTP 202; response carries `document`, `job_id`, `duplicate` |
| 9 | PDF parsing | **PASS** | `distributed-systems.pdf` → 3 pages, 1,813 chars extracted, 3 chunks |
| 10 | Markdown parsing | **PASS** | `java-backend-notes.md` → 8 chunks; `rag-notes.md` → 7 chunks |
| 11 | TXT parsing | **PASS** | `software-engineering-checklist.txt` → 1 chunk; GB18030 fallback covered by a unit test |
| 12 | Chunking | **PASS** | Heading-aware sections, sentence-boundary packing, overlap, breadcrumb prefix — 10 unit tests |
| 13 | Embedding | **PASS** | 19/19 chunks embedded (8+7+1+3), 100% success, dimension 384, 24 h content-hash cache verified (8 cache hits on re-ingest) |
| 14 | Vector search | **PASS** | Semantic mode returns hits in 26 ms; Qdrant point count grows and shrinks with document lifecycle |
| 15 | Hybrid search | **PASS** | Hybrid mode returns `vector_score` and `keyword_score` per result; both legs non-zero |
| 16 | Duplicate detection | **PASS** | Second upload of identical content → `duplicate: true`, no new document, no re-embedding |
| 17 | Re-ingest idempotency | **PASS** | Re-running the pipeline produces the same chunk count; no duplicate rows or vectors |

## RAG

| # | Item | Result | Evidence |
|---|---|---|---|
| 18 | RAG answering | **PASS** | `POST /chat/rag` returns a non-empty grounded answer |
| 19 | Citation | **PASS** | 10–13 citations returned, each with `document_id`, `document_title`, `chunk_id`, `chunk_index`, `score`, `snippet`; every citation resolves to a real chunk |
| 20 | Query rewrite | **PASS** | `queries` array contains the original plus rewritten variants; a rewrite failure degrades to the raw question (logged, non-fatal) |
| 21 | No-context honesty | **PASS** | Empty knowledge base → `grounded: false`, zero citations, explicit "no material found" answer |
| 22 | Tenant isolation | **PASS** | Another user's knowledge base returns 404; search cannot leak across users |

## Tasks & planning

| # | Item | Result | Evidence |
|---|---|---|---|
| 23 | Task planner | **PASS** | 6 phases / 18 tasks generated; every task carries a verifiable `completion_criteria` and an integer estimate |
| 24 | Planner persistence | **PASS** | `create_tasks: true` produced a hierarchical tree (parent → child), verified via `GET /tasks/tree` |
| 25 | Plan replay | **PASS** | `POST /planner/plan/apply` rebuilds tasks from a stored run's full plan payload |
| 26 | Dependencies | **PASS** | Completing a blocked task → **409**; after the dependency completes the child succeeds; cycles → **409**; self-dependency → **422** |
| 27 | Knowledge link | **PASS** | `POST /tasks/{id}/knowledge` links a document; it appears in the task detail and in the graph |

## Agent

| # | Item | Result | Evidence |
|---|---|---|---|
| 28 | Tool calling | **PASS** | `POST /agents/run` executed 4 tools (`knowledge_search` → `task_planner` → `task_create_batch` → `note_create`), all `ok: true` |
| 29 | Tool catalogue | **PASS** | `GET /agents/tools` returns 11 tools with JSON parameter schemas |
| 30 | Entity creation | **PASS** | The run created real tasks and a note; ids returned in `created` and persisted on `agent_runs.created_entity_ids` |
| 31 | Loop protection | **PASS** | Duplicate identical calls are detected, recorded as `SKIPPED`, and terminate the loop with `stopped_reason: loop_detected` |
| 32 | Bounded execution | **PASS** | `max_steps` honoured; a 1-step run performed at most 1 tool call |
| 33 | No direct DB access | **PASS** | By construction — the agent can only call registered tools (verified by code inspection and the tool registry test) |

## AI engineering

| # | Item | Result | Evidence |
|---|---|---|---|
| 34 | Prompt versioning | **PASS** | 9 templates seeded as v1; a new version becomes current, activation/rollback works, an identical version is rejected (409), reset restores the built-in |
| 35 | Prompt version on the run | **PASS** | After bumping `rag_answer` to v2, a new RAG run recorded `prompt_version: 2` |
| 36 | AI run log | **PASS** | 26+ runs recorded with type, status, provider, model, prompt name/version, tokens, latency, retry count, cost |
| 37 | Execution trace | **PASS** | `GET /ai-runs/{run_id}` returns request payload, retrieval metadata, cited sources, tool calls with arguments/results, response text and error info |
| 38 | Token accounting | **PASS** | Prompt/completion/total tokens recorded (e.g. 2,680 / 798 / 3,478 on a note-generation run) |
| 39 | Latency tracking | **PASS** | Per-run `latency_ms`; aggregated average exposed by `GET /ai-runs/metrics` |
| 40 | Provider abstraction | **PASS** | Three providers behind one interface; `AI_PROVIDER`/`EMBEDDING_PROVIDER` switch by configuration only. Only the offline engine was executed; the OpenAI-compatible and Ollama implementations are code-complete but **not run against a live vendor** |
| 41 | Graceful degradation | **PASS** | Missing key → `degraded: true` with a reason on `GET /health/deps` and on every run; the product stays fully functional |
| 42 | Retry | **PASS** | Bounded attempts + exponential backoff + jitter + wall-clock budget; non-retryable errors fail fast — 10 unit tests |
| 43 | Timeout | **PASS** | Per-call HTTP timeout, per-job `asyncio.timeout`, and an `AITimeoutError` classification |

## Platform

| # | Item | Result | Evidence |
|---|---|---|---|
| 44 | Project CRUD | **PASS** | Create/list/detail-with-stats/update/delete; duplicate name → 409 |
| 45 | Task CRUD | **PASS** | Create/list/tree/detail/update/complete/delete; illegal status transition → 409 |
| 46 | Note CRUD | **PASS** | Create/read/update/delete; search by content; AI-generated flag persisted |
| 47 | Global search | **PASS** | `Ctrl+K` command palette over documents, tasks, projects, notes and AI actions (verified in the browser) |
| 48 | Dashboard | **PASS** | Every number computed from the database; no seeded demo data anywhere in the product |
| 49 | Knowledge graph | **PASS** | 88 nodes / 155 edges across project, knowledge base, document, concept, task, note and AI-run nodes; `uses_knowledge` edges present |
| 50 | Error handling | **PASS** | 404 / 409 / 413 / 415 / 422 / 401 all return the documented `error.code` in the envelope |
| 51 | Retry & timeout coverage | **PASS** | Covered by `tests/unit/test_retry.py` and the job-level timeout test |
| 52 | Logging | **PASS** | One structured line per request with `request_id`, method, path, status and duration; `run_id` on every AI log line; secrets redacted |
| 53 | Metrics | **PASS** | `GET /metrics` returns 24 h AI counters and cache hit/miss statistics |
| 54 | Unit tests | **PASS** | 194 unit tests (chunker, offline engine, retry, security, files, parsers, tools, project integrity) |
| 55 | Integration tests | **PASS** | 51 tests (ingestion, search, RAG, analysis, tasks, planner, agent, prompts, worker) |
| 56 | API tests | **PASS** | 25 tests (envelope, auth, validation, error taxonomy, pagination, insights, isolation) |
| 57 | Frontend tests | **PASS** | 114 Vitest tests including a live API-flow test against `127.0.0.1:8000` |
| 58 | E2E tests | **PASS** | `scripts/smoke_test.py` — **75/75 assertions passed** against the running stack |
| 59 | Docker test | **N/A** | See item 6 — Docker is unavailable on this machine |
| 60 | Coverage | **PASS** | 270 backend tests, **80.4%** line coverage (`concurrency = ["greenlet","thread"]` required for accurate measurement) |

## Deliverables

| # | Item | Result | Evidence |
|---|---|---|---|
| 61 | README | **PASS** | `README.md` — screenshots, stack, features, architecture diagram, quick start, env vars, tests, AI config, structure, DB design, RAG and agent flows, engineering notes, roadmap |
| 62 | API documentation | **PASS** | `docs/api.md` (full contract) + live OpenAPI at `/docs`, `/redoc`, `/openapi.json` — 49 paths / 65 operations |
| 63 | Architecture documentation | **PASS** | `docs/architecture.md` plus `database.md`, `ai.md`, `rag.md`, `agent.md`, `deployment.md`, `testing.md`, `security.md`, `development.md` |
| 64 | Thesis documentation | **PASS** | `docs/thesis/01..10` — ten Chinese chapters grounded in the real implementation |
| 65 | Screenshots | **PASS** | 16 PNGs in `screenshots/`, all captured from the running application via `scripts/capture-screenshots.mjs` (real data, real interactions) |
| 66 | Resume description | **PASS** | `docs/resume-project.md` |
| 67 | Interview questions | **PASS** | `docs/interview-questions.md` — 40 questions with intent, the actual implementation, a model answer and likely follow-ups |
| 68 | Repository hygiene | **PASS** | `.gitignore` excludes secrets, `.runtime/`, `vendor/runtime/`, `node_modules`, `dist`, caches and editor noise; `git status` reviewed before commit |
| 69 | Migrations | **PASS** | `alembic upgrade head` on an empty database creates 23 tables; `alembic check` reports no drift; `alembic downgrade base` reverts cleanly |
| 70 | Sample data | **PASS** | `samples/` contains 4 documents plus a dependency-free PDF generator (`scripts/make_sample_pdf.py`) |

---

## Summary

| Result | Count |
|---|---|
| **PASS** | **68** |
| **FAIL** | **0** |
| **N/A** (environment limitation, documented) | **2** (Docker runtime, Docker test) |

### What is genuinely verified

The full product loop runs on this machine: upload → parse → chunk → embed → Qdrant →
hybrid retrieval → citation-enforced RAG → planning → task creation → tool-calling
agent → execution trace → dashboard, with a React UI on top and 270 backend tests plus
114 frontend tests plus a 75-assertion end-to-end harness.

### What is NOT verified, stated plainly

1. **Docker.** No Docker installation and an unreachable Docker Hub. The compose file
   and three Dockerfiles exist and were reviewed, but nothing was built or started.
2. **A hosted LLM.** No API key was configured, so every AI feature was exercised
   through the deterministic offline engine. The OpenAI-compatible and Ollama
   providers are implemented but were not run against a live vendor. Answer quality
   in the screenshots therefore reflects the offline engine, not a frontier model.
3. **`start.sh` / `stop.sh`.** Provided for Linux/macOS; the verified path is Windows
   (`start.bat` / `stop.bat`).
4. **The optional `worker` compose profile.** Provided, not started (see 1).
5. **CI.** No CI pipeline is configured. All test runs were manual.

### Known product limitations

- No OCR — a scanned PDF fails with an explicit message rather than silently
  producing an empty document.
- The reranker is lexical; no cross-encoder model is shipped.
- AI responses are not streamed token by token.
- No rate limiting, refresh-token rotation, audit log or upload virus scanning.
- Single-node only.
