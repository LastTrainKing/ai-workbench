# Development guide

How to set up the project from zero, the coding conventions, the layering rules,
and recipes for adding an endpoint and a model.

## 1. Local setup from zero

```powershell
# 1. Clone the repository and enter it
cd ai-workbench

# 2. Download the native runtimes (PostgreSQL, Redis, Qdrant)
powershell -ExecutionPolicy Bypass -File scripts\fetch-runtime.ps1

# 3. Start the infrastructure
powershell -ExecutionPolicy Bypass -File scripts\infra-start.ps1

# 4. Create the Python environment and install dependencies
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r backend\requirements.txt
.venv\Scripts\python.exe -m pip install -r backend\requirements-dev.txt

# 5. Create your .env from the template and edit it
Copy-Item .env.example .env

# 6. Verify every dependency
cd backend
..\.venv\Scripts\python.exe -m app.cli check
cd ..

# 7. Start the application (API + in-process worker)
powershell -ExecutionPolicy Bypass -File scripts\dev.ps1 start

# 8. (Optional) verify end to end
.venv\Scripts\python.exe scripts\smoke_test.py
```

For a fully local, key-free setup, set `AI_PROVIDER=offline` and
`EMBEDDING_PROVIDER=offline` in `.env`. With no `AI_API_KEY` the factory degrades
to the offline engine automatically, but setting it explicitly removes the
`degraded` warning.

The application creates missing tables and seeds prompt templates and the demo
account (`demo` / `demo1234`) on first boot. Nothing else is required.

## 2. Repository layout

```
ai-workbench/
├── backend/
│   ├── app/                # the application (see architecture.md)
│   ├── tests/              # pytest suite (conftest.py + unit/)
│   ├── alembic/            # migration scaffolding (no revisions committed yet)
│   ├── pyproject.toml      # ruff + mypy + pytest configuration
│   ├── requirements.txt
│   └── requirements-dev.txt
├── scripts/                # PowerShell operations + smoke test + sample maker
├── samples/                # sample documents used by the smoke test
├── vendor/runtime/         # downloaded native runtimes (not committed)
├── .runtime/               # runtime state: pgdata, storage, logs, pids (not committed)
├── docs/                   # this documentation
└── .env.example            # configuration template
```

## 3. Layering rules

These are enforced by review and, where practical, by the type checker.

1. **No SQL in API routes.** A route imports a service, never a repository and
   never `sqlalchemy`.
2. **No raw SQL in services.** A service calls a repository. Aggregation code
   that legitimately needs a bespoke projection (e.g. `StatsService.dashboard`,
   `GraphService.build`) may issue `select()` directly, but it lives in the
   service that owns the projection and is documented as such.
3. **No database access from the agent.** The agent may only call a registered
   `Tool`; tools call services; services call repositories.
4. **No swallowed exceptions.** Every `except` either logs with context or
   re-raises a typed error. Bare `except: pass` is not allowed.
5. **Typed errors.** Business code raises `AppError` subclasses from
   `app/core/errors.py`, never a bare `Exception` with a message. The exception
   handlers translate them into the standard envelope.
6. **Provider-agnostic AI.** Business code depends on `LLMProvider` /
   `EmbeddingProvider` interfaces, never on a concrete provider.
7. **Prompts are data.** Prompt text lives in `app/ai/prompts/builtin.py` (seed)
   and in the database (runtime). Business code asks for a prompt **by name**.
8. **Configuration over code.** Behaviour that differs between environments
   belongs in `app/core/config.py` and `.env.example`, not in a branch.
9. **One direction only.** `api → services → repositories → models/infrastructure`.
   The AI layer and the worker are called by services; they do not call upward.

### Why `list_of` / `get_one_of` / `count_of` exist

`BaseRepository.list` and `get_or_none` always select `self.model`. A repository
sometimes needs to read a *child* entity (for example `AIRunRepository` reading
`ToolCall` rows). Using `list` with another table's conditions would silently
select the wrong entity type — this was a real defect. The sanctioned way to
query a foreign model is the explicit `list_of` / `get_one_of` / `count_of`
helpers in `app/repositories/base.py`. Use them.

## 4. Coding conventions

* **Python 3.11+ syntax**, `from __future__ import annotations` at the top of
  every module.
* **Line length 100**, enforced by ruff. `ruff format` is the formatter.
* **Imports**: `ruff` with `I` (isort) enabled; `app` is first-party.
* **Type hints** on public functions. mypy runs over `app` (excluding
  `app.repositories.*` and `alembic/versions`).
* **Docstrings** explain *why*, not *what*. Module docstrings describe the design
  decision and reference the relevant `docs/` file.
* **Logging** uses `get_logger(__name__)` and structured `extra={...}` keys. Never
  pass a reserved key (`message`, `name`, `levelname`, …) through `extra` — a
  central guard drops them, but the intent is to keep logs clean.
* **Async**: never block the event loop. Do not call `time.sleep` or blocking IO
  in a coroutine.
* **Comments** only where the logic is not self-evident (e.g. the `eager_defaults`
  rationale, the event-loop starvation guard). Do not narrate obvious code.
