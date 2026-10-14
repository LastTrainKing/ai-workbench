# AI Workbench

**A local-first personal AI agent for Windows.**

Say what you want done. It plans, uses tools on your own machine — files, code, git,
terminal, documents — verifies the result against real evidence, and fixes what failed.
Every action is permission-gated, auditable, and reversible.

## The difference

**Other assistants are strong at capability, and hand the same thing to everyone.**
This one is strong at *not starting from zero when it works for you* — because it carries
three things about you into every job:

```
① 记忆      what is true about you    backend/app/ai/memory/     → MEMORY.md
② 知识库    what you have             backend/app/ai/knowledge/  → RAG.md
③ Skill     how you work              backend/app/ai/skills/     → SKILL.md
        ↓ combined
   your work, done your way — faster, and not generic
```

It does not wait for you to teach it. **On the first day it goes out to public sources,
finds ways of working that fit you, verifies them, and asks which to install** — so a new
account has a library rather than an empty page.

And it does not start reasoning when it does not have to. A question your own memory can
answer is answered from memory in **1.6 ms** instead of going to the model — **15.5× faster
at the retrieval layer** than the general path. Measured, not asserted: see
[RETRIEVAL.md](RETRIEVAL.md).

---

It is not a chat wrapper, and it is not a dashboard. It closes one loop:

```
资料 → 知识 → 规划 → 产出 → 机器验证 → 用户验收 → 偏好沉淀 →（下一次产出）
```

Read it as a loop. The last arrow is the point: the product is not finished when it
produces a file, it is finished when you look at the file and say yes or no — and a
"no" with a reason becomes something it holds.

<p align="center">
  <img src="samples/demo-output/答辩PPT-第一版-11页.png" alt="A real deliverable: 11 slides, produced from three imported documents" width="72%">
</p>
<p align="center"><sub>Not a mockup: 70,538 B / 11 slides, produced by the agent from three imported documents and opened with <code>python-pptx</code> to count them.</sub></p>

## What makes it different

**The model decides what to do. The code decides whether it worked.**

Verification is a tool call whose exit code is read, not the model's opinion of its own
output. If `pytest` exits 1, the step failed, whatever the model says about it. Without
that rule, an agent framework is just a model complimenting itself.

**A retry is a retry, not a loop.**

Recovery used to ask the model for a root-cause analysis and then throw the answer away,
so every retry ran with an identical prompt and failed identically — one run burned 105
tool calls in two steps and never recovered. Now each attempt records what it tried, the
analysis is injected into the next attempt, two consecutive attempts with the same tools
*and* the same arguments are refused as an ineffective retry, and no single step can
spend more than a third of the run's budget. See [AGENT.md](AGENT.md).

**A preference is a constraint, not an instruction.**

```
任务目标（硬约束）  >  长期偏好（软约束）  >  默认行为
```

"页数更少" once produced a 2-slide deck for a 15-minute talk, because the preference was
executed as *the task* rather than as *a standard to apply while doing it*. The duration
is now read out of the goal in code and turned into a page range that no preference can
argue with; a preference that cannot be honoured is reported rather than silently
dropped. See [MEMORY.md](MEMORY.md).

**It remembers, and it asks first.**

The trigger is *delivery acceptance*, not conversation. A conversation turn is a guess
about what you prefer; rejecting a real artifact with a reason is a statement about it.
Facts worth keeping are separated from noise by a filter that rejects far more than it
keeps — nine evaluations wrote zero memories in one measured run. Anything uncertain
becomes a question, a contradiction becomes a question, and every memory shows the
delivery it came from with a link back to it.

**It shows you what it knows.**

Every fact the system holds about you is listed, editable, archivable and deletable.
Every proposed fact waits for your decision. The context preview shows the exact text
that would be sent to the model, block by block, with the token cost of each.

**It runs as a desktop application.**

A Windows window you launch from the Start menu, with no address bar and no console.
Closing it stops the backend, guaranteed by a Windows job object, so a crash or a
force-kill cannot leave PostgreSQL, Redis and Qdrant running behind a closed window.

> **Status of the UI.** This build renders its interface with **WebView2** — the window is
> a native window, but its contents are a web page, not native controls. Moving to a
> native Qt 6 / QML interface is an open release-blocking work item, tracked as RF-01 in
> `RELEASE-FREEZE-ISSUES.md`. The earlier wording here ("no browser") overstated it.

