# Deployment and operations

AI Workbench runs as **native, project-scoped processes**. This document covers
prerequisites, the runtime download, starting the infrastructure and the
application, the environment-variable reference, troubleshooting, and production
hardening notes.

## 1. What is and is not provided

Be aware of the following before you start:

* The supported and verified way to run the stack is the PowerShell scripts under
  `scripts/`. They download native Windows binaries and start them as
  project-scoped processes.
* **Docker was never run.** The development machine has no Docker installation and
  its Docker Hub registry endpoint is unreachable, so no container was built or
  started. A `docker-compose.yml` **is** included at the repository root, and it
  says so itself in its header: *provided and reviewed, not executed*. Treat
  containerised deployment as new work and verify it yourself.
* **One-command entry points do exist**: `start.bat` / `stop.bat` at the repository
  root, with the implementation under `scripts/` (the root copies are thin wrappers
  so there is exactly one copy of the logic). They start the whole native stack and
  prove readiness rather than assuming it. `scripts/dev.ps1 start` remains the
  developer entry point. `start.sh` / `stop.sh` are the POSIX counterparts; they are
  **not verified** — see "Windows-only verification" in [testing.md](testing.md).
* **The frontend is a separate workstream.** A React 18 + Vite 5 + Tailwind
  project lives under `frontend/` and is developed in parallel with the backend;
  this document set focuses on the backend API and AI subsystem, which is complete
  and documented in [api.md](api.md). `scripts/dev.ps1` starts a frontend only if
  `frontend/package.json` exists.

## 2. Prerequisites

| Requirement | Notes |
|---|---|
| Windows 10/11 | The scripts are PowerShell and target Windows binaries |
| PowerShell 5.1+ | `-ExecutionPolicy Bypass` is used in the examples |
| Python 3.11+ | 3.13.14 was used on the development machine; the project declares `>=3.11` |
| `curl.exe` | Used by `fetch-runtime.ps1` (ships with Windows 10+) |
| A virtual environment at `.venv` | Created by you; `scripts/dev.ps1` expects `.venv/Scripts/python.exe` |

Ports used (all bound to `127.0.0.1`): PostgreSQL 5432, Redis 6379, Qdrant 6333
(and gRPC 6334), API 8000, frontend dev server 5173.

## 3. Native runtime setup

Runtimes are downloaded as native binaries into `vendor/runtime/`. They are not
committed to git.

```powershell
powershell -ExecutionPolicy Bypass -File scripts\fetch-runtime.ps1
```

`fetch-runtime.ps1` downloads and extracts (skipping anything already present):

| Component | Source | Extracted to | Executable |
|---|---|---|---|
| Qdrant | `github.com/qdrant/qdrant` release **v1.12.4** (`qdrant-x86_64-pc-windows-msvc.zip`) | `vendor/runtime/qdrant` | `qdrant.exe` |
| Redis | `github.com/tporadowski/redis` release **5.0.14.1** | `vendor/runtime/redis` | `redis-server.exe` |
| PostgreSQL | EnterpriseDB **16.4-1** Windows x64 binaries | `vendor/runtime/pgsql` | `pgsql\bin\postgres.exe` |

Paths and ports are defined once in `scripts/lib.ps1` and reused by every script.

## 4. Starting the infrastructure

```powershell
powershell -ExecutionPolicy Bypass -File scripts\infra-start.ps1
```

This is idempotent and always safe to re-run. It:

1. Verifies the runtimes exist (otherwise it tells you to run `fetch-runtime.ps1`).
2. **PostgreSQL** — if port 5432 is closed and no cluster exists, runs `initdb`
   with `scram-sha-256` auth into `.runtime/pgdata`; then starts `postgres.exe`
   detached and polls the port. It provisions the `aiworkbench` role and
   `aiworkbench` database if missing.
3. **Redis** — writes `.runtime/redis.conf` (bound to 127.0.0.1, persistence
   disabled, `maxmemory 256mb`, `allkeys-lru`) and starts `redis-server.exe`.
4. **Qdrant** — writes `.runtime/qdrant/config/config.yaml` (storage under
   `.runtime/qdrant`, telemetry disabled, HTTP 6333, gRPC 6334) and starts
   `qdrant.exe`.

Logs go to `.runtime/logs/*.out.log` and `*.err.log`.

