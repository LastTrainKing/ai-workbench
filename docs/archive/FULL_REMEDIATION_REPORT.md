# FULL REMEDIATION REPORT — AI Workbench release freeze

Branch `fix/release-freeze-20261009`, base commit `682ed8d` (code build `de4dae1`).
Every fix below is on that branch and every one carries a regression test that **fails on
the pre-fix code**. Test names are given so the claim can be checked rather than believed.

The consolidated issue table, including what is *not* fixed, is `RELEASE-FREEZE-ISSUES.md`.
The pass/fail roll-up is `ACCEPTANCE_MATRIX.md`. The honest list of what remains is
`KNOWN_ISSUES.md`.

## Constraints observed throughout

| Constraint | How it was held |
|---|---|
| Zero real model calls | The test suite forces `AI_PROVIDER=offline`; agent and RAG tests drive a stub provider. No API key exists in the working tree. |
| Real user data untouched | `%LOCALAPPDATA%\AI Workbench Data` file count and byte total are **identical** before and after (`build/release-freeze/data-baseline-{before,after}.json`). |
| No weakened assertions | Exactly one pre-existing assertion changed, and it asserted the defect itself — see RF-04 below. |
| No `NOT VERIFIED` promoted to `PASS` | `ACCEPTANCE_MATRIX.md` uses only PASS / FAIL / BLOCKED / NOT RUN. |

---

## RF-01 — Desktop UI is a WebView2 page, not a native interface

- **Severity** P0 · **Status** OPEN (architectural) · **Category** product-requirement conflict
- **Root cause** The desktop shell is Tauri 2 + WebView2 hosting the React build. The
  requirement is a native Windows UI (Qt 6 / Qt Quick preferred) with no embedded web page.
- **Affected** `desktop/src-tauri/**`, `frontend/**`, `installer/ai-workbench.nsi`
- **What changed this round** Only the interim safety item (§13) and the documentation:
  - `README.md` no longer says "no browser"; it states that the window renders with
    WebView2 and names RF-01 as an open release blocker.
  - `tauri.conf.json` CSP was already `null`. **Not fixed** — see `KNOWN_ISSUES.md` KI-01
    for why it was left rather than half-done.
- **Regression test** `test_release_freeze_versioning.py::test_frontend_and_shell_metadata_match`
  pins the version only; there is no test that can assert "is native".
- **Verdict** This is the single item that makes a "pass freeze" verdict impossible. It is
  a multi-week rewrite, not a fix, and the user's own instruction says to evaluate it as an
  independent work item rather than substitute a beautified web page.

## RF-02 — A fresh install came up in development mode with a known account

- **Severity** P0 · **Status** FIXED
- **Root cause** `installer/ai-workbench.nsi` did `CopyFiles .env.example .env`. The
  template selects `APP_ENV=dev`, `DEBUG=true` and a placeholder `SECRET_KEY`, and in that
  configuration `main.py` seeds `demo / demo1234`. A shipped product's first run was a
  development build with a published credential, on a machine where it reads the user's
  files and runs commands.
- **Affected** `installer/ai-workbench.nsi` (install section), `backend/app/core/production_env.py` (new),
  `backend/app/cli.py` (`init-env`), `scripts/start-desktop.bat`, `.env.example`
- **Change**
  - New `app.core.production_env.ensure_production_env(root)`: writes `APP_ENV=prod`,
    `DEBUG=false` and a fresh `secrets.token_urlsafe(48)` key, **only when `.env` is
    absent**. An existing file is returned as `None` and left byte-identical.
  - `python -m app.cli init-env` exposes it; `scripts/start-desktop.bat` runs it before
    the API starts. Not the installer: the key must survive a repair, and an installer
    step re-runs on every repair.
  - The installer no longer copies the example at all.
- **Tests** `test_release_freeze_production_env.py` — 10 cases including
  `test_an_existing_file_is_never_touched` (3 consecutive calls, byte-for-byte),
  `test_generated_key_satisfies_the_production_validator` (feeds the key back through
  `Settings(app_env="prod")`), and static assertions that the `CopyFiles` line is gone and
  that the launcher runs `init-env` before `api: start requested`.
