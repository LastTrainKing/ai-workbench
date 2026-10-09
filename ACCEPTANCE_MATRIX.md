# ACCEPTANCE MATRIX — AI Workbench release freeze

> **Superseded by `RELEASE-ACCEPTANCE.md`.** This matrix covers the first fix round. The
> second round re-ran everything, rebuilt the installer from the final commit, and ran a
> fresh install end to end; its results are in `RELEASE-ACCEPTANCE.md`, and its verdict is
> in `FINAL-VERDICT.md`. This file is kept because it records what was true at that point —
> several rows below were `NOT RUN` there and are `PASS` now.

Branch `fix/release-freeze-20261009` · version **2.2.0** · base `682ed8d`

**Verdict vocabulary is exactly four values.** No "mostly", no "verified except". A `PASS`
means it was executed in this round and the evidence is named. A `PASS` from an earlier
report is not carried forward.

## Verdict

> **DEFER FREEZE — one clear blocking defect (RF-01), plus four items BLOCKED by the
> absence of hardware, Docker or a signing certificate on this machine.**
>
> Every defect that could be confirmed and fixed in this environment has been fixed and is
> covered by a test that fails on the previous code. What remains is not "more work on the
> same thing": RF-01 is an architecture change, and the BLOCKED items need a machine this
> one is not.

---

## 1. Security

| # | Item | Result | Evidence |
|---|---|---|---|
| 1.1 | Password hashing does not truncate; legacy hashes still work and upgrade | **PASS** | `pytest tests/unit/test_release_freeze_security.py tests/api/test_release_freeze_auth.py` → 37 passed. Includes the old rule kept as a witness and a byte-72 boundary case. |
| 1.2 | Logout revokes durably; storage outage fails closed | **PASS** | Same run. Three-state answer asserted directly; cache-cleared revocation asserted; valid token not locked out. |
| 1.3 | Production refuses every placeholder `SECRET_KEY` | **PASS** | `pytest tests/unit/test_release_freeze_security.py` → 29 passed, 10 parametrised placeholders. |
| 1.4 | A fresh install is a production install with a random key | **PASS** | `pytest tests/unit/test_release_freeze_production_env.py` → 10 passed; generated key fed back through `Settings(app_env="prod")`. |
| 1.5 | Existing `.env` is never overwritten on upgrade/repair | **PASS** | `test_an_existing_file_is_never_touched` (3 calls, byte-for-byte). |
| 1.6 | Skill discovery denies without a policy; redirects re-authorised per hop | **PASS** | `pytest tests/unit/test_release_freeze_skill_network.py` → 17 passed. |
| 1.7 | AI provider config is per account; key never returned | **PASS** | `pytest tests/unit/test_release_freeze_provider_isolation.py tests/api/test_release_freeze_provider_api.py` → 29 passed. |
| 1.8 | Provider base URL validated (scheme/host/userinfo) | **PASS** | Same run, 13 parametrised URLs. |
| 1.9 | Every route requires authentication | **PASS** | `pytest tests/api/test_release_freeze_ownership.py` → 152 routes swept, 4 deliberately public. Found and fixed one uncovered route. |
| 1.10 | Cross-account ownership on read/update/delete/list | **PASS** | Same file: projects, KBs, tasks, notes, list filtering, foreign parent id. |
| 1.11 | Secret scan — working tree and full Git history | **PASS** | `pytest tests/unit/test_project_integrity.py` → all passed. Two commits were rewritten so the synthetic test fixtures never entered history. |
| 1.12 | No credential in the working `.env` | **PASS** | The one live-looking value was removed; `# DEEPSEEK_API_KEY=` is now empty. **Vendor revocation status: NOT CONFIRMED** (no live call permitted this round). |
| 1.13 | Qdrant requires an API key; CORS off | **PASS (static)** | `pytest tests/unit/test_release_freeze_deployment.py` → 13 passed. **Runtime: BLOCKED** — see 7.2. |
| 1.14 | Tauri/WebView2 CSP is not `null` | **FAIL** | `desktop/src-tauri/tauri.conf.json` still has `"csp": null`. Left deliberately: see `KNOWN_ISSUES.md` KI-01. |
| 1.15 | Password length limits consistent and non-truncating | **PASS** | Register and login share one ceiling; over-length rejected with 422. |