Stop / inspect:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\infra-stop.ps1
powershell -ExecutionPolicy Bypass -File scripts\infra-status.ps1
```

### A note on `pg_ctl`

`infra-start.ps1` launches `postgres.exe` directly instead of `pg_ctl start`.
On Windows, `pg_ctl` keeps a handle on the inherited stdout pipe, which makes
non-interactive shells block until the server exits. Starting the server detached
and polling the port is deterministic.

## 5. Creating the Python environment

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r backend\requirements.txt
Copy-Item .env.example .env     # then edit .env
```

`backend/requirements.txt` pins compatible-release ranges for FastAPI, Uvicorn,
Pydantic, SQLAlchemy, asyncpg, Alembic, redis, qdrant-client, httpx, pypdf,
markdown-it-py, PyJWT, bcrypt, numpy and psutil. `backend/requirements-dev.txt`
adds the test/lint tooling.

Verify every dependency and print the provider in use:

```powershell
cd backend
..\.venv\Scripts\python.exe -m app.cli check
```

This prints an `OK`/`FAIL`/`WARN` line for PostgreSQL, Redis, Qdrant, the LLM
provider and the embedding provider, and exits non-zero if a hard dependency is
down.

## 6. Starting the application

```powershell
powershell -ExecutionPolicy Bypass -File scripts\dev.ps1 start
```

`dev.ps1` starts the API (which runs the in-process worker when
`WORKER_ENABLED=true`), and starts the frontend **only if** `frontend/package.json`
exists. PID files are written to `.runtime/pids` and logs to `.runtime/logs`.

Other actions:

```powershell
scripts\dev.ps1 status          # table of service / port / listening / pid
scripts\dev.ps1 logs api        # tail the API logs
scripts\dev.ps1 stop            # stop the application (infrastructure stays up)
scripts\dev.ps1 restart
```

Manual equivalents (useful for `--reload`):

```powershell
cd backend
..\.venv\Scripts\python.exe -m app.cli serve --host 127.0.0.1 --port 8000 --reload
..\.venv\Scripts\python.exe -m app.cli worker --concurrency 2   # separate worker
```

On first boot the API creates any missing tables
(`Base.metadata.create_all`), seeds the ten prompt templates and creates the
demo account **demo / demo1234** if no user exists. All of this is idempotent.

The ten templates are `rag_answer`, `document_summary`, `knowledge_extraction`,
`task_planner`, `study_plan`, `interview_generator`, `note_generation`,
`query_rewrite`, `agent_system` and `agent_planner` (`app/ai/prompts/builtin.py`).

Once running:

* API docs: `http://127.0.0.1:8000/docs`
* OpenAPI JSON: `http://127.0.0.1:8000/openapi.json`
* Health: `http://127.0.0.1:8000/api/v1/health`
* Dependency readiness: `http://127.0.0.1:8000/api/v1/health/deps`
* Qdrant dashboard: `http://127.0.0.1:6333/dashboard`

## 7. CLI reference

`python -m app.cli <command>`:

| Command | Purpose |
|---|---|
| `serve [--host] [--port] [--reload]` | Run the API (with the in-process worker) |
| `worker [--concurrency]` | Run the background worker only |
| `seed` | Seed prompt templates and the demo account |
| `check` | Verify every external dependency |
| `stats` | Print row counts per table |
| `reindex --knowledge-base <uuid>` | Queue a full re-index of a knowledge base |
| `create-user --username --email --password [--display-name]` | Create an account |

## 8. Environment variables

Full list with defaults. All are read from `.env` (and `backend/.env`) by
`app/core/config.py`.

### Application

| Variable | Default | Notes |
|---|---|---|
| `APP_NAME` | AI Workbench | |
| `APP_ENV` | dev | `dev` \| `test` \| `prod` |
| `DEBUG` | true | When false, unexpected errors are not leaked to clients |
| `API_V1_PREFIX` | /api/v1 | |
| `SECRET_KEY` | (placeholder) | **Must** be set; JWT signing key (HS256) |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | 1440 | 24 h |
| `CORS_ORIGINS` | http://localhost:5173,http://127.0.0.1:5173 | Comma-separated allow-list |

### Database / cache / vector store