**Its workspace is inside the project, not on your desktop.**

The agent's working directory used to default to `%USERPROFILE%\Desktop\AI Workbench
Workspace` — the product emptying its pockets onto the one screen you look at all day.
It now lives at `<project>/.runtime/workspace` with a fixed layout
(`deliverables/ notes/ sources/ .tmp/`). See [WORKSPACE.md](WORKSPACE.md).

<p align="center">
  <img src="screenshots/native/dashboard.png" alt="Home: say what you want done, and what came out of the last five runs" width="49%">
  <img src="screenshots/native/employees.png" alt="My employees: six role templates, and the roster with its contracts" width="49%">
</p>
<p align="center">
  <img src="screenshots/native/deliverables.png" alt="Deliverables: every file with its verification evidence and the operations it actually supports" width="49%">
  <img src="screenshots/native/memory.png" alt="Memory: what is waiting for approval, what is in force, and exactly what the model will be told" width="49%">
</p>
<p align="center"><sub>The native Windows client — the thing the installer ships. Captured on a real
display at 1280×860; the rest of the pages are in <a href="screenshots/native/"><code>screenshots/native/</code></a>.
The older web client's screenshots are still in <a href="screenshots/ui-verification/"><code>screenshots/ui-verification/</code></a>,
and they show the <em>other</em> client, not this one.</sub></p>

**Why the screenshots are regenerable, not just committed.** They come from
`tools/shot_real_pages.py`, which drives the real window on a real display — the headless
harness reports *zero font families* on this machine, so it cannot show a glyph, and a
missing font fallback is exactly the defect it would therefore miss. This project has
already shipped one: an avatar initial was changed to an emoji, the offscreen harness
rendered it clean, and the real window drew a tofu block.

---

## Table of contents

Design, in ten minutes:

- [ARCHITECTURE.md](ARCHITECTURE.md) — three layers (cognition / action / delivery), and why the boundaries are where they are
- [AGENT.md](AGENT.md) — the loop, recovery, and how a retry actually changes strategy
- [DELIVERABLE.md](DELIVERABLE.md) — the delivery model, verification vs acceptance, what the machine cannot check
- [MEMORY.md](MEMORY.md) — what is remembered, when, and why so little of it
- [WORKSPACE.md](WORKSPACE.md) — where the agent works, and the layout that area has

Deeper engineering detail:

- [Autonomous agent](docs/autonomous-agent.md) — the loop, the states, the limits
- [Permissions](docs/permissions.md) — what the agent may do, and why the defaults are what they are
- [Architecture](docs/architecture.md) — the long version, including the bugs that produced each decision
- [Interview questions](docs/interview.md) — 20 questions with the real implementation behind each
- [Resume description](docs/resume.md) — what was built, and what was not

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
| "I have the file but cannot find the answer" | Hybrid retrieval (vector + keyword) with a reranker, and answers that are labelled as verified **only when every claim-bearing sentence cites a retrieved source** — the citation check is structural, not fact-level entailment (see `docs/rag.md`) |
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

**Autonomous agent** — PLAN → EXECUTE → OBSERVE → VERIFY → FIX → RETRY → DONE. It
commits to a plan whose every step declares how it will be verified, snapshots the
workspace before touching anything, and checks each step against a real tool result
rather than its own prose. Pause, resume and cancel are live controls; cancel reaches
into the running process tree. Bounded on every axis — steps, tool calls, retries,
wall clock, files modified. See [docs/autonomous-agent.md](docs/autonomous-agent.md).

**Computer tools** — 44 tools across filesystem, code, git, terminal, build-and-test,
process, network and observation. Each carries a permission level, a risk level, a
timeout and an audit row. A missing library is reported as a failure, never silently
downgraded.

**Permission system** — four levels (observe / workspace / system / full control) and
three modes (assist / supervised / autonomous). Deny beats allow, always. Protected
locations — SSH keys, browser credential stores, the project's own `.env` — are merged
in from the built-in policy on every load, so a settings file can add protections but
never remove them. High-risk commands prompt, and the prompt explains the reason and the
impact. See [docs/permissions.md](docs/permissions.md).

**Snapshots, diff and rollback** — the workspace is captured before a run and the state
it left behind is recorded afterwards. Rollback compares against the second manifest, so
a file the *user* edited after the run is reported as a conflict instead of being
silently overwritten.

**Personal context** — profile, memory, preferences and knowledge kept as four separate
things, because merging them into one vector store is how "an AI that knows me" becomes
"an AI that recalls irrelevant fragments". A memory is stored only when the evaluator is
confident or the user confirms; contradictions become questions; sensitive content is
never stored at any confidence. Every injection is recorded so "what did it know when it
said that" is answerable afterwards.

**Document generation** — `document_write` produces docx, xlsx, pptx, pdf, md and csv
from structured input; `document_read` reads them back. Style comes from user
preferences, so the same request produces a different document for a different person.
**Generation is not completion**: the tool reports `verified: false` and the agent must
read the file back and check the page count, the row count, the size.

**Windows desktop shell** — Tauri + WebView2. No browser, no address bar, no console
window. A splash reports per-service readiness from real TCP and HTTP probes. The window
blocks the browser tells (context menu, F5, Ctrl+P, F12, Ctrl+wheel zoom) while leaving
text selection and right-click working inside form fields. Closing the window stops the
backend, guaranteed by a job object with `KILL_ON_JOB_CLOSE` — a crash or a force-kill
cannot leave services running.

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

Four layers, and the boundaries are enforced rather than documented:

```
Windows desktop shell (Tauri + WebView2)
    └── starts the backend, owns the window, guarantees shutdown
            │
        FastAPI  ─────────────────────────────────────────────
            api/         HTTP surface, request validation
            services/    use cases, transactions
            repositories/ data access
            models/      SQLAlchemy, 29 tables
            │
        Agent → Tool → Permission Manager → Tool Executor
            │              │
            │         Service → Repository → Database
            │
        Computer tools (44) — filesystem, code, git, terminal,
            build/test, process, network, observation