## 2. Correctness

| # | Item | Result | Evidence |
|---|---|---|---|
| 2.1 | A stopped agent is not recorded as a success | **PASS** | `pytest tests/integration/test_release_freeze_run_state.py` → 23 passed. Real executor, looping stub, asserts `ai_runs` + `agent_runs` + payload agree. |
| 2.2 | Deliverable success verified against the file | **PASS** | Same file; missing and empty files both rejected. |
| 2.3 | Terminal state written once | **PASS** | `RunRecorder.finish` guards `_finished`; asserted via the executor tests. |
| 2.4 | A crashed job is bounded by `max_attempts` | **PASS** | Same file: exhausted → terminal `FAILED`, not claimed again, document marked failed with it. |
| 2.5 | Interrupted runs reported as `INTERRUPTED`, not `FAILED` | **PASS** | `scripts/verify_interrupted_runs.py` → **21/21**: real process killed and restarted, interrupted run becomes `INTERRUPTED` with a reason, finished and cancelled runs untouched, reconcile idempotent across a second restart, ownership still enforced. |
| 2.8 | Run history survives a real restart | **PASS** | `scripts/verify_agent_run_persistence.py` → **18/18**: writes a run, kills the process, restarts, reads it back identical, not claimed live, other account still refused with `run_not_found`. |
| 2.6 | RAG grounding requires every claim to cite a real source | **PASS** | `pytest tests/unit/test_release_freeze_rag_citations.py` → 14 passed. |
| 2.7 | Offline provider cannot be marked grounded | **PASS** | `test_no_retrieved_sources_cannot_be_grounded`, plus the integration RAG tests still pass. |

## 3. Build, version and delivery

| # | Item | Result | Evidence |
|---|---|---|---|
| 3.1 | One version across shell, installer, backend, frontend, changelog | **PASS** | `pytest tests/unit/test_release_freeze_versioning.py` → 15 passed; `scripts/sync_version.py --check` exits 0. |
| 3.2 | `pytest` summary is readable | **PASS** | `addopts` has no `-q`; the full run printed `1204 passed, 10 skipped`. |
| 3.3 | Installer compiles after the `.env` change | **PASS** | `scripts/check_installer_syntax.py` → PASSED, no fatal warnings. |
| 3.4 | Backend regression | **PASS** | `1204 passed, 10 skipped, 0 failed in 575s`. Log: `build/release-freeze/backend-tests.log`. Baseline was `711 passed, 6 skipped`. |
| 3.5 | Frontend typecheck | **PASS** | `npm run typecheck` → 0 errors. |
| 3.6 | Frontend lint | **PASS** | `npm run lint` → 0 errors, 7 warnings (all pre-existing, all `react-refresh`/`exhaustive-deps`). |
| 3.7 | Frontend unit tests | **PASS** | `npm test` → 127 passed, 10 skipped. |
| 3.8 | `ruff check app tests` | **PASS** | All checks passed. |
| 3.9 | `ruff format --check app tests` | **FAIL** | 103 files would be reformatted — **pre-existing**; an untouched file (`app/core/logging.py`) also fails, so this check has never passed on this repo. Not reformatted during a freeze: see `KNOWN_ISSUES.md` KI-03. |
| 3.10 | Installer and ZIP rebuilt from the freeze commit | **NOT RUN** | Requires a full Tauri release build (Rust toolchain, ~440 MB payload). Not attempted this round. |
| 3.11 | Artifact SHA-256 recorded | **NOT RUN** | Depends on 3.10. |
| 3.12 | Installer Authenticode signature | **BLOCKED** | No code-signing certificate. |
| 3.13 | Clean-room install on a machine that never had the dev tree | **NOT RUN** | Requires the artifact from 3.10. |

## 4. Data safety