| Variable | Default |
|---|---|
| `DATABASE_URL` | `postgresql+asyncpg://aiworkbench:aiworkbench@127.0.0.1:5432/aiworkbench` |
| `DB_ECHO` | false |
| `DB_POOL_SIZE` | 10 |
| `DB_MAX_OVERFLOW` | 20 |
| `REDIS_URL` | `redis://127.0.0.1:6379/0` |
| `CACHE_ENABLED` | true |
| `CACHE_TTL_SEARCH_SECONDS` | 180 |
| `CACHE_TTL_PROMPT_SECONDS` | 600 |
| `CACHE_TTL_STATS_SECONDS` | 30 |
| `QDRANT_URL` | `http://127.0.0.1:6333` |
| `QDRANT_API_KEY` | (empty) |
| `QDRANT_COLLECTION` | ai_workbench_chunks |
| `QDRANT_VECTOR_SIZE` | 0 (detect on demand) |
| `QDRANT_DISTANCE` | Cosine |

### AI

| Variable | Default |
|---|---|
| `AI_PROVIDER` | deepseek (`deepseek` \| `openai_compatible` \| `ollama` \| `offline`) |
| `AI_BASE_URL` | `https://api.deepseek.com` |
| `AI_API_KEY` | (empty → offline engine) |
| `AI_MODEL` | deepseek-flash |
| `AI_TEMPERATURE` | 0.2 |
| `AI_MAX_TOKENS` | 8192 |
| `AI_TIMEOUT_SECONDS` | 90 |
| `AI_MAX_RETRIES` | 3 |
| `AI_BACKOFF_BASE_SECONDS` | 1.0 |
| `EMBEDDING_PROVIDER` | openai_compatible |
| `EMBEDDING_BASE_URL` | (empty → falls back to `AI_BASE_URL`) |
| `EMBEDDING_API_KEY` | (empty → falls back to `AI_API_KEY`) |
| `EMBEDDING_MODEL` | text-embedding-3-small |
| `EMBEDDING_BATCH_SIZE` | 16 |
| `EMBEDDING_TIMEOUT_SECONDS` | 60 |

A DeepSeek key can also be supplied through its own namespace —
`DEEPSEEK_API_KEY`, `DEEPSEEK_BASE_URL`, `DEEPSEEK_MODEL` — so a DeepSeek
credential does not have to go into the generic OpenAI-compatible variables. All
three default to the same values as the generic pair above.

### RAG / storage / worker / observability

| Variable | Default |
|---|---|
| `CHUNK_SIZE` | 900 |
| `CHUNK_OVERLAP` | 150 |
| `RETRIEVAL_TOP_K` | 8 |
| `RETRIEVAL_CANDIDATE_K` | 40 |
| `HYBRID_ALPHA` | 0.6 |
| `RERANK_ENABLED` | true |
| `STORAGE_DIR` | `<root>/.runtime/storage` |
| `MAX_UPLOAD_MB` | 25 |
| `ALLOWED_EXTENSIONS` | .md,.markdown,.txt,.pdf |
| `WORKER_ENABLED` | true |
| `WORKER_POLL_INTERVAL` | 1.0 |
| `WORKER_CONCURRENCY` | 2 |
| `WORKER_MAX_ATTEMPTS` | 3 |
| `WORKER_STALE_JOB_SECONDS` | 900 |
| `WORKER_JOB_TIMEOUT_SECONDS` | 900 |
| `LOG_LEVEL` | INFO |
| `LOG_JSON` | false |
| `METRICS_ENABLED` | true |

### Secrets

Real secrets belong in `.env`, which must not be committed. `.env.example` is the
committed template and contains no real values. The startup validator rejects an
empty `SECRET_KEY`.

## 9. Troubleshooting

### Port conflicts

The scripts distinguish "this port is held by our own service" from "this port is
held by something else", and they say which:

* **Held by our service and answering its probe** — treated as success and skipped.
  `infra-start.ps1` logs `SKIPPED` with the evidence (`HTTP /healthz 200`, `PONG`,
  `already serving on <port>`); `dev.ps1` does the same for the API. This is what
  makes re-running the scripts safe.
* **Held by something else** — `infra-start.ps1` refuses rather than starting a
  second copy, and names the port:
  `Port 5432 is already in use, but it is not this project's PostgreSQL (no live
  pid in ...\postmaster.pid). Stop it or change PGPORT.`,
  `Port 6379 is in use but does not answer PING - it is not this project's Redis.`,
  `Port 6333 is in use but does not answer HTTP /healthz - it is not this
  project's Qdrant.`
* **The API side has no ownership probe.** A foreign process on 8000 does not get
  a specific message; `dev.ps1` tries to start anyway, the child fails to bind, and
  after the readiness timeout you get
  `<name> failed to become ready within <N> seconds` followed by the two log paths.
  If you see that, check the port before reading the log.