```

**The agent never touches the database.** It calls tools; tools call services; services
call repositories. That is what makes every action nameable, classifiable and
interruptible — and what makes the audit trail meaningful, because a row says "called
`file_write` on `x.py`", not "executed an UPDATE".

**The desktop shell holds no business logic.** It starts processes, shows a window and
stops processes. Everything else is FastAPI.


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

There are two ways to run this, and they are not the same product.

### A. The native Windows desktop app (this is the product)

`desktop-native/` is a Qt 6 / QML application. It is the shipping client: **no browser,
no local HTTP server** — it calls the service layer in-process. Everything described on
this page (employees, deliverables, verification, memory, permissions) is reachable
from it.

**Install** — run `AI-Workbench-Native-Setup.exe` from the GitHub Release assets. It
bundles the PostgreSQL / Redis / Qdrant binaries and starts them as project-scoped
processes; nothing is installed as a Windows service and no system setting is changed.
User data goes to `%LOCALAPPDATA%\AI Workbench Data`, **not** into the program
directory, so uninstalling the app does not delete your work.

**Run from source** (needs the infrastructure running — see B below):

```bat
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r backend\requirements-dev.txt
.venv\Scripts\python.exe desktop-native\main.py
```

`backend\requirements-dev.txt` pulls in `desktop-native\requirements.txt` (PySide6), so
one command is enough. **The test suite needs it too** — two test modules import the
bridge, which imports PySide6. That was not true until it was found in a clean clone:
the README used to send a stranger to `pytest` without it, and the run died on
collection with `ModuleNotFoundError: No module named 'PySide6'`.

**Build the installer** (PyInstaller + NSIS, ~20 minutes):

```bat
.venv\Scripts\python.exe desktop-native\build_native.py
.venv\Scripts\python.exe scripts\build_native_installer.py --build
```

`build_native.py` runs `verify_native_package.py` afterwards and **fails if the built
application does not launch** — a package that starts on the build machine and nowhere
else is the failure this step exists to catch.

**Platform support: Windows only, and that is verified.** The Qt/QML client is
platform-neutral in principle, but the installer, the bundled runtime and every
acceptance run so far are Windows. There is no macOS or Linux claim, and no CI badge
claiming one.

**Verified UI acceptance** — these run headless and are what "the interface works" is
based on:

```bat
.venv\Scripts\python.exe desktop-native\verify_native_shell.py     :: 31 checks
.venv\Scripts\python.exe desktop-native\verify_projection.py       :: 108 assertions
.venv\Scripts\python.exe tools\check_layout.py                     :: 19 pages x 3 sizes
.venv\Scripts\python.exe tools\check_qml_pages.py                  :: 20 pages load
```

**Screenshots.** `screenshots/` and `desktop-native/docs/` hold real captures. They were
taken on a machine with the product's fonts installed; the headless harness in this
repository cannot reproduce glyph rendering (it reports zero font families), so
screenshots are supplementary evidence, not the primary one — the primary one is the
projection assertions above.

### B. The web stack (older path, still supported)

The FastAPI backend plus the `frontend/` web client. Useful for development and for the
API, but it is **not** what the desktop installer ships.

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
no system changes), then starts the API and the UI.

**Desktop shortcut.** The first run also creates `AI Workbench.lnk` on your Desktop
(with a proper icon). Double-clicking it starts the whole stack **with no console
window** and opens your browser on <http://127.0.0.1:5173> as soon as the frontend
really serves a page:

```
AI Workbench.lnk
  -> wscript.exe  scripts\start-hidden.vbs
       -> scripts\start.bat --hidden
            -> PostgreSQL / Redis / Qdrant / API / frontend
                 -> browser opens