* **No emoji** in code, comments or documentation.
* **English** for code, identifiers and `docs/*.md`; **Chinese** is acceptable in
  user-facing strings and `docs/thesis/*.md`.

### Formatting and linting

```powershell
cd backend
..\.venv\Scripts\python.exe -m ruff check app tests
..\.venv\Scripts\python.exe -m ruff format app tests
..\.venv\Scripts\python.exe -m mypy app
```

## 5. How to add an endpoint

1. **Decide the layer.** A route that only reads and shapes data still goes
   through a service; the service may be thin.
2. **Add the service method** in `app/services/`. Put business rules and
   ownership checks here. Raise typed errors (`NotFoundError`, `ConflictError`,
   `ValidationError`).
3. **Add repository methods if needed** in the relevant `*_repo.py`. Use
   `get_owned` for ownership. Never build SQL in the service.
4. **Add or reuse a Pydantic schema** in `app/schemas/` for the request body.
5. **Add the route** in the correct router under `app/api/v1/`. Use the shared
   dependencies:

   ```python
   from app.api.deps import CurrentUser, DbSession, Pagination
   from app.core.response import build_page, ok

   @router.post("", status_code=status.HTTP_201_CREATED, summary="…")
   async def create_thing(
       payload: ThingCreate, user: CurrentUser, session: DbSession
   ) -> dict:
       thing = await ThingService(session).create(owner_id=user.id, **payload.model_dump())
       return ok(ThingOut.model_validate(thing).model_dump(mode="json"))
   ```

   * Return `ok(data)` for a single object and `build_page(...)` for a list.
   * Use `CurrentUser` for authentication; do not read the token yourself.
   * Use `status.HTTP_201_CREATED` for creates, `202` for accepted async work.
6. **Register the router** only if it is a new router; existing routers are
   already included in `app/api/v1/__init__.py`.
7. **Add a smoke-test check** in `scripts/smoke_test.py` if the endpoint is part
   of the product loop.
8. **Document it** in [api.md](api.md).

## 6. How to add a model and a migration

1. **Define the model** in the appropriate module under `app/models/`. Inherit
   `Base` plus `UUIDPrimaryKeyMixin` and `TimestampMixin` unless there is a reason
   not to (join tables use composite primary keys and no timestamps).
2. **Declare constraints and indexes** in `__table_args__` with explicit names
   that follow the naming convention (or rely on the convention). Do not create
   two indexes with the same name — a duplicate index name between a column-level
   `index=True` and an explicit `Index(...)` once broke `create_all`. If you add
   an explicit index on a column that already has `index=True`, remove one.
3. **Export it** from `app/models/__init__.py` so Alembic autogenerate and the
   test fixtures see it.
4. **Add a repository** in the matching `*_repo.py` and a service method.
5. **Schema creation** happens automatically via
   `Base.metadata.create_all` in `_bootstrap_schema()` for any table that does
   not yet exist, so a fresh database picks up a new table with no extra step.
6. **Migrations.** Alembic is configured as a dependency and the naming
   convention is deterministic, but **no revision files are committed yet**
   (`backend/alembic/versions/` is empty). If you adopt Alembic, generate the
   first revision with `alembic revision --autogenerate -m "…"` and start
   versioning changes. Until then, changes to *existing* tables are not applied
   automatically by `create_all` and must be handled manually — be aware of this
   limitation.
7. **Add tests**: a unit test if the logic is pure, and a check in the smoke test
   if it is part of the product loop.

## 7. Testing your change

```powershell
cd backend
..\.venv\Scripts\python.exe -m pytest tests/unit -q     # fast, no services
..\.venv\Scripts\python.exe -m pytest -q                # needs PostgreSQL + Redis
cd ..
.venv\Scripts\python.exe scripts\smoke_test.py          # needs the running backend
```

See [testing.md](testing.md) for the full strategy and what is not covered.

## 8. Commit conventions

Use Conventional Commits so history is readable and tooling-friendly:

```
<type>(<scope>): <subject>

<body: what and why>

<footer: refs, breaking changes>
```

Types: `feat`, `fix`, `refactor`, `perf`, `docs`, `test`, `chore`, `build`, `ci`.

Scopes mirror the packages: `api`, `services`, `repos`, `models`, `ai`,
`providers`, `rag`, `agent`, `worker`, `core`, `scripts`, `docs`, `tests`.

Examples:

```
feat(agent): add knowledge_link tool
fix(worker): always yield when no job is claimed (event-loop starvation)
docs(rag): document the hybrid fusion formula
refactor(repos): add explicit list_of/get_one_of/count_of helpers
```

Guidelines:

* One logical change per commit; do not mix a refactor with a behaviour change.
* The subject is imperative, lower-case, no trailing period, ≤ 72 characters.
* The body explains *why*; the diff shows *what*.
* Reference the issue or task when there is one.
* Never commit `.env`, downloaded runtimes, or `.runtime/` state. Keep real
  secrets out of the repository; `.env.example` is the committed template.

## 9. Related documents

* [architecture.md](architecture.md) — the layers in detail
* [database.md](database.md) — the schema and the `list_of` rationale
* [testing.md](testing.md) — the test strategy
* [deployment.md](deployment.md) — running the stack