- **Command** `python -m pytest tests/unit/test_release_freeze_production_env.py` → 10 passed
- **Also** `scripts/check_installer_syntax.py` → compiles, no fatal warnings.

## RF-03 — Production accepted placeholder secret keys

- **Severity** P0 · **Status** FIXED
- **Root cause** `Settings._production_is_not_a_development_default` compared the key to
  one exact string plus a length check. `change-me-to-a-random-32-plus-character-string`
  — the value that actually shipped in `.env.example` — is 47 characters, is not the
  built-in default, and **passed**.
- **Affected** `backend/app/core/config.py` (`_secret_problems`), `.env.example`
- **Change** Rejects placeholder text (`change-me`, `example`, `replace-me`, `placeholder`,
  `your-secret`, `dummy`, …), keyboard/sequence runs, short values and degenerate patterns
  (fewer than 10 distinct characters). `.env.example` now ships an empty value with a
  generation command in the comment.
- **Tests** `test_release_freeze_security.py::test_production_rejects_every_placeholder_secret`
  (10 parametrised values), `::test_production_accepts_a_real_random_key`,
  `::test_development_and_test_are_unaffected`
- **Command** `python -m pytest tests/unit/test_release_freeze_security.py` → 29 passed

## RF-04 — Passwords were silently truncated at 72 bytes

- **Severity** P0 · **Status** FIXED
- **Root cause** `hash_password` applied bcrypt's 72-byte limit itself
  (`password.encode("utf-8")[:72]`). Two consequences: a multibyte password could be cut in
  the middle of a character, and two passwords agreeing on their first 72 bytes were one
  password. The schema limited **characters**, not bytes.
- **Affected** `backend/app/core/security.py`, `backend/app/schemas/auth.py`,
  `backend/app/services/auth_service.py`
- **Change** Versioned scheme `bcrypt_sha256$` over `base64(sha256(password))` — 44 ASCII
  bytes, always under the ceiling, and base64 rather than the raw digest because a raw
  digest can contain a NUL and bcrypt stops at the first one. Legacy bare `$2b$` hashes
  still verify through the old rule and are upgraded on the next successful login
  (`password_needs_rehash`), so no account is reset. Register and login now share one
  ceiling (128 characters); over-length is **rejected**, not truncated.
- **Also fixed** The login timing-equaliser was `"$2b$12$" + "x"*53` — an invalid hash, so
  `checkpw` returned immediately. That is an account-existence oracle, not an equaliser. It
  is now a real hash of a value nobody knows.
- **Tests** `test_release_freeze_security.py` (16) and `test_release_freeze_auth.py` (8),
  including `test_legacy_rule_reproduces_the_truncation_bug` (keeps the old behaviour as a
  witness), `test_new_scheme_rejects_passwords_that_only_differ_after_byte_72`,
  `test_multibyte_boundary_is_not_split_mid_character`, and an end-to-end legacy login that
  asserts the stored hash is upgraded.
- **One pre-existing assertion changed** `test_security_and_files.py::test_long_passwords_are_truncated_not_crashing`
  asserted that `hash_password("a"*500)` verified — i.e. that 428 characters were discarded
  silently. It now asserts rejection, with the old expectation quoted in its docstring.
  This is the removal of an assertion that encoded a defect, not a relaxation.
- **Command** `python -m pytest tests/unit/test_release_freeze_security.py tests/api/test_release_freeze_auth.py` → 37 passed

## RF-05 — Logout failed open when Redis was unavailable

- **Severity** P1 · **Status** FIXED
- **Root cause** `is_token_revoked` returned a bool built from `cache_get`, which returns
  `None` when Redis is down. `None` was read as "not revoked", so a logged-out token became
  valid again for the rest of its 24 hours — and nothing said so.