Find the owner with `netstat -ano | findstr :8000` and `Get-Process -Id <pid>`, then
stop it or change the port in `.env`.

### Missing runtimes

`infra-start.ps1` and `dev.ps1 start` both check for the three executables and
exit with `Missing runtime(s): ...` if any are absent. Run
`fetch-runtime.ps1` and retry. If a download fails, verify network access to
GitHub releases and `get.enterprisedb.com`.

### The PowerShell `Path` / `PATH` duplication issue

This is the single most common startup failure on a Windows machine whose shell
is Git Bash / MSYS. Those shells export several casing variants of `PATH`
(`Path`, `PATH`, `path`) in the same environment block. Windows PowerShell 5.1
builds a case-sensitive dictionary when starting a child process, so
`Start-Process` fails with:

```
Item has already been added. Key in dictionary: 'Path'
```

`scripts/lib.ps1` fixes this centrally in `Repair-ProcessEnvironment`, which is
executed when the library is dot-sourced. It collapses every casing variant down
to a single canonical `Path` entry. Because every operational script dot-sources
`lib.ps1`, the fix applies everywhere. If you write a new script, dot-source
`lib.ps1` for this reason.

### Qdrant dimension mismatch

If you change `EMBEDDING_MODEL` after a collection already exists, the vector
store raises a `VectorStoreError` explaining that the dimension changed. Rebuild:

```powershell
.venv\Scripts\python.exe -m app.cli reindex --knowledge-base <uuid>
```

or delete the collection (default name `ai_workbench_chunks`) and re-upload.

### Redis is down

The application keeps working: cache reads and writes degrade to no-cache with a
single `redis_unavailable_degrading_to_no_cache` warning. `GET /health/deps`
reports `redis.ok: false` and the overall status `degraded`.

### Qdrant reports "Collection data already exists"

Symptom: ingestion fails and the job error contains

```
Wrong input: Can't create collection with name ai_workbench_chunks.
Collection data already exists at ./storage\collections\ai_workbench_chunks
```

This happens when Qdrant's on-disk state and its in-memory catalogue disagree —
for example after a hard kill (`taskkill /F`) or a delete that returned HTTP 500.
`GET /collections` may even list nothing while the directory is still on disk, so
the client believes the collection is missing and tries to create it.

Recovery (this is the reliable fix — deleting through the API can leave the same
stale directory behind):

```powershell
# 1. stop the API and Qdrant
powershell -ExecutionPolicy Bypass -File scripts\dev.ps1 stop
Get-Process qdrant -ErrorAction SilentlyContinue | Stop-Process -Force

# 2. remove the stale storage
Remove-Item -Recurse -Force .runtime\qdrant\storage\collections

# 3. restart
powershell -ExecutionPolicy Bypass -File scripts\infra-start.ps1
powershell -ExecutionPolicy Bypass -File scripts\dev.ps1 start

# 4. re-ingest: either re-upload, or queue a re-index
.venv\Scripts\python.exe -m app.cli reindex --knowledge-base <uuid>
```

`VectorStore.ensure_collection()` also detects this race and re-checks the
collection before failing, so a transient disagreement no longer aborts
ingestion — but a genuinely stale directory still needs step 2.

### AI answers look extractive / repetitive

Check `GET /api/v1/health/deps` → `ai.llm.degraded`. If it is `true`, no cloud key
is configured and the deterministic offline engine is answering
(`model = heuristic-extractive-v1`). Set `AI_API_KEY` in `.env` (and
`EMBEDDING_API_KEY` if you use a cloud embedding model) and restart to use a real
model.

### A document is stuck in `PROCESSING`

Check `GET /api/v1/documents/{id}` for `job_status`, `job_stage` and `job_error`,
and the worker logs. A job that exceeds `WORKER_JOB_TIMEOUT_SECONDS` is failed; a
job whose worker crashed is requeued after `WORKER_STALE_JOB_SECONDS` on the next
worker start. `POST /api/v1/documents/{id}/reprocess` re-queues ingestion.

## 10. Production hardening notes

This is a local-first developer/graduate-project deployment, not a hardened
production SaaS. Before exposing it to untrusted networks:

* Put a TLS-terminating reverse proxy in front of the API (the dev setup is
  plain HTTP on 127.0.0.1).
* Set `DEBUG=false`, a strong `SECRET_KEY`, and a real `DATABASE_URL` with a
  non-default password.
