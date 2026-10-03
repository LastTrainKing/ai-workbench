# AI Workbench

**A local-first AI knowledge & task execution platform.**
Upload your documents, ask grounded questions with citations, turn knowledge into an
executable task tree, and let a tool-calling agent do the work — with a full audit
trail of every AI call.

It is not a chat wrapper. It closes one loop:

```
资料 → 知识 → 任务 → 执行 → 结果 → 再沉淀为知识
documents → knowledge → tasks → execution → results → back into knowledge
```

<p align="center">
  <img src="screenshots/01-dashboard.png" alt="Dashboard" width="49%">
  <img src="screenshots/04-rag-chat.png" alt="Grounded RAG chat with citations" width="49%">
</p>
<p align="center">
  <img src="screenshots/05-task-planner.png" alt="Task planner" width="49%">
  <img src="screenshots/06-agent.png" alt="Tool-calling agent trajectory" width="49%">
</p>
<p align="center">
  <img src="screenshots/02-knowledge.png" alt="Knowledge bases" width="49%">
  <img src="screenshots/07-ai-runs.png" alt="AI execution traces" width="49%">
</p>

---

## Table of contents

- [Why this project exists](#why-this-project-exists)
- [Core features](#core-features)
- [Tech stack](#tech-stack)
- [Architecture](#architecture)
- [Quick start](#quick-start)
- [Environment variables](#environment-variables)
- [Running with Docker](#running-with-docker)
- [Local development](#local-development)
- [Tests](#tests)
- [AI configuration](#ai-configuration)
- [Project structure](#project-structure)
- [Database design](#database-design)
- [RAG pipeline](#rag-pipeline)
- [Agent & tools](#agent--tools)
- [API](#api)
- [Engineering notes](#engineering-notes)
- [Roadmap](#roadmap)
- [License](#license)

---

## Why this project exists

People accumulate PDFs, Markdown notes, course material, meeting records and READMEs.
The usual outcome is: *lots of material, nothing findable; lots read, nothing retained;
plenty to do, nothing turned into a task; and the material and the work stay in
separate worlds.*

AI Workbench attacks that specific problem:

| Symptom | What the product does |
|---|---|
| "I have the file but cannot find the answer" | Hybrid retrieval (vector + keyword) with a reranker and **citation-enforced** answers |
| "I read it but did not retain it" | AI extraction of concepts, terms, key points, flashcards and a study plan |
| "I know what to do but never start" | Goal → phase → task → subtask planning with estimates, dependencies and verifiable completion criteria |
| "The task and the source are disconnected" | A real `task ↔ document` link, and a knowledge graph over projects, documents, concepts, tasks and AI runs |
| "I cannot tell what the AI actually did" | Every run stores its prompt **and prompt version**, retrieved sources, tool calls, tokens, latency, retries and errors |

Everything runs locally by default: PostgreSQL, Redis and Qdrant are project-scoped
native processes, and the model backend can be a local Ollama daemon — your documents
never have to leave the machine.

---

## Core features

**Knowledge ingestion** — Markdown / TXT / PDF upload with extension and MIME
validation, streaming size limits, content-hash duplicate detection, and an
asynchronous pipeline (parse → normalise → heading-aware chunk → embed → Qdrant).
Status is `PENDING → PROCESSING → COMPLETED | FAILED` with live progress and a
human-readable failure reason.

**Grounded RAG** — query rewrite → hybrid retrieval → lexical rerank → budgeted
context assembly → generation → citation extraction. The prompt forbids answering
from prior knowledge; if retrieval finds nothing the API says so instead of
hallucinating.

**Search** — keyword, semantic and hybrid modes, with the dense and sparse scores
returned separately so the UI can show *why* a chunk matched.

**AI document analysis** — summary, concepts, key points, terms, TODO extraction,
7-day study plan, interview questions, Markdown note and flashcards. Outputs are
persisted (concepts into the graph, notes into the workspace), not just displayed.

**Task planner** — produces a real task tree with per-task estimates, priorities,
dependencies and verifiable completion criteria, optionally persisted as tasks with
a single extra call.

**Tool-calling agent** — a bounded ReAct-style loop over 11 typed tools. The agent
cannot touch the database; it can only call tools, which call services, which call
repositories. Duplicate-call loop detection and a per-run write-operation cap keep it
safe.

**Prompt versioning** — prompts are database rows with an immutable version history.
Business code requests a prompt *by name*; every AI run records which version answered.

**Multi-provider AI** — DeepSeek (default), any OpenAI-compatible endpoint, Ollama
(fully local) and a deterministic offline engine. Switching is a configuration change
**or a click in Settings → AI Provider**, and it takes effect on the very next AI call.
When a cloud provider is selected but no key is available the factory degrades to the
offline engine and records `degraded: true` plus the reason — the product keeps working
and never pretends a hosted model answered.

**Runtime provider configuration** — provider, model, base URL, API key, temperature,
max tokens and timeout are all editable in the UI. Changes persist to
`.runtime/ai-config.json` (git-ignored, local machine only) with `.env` as the
baseline, so a key never has to be written into source or committed to Git. The API
key is write-only: reads return `api_key_set` and a mask like `sk-****(len=35)`.

**AI execution trace** — request payload, prompt name/version, retrieval metadata,
cited sources, tool calls with arguments and results, token usage, latency, retry
count, cost estimate and error classification.

**Async worker** — a PostgreSQL-backed queue using `SELECT … FOR UPDATE SKIP LOCKED`,
bounded retries with exponential backoff, stale-job recovery and a job-level timeout.
It can run in-process (one command to start) or as its own process.

**Dashboard** — every number is computed from the database: tasks due today and
overdue, knowledge and chunk counters, storage usage, AI runs, tokens, average
latency, failure rate, provider status and an activity feed. There is no seeded demo
data anywhere in the product.

---

## Tech stack

| Layer | Choice | Why |
|---|---|---|
| Backend | Python 3.13 · FastAPI · Pydantic v2 | async-first, typed, OpenAPI for free |
| ORM / migrations | SQLAlchemy 2.0 (async) · asyncpg · Alembic | explicit queries, real migrations |
| Relational DB | PostgreSQL 16 | JSONB, `SKIP LOCKED`, real constraints |
| Vector DB | Qdrant 1.12 | payload filtering for tenant isolation, HNSW |
| Cache / locks | Redis 5 | search cache, prompt cache, namespaces, locks |
| Background work | custom DB-backed worker | Windows-friendly, transactional with business data |
| Frontend | React 18 · TypeScript · Vite · Tailwind | fast, typed, no UI-kit look |
| Server state | TanStack Query · Zustand | caching, polling, small local state |
| Graph view | `d3-force` + hand-rolled SVG | no charting-library bloat |
| Tests | Pytest · Vitest · Playwright | unit → integration → API → E2E |

Deliberately **not** used: LangChain (the provider/retriever/agent/tool layers are
small enough to own outright and are far easier to reason about), Celery (poor fit on
Windows and unnecessary given the DB-backed queue), and a component kit (the visual
identity matters for this project).

---

## Architecture

```mermaid
flowchart TB
    subgraph Client
        UI["React SPA<br/>Vite · Tailwind · TanStack Query"]
    end

    subgraph API["FastAPI — app/api"]
        R["/api/v1 routers<br/>validation · envelope · pagination"]
    end

    subgraph Application["Services — app/services"]
        S1[AuthService]
        S2[DocumentService]
        S3[TaskService]
        S4[SearchService]
        S5[ChatService]
        S6[PlannerService]
        S7[AgentService]
        S8[StatsService]
    end

    subgraph AILayer["AI layer — app/ai"]
        P["providers<br/>openai_compatible · ollama · offline"]
        PR["prompt registry<br/>(versioned, cached)"]
        RAG["rag<br/>chunker · embedding · retriever · pipeline"]
        AG["agent<br/>executor · tools"]
        TR["tracing<br/>(ai_runs · tool_calls · sources)"]
    end

    subgraph Repos["Repositories — app/repositories"]
        RP["only layer that issues SQL"]
    end

    subgraph Infra["Infrastructure — app/infrastructure"]
        PG[(PostgreSQL 16)]
        RD[(Redis 5)]
        QD[(Qdrant 1.12)]
        FS[/local storage/]
    end

    subgraph Worker["Worker — app/worker"]
        W["DB-backed queue<br/>SKIP LOCKED · retries · timeout"]
    end

    UI -->|HTTP JSON| R
    R --> Application
    Application --> AILayer
    Application --> Repos
    AILayer --> Repos
    Repos --> PG
    AILayer --> QD
    Application --> RD
    Application --> FS
    W --> Application
    PG -.->|jobs| W
```

**Layering rules enforced in review**

- API routes validate and serialise; they never contain business logic and never
  issue SQL.
- Services orchestrate; only repositories talk to the database.
- The AI layer depends on provider *interfaces*, never on a vendor SDK.
- The agent reaches the database only through tools.
- Exceptions are never swallowed. Infrastructure outages (Redis, Qdrant) degrade with
  a logged warning and a documented fallback.

Full detail: [`docs/architecture.md`](docs/architecture.md).

---

## Quick start

### Windows (verified path)

```bat
:: 1. one-time environment setup
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r backend\requirements-dev.txt
cd frontend && npm install && cd ..

:: 2. start everything (downloads the native runtimes on first run)
start.bat
```

Open <http://127.0.0.1:5173> and sign in with **`demo` / `demo1234`**.

`start.bat` checks the environment, downloads PostgreSQL / Redis / Qdrant into
`vendor/` if missing, starts them as project-scoped processes (no Windows services,
no system changes), then starts the API and the UI. `stop.bat` shuts it all down.

### Linux / macOS

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r backend/requirements-dev.txt
cd frontend && npm install && cd ..

# PostgreSQL, Redis and (optionally) Qdrant must be reachable on localhost.
./start.sh
```

### Docker

```bash
cp .env.example .env      # then set SECRET_KEY (and AI_API_KEY if you have one)
docker compose up -d
```

> **Honest caveat.** The compose path is provided and reviewed but **was not executed
> on the machine this project was developed on** — that machine has no Docker
> installation and its Docker Hub endpoint is unreachable. Everything else in this
> repository *was* executed and verified against native PostgreSQL 16.4 / Redis 5 /
> Qdrant 1.12.4. See [`docs/deployment.md`](docs/deployment.md).

---

## Environment variables

Copy `.env.example` to `.env`. The most important keys:

| Variable | Default | Purpose |
|---|---|---|
| `SECRET_KEY` | — | **required**; JWT signing key |
| `DATABASE_URL` | `postgresql+asyncpg://aiworkbench:aiworkbench@127.0.0.1:5432/aiworkbench` | relational store |
| `REDIS_URL` | `redis://127.0.0.1:6379/0` | cache |
| `QDRANT_URL` | `http://127.0.0.1:6333` | vector store |
| `QDRANT_COLLECTION` | `ai_workbench_chunks` | collection name |
| `AI_PROVIDER` | `deepseek` | `deepseek` \| `openai_compatible` \| `ollama` \| `offline` |
| `DEEPSEEK_API_KEY` | *(empty)* | empty ⇒ offline engine, clearly reported |
| `DEEPSEEK_BASE_URL` | `https://api.deepseek.com` | DeepSeek endpoint |
| `DEEPSEEK_MODEL` | `deepseek-flash` | DeepSeek V4.1 Flash |
| `AI_BASE_URL` / `AI_API_KEY` / `AI_MODEL` | — | used by the non-DeepSeek providers |
| `EMBEDDING_PROVIDER` | `openai_compatible` | `openai_compatible` \| `ollama` \| `offline` |
| `EMBEDDING_MODEL` | `text-embedding-3-small` | embedding model |
| `CHUNK_SIZE` / `CHUNK_OVERLAP` | `900` / `150` | chunking |
| `RETRIEVAL_TOP_K` / `RETRIEVAL_CANDIDATE_K` | `8` / `40` | retrieval breadth |
| `HYBRID_ALPHA` | `0.6` | dense-leg weight in hybrid fusion |
| `RERANK_ENABLED` | `true` | lexical reranking |
| `WORKER_ENABLED` | `true` | run the worker inside the API process |
| `WORKER_CONCURRENCY` | `2` | parallel jobs |
| `MAX_UPLOAD_MB` | `25` | upload limit (enforced while streaming) |

Full list with comments: [`.env.example`](.env.example).

---

## Running with Docker

`docker compose up -d` starts `postgres`, `redis`, `qdrant`, `api` (with the worker
in-process) and `frontend` (Nginx serving the SPA and reverse-proxying `/api`).

To run the worker as a separate container instead:

```bash
# set WORKER_ENABLED=false in .env first
docker compose --profile worker up -d
```

---

## Local development

```bash
# infrastructure only
powershell -ExecutionPolicy Bypass -File scripts\infra-start.ps1
powershell -ExecutionPolicy Bypass -File scripts\infra-status.ps1

# API with auto-reload (worker in-process)
cd backend && ..\.venv\Scripts\python.exe -m app.cli serve --reload

# worker as its own process (set WORKER_ENABLED=false in .env)
python worker/run.py --concurrency 2

# frontend
cd frontend && npm run dev
```

Useful CLI commands:

```bash
python -m app.cli check      # verify PostgreSQL / Redis / Qdrant / AI providers
python -m app.cli seed       # seed prompt templates + the demo account
python -m app.cli stats      # row counts per table
python -m app.cli reindex --knowledge-base <uuid>
python -m app.cli create-user --username alice --email a@b.dev --password secret123
```

---

## Tests

```bash
# backend — unit + integration + API (needs the infrastructure running)
cd backend
..\.venv\Scripts\python.exe -m pytest                      # everything
..\.venv\Scripts\python.exe -m pytest tests/unit           # fast, no services needed
..\.venv\Scripts\python.exe -m pytest --cov=app            # with coverage

# end-to-end against a running server (75 assertions)
python scripts/smoke_test.py

# frontend
cd frontend
npm run test          # Vitest
npm run typecheck     # tsc --noEmit
npm run lint          # ESLint
npm run build         # production build
```

The backend suite runs against a dedicated `aiworkbench_test` database and Redis
database index 1, so it never touches development data.

See [`docs/testing.md`](docs/testing.md) for the full strategy.

---

## AI configuration

**Default: DeepSeek.** The model answering every AI feature is shown in the top bar
and on **Settings → AI Provider**, and it can be changed there without editing a file
or restarting.

```ini
AI_PROVIDER=deepseek
DEEPSEEK_API_KEY=sk-...            # .env — git-ignored, never committed
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-flash      # DeepSeek V4.1 Flash (fast, low cost)
AI_MAX_TOKENS=8192
```

Get a key at <https://platform.deepseek.com/>. `deepseek-flash` is a **reasoning
model**: it emits a chain of thought before the answer, so the token budget has to be
generous — 8192 is the default and the code reports
`ai_reasoning_budget_exhausted` rather than returning an empty answer if it runs out.

**Switching providers** (Settings → AI Provider, or `PATCH /api/v1/ai-config`):

| Provider | Needs | Notes |
|---|---|---|
| **DeepSeek** | API key | Default. `deepseek-flash` / `deepseek-v4-pro` |
| **OpenAI Compatible** | API key + base URL | OpenAI, Qwen, Moonshot, Zhipu, SiliconFlow, vLLM, LM Studio |
| **Ollama** | local daemon | Fully local; `ollama serve` + a pulled model |
| **Offline** | nothing | Deterministic extractive engine; never fabricates |

Precedence is **runtime override → `.env` → default**, and the Settings page shows
where each value came from. Clearing an override (`DELETE /api/v1/ai-config`) falls
back to `.env`.

```bash
# change provider without touching a file
curl -X PATCH http://127.0.0.1:8000/api/v1/ai-config \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"provider":"ollama","model":"qwen2.5:7b","base_url":"http://127.0.0.1:11434"}'

# prove the current configuration works (one small request)
curl -X POST http://127.0.0.1:8000/api/v1/ai-config/test -H "Authorization: Bearer $TOKEN"

# which provider is actually answering, and why
curl http://127.0.0.1:8000/api/v1/health/deps | jq .data.ai.llm
```

**No key at all:** the product still runs. Every AI feature falls back to the offline
engine, `GET /health/deps` reports `degraded: true` with the reason, the UI shows a
warning badge, and each `ai_runs` row records `provider=offline` — so nothing is ever
misrepresented as a hosted model's output.

**Fully local (privacy mode):**

```bash
ollama serve && ollama pull qwen2.5:7b && ollama pull nomic-embed-text
```

```ini
AI_PROVIDER=ollama
AI_BASE_URL=http://127.0.0.1:11434
AI_MODEL=qwen2.5:7b
EMBEDDING_PROVIDER=ollama
EMBEDDING_MODEL=nomic-embed-text
```

The offline engine is a real deterministic implementation — hashed bag-of-ngrams
embeddings with sublinear TF and L2 normalisation, extractive sentence-scoring QA,
TF-IDF keyword/term extraction, heading-based planning. It is a documented fallback,
not a mock. Quality is naturally below a hosted LLM.

---

## Project structure

```
ai-workbench/
├── backend/
│   ├── app/
│   │   ├── api/            # routers, dependencies, error handlers
│   │   ├── core/           # config, logging, errors, security, retry, cache, dates
│   │   ├── domain/         # enums
│   │   ├── models/         # 22 SQLAlchemy models
│   │   ├── repositories/   # the only layer that issues SQL
│   │   ├── services/       # application layer (auth, documents, tasks, RAG, agent…)
│   │   ├── schemas/        # Pydantic request/response contracts
│   │   ├── ai/             # providers · prompts · parsers · rag · agent · tracing
│   │   ├── infrastructure/ # database, vector store, files
│   │   ├── worker/         # DB-backed job queue
│   │   ├── main.py         # app factory, middleware, lifespan
│   │   └── cli.py          # serve / worker / seed / check / stats / reindex
│   ├── alembic/            # migrations
│   ├── tests/              # unit · integration · api
│   └── requirements*.txt
├── frontend/               # React + TS + Vite + Tailwind
├── worker/                 # standalone worker launcher + Dockerfile
├── scripts/                # infra-start/stop/status · dev · fetch-runtime · smoke_test
├── samples/                # sample documents used by the tests and the smoke test
├── docs/                   # architecture · database · api · ai · rag · agent ·
│                           # deployment · testing · security · development
│   └── thesis/             # 10-chapter graduation thesis support pack (中文)
├── screenshots/
├── docker-compose.yml
├── start.bat / stop.bat / start.sh / stop.sh
├── .env.example
└── FINAL_CHECKLIST.md · PROJECT_DELIVERY.md
```

---

## Database design

22 tables, all with explicit primary keys, foreign keys, unique constraints, indexes
and timestamps:

```
users ─┬─ projects ─┬─ knowledge_bases ─┬─ documents ─┬─ document_chunks ─→ Qdrant point
       │            │                   │             └─ document_concepts ─┐
       │            │                   └─ tags (document_tags)            │
       │            ├─ tasks ─┬─ task_dependencies (self-referencing tree) │
       │            │         └─ task_documents  (knowledge ↔ task link)   │
       │            └─ notes                                            concepts
       ├─ prompts ── prompt_versions        (prompt versioning)
       ├─ ai_runs ─┬─ tool_calls            (agent tool calls)
       │           ├─ ai_run_sources        (citations / provenance)
       │           └─ agent_runs            (agent trajectory)
       ├─ jobs                              (background queue)
       └─ activity_events                   (dashboard feed)
```

The `task ↔ document` link and `ai_run_sources` are what make the closed loop
queryable: *which knowledge supports which task*, and *which documents an AI answer
actually used*.

ER diagram and a column-by-column reference: [`docs/database.md`](docs/database.md).

---

## RAG pipeline

```
question
  │
  ├─ 1. query rewrite            (LLM → 2-3 retrieval-friendly variants, cached)
  ├─ 2. dense retrieval          (Qdrant cosine, filtered by knowledge base)
  ├─ 3. sparse retrieval         (PostgreSQL candidate generation + in-process BM25-style scorer)
  ├─ 4. fusion                   (min-max normalised weighted sum: α·dense + (1-α)·sparse)
  ├─ 5. rerank                   (query-term coverage, bigram phrase hit, heading match, length prior)
  ├─ 6. context assembly         (numbered 【资料 n】 blocks inside a token budget)
  ├─ 7. generation               (prompt forbids answering from prior knowledge)
  ├─ 8. citation extraction      (map [n] markers back to chunk ids)
  └─ 9. trace                    (retrieval meta + sources persisted on the ai_run)
```

Chunking is heading-aware: Markdown headings split the document into sections first,
sentences are packed to the target size with overlap, and each chunk's embedded text
is prefixed with its heading breadcrumb (`JVM > 垃圾回收`), which measurably improves
retrieval and makes citations readable.

Details, formulas and tuning knobs: [`docs/rag.md`](docs/rag.md).

---

## Agent & tools

11 registered tools, each with a JSON parameter schema:

| Tool | Kind | Purpose |
|---|---|---|
| `knowledge_search` | read | hybrid search inside the knowledge base |
| `document_read` | read | read a document's text |
| `task_list` | read | existing tasks (avoid duplicates) |
| `project_list` | read | projects available for assignment |
| `calculator` | utility | AST-restricted arithmetic |
| `task_planner` | ai | generate a phased plan |
| `task_create` | write | create one task |
| `task_create_batch` | write | persist a whole plan as a task tree |
| `task_update` | write | change status / priority / estimates |
| `note_create` | write | persist a Markdown note |
| `knowledge_link` | write | link a document to a task |

Safety: one tool per step, max 8 steps by default, duplicate-call loop detection, a
cap on write operations per run, and no direct database access. Every step is
persisted to `tool_calls` and replayable from the AI Run page.

Adding a tool is three steps — see [`docs/agent.md`](docs/agent.md).

---

## API

49 paths / 65 operations, all under `/api/v1`, all returning a uniform envelope:

```jsonc
{ "success": true,  "data": { … } }
{ "success": false, "error": { "code": "not_found", "message": "…", "details": {…} } }
```

Interactive docs: <http://127.0.0.1:8000/docs> · ReDoc: `/redoc` · OpenAPI: `/openapi.json`

Highlights: `auth` · `users` · `projects` · `knowledge-bases` · `documents` (upload,
chunks, content, analyze, reprocess, embedding-status) · `search` · `chat/rag` ·
`planner` · `agents` (+ tool catalogue) · `prompts` (versioning) · `ai-runs` (+ trace,
metrics) · `tasks` (+ tree, dependencies, knowledge links) · `notes` · `graph` ·
`stats/dashboard` · `health` · `health/deps` · `metrics`.

Full contract with every field and enum: [`docs/api.md`](docs/api.md).

---

## Engineering notes

Real defects found and fixed during verification — each one is now covered by a test:

| # | Defect | Impact | Fix |
|---|---|---|---|
| 1 | Worker main loop returned without awaiting whenever every slot was busy | **Event loop starvation — the entire process froze, including the HTTP API** | always yield when nothing was claimed; regression test measures the maximum gap between yields |
| 2 | `extra={"message": …}` in a logging call | `KeyError: Attempt to overwrite 'message' in LogRecord` *inside the exception handler*, turning a clean 409 into a 500 | fixed at the call site **and** with a central guard that drops reserved keys |
| 3 | `updated_at` expired after flush and lazily reloaded | `MissingGreenlet` while serialising a response | `eager_defaults` — server defaults fetched via `RETURNING` |
| 4 | Generic repository helper selected `self.model` while given another table's conditions | child-entity queries returned the **wrong entity type** | explicit `list_of` / `get_one_of` / `count_of` helpers |
| 5 | After merging service modules, agent tools still imported the old module paths | write tools silently returned `ok: false` | fixed, plus an import-integrity test over every module |
| 6 | Failure recording opened a second session while the first held the row lock | **self-deadlock**: the pipeline hung forever | roll the doomed transaction back first, then record independently |
| 7 | `tokens.extend(run)` on a string | emitted single CJK characters, flooding the embedding with noise | `append` the run as one token |
| 8 | Duplicate index name (column `index=True` + explicit `Index`) | `create_all` failed with `DuplicateTableError` | removed the duplicate; test asserts no duplicate index names |
| 9 | AI-run payload sanitiser capped nesting at depth 6 | real task plans were replaced with `"<max-depth>"` | cap raised above any structure the product produces |
| 10 | Context discovery guessed which message held the material | the offline engine answered from the prompt's own instructions | explicit `<document>` / `<context>` tags in the templates |

Also worth noting:

- The dev machine has **no Docker** and an unreachable Docker Hub, so the verified
  runtime is native project-scoped processes. `docker-compose.yml` is provided but
  explicitly unverified — see the warning at the top of that file.
- `Path`/`PATH`/`path` all being present in the environment makes PowerShell 5.1's
  `Start-Process` fail with *"Item has already been added"*. `scripts/lib.ps1`
  collapses the duplicates; the issue is documented in `docs/deployment.md`.
- SQLAlchemy's async engine runs ORM work inside greenlets, so coverage must declare
  `concurrency = ["greenlet", "thread"]` or the reported percentage is wildly
  understated (68% → 78% once configured correctly).

---

## Roadmap

**Not implemented (stated honestly):**

- OCR for scanned PDFs — a PDF without an extractable text layer fails with a clear message
- A cross-encoder reranker — the shipped reranker is lexical by design (no large model download)
- Token-level streaming answers — responses arrive complete
- Multi-tenancy beyond per-user row ownership; no team/organisation model
- Rate limiting, refresh-token rotation, audit log, virus scanning on upload
- `docker-compose.yml` verified on a Docker-capable machine
- Playwright E2E wired into CI (the harness exists, CI does not)

**Planned next:** OCR fallback, cross-encoder rerank, SSE streaming, semantic caching
of answers, CI pipeline, and an export/import for a whole workspace.

---

## License

[MIT](LICENSE)