- **Affected** `backend/app/core/security.py`, `backend/app/api/deps.py`,
  `backend/app/api/v1/auth.py`, `backend/app/models/user.py`,
  `backend/alembic/versions/20261009_1923_6068bb13c474_revoked_tokens.py` (new)
- **Change** New `revoked_tokens` table is the authority; Redis is a fast path in front of
  it. `RevocationState` has three values — `REVOKED`, `NOT_REVOKED`,
  `STORAGE_UNAVAILABLE` — and `get_current_user` refuses the request in the third case with
  a distinct 503 code. Nothing is written on the failure path, so a transient outage cannot
  permanently lock out valid tokens. `purge_expired_revocations` runs at startup so the
  table stays the size of the logout window.
- **Tests** `test_release_freeze_security.py` (7) and `test_release_freeze_auth.py`
  (`test_logout_makes_the_token_stop_working`, `test_revocation_survives_losing_the_cache`,
  `test_never_revoked_token_is_not_locked_out_by_a_cache_miss`)
- **Command** `python -m pytest tests/unit/test_release_freeze_security.py tests/api/test_release_freeze_auth.py` → 37 passed

## RF-06 — A stopped agent was recorded as a success

- **Severity** P1 · **Status** FIXED
- **Root cause** `executor.py` ended with
  `agent_run.status = SUCCEEDED if final_answer else FAILED`, then called
  `recorder.finish(status=SUCCEEDED)` unconditionally. Every non-`final_answer` ending
  produced explanatory text, and the truthiness of that text was the whole success test —
  so `max_steps`, `loop_detected` and `empty_response` were all written as `SUCCEEDED`.
- **Affected** `backend/app/ai/agent/executor.py`, `backend/app/domain/enums.py`,
  `backend/app/main.py`, `backend/app/ai/agent/plan.py`, frontend status vocabulary
- **Change** `AgentStopReason` → `RunStatus` is one mapping, and only `FINAL_ANSWER` can
  reach `SUCCEEDED`. New terminal values `INCOMPLETE` and `INTERRUPTED`. Before a
  `SUCCEEDED` is written, `_verify_deliverables` checks that every path-shaped artifact the
  run claims to have produced exists and is not empty. Startup reconciliation marks
  interrupted runs `INTERRUPTED` (and `RunState` gained a matching value) instead of
  flattening them to `FAILED`.
- **Tests** `test_release_freeze_run_state.py` — 23 cases including
  `test_agent_hitting_max_steps_is_incomplete_not_succeeded` (drives a real executor
  against a looping stub and asserts the `ai_runs`, `agent_runs` and payload all agree) and
  `test_claimed_deliverable_that_does_not_exist_is_not_a_success`.
- **Command** `python -m pytest tests/integration/test_release_freeze_run_state.py` → 23 passed

## RF-07 — A crashed job was retried forever

- **Severity** P1 · **Status** FIXED
- **Root cause** `JobRepository.requeue_stale` moved every stale `RUNNING` job back to
  `QUEUED` without consulting `attempts`/`max_attempts`. `claim_next` incremented the
  counter; the recovery path ignored the ceiling it was incrementing towards. On a desktop
  product that restarts often, `max_attempts` bounded nothing.
- **Affected** `backend/app/repositories/ai_repo.py`
- **Change** An exhausted stale job becomes terminal `FAILED` with a reason and
  `finished_at`; a job with attempts left is requeued **with a backoff**. The document
  attached to an exhausted job is marked `FAILED` too, so the queue and the document cannot
  disagree about whether work is still happening.
- **Tests** `test_release_freeze_run_state.py` — requeue both sides, not-claimed-again,
  document consistency, and fresh jobs untouched.
- **Command** `python -m pytest tests/integration/test_release_freeze_run_state.py` → 23 passed

## RF-08 — Skill discovery's outbound gate was empty

- **Severity** P1 · **Status** FIXED
- **Root cause** `_authorise(permissions, url)` returned early when `permissions` was
  `None`, and `POST /skills/discover` — the only caller — never passed a policy. The domain
  check on that path never ran. `fetch_skill_file` also used `follow_redirects=True`, which
  authorises the first URL and then follows wherever it is sent.
