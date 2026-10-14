# Independent review — RF-02 … RF-17

Branch `fix/release-freeze-20261009`. This is a re-verification from the **source**, not a
restatement of the previous report. Where the previous report and the code disagreed, the
code won; the two places that happened are called out.

The question this document answers is not "do the tests pass" — that is
`ACCEPTANCE_MATRIX.md`. It is: **does each test actually reproduce the defect it claims
to cover, and is there any code that passes its test and is still wrong?**

## How "does the test reproduce the defect" was answered

Running the new tests against the pre-fix commit proves only that they import new modules.
So each fix was **reintroduced by a targeted edit** and the test that is supposed to catch
it was run. A test that still passes with its defect back is not a regression test.

`scripts/verify_release_freeze_mutations.py` does this and exits non-zero if any defect
goes uncaught.

```
  CAUGHT  RF-02 installer copies .env.example            1 failed in 0.33s
  CAUGHT  RF-03 placeholder key accepted                 7 failed, 3 passed
  CAUGHT  RF-04 bcrypt truncation                        1 failed in 1.28s
  CAUGHT  RF-05 revocation fails open                    1 failed in 0.73s
  CAUGHT  RF-06 stopped run marked succeeded             1 failed in 3.78s
  CAUGHT  RF-07 unbounded job retry                      1 failed in 3.99s
  CAUGHT  RF-08 missing policy allowed                   1 failed in 0.70s
  CAUGHT  RF-09 shared provider config                   1 failed in 0.62s
  CAUGHT  RF-10 Qdrant CORS enabled                      1 failed in 0.30s
  CAUGHT  RF-11 one citation grounds everything          1 failed in 0.81s
  CAUGHT  RF-12 kill by image name                       1 failed, 4 passed
  CAUGHT  RF-13 version drift                            1 failed in 0.36s
  CAUGHT  RF-14 compose publishes on all interfaces      1 failed in 0.37s
  CAUGHT  RF-16 unauthenticated delivery states          1 failed in 5.64s
  --------------------------------------------------------------------------
  All 14 defects are caught by their regression test.
```

RF-15 and RF-17 are covered differently — see below.

---

## Per-defect record

### RF-02 — a fresh install came up in development mode with a known account

| | |
|---|---|
| **Original** | `installer/ai-workbench.nsi` copied `.env.example` to `.env`. The template selects `APP_ENV=dev`, `DEBUG=true`, placeholder `SECRET_KEY`, under which `main.py` seeds `demo / demo1234`. |
| **Fix location** | `backend/app/core/production_env.py` (new), `backend/app/cli.py::_init_env`, `scripts/start-desktop.bat`, `installer/ai-workbench.nsi` |
| **Actual change** | `ensure_production_env(root)` writes `APP_ENV=prod`, `DEBUG=false` and `secrets.token_urlsafe(48)`, **only when `.env` is absent**; an existing file is returned as `None` and left byte-identical. The launcher runs `python -m app.cli init-env` before the API starts. The installer no longer copies the example. |
| **Regression test** | `test_release_freeze_production_env.py` — 10 cases |
| **Measured** | 10 passed. Mutation (re-add the `CopyFiles` line) → **1 failed**. |
| **Residual risk** | The launcher path is asserted statically and by the installer acceptance run, which exercised a fresh install. A *repair* over an install that already has a `.env` was exercised (step 8 of the acceptance run) and the run counts were unchanged. |
| **Verdict** | **PASS** |

### RF-03 — production accepted placeholder secret keys

| | |
|---|---|
| **Original** | The prod validator compared the key to one exact string plus a length check, so `change-me-to-a-random-32-plus-character-string` (47 chars) passed. |
| **Fix location** | `backend/app/core/config.py::_secret_problems` |
| **Actual change** | Rejects placeholder text, keyboard/sequence runs, short values and degenerate patterns (<10 distinct characters). `.env.example` ships an empty value. |
| **Regression test** | `test_release_freeze_security.py::test_production_rejects_every_placeholder_secret` — 10 parametrised values |
| **Measured** | 29 passed in that file. Mutation (`markers = []`) → **7 failed**. |
| **Residual risk** | The gate stops *shipped placeholders*; a user who deliberately types a long random-looking-but-weak key is not stopped. That is not a defect the gate can fix, and the docstring says so. |
| **Verdict** | **PASS** |

### RF-04 — passwords were silently truncated at 72 bytes

