# Project delivery

A handover document for **AI Workbench 1.0.0** — what was built, how to run it, what
was verified, and how to use it for a graduation thesis, a resume and an interview.

- **Delivered**: 2026-10-03
- **Location**: `C:\Users\user\Desktop\ai-workbench`
- **Verification**: see [`FINAL_CHECKLIST.md`](FINAL_CHECKLIST.md) — 68 PASS, 0 FAIL,
  2 N/A (Docker runtime, documented)

---

## 1. What this is

A **local-first AI knowledge and task execution platform**. It closes one loop:

```
资料 → 知识 → 任务 → 执行 → 结果 → 再沉淀为知识
documents → knowledge → tasks → execution → results → back into knowledge
```

It is deliberately not a chat wrapper and not a CRUD admin panel. The engineering
content is in four places:

1. **A grounded RAG pipeline** that is not allowed to answer without sources.
2. **A tool-calling agent** that can only reach the database through typed tools.
3. **An AI engineering layer** — provider abstraction, prompt versioning, execution
   tracing, bounded retry, structured output, token and cost accounting.
4. **A verifiable closed loop** — knowledge links to tasks, tasks link to AI runs, and
   every AI answer records exactly which documents produced it.

---

## 2. Final tech stack

| Layer | Technology | Version |
|---|---|---|
| Language (backend) | Python | 3.13.14 |
| Web framework | FastAPI | 0.142.2 |
| Validation | Pydantic / pydantic-settings | 2.13.5 / 2.15.0 |
| ORM | SQLAlchemy (async) | 2.0.54 |
| DB driver | asyncpg | 0.31.0 |
| Migrations | Alembic | 1.20.0 |
| Relational DB | PostgreSQL | 16.4 |
| Cache | Redis | 5.0.14.1 |
| Vector DB | Qdrant (+ qdrant-client 1.12.2) | 1.12.4 |
| HTTP client | httpx | 0.28.1 |
| PDF parsing | pypdf | 5.9.0 |
| Auth | PyJWT + bcrypt | 2.15.1 / 4.3.0 |
| Frontend | React + TypeScript | 18 / 5.x |
| Build | Vite | 5.4.21 |
| Styling | Tailwind CSS | 3.4 |
| Server state | TanStack Query | 5.x |
| Client state | Zustand | 5.x |
| Graph layout | d3-force | 3.x |
| Backend tests | Pytest + pytest-asyncio + pytest-cov + pytest-timeout | 8.4.2 |
| Frontend tests | Vitest + Testing Library | 2.1.4 |
| Linting | Ruff / ESLint / Prettier | 0.16.10 |
| Browser automation (screenshots) | playwright-core driving system Edge | — |

Not used, deliberately: **LangChain** (the provider/retriever/agent layers are small
enough to own), **Celery** (poor Windows fit; a DB-backed queue is transactional with
the business data), any UI component kit (the visual identity matters here).

---

## 3. Feature completion

| Area | Status | Notes |
|---|---|---|
| Dashboard with real data | Complete | Every figure computed from the database; no seeded demo data |
| Knowledge bases (CRUD + stats) | Complete | Duplicate names rejected; cascade delete removes chunks and vectors |
| Document import (MD / TXT / PDF) | Complete | Streaming size limit, MIME check, content-hash dedup |
| Ingestion pipeline | Complete | Parse → normalise → chunk → embed → Qdrant, with per-stage progress and persisted failures |
| Chunking | Complete | Heading-aware, sentence-boundary packing, overlap, breadcrumb prefix |
| Embedding | Complete | Batched, content-hash cached (24 h), dimension auto-detected |
| Vector store | Complete | Lazy collection creation, payload indexes, filtered search |
| Keyword / semantic / hybrid search | Complete | Both score legs returned for explainability |
| Reranking | Complete | Lexical (coverage, phrase, heading, length prior) — no cross-encoder |
| RAG with citations | Complete | Query rewrite → retrieval → assembly → generation → citation mapping |
| AI document analysis | Complete | summary, concepts, key points, terms, TODOs, study plan, interview questions, note, flashcards |
| Task planner | Complete | Goal → phase → task → subtask, with estimates, dependencies and criteria |
| Task management | Complete | Tree, dependencies (cycle-safe), status rules, knowledge links |
| Tool-calling agent | Complete | 11 tools, bounded loop, loop detection, write cap |
| AI execution trace | Complete | Prompt name **and version**, sources, tool calls, tokens, latency, retries, cost |
| Prompt versioning | Complete | Full history, activation, rollback, reset |
| Multi-provider AI | Complete | OpenAI-compatible, Ollama, deterministic offline engine |
| Async worker | Complete | DB queue, `SKIP LOCKED`, retries, stale recovery, job timeout |
| Redis caching | Complete | Search, prompts, stats, embeddings, with namespace invalidation |
| Search / command palette | Complete | `Ctrl+K` over documents, tasks, projects, notes and AI actions |
| Knowledge graph | Complete | Real projection of foreign keys and join tables |
| Frontend UI | Complete | 18 pages, dark-first, three-column shell, responsive |
| Markdown editor | Complete | Split-pane live preview |
| Docker | **Provided, not executed** | No Docker on the dev machine; see the warning in `docker-compose.yml` |
| OCR for scanned PDFs | Not implemented | Fails with an explicit message |
| Token streaming | Not implemented | Responses arrive complete |