- **Affected** `backend/app/ai/skills/discovery.py`, `backend/app/api/v1/skills.py`
- **Change** A missing policy is a denial. The route injects the agent's own policy
  (`build_policy(user.id)`). Redirects are resolved and re-authorised hop by hop, bounded
  to three.
- **Tests** `test_release_freeze_skill_network.py` — 17 cases: missing policy denied,
  loopback/private/link-local/metadata/decimal-IP/suffix-spoof refused, redirect to
  loopback refused **before the request is made**, relative redirect resolved, loop bounded,
  and the client asserted to be built with `follow_redirects=False`.
- **Command** `python -m pytest tests/unit/test_release_freeze_skill_network.py` → 17 passed

## RF-09 — One account could change another account's AI provider

- **Severity** P1 · **Status** FIXED
- **Root cause** Provider, model, base URL and API key lived in one file
  (`.runtime/ai-config.json`) that every account read *and wrote*. Account A changing the
  model changed what B's next call used; A could point B's prompts and uploaded documents at
  an endpoint A controlled, and the bill landed on B's provider account.
- **Affected** `backend/app/ai/runtime_config.py`, `backend/app/api/v1/ai_config.py`,
  `backend/app/api/deps.py`
- **Change** Layered store: account file → installation file → environment → defaults. A
  request can only write its own layer; `save(None, …)` and `clear(None)` are refused with
  `ai_config_scope_required`. The installation file is still read so an existing install's
  settings survive. `build_llm_provider()` is called from deep inside the RAG pipeline and
  the agent, none of which take an owner, so the auth dependency binds the request to an
  account via a contextvar that `resolve()` reads when it is not told one. Base URLs are now
  validated: https for remote hosts, http allowed only for loopback, no embedded userinfo,
  no query/fragment, no non-http scheme.
- **Tests** `test_release_freeze_provider_isolation.py` (23) and
  `test_release_freeze_provider_api.py` (6), including an A/B pair over HTTP proving B is
  untouched, that reset only affects the caller, and that the key never appears in any
  response body.
- **Command** `python -m pytest tests/unit/test_release_freeze_provider_isolation.py tests/api/test_release_freeze_provider_api.py` → 29 passed

## RF-10 — Qdrant had no authentication, and CORS was on

- **Severity** P1 · **Status** FIXED (code); runtime verification BLOCKED
- **Root cause** `scripts/infra-start.ps1` generated `enable_cors: true` and no `api_key`,
  and nothing anywhere sent a key. Qdrant on loopback with CORS open is reachable by every
  process on the machine and every page the browser loads.
- **Affected** `scripts/infra-start.ps1`, `backend/app/core/config.py`, `docker-compose.yml`
- **Change** The launcher generates a key on first start, persists it to
  `<runtime>/qdrant-api-key.txt`, enforces it in the generated config, and **rewrites an
  existing config** so the fix takes effect on the next start. The backend reads the same
  file (an explicit `QDRANT_API_KEY` still wins), because the launcher and the backend are
  separate processes. CORS is off.