| | |
|---|---|
| **Original** | `hash_password` applied bcrypt's 72-byte limit itself. Two passwords agreeing on their first 72 bytes were one password; a multibyte password could be cut mid-character. |
| **Fix location** | `backend/app/core/security.py`, `schemas/auth.py`, `services/auth_service.py` |
| **Actual change** | Versioned `bcrypt_sha256$` over `base64(sha256(password))`. Legacy bare `$2b$` hashes still verify through the old rule and are upgraded on the next successful login. Register and login share one ceiling (128 chars); over-length is **rejected**. The login timing-equaliser is now a real hash instead of an invalid string that made `checkpw` return immediately. |
| **Regression test** | `test_release_freeze_security.py` (16) + `test_release_freeze_auth.py` (8) |
| **Measured** | 37 passed. Mutation (restore the truncating hash) → **1 failed**. |
| **Residual risk** | The legacy path is exercised by a test that constructs a legacy hash directly; no real pre-change account exists in this environment to migrate. The upgrade branch is covered by `test_legacy_hash_logs_in_and_is_upgraded`. |
| **Assertion changed** | `test_security_and_files.py::test_long_passwords_are_truncated_not_crashing` asserted the defect (that 428 characters were silently discarded). It now asserts rejection. **This is the removal of an assertion that encoded wrong behaviour, not a relaxation.** |
| **Verdict** | **PASS** |

### RF-05 — logout failed open when Redis was unavailable

| | |
|---|---|
| **Original** | `is_token_revoked` returned a bool built from `cache_get`, which returns `None` when Redis is down. `None` read as "not revoked". |
| **Fix location** | `security.py`, `api/deps.py`, `api/v1/auth.py`, `models/user.py`, migration `20261009_1923_6068bb13c474` |
| **Actual change** | `revoked_tokens` table is the authority; Redis is a fast path. `RevocationState` has three values and `get_current_user` refuses the request on `STORAGE_UNAVAILABLE`. Nothing is written on the failure path. `purge_expired_revocations` runs at startup. |
| **Regression test** | `test_release_freeze_security.py` (7) + `test_release_freeze_auth.py` (3, over HTTP) |
| **Measured** | 37 passed. Mutation (return `NOT_REVOKED` on `SQLAlchemyError`) → **1 failed**. |
| **Residual risk** | The migration is exercised against a scratch database by `verify_migration.py` (19/19) but not against a database that already had the old schema *with revocation rows* — there were none to have. |
| **Verdict** | **PASS** |

### RF-06 — a stopped agent was recorded as a success

| | |
|---|---|
| **Original** | `agent_run.status = SUCCEEDED if final_answer else FAILED`, then `recorder.finish(status=SUCCEEDED)` unconditionally. `max_steps`, `loop_detected` and `empty_response` all produced explanatory text, so all were `SUCCEEDED`. |
| **Fix location** | `ai/agent/executor.py`, `domain/enums.py`, `main.py`, `ai/agent/plan.py`, frontend vocabulary |
| **Actual change** | `AgentStopReason` → `RunStatus` is one mapping; only `FINAL_ANSWER` reaches `SUCCEEDED`. New `INCOMPLETE` and `INTERRUPTED`. `_verify_deliverables` runs before a `SUCCEEDED` is written. Startup reconcile marks interrupted runs `INTERRUPTED`. |
| **Regression test** | `test_release_freeze_run_state.py` — 23 cases, driving a real executor against a looping stub |
| **Measured** | 23 passed. Mutation (restore the `bool(final_answer)` test) → **1 failed**. |
| **Residual risk** | `_verify_deliverables` checks path-shaped artifacts only; a run whose deliverable is an entity row (a created task) is not file-verified. That is correct — a task is not a file — but it means "verified" is weaker for non-file deliverables, and the docstring says so. |
| **Verdict** | **PASS** |

### RF-07 — a crashed job was retried forever