| # | Item | Result | Evidence |
|---|---|---|---|
| 4.1 | Real installed user data unchanged | **PASS** | `%LOCALAPPDATA%\AI Workbench Data`: **1530 files / 51,775,575 bytes** before and after — identical. `build/release-freeze/data-baseline-{before,after}.json`. |
| 4.2 | Development `.runtime` not destructively modified | **PASS (with a note)** | File count grew 24,156 → 59,430. The growth is the test suite's own footprint: `pgdata` (test database), `test-storage`, `logs`. No user document, snapshot or config was deleted or rewritten. |
| 4.3 | No destructive operation on real data | **PASS** | Every test uses `TEST_DATA_DIR`/`aiworkbench_test`; `scripts/_test_data_guard.py` exits 3 on path overlap. |
| 4.4 | Migration: fresh / upgrade-with-data / repeated start / drift refusal | **PASS** | `scripts/verify_migration.py` → **19/19** on a scratch database. Includes the new `revoked_tokens` migration (34 tables), an upgrade from the previous head with a row preserved, and a drift case that is refused rather than stamped. |
| 4.5 | Retention `--apply` on an isolated copy; real store untouched | **PASS** | `scripts/verify_retention_safety.py` → **15/15**. Planning left the real store byte-identical; archive → verify → delete → restore round-tripped with every checksum matching; a tampered archive restored nothing. Real store digest `a0730ac61dbf296f…`, 16,493 files, 3,021 audit rows — unchanged. |

## 5. Regression tests added

| # | Item | Result |
|---|---|---|
| 5.1 | Every fix has a test that fails on the pre-fix code | **PASS** |
| 5.2 | No assertion weakened to make a test pass | **PASS** — one pre-existing assertion changed; it asserted the defect. Documented in RF-04. |
| 5.3 | Every skip has a stated reason | **PASS** — 10 skips: 4 deliberately-public endpoints, 4 sample-PDF fixture not generated, 2 conditional (not a git repo / git unavailable). |
| 5.4 | Zero real model calls | **PASS** — `AI_PROVIDER=offline` forced in `conftest.py`; agent/RAG tests use stubs. |

## 6. Product requirement

| # | Item | Result |
|---|---|---|
| 6.1 | Desktop UI is native, not an embedded web page | **FAIL** — RF-01. Tauri + WebView2 + React. |
| 6.2 | No UI-facing localhost web page in the production desktop path | **FAIL** — same root cause. |
| 6.3 | Documentation matches the code | **PASS** — README "citation-enforced" removed; "no browser" corrected to state WebView2 and name RF-01; `docs/api.md`/`docs/deployment.md` demo-account claims corrected; `docs/rag.md` describes the real guarantee. |

## 7. BLOCKED — needs something this machine does not have

| # | Item | Blocked by | What would be needed |
|---|---|---|---|
| 7.1 | Multi-monitor DPI (100/125/150/175%, mixed DPI, hot-plug) | No second display, no controllable DPI | A machine with two displays at different scale factors, or a VM where the DPI can be changed. |
| 7.2 | `docker compose` actually starts and serves | No Docker installed; registry unreachable | A host with Docker and registry access. The static security fixes are done and asserted. |
| 7.3 | Installer code signing | No certificate | A trusted Authenticode certificate. |
| 7.4 | Symlink/junction escape at SYSTEM level | Requires elevation this session does not hold | An elevated shell on NTFS with real reparse points. WORKSPACE-level behaviour is verified. |

## 8. Summary counts

| Result | Count | Where |
|---|---|---|
| PASS | 40 | §1 (14), §2 (8), §3 (8), §4 (5), §5 (4), §6 (1) |
| FAIL | 4 | 1.14 CSP, 3.9 `ruff format` baseline, 6.1 and 6.2 (both RF-01) |
| BLOCKED | 4 | 7.1 DPI, 7.2 Docker, 7.3 signing certificate, 7.4 SYSTEM-level symlink |
| NOT RUN | 3 | 3.10 artifact rebuild, 3.11 hashes, 3.13 clean-room install |

The four FAILs are three distinct facts:

- **RF-01** (6.1, 6.2) — the release blocker. An architecture change, not a fix.
- **1.14 CSP is `null`** — deliberately left rather than half-done; see `KNOWN_ISSUES.md` KI-01.
- **3.9 `ruff format --check`** — a pre-existing repo condition that this round did not
  introduce (an untouched file also fails it). Not churned during a freeze; see KI-03.

The three NOT RUN items are all downstream of 3.10: rebuilding the installer and ZIP needs
a full Rust/Tauri release build that was not attempted this round, so the hashes and the
clean-room install cannot run either.

The four verification scripts are executed against **scratch** databases and an isolated
copy of the snapshot store. The development database and the real snapshot store were not
written to: the retention run reports the real store byte-identical before and after, by
digest.