- **Tests** `test_release_freeze_deployment.py` — CORS asserted off on active config lines
  only (the script's own comment quotes the old value), key generation and persistence
  asserted, and the file-backed fallback exercised against a temp runtime root.
- **Command** `python -m pytest tests/unit/test_release_freeze_deployment.py` → 13 passed

## RF-11 — One valid citation marked the whole answer grounded

- **Severity** P1 · **Status** FIXED
- **Root cause** `grounded = bool(used_ids)`. A single valid number anywhere in the answer
  marked the whole thing grounded, including the paragraphs around it that cited nothing.
  `README.md` then described the result as "citation-enforced", which was never true.
- **Affected** `backend/app/ai/rag/pipeline.py`, `backend/app/api/v1/ai.py`, `README.md`,
  `docs/rag.md`
- **Change** `analyse_citations` splits the answer into claim-bearing units and requires
  every one to carry a marker resolving to a real source, with no dangling markers. A
  missing or invalid citation triggers **one** retry with the requirement restated —
  skipped for the deterministic offline engine, which cannot change its own output. Still
  ungrounded → the answer is returned with an explicit `⚠️ 未验证` note naming the reason.
  `retrieved_sources`, `used_citation_ids` and `grounded` are now separate fields. The docs
  state precisely what `grounded` means: a structural check, not fact-level entailment.
- **Tests** `test_release_freeze_rag_citations.py` — 14 cases including
  `test_one_citation_for_many_claims_is_not_grounded` (the defect) and
  `test_dangling_citation_invalidates_an_otherwise_cited_answer`.
- **Command** `python -m pytest tests/unit/test_release_freeze_rag_citations.py` → 14 passed

## RF-12 — Verification scripts killed the user's running application

- **Severity** P1 · **Status** FIXED
- **Root cause** Five installer/cold-start scripts closed the app with
  `taskkill /F /IM "AI Workbench.exe"`, which matches on the image name and stops every
  instance on the machine — including the one the user has open. Four sites also passed
  `shell=True` with an f-string command line to the installer, where a path containing `&`
  or `%` is reinterpreted by cmd.exe.
- **Affected** `scripts/_process_guard.py` (new), `verify_installer.py`,
  `verify_installer_acceptance.py`, `verify_installer_entries.py`, `verify_relocation.py`,
  `verify_cold_start_final.py`
- **Change** `stop_app_processes(roots)` enumerates running processes, keeps those whose
  executable path is under a root the test owns, and stops those by PID with `/T`.
  `run_nsis_installer` keeps the raw command line NSIS requires and refuses a path cmd would
  reinterpret. Also fixed: console output was decoded as UTF-8, which raises on a Chinese
  Windows code page inside a reader thread where the traceback never reaches the caller.
- **Tests** `test_release_freeze_process_safety.py` — 31 cases. The "no image-name kills"
  assertions parse the scripts with `ast` and ignore docstrings, because several of these
  files describe the bug they were fixed for.
- **Command** `python -m pytest tests/unit/test_release_freeze_process_safety.py` → 31 passed

## RF-13 — Four versions for one product

- **Severity** P2 · **Status** FIXED
- **Root cause** Tauri shell `2.0.0`, installer `2.1.0`, backend `1.0.0`, frontend `1.0.0`,
  changelog `1.1.0`. Documented as intentional; for a user-visible version that is not
  defensible.
- **Affected** `VERSION` (new), `scripts/sync_version.py` (new),
  `backend/app/core/config.py`, `desktop/**`, `frontend/package.json`,
  `installer/ai-workbench.nsi`, `CHANGELOG.md`, `scripts/build_installer_payload.py`
- **Change** `VERSION` at the repository root is the source; `sync_version.py` propagates it
  and `--check` verifies. The backend reads the same file and reports `0.0.0-unknown` rather
  than a plausible number if it cannot. Freeze version: **2.2.0**. `VERSION` is added to the
  installer payload. `pytest`'s addopts no longer carries `-q` (duplicated with the command
  line it became `-qq`, which suppresses the summary the reports are built from).
- **Tests** `test_release_freeze_versioning.py` — 15 cases, including one that fails the
  moment any of the five carriers drifts.
- **Command** `python -m pytest tests/unit/test_release_freeze_versioning.py` → 15 passed

## RF-14 — docker-compose published everything on every interface

- **Severity** P2 · **Status** FIXED (static); runtime execution BLOCKED
- **Root cause** PostgreSQL `5432`, Redis `6379`, Qdrant `6333/6334`, API `8000` and
  frontend `5173` were published as `${PORT}:PORT`. PostgreSQL's password defaulted to
  `aiworkbench`, Redis had no authentication, Qdrant had none.