| | |
|---|---|
| **Original** | `requeue_stale` moved every stale `RUNNING` job back to `QUEUED` without consulting `attempts`/`max_attempts`. |
| **Fix location** | `repositories/ai_repo.py::JobRepository.requeue_stale` |
| **Actual change** | Exhausted → terminal `FAILED` with reason and `finished_at`; attempts remaining → requeued **with backoff**. The attached document is marked `FAILED` too. |
| **Regression test** | `test_release_freeze_run_state.py` (requeue both sides, not-claimed-again, document consistency, fresh jobs untouched) |
| **Measured** | 23 passed. Mutation (`if False:` on the attempts check) → **1 failed**. |
| **Residual risk** | The "process dies mid-write" timeline is covered at the state level, not by actually killing a process mid-transaction. `verify_interrupted_runs.py` kills a real process but for agent runs, not for queue jobs. |
| **Verdict** | **PASS** (with the above noted as a test-coverage limit, not a code defect) |

### RF-08 — skill discovery's outbound gate was empty

| | |
|---|---|
| **Original** | `_authorise` returned early when `permissions` was `None`, and `POST /skills/discover` — the only caller — never passed one. `fetch_skill_file` used `follow_redirects=True`. |
| **Fix location** | `ai/skills/discovery.py`, `api/v1/skills.py` |
| **Actual change** | A missing policy is a denial. The route injects `build_policy(user.id)`. Redirects are resolved and re-authorised hop by hop, bounded to three. |
| **Regression test** | `test_release_freeze_skill_network.py` — 17 cases |
| **Measured** | 17 passed. Mutation (restore the early return) → **1 failed**. |
| **Residual risk** | DNS rebinding is not solved — the name is resolved once at check time and again at connect time. This is stated in `check_domain`'s docstring and is unchanged by this round. |
| **Verdict** | **PASS** (rebinding remains a documented limitation) |

### RF-09 — one account could change another account's AI provider

| | |
|---|---|
| **Original** | Provider, model, base URL and key lived in one file every account read *and wrote*. |
| **Fix location** | `ai/runtime_config.py`, `api/v1/ai_config.py`, `api/deps.py` |
| **Actual change** | Layered store: account file → installation file → environment → defaults. A request can only write its own layer. `build_llm_provider()` is called from code that takes no owner, so the auth dependency binds the request to an account via a contextvar. Base URLs validated (https for remote, http only for loopback, no userinfo/query/fragment). |
| **Regression test** | `test_release_freeze_provider_isolation.py` (23) + `test_release_freeze_provider_api.py` (6, A/B over HTTP) |
| **Measured** | 29 passed. Mutation (make `_path_for` always return the install path) → **1 failed**. |
| **Residual risk** | The contextvar binds a request; a **background worker** has no owner and therefore uses the installation layer. That is the correct reading of "whose setting is this?" with no user, but it means a worker's model choice is not per-account. Documented in the module. |
| **Verdict** | **PASS** (worker scope documented) |

### RF-10 — Qdrant had no authentication, and CORS was on

| | |
|---|---|
| **Original** | `infra-start.ps1` generated `enable_cors: true` and no `api_key`; nothing sent a key. |
| **Fix location** | `scripts/infra-start.ps1`, `core/config.py`, `docker-compose.yml` |
| **Actual change** | Launcher generates a key, persists it, enforces it, and rewrites an existing config so the fix takes effect. The backend reads the same file. CORS off. |
| **Regression test** | `test_release_freeze_deployment.py` — 13 cases |
| **Measured** | 13 passed. Mutation (`enable_cors: true`) → **1 failed**. |
| **Residual risk** | **The runtime behaviour is not verified**: this machine has no Docker and the dev Qdrant was not restarted with a key. Static assertions only. |
| **Verdict** | **PASS (static)** / **BLOCKED (runtime)** |
| **Found by this review** | The launcher wrote the key to `<repo>/.runtime`, which on an **installed** copy is inside the program directory while the backend reads the *runtime* directory — so the key would not have been found and every vector call would have 401'd. Fixed; see "Defects found by this review". |

### RF-11 — one valid citation marked the whole answer grounded

| | |
|---|---|
| **Original** | `grounded = bool(used_ids)`. |
| **Fix location** | `ai/rag/pipeline.py`, `api/v1/ai.py`, `README.md`, `docs/rag.md` |
| **Actual change** | `analyse_citations` splits the answer into claim-bearing units and requires every one to cite a real source with no dangling markers. One retry when ungrounded (skipped for the offline engine). Ungrounded answers carry an explicit `⚠️ 未验证` note. `retrieved_sources`, `used_citation_ids` and `grounded` are separate fields. |
| **Regression test** | `test_release_freeze_rag_citations.py` — 14 cases |
| **Measured** | 14 passed. Mutation (`supported_units >= 1`) → **1 failed**. |
| **Residual risk** | `grounded` is **structural**, not fact-level entailment. The code and the docs both say so; nothing claims otherwise. |
| **Verdict** | **PASS** |

