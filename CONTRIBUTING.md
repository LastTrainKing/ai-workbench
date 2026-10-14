# Contributing to AI Workbench

Thanks for taking a look. This document describes how the codebase is organised, the
rules that keep it maintainable, and how to get a change merged.

---

## Getting set up

```bash
git clone <your-fork-url> ai-workbench
cd ai-workbench

# backend
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r backend/requirements-dev.txt   # Windows
# .venv/bin/python -m pip install -r backend/requirements-dev.txt         # Linux/macOS

# frontend
cd frontend && npm install && cd ..

# infrastructure + app
start.bat          # Windows
./start.sh         # Linux / macOS
```

Verify your setup before writing code:

```bash
cd backend && ../.venv/Scripts/python.exe -m app.cli check
cd .. && python scripts/smoke_test.py      # 75 end-to-end assertions
```

---

## Architectural rules

These are not style preferences — breaking them is what makes a project of this size
unmaintainable. Reviewers will ask you to change a PR that violates them.

1. **Layering.** `api → services → repositories → models`. A route validates and
   serialises; it never contains business logic and never issues SQL. Only
   repositories talk to the database.
2. **The agent reaches the database only through tools.** Tools call services,
   services call repositories.
3. **AI code depends on interfaces.** `LLMProvider` / `EmbeddingProvider` are the
   only contracts the rest of the code knows. Never import a vendor SDK outside
   `app/ai/providers/`.
4. **Never swallow exceptions.** No `except: pass`. Either translate into a typed
   `AppError` and let the handler produce the envelope, or let it propagate.
   Infrastructure outages degrade with a **logged warning** and a documented
   fallback.
5. **Prompts are data.** Never hardcode prompt text in business logic — add or edit a
   template in `app/ai/prompts/builtin.py` and go through the registry.
6. **No unbounded loops or retries.** Every retry is bounded in attempts *and*
   wall-clock time (`app/core/retry.py`).
7. **Every external call has a timeout.**
8. **Tests cover the failure path**, not just the happy path.

---

## Adding a database table

1. Create the model in `app/models/<domain>.py`, inheriting `Base, UUIDPrimaryKeyMixin,
   TimestampMixin`. Give every foreign key an explicit `ondelete=`.
2. Export it from `app/models/__init__.py`.
3. Generate the migration against an **empty** database (autogenerate against a
   populated one produces an empty diff):

   ```bash
   createdb aiworkbench_migrate
   DATABASE_URL=postgresql+asyncpg://aiworkbench:aiworkbench@127.0.0.1:5432/aiworkbench_migrate \
     python -m alembic revision --autogenerate -m "add <table>"
   DATABASE_URL=...alembic upgrade head
   DATABASE_URL=...alembic check     # must print "No new upgrade operations detected"
   ```
4. Add a repository, then a service, then a router. Add a test.

## Adding an endpoint

1. Request/response schemas in `app/schemas/`.
2. Business logic in a service (raise `NotFoundError`, `ConflictError`,
   `ValidationError` — never `HTTPException`).
3. Router entry with `summary=`, typed parameters and the standard envelope via
   `ok(...)` / `build_page(...)`.
4. A test in `backend/tests/api/`.

## Adding an agent tool

1. Subclass `Tool`, declare `spec: ClassVar[ToolSpec]` and `is_write`.
2. Implement `execute(self, ctx, **kwargs) -> ToolResult`.
3. Register it in `build_default_registry()`.
4. Update the tool table in `README.md`, `docs/agent.md` and the test that asserts the
   registry contents (`tests/unit/test_tools.py`).

## Adding a prompt template

1. Add a `PromptTemplate` to `app/ai/prompts/builtin.py` with a `task=` marker and
   `{variable}` placeholders. Wrap the material in `<document>` / `<context>` tags —
   the offline engine relies on those tags to locate the content.
2. Append it to `PROMPT_INVENTORY`.
3. If it needs structured output, set `json_output=True` and document the JSON shape
   in the template itself.
4. Existing installations pick up new templates on the next boot; changed content is
   versioned through the prompt API (`POST /api/v1/prompts/{name}/reset`).

---

## Style

**Python** — `ruff check` and `ruff format` must pass. Line length 100. Type hints on
public functions. Prefer explicit code over clever code; comment *why*, not *what*.

**TypeScript / React** — `npm run lint`, `npm run typecheck` and `npm run build` must
pass. Function components with hooks. Server state through TanStack Query — do not
copy API data into Zustand. Every list has a loading, empty and error state.

**Commits** — [Conventional Commits](https://www.conventionalcommits.org/):

```
feat(agent): add knowledge_link tool
fix(worker): stop the main loop from starving the event loop
docs(rag): document the hybrid fusion formula
test(tasks): cover circular dependency rejection
refactor(repositories): make cross-entity queries explicit
```

## Before opening a pull request

```bash
# backend — lint and tests
cd backend
../.venv/Scripts/python.exe -m ruff check ../backend/app ../backend/tests ../desktop-native
../.venv/Scripts/python.exe -m pytest

# desktop client — the interface's own acceptance
cd ..
.venv/Scripts/python.exe desktop-native/verify_projection.py    # 108 assertions
.venv/Scripts/python.exe desktop-native/verify_native_shell.py  # 31 checks
.venv/Scripts/python.exe tools/check_layout.py                  # 19 pages x 3 sizes
.venv/Scripts/python.exe tools/check_qml_pages.py               # 20 pages load

# frontend (the web client — not what the desktop installer ships)
cd frontend
npm run lint && npm run typecheck && npm run test && npm run build

# end-to-end (needs a running server)
cd .. && python scripts/smoke_test.py
```

**Changed something in the interface? Run `verify_projection.py`, not just the tests.**
It asserts what the screen actually renders against the data behind it — `isVisible()`
and `text` on the real QML objects. This project has twice shipped "all tests green,
the user sees something false": the home screen read `summary.llm` where the field was
`summary.providers.llm` (a wrong field name raises nothing; it just makes the screen
lie), and a missing font fallback rendered an icon as a tofu block. Neither is a logic
error, and neither is caught by a test that only checks "the page loaded".

**Before adding an assertion, break the thing it guards and watch it fail.** An
assertion that has never failed is not evidence. Two of the five mutation checks run in
this repository's history produced *no* failure — and in both cases the mutation was
wrong, not the assertion.

Then check:

- [ ] no secrets, keys, absolute paths, `node_modules`, `__pycache__` or `.runtime/`
      content staged
- [ ] `git status` is clean apart from your intended files
- [ ] new behaviour has tests, including its failure path
- [ ] documentation updated when behaviour or configuration changed
- [ ] if you changed the schema, `alembic check` reports no pending operations
- [ ] if you changed a user-visible string, it is in `qml/theme.js` or came from the
      backend — not typed into a page twice
- [ ] `python tools/scan_for_secrets.py` is clean (it exits non-zero when it finds
      nothing at all, which means it did not run — see `tools/README.md`)

## Reporting a bug

Include: what you did, what you expected, what happened, the `request_id` from the
response header (it correlates every log line for that request), the relevant
`ai_run` id if it involves AI, and your `GET /api/v1/health/deps` output.

## Security

Do not open a public issue for a vulnerability — use GitHub's private vulnerability
reporting. See `SECURITY.md` for the full policy, including how credentials are stored
and what to do if one is ever committed. Never commit credentials; `.env` is
git-ignored and `.env.example` is the template.

## License

By contributing you agree that your work is released under the [MIT License](LICENSE).