---

## 4. Test results

Executed on 2026-10-03 against the running stack.

```
backend/tests/unit           194 passed
backend/tests/integration     51 passed
backend/tests/api             25 passed
                            -----------
                             270 passed     coverage 80.4%

frontend (vitest)            114 passed
scripts/smoke_test.py         75/75 assertions passed
```

Coverage highlights: models 100%, `core/retry` 96%, `core/security` 95%,
`core/config` 97%, `infrastructure/files` 90%, `ai/providers` 83–91%,
`services/document_service` 83%, `services/ai_services` 85%, `worker/runner` 59%
(the worker's retry/backoff branches are exercised indirectly).

**A note on measuring coverage here.** SQLAlchemy's async engine executes ORM work
inside greenlets; without `concurrency = ["greenlet", "thread"]` in the coverage
configuration the tool silently misses those lines and reports ~68% instead of 80.4%.

---

## 5. Docker start

```bash
cp .env.example .env
# set SECRET_KEY (required) and optionally AI_API_KEY
docker compose up -d
```

Services: `postgres` · `redis` · `qdrant` · `api` (worker in-process) · `frontend`
(Nginx serving the SPA and proxying `/api`). Optional separate worker:

```bash
# WORKER_ENABLED=false in .env first
docker compose --profile worker up -d
```

> **This was not executed.** The development machine has no Docker installation and
> `registry-1.docker.io` is unreachable from it. The compose file and three
> Dockerfiles are provided and reviewed; treat them as a starting point and verify on
> a Docker-capable machine.

## 5b. Verified native start (the path that was actually tested)

```bat
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r backend\requirements-dev.txt
cd frontend && npm install && cd ..
start.bat
```

`start.bat` downloads PostgreSQL / Redis / Qdrant into `vendor/` on first run, starts
them as project-scoped processes, then starts the API and the UI. Nothing is installed
as a Windows service and no system setting is modified.

---

## 6. Default access points

| Service | URL | Credentials |
|---|---|---|
| Web UI | http://127.0.0.1:5173 | `demo` / `demo1234` |
| API | http://127.0.0.1:8000 | — |
| API docs (Swagger) | http://127.0.0.1:8000/docs | — |
| API docs (ReDoc) | http://127.0.0.1:8000/redoc | — |
| OpenAPI JSON | http://127.0.0.1:8000/openapi.json | — |
| Dependency health | http://127.0.0.1:8000/api/v1/health/deps | — |
| Qdrant dashboard | http://127.0.0.1:6333/dashboard | — |
| PostgreSQL | `127.0.0.1:5432` / db `aiworkbench` | `aiworkbench` / `aiworkbench` |
| Redis | `127.0.0.1:6379` | — |

Local ports are bound to `127.0.0.1` only. The credentials above are development
defaults and must be changed before exposing anything beyond localhost.

---

## 7. GitHub publication checklist

Before pushing:

```bash
git status          # nothing unintended staged
git diff --cached   # review what you are about to commit
git log --oneline   # readable history
```

Verify **none** of the following are tracked:

- [ ] `.env` or any file containing an API key, password or secret
- [ ] `.runtime/` (database files, uploaded documents, logs, PID files)
- [ ] `vendor/runtime/` (the ~350 MB of downloaded binaries)
- [ ] `node_modules/`, `dist/`, `__pycache__/`, `.pytest_cache/`, `.ruff_cache/`
- [ ] `.coverage`, `htmlcov/`, `tests/coverage/`
- [ ] absolute local paths inside source or documentation
- [ ] editor or IDE configuration

Recommended repository setup:

1. **Description** — "Local-first AI knowledge & task execution platform: grounded RAG
   with citations, a tool-calling agent, and a full AI execution trace."
2. **Topics** — `fastapi` `rag` `ai-agents` `tool-calling` `qdrant` `postgresql`
   `redis` `react` `typescript` `local-first` `llm` `prompt-engineering`
3. **License** — MIT (already included).
4. Pin the README screenshots; they are the first thing a reviewer sees.
5. Tag `v1.0.0` after the first push and paste the changelog section into the release
   notes.

---

## 8. Using this project for a graduation thesis

The repository already contains a ten-chapter support pack in `docs/thesis/`:

| Chapter | Maps to |
|---|---|
| 01 项目背景 | The problem, existing tools and their gaps |
| 02 需求分析 | Functional and non-functional requirements, use cases |
| 03 可行性分析 | Technical, economic and operational feasibility |
| 04 系统总体设计 | Layered architecture, module decomposition, deployment topology |
| 05 数据库设计 | 22 tables, ER diagram, constraints and indexes |
| 06 详细设计 | Per-module design: ingestion, retrieval, planning, worker |
| 07 AI 模块设计 | Provider abstraction, prompt versioning, RAG, agent, tracing |
| 08 系统实现 | Implementation walkthrough with real class and endpoint names |
| 09 系统测试 | Test strategy, the 75 assertions, the ten defects found and fixed |
| 10 总结与展望 | Contributions, honest limitations, future work |

Suggested way to use them: take the structure and the facts, then rewrite in your own
voice and add your own analysis. Every number in those chapters is reproducible from
the repository — re-run `python scripts/smoke_test.py` and
`python -m pytest --cov=app` and paste your own output.

**Claim what is true.** The strongest, most defensible contributions are: the
closed knowledge→task→execution loop; the citation-enforced RAG design; the
provider-agnostic AI layer with reported degradation; the tool-scoped agent with
safety limits; and the execution trace that makes AI behaviour auditable.

---

## 9. Using this project on a resume

Full text: [`docs/resume-project.md`](docs/resume-project.md).

The short version — lead with architecture and AI engineering, not with "used
FastAPI":

> Designed and built a local-first AI knowledge & task platform (FastAPI + PostgreSQL
> + Redis + Qdrant + React/TypeScript). Implemented a citation-enforced RAG pipeline
> (query rewrite → hybrid dense/sparse retrieval → lexical rerank → grounded answer),
> a bounded tool-calling agent over 11 typed tools with no direct database access, and
> a provider-agnostic AI layer (OpenAI-compatible / Ollama / offline) with versioned
> prompts and a full execution trace. 270 backend tests at 80% coverage; verified
> end-to-end with a 75-assertion harness.

Do **not** claim: a hosted-model evaluation, production traffic, Docker verification,
or any benchmark you did not run.

---

## 10. Interview focus

Full list: [`docs/interview-questions.md`](docs/interview-questions.md) — 40 questions
with intent, implementation, model answer and follow-ups.

The five you will almost certainly be asked, and the honest answers:

1. **"How is this different from a ChatGPT wrapper?"** — The model never sees an
   ungrounded prompt. Retrieval is hybrid and reranked, the answer must cite numbered
   sources, and every run records which chunks produced it. The agent cannot answer
   without first searching the knowledge base.

2. **"Why did you not use LangChain?"** — The provider, retriever, agent and tool
   layers are each a few hundred lines. Owning them means I can explain every decision,
   the failure behaviour is explicit, and switching providers is a config change rather
   than a framework upgrade.

3. **"How do you stop the agent doing something dangerous?"** — The agent can only call
   registered tools; it has no database handle. Tools declare JSON schemas that are
   validated before execution, write tools are counted against a per-run cap, identical
   repeated calls terminate the loop, and every call is persisted with its arguments
   and result.

4. **"What happens when the model API is down?"** — Errors are classified
   (auth / rate-limit / timeout / provider / format). Only retryable ones are retried,
   with exponential backoff, jitter, an attempt cap and a wall-clock budget. If no key
   is configured at all, the factory degrades to the deterministic offline engine and
   reports `degraded: true` with a reason — visible in the UI, never hidden.

5. **"Tell me about a hard bug you fixed."** — The worker's main loop returned without
   awaiting whenever every slot was busy, which starved the event loop and froze the
   entire process, including the HTTP API. It only appeared once ingestion actually
   ran. The fix is to always yield when nothing was claimed, and there is now a
   regression test that measures the maximum gap between yields of a concurrent
   coroutine. (Nine more are documented in `README.md` and `CHANGELOG.md`.)

---

## 11. Known limitations

Stated plainly so nothing here is oversold:

1. **Docker is unverified** on the development machine.
2. **No hosted model was exercised.** All AI verification used the offline engine. Its
   answers are extractive and lower quality than a frontier model's; the pipeline
   around it is what is being demonstrated.
3. **`start.sh` / `stop.sh` were not run** — the verified path is Windows.
4. **No OCR**, so scanned PDFs fail explicitly.
5. **The reranker is lexical** — no cross-encoder model is bundled.
6. **No token streaming.**
7. **No rate limiting, refresh-token rotation, audit log or virus scanning.**
8. **Single-node** — horizontal scaling would need the worker split out and the
   embedding step made idempotent across instances.
9. **No CI pipeline** — all test runs were manual.