* Restrict `CORS_ORIGINS` to your real origins.
* Add rate limiting (none is implemented) and refresh-token rotation (only
  long-lived access tokens exist).
* Enable Redis persistence or accept that the cache is disposable.
* Run the worker as a managed process and scale `WORKER_CONCURRENCY` with care.
* Review [security.md](security.md) for the full list of gaps that remain.

## 11. Related documents

* [architecture.md](architecture.md) — topology and the worker
* [security.md](security.md) — threat model
* [testing.md](testing.md) — how to verify the deployment

---

## Update — desktop application delivery

### The shortcut points at the executable

```
AI Workbench.lnk
  → desktop\src-tauri\target\release\ai-workbench-desktop.exe
       → starts PostgreSQL / Redis / Qdrant / API itself
            → opens its own window on http://127.0.0.1:8000/
```

No browser. No port 5173. No console window.

`scripts/create-shortcut.ps1` **fails loudly** if the executable has not been built,
printing the build command, and explicitly refuses to fall back to a browser launcher.
A shortcut that quietly opens Edge instead of the application is worse than no shortcut,
because the user only finds out after double-clicking it. A `-Browser` switch keeps the
old behaviour for development.

`scripts/start.bat` and `scripts/restart.bat` no longer open a browser by default;
`--browser` is the opt-in.

### Shutdown

Closing the window stops the backend. This is guaranteed at the operating-system level by
a job object, not by the application asking nicely — see
[`security.md`](security.md#process-lifecycle).

### Verifying a build

```powershell
powershell -ExecutionPolicy Bypass -File scripts\verify-desktop-entry.ps1
```

Twelve assertions across three exit scenarios, printing PASS or FAIL with the evidence
that decided it. The script also prints the lifecycle log, so the close path can be
checked against the log rather than against the script's own conclusion.

### Building the deliverables

Two artefacts ship, and both are built from the working tree — so both are only as
current as the last build:

```powershell
# 1. the frontend bundle the API serves
cd frontend; npm run build

# 2. the native shell (Rust release build, ~2 min incremental)
cd ..\desktop; npm run build

# 3. the installer: assembles the payload, then compiles it with NSIS (~6 min)
cd ..; .venv\Scripts\python.exe scripts\build_installer.py --build

# 4. the source archive, from `git ls-files` (so nothing ignored can leak in)
.venv\Scripts\python.exe scripts\build_delivery_zip.py --output archive\releases\AI-Workbench-<commit>-final.zip
```

Step 3 must run *after* step 2: `build_installer_payload.py` copies
`desktop/src-tauri/target/release/ai-workbench-desktop.exe` in as `AI Workbench.exe`,
and if it is missing the payload assembles without a launcher — producing an
installer that installs perfectly and cannot start. `build_installer.py` checks for
it and says so rather than shipping that.

After a rebuild, re-run the acceptance:

```powershell
.venv\Scripts\python.exe scripts\verify_installer_static.py
.venv\Scripts\python.exe scripts\verify_installer.py --apply
```

### Two version numbers, and why they differ

The installer declares **2.1.0** (`installer/ai-workbench.nsi`, also written into the
exe's version resource) while the Tauri shell declares **2.0.0**
(`desktop/src-tauri/tauri.conf.json`). They version different things — the shipped
product release versus the shell binary — and neither is derived from the other, so
they are not expected to match.

Note also that `tauri build` emits its *own* bundle at
`desktop/src-tauri/target/release/bundle/nsis/AI Workbench_2.0.0_x64-setup.exe`.
That is **not** the shipped installer; it is a by-product of building the shell. The
shipped installer is `build/installer/AI-Workbench-Setup.exe`, compiled from
`installer/ai-workbench.nsi` by step 3 above.

### What the installer is not

It still expects the project directory to exist. It is not a self-contained install, and
saying otherwise would be the kind of claim that falls apart on someone else's machine.

---

*Checked against the source at `c6bad76` (2026-10-07): every script, path, port and
default named in this document was checked for existence and for agreement with
`config.py`, `lib.ps1`, `dev.ps1`, `infra-start.ps1` and the installer sources. Five
corrections came out of it: `docker-compose.yml` and `start.bat`/`start.sh` **do** exist
(§1 said they did not), there are ten prompt templates rather than nine, four AI
defaults were wrong, and the port-conflict messages quoted in §9 existed in no script.
The rest of the document was compared item by item and matched.*