### RF-12 — verification scripts killed the user's running application

| | |
|---|---|
| **Original** | Five scripts used `taskkill /F /IM "AI Workbench.exe"`, which stops every instance on the machine. Four sites used `shell=True` with an f-string for the installer. |
| **Fix location** | `scripts/_process_guard.py` (new) + the five scripts |
| **Actual change** | `stop_app_processes(roots)` stops only processes whose executable path is under a root the test owns, by PID. `run_nsis_installer` keeps the raw command line NSIS needs and refuses a path cmd would reinterpret. |
| **Regression test** | `test_release_freeze_process_safety.py` — 31 cases |
| **Measured** | 31 passed. Mutation (restore the `taskkill /IM` line) → **1 failed**. |
| **Residual risk** | The scoped lookup uses `Get-CimInstance Win32_Process`; on a host where that is unavailable it returns an empty list and stops nothing, which fails safe (the test then reports the app still running). |
| **Verdict** | **PASS** |
| **Found by this review** | The first version of the replacement passed the command line as `["cmd", "/C", …]`. Python escaped the inner quotes, cmd received `\"C:\…\Setup.exe\"` as the program name, and the install silently did not happen — the acceptance run reported "not installed", three layers from the cause. Fixed by passing a **string** to `subprocess.run(..., shell=False)`, which Windows hands to `CreateProcess` verbatim. The regression test now asserts the argument is a string and that no `cmd` is in the middle. |

### RF-13 — four versions for one product

| | |
|---|---|
| **Original** | Tauri `2.0.0`, installer `2.1.0`, backend `1.0.0`, frontend `1.0.0`, changelog `1.1.0`. |
| **Fix location** | `VERSION` (new), `scripts/sync_version.py` (new), `core/config.py`, `desktop/**`, `installer/ai-workbench.nsi`, `CHANGELOG.md`, `scripts/build_installer_payload.py` |
| **Actual change** | `VERSION` is the source; the sync script propagates and `--check` verifies. Freeze version **2.2.0**. `VERSION` added to the payload. `pytest`'s duplicate `-q` removed. |
| **Regression test** | `test_release_freeze_versioning.py` — 15 cases |
| **Measured** | 15 passed. Mutation (set `desktop/package.json` to 1.0.0) → **1 failed**. |
| **Residual risk** | `Cargo.lock` changed during the release build (a dependency resolved differently); it is committed and consistent. |
| **Verdict** | **PASS** |
| **Found by this review** | `VERSION` was added to the payload but **not to the NSIS uninstall list**, so a fresh uninstall left the install directory behind containing exactly that one file. Fixed. |

### RF-14 — docker-compose published everything on every interface

| | |
|---|---|
| **Original** | PostgreSQL, Redis, Qdrant, API and frontend published as `${PORT}:PORT`; PostgreSQL's password defaulted to `aiworkbench`; Redis and Qdrant had no authentication. |
| **Fix location** | `docker-compose.yml` |
| **Actual change** | Data services not published at all; API and frontend on `127.0.0.1`; `POSTGRES_PASSWORD`, `REDIS_PASSWORD` and `QDRANT_API_KEY` required (`:?`); Qdrant CORS off. |
| **Regression test** | `test_release_freeze_deployment.py` — 7 static assertions |
| **Measured** | 13 passed. Mutation (restore `${API_PORT}:8000`) → **1 failed**. |
| **Residual risk** | **Never executed.** No Docker on this machine. The file is asserted statically and by `yaml.safe_load`; it is not known to start. |
| **Verdict** | **PASS (static)** / **BLOCKED (runtime)** |

### RF-15 — a live API key sat in a commented line of the development `.env`

| | |
|---|---|
| **Original** | A `sk-`-prefixed key in a commented-out `DEEPSEEK_API_KEY=` line. Git-ignored, so never in Git, but on disk. |
| **Fix location** | `.env` (working copy only) |
| **Actual change** | The value was removed; the comment is kept. Not reproduced in any repository file or report. |
| **Regression test** | Not applicable — this is a working-copy state, not code. Covered by the sweep below. |
| **Measured** | Sweep of 8 surfaces (app, tests, scripts, templates, installer payload, built installer, runtime logs, frontend source): **0 suspects**. `test_project_integrity.py`: 146 passed, including the full-Git-history scan. |
| **Residual risk** | **Whether the key was revoked at the vendor is unknown.** This round makes no real model calls, so it cannot be checked. Recorded as "removed from the working copy; revocation status unknown". |
| **Verdict** | **MITIGATED — rotation unconfirmed** |