```

If it is missing, create it with:

```bat
powershell -ExecutionPolicy Bypass -File scripts\create-shortcut.ps1
```

A failure is never swallowed: nothing is opened in the browser, the reason is written
to `.runtime\logs\startup-error.log`, and the launcher shows a message box pointing
at that file.

**Other commands:**

```bat
scripts\stop.bat              :: stop everything (all data preserved)
scripts\stop.bat --app-only   :: stop just the API + UI, keep the databases running
scripts\restart.bat           :: restart the application only
scripts\restart.bat --full    :: restart infrastructure too
scripts\infra-status.ps1      :: readiness report for every dependency
```

A shortcut is a shell object, so `create-shortcut.ps1` asks the shell to build it
(`WScript.Shell`) rather than hand-writing the binary format — that is what Explorer
itself does. If the Desktop is redirected, pass `-Destination`.

### Startup is gated on real readiness

Every service is proven usable before the next step runs — a listening port is never
treated as ready:

| Service | Probe | Why not just the port |
|---|---|---|
| PostgreSQL | `pg_isready`, then `psql -c "SELECT 1"` | the server binds the port before crash recovery finishes and answers `FATAL: the database system is starting up` until it is done |
| Redis | `redis-cli ping` → `PONG` | an open socket does not mean Redis is serving |
| Qdrant | `HTTP GET /healthz` → 200 | the port opens before the HTTP API is up |
| API | `HTTP GET /api/v1/health` → 200 | the socket binds before the worker has started |
| Frontend | `HTTP GET /` → 200 | Vite binds the port before it can serve a page |

Ordering is strictly serial (`start → ready → provision → verify`), a service that
misses its budget is reported `FAILED` and the run exits non-zero, and the browser is
only opened after the frontend answers. `scripts/startup-lock.ps1` serialises
concurrent launches, so an impatient double-click waits for the run already in
progress instead of starting a second one.

Every transition is appended to `.runtime\logs\startup.log`:

```
2026-10-03 11:00:15 Infrastructure STARTING
2026-10-03 11:00:16 PostgreSQL STARTING - port 5432
2026-10-03 11:00:16 PostgreSQL READY - 0s via pg_isready
2026-10-03 11:00:28 Redis READY - 1s via redis-cli ping
2026-10-03 11:00:36 Qdrant READY - 1s via HTTP /healthz
2026-10-03 11:00:41 api READY - 3s via HTTP GET http://127.0.0.1:8000/api/v1/health
2026-10-03 11:00:44 web READY - 1s via HTTP GET http://127.0.0.1:5173/
2026-10-03 11:00:45 opening the browser
```

`stop` is scope-safe: PostgreSQL is stopped through `pg_ctl -D <our data dir>`, and
Redis / Qdrant / API / frontend are stopped by pid file **verified to belong to this
project** (image under `vendor\runtime`, or a matching command line), plus the port
owner. There is deliberately no `taskkill /IM python.exe` or `/IM node.exe` — that
would kill your other projects.

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
# one-time: everything the tests and the desktop client need
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r backend\requirements-dev.txt

# backend — unit + integration + API (needs the infrastructure running)
cd backend
..\.venv\Scripts\python.exe -m pytest                      # everything
..\.venv\Scripts\python.exe -m pytest tests/unit           # fast, no services needed
..\.venv\Scripts\python.exe -m pytest --cov=app            # with coverage

# frontend
cd frontend
npm run test          # Vitest — 114 assertions
npm run typecheck     # tsc --noEmit
npm run lint          # ESLint
npm run build         # production build
```