- **Affected** `docker-compose.yml`
- **Change** The data services are not published at all — only api/worker reach them, over
  the `awb` bridge network. The API and frontend publish on `127.0.0.1`. `POSTGRES_PASSWORD`,
  `REDIS_PASSWORD` and `QDRANT_API_KEY` are required (`:?`), so `docker compose up` fails
  with a named error instead of starting an open database. Qdrant CORS off. Redis requires a
  password; connection URLs updated for both api and worker.
- **Tests** `test_release_freeze_deployment.py` (7 static assertions)
- **Not done** Actually running compose — no Docker on this machine. See `KNOWN_ISSUES.md` KI-02.

## RF-15 — A live API key sat in a commented line of the development `.env`

- **Severity** P2 · **Status** MITIGATED (rotation unconfirmed)
- **Root cause** A `sk-`-prefixed key in a commented-out `DEEPSEEK_API_KEY=` line. The
  file is git-ignored, so it never entered Git — but it was on disk, and any full-disk
  capture would have included it.
- **Change** The value was removed; the comment is kept so the line's purpose is still
  readable. The key is **not** reproduced anywhere in this repository or these reports.
- **Not confirmed** Whether the key was revoked at the vendor. This round makes no real
  model calls, so it cannot be tested, and the honest record is "removed from the working
  copy; revocation status unknown".

## RF-16 — The route sweep found an unauthenticated endpoint

- **Severity** P2 · **Status** FIXED
- **Root cause** `GET /api/v1/delivery/states` had no auth dependency. Its body is a static
  enum list, so nothing leaked — but it was the one place a later edit could have put
  something real without anyone noticing the dependency was missing.
- **Affected** `backend/app/api/v1/delivery.py`
- **Change** Takes `CurrentUser`. The docstring records that it does not use the value, and
  why it takes it anyway.
- **Tests** `test_release_freeze_ownership.py` — 152 routes swept for authentication, plus
  A/B ownership across projects, knowledge bases, tasks and notes, plus the two cases a
  detail-page check misses (list filtering, and passing someone else's parent id).
- **Command** `python -m pytest tests/api/test_release_freeze_ownership.py` → all passed

---

## Test suite added this round

13 files, ~230 new test cases. All under `backend/tests/`, all offline.

| File | Cases | Covers |
|---|---|---|
| `unit/test_release_freeze_security.py` | 29 | RF-03, RF-04, RF-05 |
| `api/test_release_freeze_auth.py` | 8 | RF-04, RF-05 end to end |
| `unit/test_release_freeze_skill_network.py` | 17 | RF-08 |
| `unit/test_release_freeze_deployment.py` | 13 | RF-10, RF-14 |
| `integration/test_release_freeze_run_state.py` | 23 | RF-06, RF-07 |
| `unit/test_release_freeze_provider_isolation.py` | 23 | RF-09 |
| `api/test_release_freeze_provider_api.py` | 6 | RF-09 over HTTP |
| `unit/test_release_freeze_rag_citations.py` | 14 | RF-11 |
| `unit/test_release_freeze_production_env.py` | 10 | RF-02 |
| `unit/test_release_freeze_versioning.py` | 15 | RF-13 |
| `unit/test_release_freeze_process_safety.py` | 31 | RF-12 |
| `api/test_release_freeze_ownership.py` | ~160 | RF-16, §14 |
| `unit/test_project_integrity.py` (edited) | — | `revoked_tokens` registered |
| `unit/test_security_and_files.py` (edited) | — | the truncation assertion, corrected |

## Commands run

```
cd backend
..\.venv\Scripts\python.exe -m pytest                      # full backend suite
..\.venv\Scripts\python.exe -m pytest tests/unit tests/api # fast subset
..\.venv\Scripts\ruff.exe check app tests                  # lint
cd ..\frontend
npm run typecheck && npm run lint && npm test
cd ..
.venv\Scripts\python.exe scripts/check_installer_syntax.py
.venv\Scripts\python.exe scripts/sync_version.py --check
```

Results are recorded in `ACCEPTANCE_MATRIX.md` and the raw log is
`build/release-freeze/backend-tests.log`.