### RF-16 — `GET /delivery/states` had no authentication

| | |
|---|---|
| **Original** | The route took no `CurrentUser`. Its body is a static enum list, so nothing leaked, but it was the one place a later edit could have put something real without anyone noticing the dependency was missing. |
| **Fix location** | `api/v1/delivery.py` |
| **Actual change** | Takes `CurrentUser`; the docstring records that it does not use the value and why it takes it anyway. |
| **Regression test** | `test_release_freeze_ownership.py` — 152 routes swept |
| **Measured** | All passed. Mutation (remove the parameter) → **1 failed**. |
| **Verdict** | **PASS** |

### RF-17 — interrupted runs were flattened to `FAILED`

| | |
|---|---|
| **Original** | Startup marked every `RUNNING` agent run `FAILED`, which cannot distinguish "died mid-run" from "produced its file but did not write the final status". |
| **Fix location** | `main.py::_reconcile_interrupted_agent_runs`, `ai/agent/plan.py` |
| **Actual change** | Runs are marked `INTERRUPTED`; `RunState.INTERRUPTED` was added so the state and the status agree; the scratchpad state is reassigned (not mutated in place, which JSONB would not persist). |
| **Regression test** | `scripts/verify_interrupted_runs.py` — real process killed and restarted |
| **Measured** | **21/21**. Interrupted → `INTERRUPTED` with a reason; finished and cancelled runs untouched; reconcile idempotent across a second restart; ownership still enforced (`404 run_not_found`). |
| **Residual risk** | This round marks interrupted runs honestly; it does **not** add heartbeat/checkpoint, so a run cannot be *resumed* — only reported. The requirement allowed either, and the honest-reporting half is what is implemented. |
| **Verdict** | **PASS** (resumption is a separate, unimplemented feature) |
| **Found by this review** | `verify_interrupted_runs.py` kept its own hardcoded list of terminal statuses, so adding `INTERRUPTED` made correct behaviour report as a failure. Both assertions now ask the enum. |

---

## Defects found by this review (not in the original list)

Four, all introduced by this round's own changes and all fixed:

| # | What | How it surfaced | Severity |
|---|---|---|---|
| **N-1** | The Qdrant key was written to `<repo>/.runtime`, which on an installed copy is **inside the program directory**, while the backend reads the *runtime* directory. The key would not have been found and every vector call would have 401'd. It also left a stray file that stopped the uninstaller removing the install directory. | Installer acceptance run: "the install directory is gone" failed, and the leftover directory contained `qdrant-api-key.txt`. | High |
| **N-2** | `VERSION` was added to the payload but not to the NSIS uninstall list, so a fresh uninstall left the install directory behind with exactly that one file in it. | Same run. | Medium |
| **N-3** | `run_nsis_installer` passed `["cmd", "/C", …]`; Python escaped the quotes, cmd saw `\"…Setup.exe\"` as the program name, and the install silently did not happen. | Same run: "installed" failed with no error from the installer. | High (test-only path) |
| **N-4** | `verify_interrupted_runs.py` had its own terminal-status list, so adding `INTERRUPTED` made correct behaviour report as failure. | Running the script after the RF-06 change. | Low (test-only) |

N-1 and N-2 are the same class of mistake: **a file the product creates that nothing else
knows about.** N-1 put a file in the wrong directory; N-2 created one the uninstaller had
never heard of. Both were found by running the installer end to end, which is the only way
either could have been found.

---

## What this review did **not** verify

Stated so the gap is not mistaken for coverage:

| Item | Why |
|---|---|
| Real model calls of any kind | No API key exists. Everything model-dependent is `BLOCKED_NO_API_KEY`. |
| Docker compose actually starting | No Docker. |
| Qdrant rejecting an unauthenticated request at runtime | Requires restarting the store with a key; the dev store was left running. |
| Multi-monitor DPI | One display. |
| Signing the installer | No certificate. |
| Accessibility (screen reader, focus order) | Not attempted. |
| Qt packaging | Not started; see `NATIVE-UI-MIGRATION.md`. |