**A full `pytest` run calls the model six times.** The six live tests in
`tests/integration/test_deepseek_live.py` read the key through the application's own
config resolver, so on a machine with a provider configured they run rather than skip.
To avoid the spend, deselect them — but do not report their skip as a pass:

```bash
..\.venv\Scripts\python.exe -m pytest --deselect tests/integration/test_deepseek_live.py
```

### What the suite needs beyond the Python packages

Three things are machine-local, and **all three were discovered by running the suite
from a fresh clone** — on a development machine they are simply already there:

| | Why | Without it |
|---|---|---|
| **PostgreSQL, Redis, Qdrant running** | these are integration tests, not mocks | most tests fail; a few skip with a reason |
| **`pg_dump` on `PATH`** (or `vendor/runtime/pgsql/`) | the schema guard takes a backup before a forward migration, and refuses to migrate without one | `SchemaOutOfDateError`: "找不到 pg_dump" |
| **Qdrant's API key at `.runtime/qdrant-api-key.txt`**, if your instance requires one | the tests move the runtime directory, so the key moves with it | `401 Unauthorized` from Qdrant → embedding tests fail |

Start the three engines and put Qdrant's key where the tests look for it:

```bat
scripts\start.bat --infra-only
:: if your Qdrant was started with an API key, put it here:
::   .runtime\qdrant-api-key.txt
```

A clean clone with none of this installed still builds and runs the *application*; it is
the integration suite that needs the infrastructure. That distinction is real and was
measured — see `docs/release/BUILD_AND_INSTALL.md` §5.

### Verification scripts

These are the tests that catch what unit tests cannot: whether the wiring is real, and
whether the packaged application behaves like an application. All of them run against a
live server and assert on observed behaviour rather than on code structure.

| Script | Assertions | What it proves |
|---|---|---|
| `scripts/verify-desktop-entry.ps1` | 12 | The shortcut opens a native window; no browser, no console, no port 5173; three exit paths leave zero processes behind |
| `scripts/verify_shortcut.py` | 7 | The `.lnk` bytes point at the executable, read without the Shell API |
| `scripts/verify-agent-ui.mjs` | 26 | Clicking 暂停 issues `POST /pause`; polling stops on a terminal state and on unmount |
| `scripts/verify-context-ui.mjs` | 22 | 添加 issues `POST /memories`; the wizard writes nothing until the confirm step |
| `scripts/verify-shell-behaviour.mjs` | 21 | F5 does not reload, right-click still works in inputs, the offline banner appears and clears itself |
| `scripts/verify-focus-grid.mjs` | 17 | Arrow keys move focus, Enter opens, Tab is not swallowed |
| `scripts/verify-splash.mjs` | 10 | Per-service rows render from a real status payload |
| `scripts/verify-sidebar.mjs` | 10 | All 16 old routes still resolve after the sidebar was restructured |
| `scripts/test_context_api.py` | 31 | Every endpoint: success, 404 and validation failure |
| `scripts/test_memory_loop.py` | 18 | Noodles are not remembered, passwords never land, a contradiction becomes a question |
| `scripts/test_personalisation.py` | 8 | The same goal produces two different plans for two different profiles (similarity 0.298) |

The backend suite runs against a dedicated `aiworkbench_test` database and Redis
database index 1, so it never touches development data.

See [`docs/testing.md`](docs/testing.md) for the full strategy.

---

## Known limits

Stated plainly, because a project that hides its edges is a project you cannot trust:

- **No browser control.** `screenshot` and `screen_read` observe the screen; there is no
  click, no typing, no page automation.
- **No mouse or keyboard control.**
- **No OCR.** A scanned PDF with no text layer is reported as such rather than returned
  as an empty document.
- **Embeddings are offline hashes**, not a semantic model — DeepSeek does not expose an
  embedding endpoint. Retrieval quality is correspondingly limited.
- **Ollama and OpenAI-compatible endpoints are not tested against a live server.** The
  code paths exist and degrade correctly when unreachable, but no real endpoint was used.
- **The Docker path is unverified.** It is written and documented; it has not been run.
- **The installer still expects the project directory to exist.** It is not a
  self-contained install.

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
└── DELIVERY.md
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
