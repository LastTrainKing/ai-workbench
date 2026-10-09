# Release acceptance — four-layer test matrix

Branch `fix/release-freeze-20261009` · commit `8c0270b4b17c7af0d31dcf2dd5f9904eb36561fc` ·
version **2.2.0** · no API key available

Every result below was produced in this round. Nothing is carried forward from an earlier
report. Results are split into five buckets, because four of them are not the same kind of
statement as the first:

1. source and automated tests — **verified**
2. local integration — **verified**
3. real model capability — **BLOCKED_NO_API_KEY / NOT_RUN_NO_API_KEY**
4. hardware or system environment — **BLOCKED**
5. unfixed defects — **listed**

---

## Layer 1 — static

| Check | Command | Result |
|---|---|---|
| Python syntax (backend) | `pytest` collection | **PASS** |
| Python syntax (PoC) | `py_compile desktop-native/**/*.py` | **PASS** |
| Lint (backend) | `ruff check app tests` | **PASS** — all checks passed |
| Lint (PoC) | `ruff check desktop-native/` | **PASS** — all checks passed |
| Format check | `ruff format --check app tests` | **FAIL — pre-existing.** 103 files would be reformatted; an untouched file also fails it, so this check has never passed on this repository. Not churned during a freeze (KI-03). |
| Frontend typecheck | `npm run typecheck` | **PASS** — 0 errors |
| Frontend lint | `npm run lint` | **PASS** — 0 errors, 7 warnings (all pre-existing `react-refresh` / `exhaustive-deps`) |
| Rust compile | `cargo build --release` | **PASS** — `ai-workbench-desktop v2.2.0`, 3m47s |
| QML load | `main.py --self-check` | **PASS** — window, 104 objects, 12 slots |
| Secret scan (8 surfaces) | working tree, tests, scripts, templates, installer payload, built installer, runtime logs, frontend source | **PASS** — 0 suspects |
| Secret scan (git history) | `test_project_integrity.py` | **PASS** — 146 tests, full-history walk included |
| Config template | `.env.example` has no `SECRET_KEY` value | **PASS** |
| Version consistency | `scripts/sync_version.py --check` | **PASS** — five carriers agree on 2.2.0 |

## Layer 2 — automated tests

| Suite | Command | Result |
|---|---|---|
| Backend (full) | `pytest` | **1204 passed, 10 skipped, 0 failed** in 724.80s |
| Frontend | `npm test` | **127 passed, 10 skipped** |
| Database migration | `verify_migration.py` | **19/19** |
| Run persistence across restart | `verify_agent_run_persistence.py` | **18/18** |
| Interrupted-run recovery | `verify_interrupted_runs.py` | **21/21** |
| Snapshot archive / restore / retention | `verify_retention_safety.py` | **15/15** |
| Queue retry and terminal states | `test_release_freeze_run_state.py` | **23 passed** |
| Permission isolation (routes) | `test_release_freeze_ownership.py` | **152 routes + A/B, all passed** |
| Provider isolation | `test_release_freeze_provider_isolation.py` + `_api.py` | **29 passed** |
| RAG citation and grounding | `test_release_freeze_rag_citations.py` | **14 passed** |
| Skill / outbound network security | `test_release_freeze_skill_network.py` | **17 passed** |
| Regression tests actually catch their defects | `verify_release_freeze_mutations.py` | **14/14 caught** |

**Skip accounting (10).** 4 are the deliberately public endpoints in the route sweep; 4 are
a sample-PDF fixture that is not generated; 2 are conditional (not a git repository / git
unavailable). No skip hides a failure.

### Layer 2 warnings

`pytest` emitted 3 warnings: `Api key is used with an insecure connection`. The Qdrant
client sends the API key over `http://127.0.0.1:6333`. That is loopback, so the key never
crosses a network interface, but it does travel in clear text within the machine. Recorded
rather than silenced: it is acceptable for a local service and would not be for a remote
one.

## Layer 3 — local integration

Run against an isolated install directory and an isolated data directory. The real data
directory is verified unchanged at the end of every step.

| # | Step | Result |
|---|---|---|
| 1 | Fresh install into an isolated path | **PASS** |
| 2 | First start, all four services ready | **PASS** — API answered in 37.9s |
| 3 | Schema built by the migration path | **PASS** — `schema_fresh_database \| alembic_head=6068bb13c474` |
| 4 | Installed database records an alembic revision | **PASS** — `6068bb13c474` |
| 5 | Account created and a run written over the installed API | **PASS** |
| 6 | Close: all four ports released | **PASS** |
| 7 | Second start | **PASS** — 21.2s |
| 8 | The run survived the restart | **PASS** — counts identical |
| 9 | Repair entry point, run from the user's own path | **PASS** — exit 0 |
| 10 | Data still there after the repair | **PASS** |
| 11 | Uninstall removes the install directory | **PASS** |
| 12 | Uninstall releases all ports | **PASS** |
| 13 | Real data directory unchanged | **PASS** — mtime and file count identical |
| — | **Total** | **32/32** |

### Native UI proof of concept

`desktop-native/verify_native_ui.py` — **28/28**. Uses the test database and a temporary
account, both removed afterwards.

Covered: window and QML tree; DPI ratio; runtime info from the real settings; a real
business page (create → count in the database → complete → read the stored status); provider
configuration per account with a write-only key; a real agent run recorded in `ai_runs` with
an honest `INCOMPLETE` / `max_steps` result; cancel; file operations through the permission
layer including two refusals; and the named-pipe IPC with its size cap, unknown-method
refusal and structured errors.

### Qdrant authentication, verified at runtime

The dev store was restarted with the launcher's generated key, which also confirmed the
fix for the defect this round introduced (the key had been written into the program
directory instead of the runtime directory):

| Check | Result |
|---|---|
| Key file written to the runtime directory | **PASS** |
| Generated config has `enable_cors: false` | **PASS** |
| Generated config binds `127.0.0.1` | **PASS** |
| Unauthenticated request to Qdrant | **401** |
| Request with the key | **200** |
| Backend reads the same key the launcher wrote | **PASS** |

## Layer 4 — release build

| Item | Value |
|---|---|
| Commit | `8c0270b4b17c7af0d31dcf2dd5f9904eb36561fc` |
| Working tree at build time | clean (`git status --porcelain` = 0 lines) |
| Frontend build | `npm run build` — 40.93s |
| Rust build | `cargo build --release` — 3m47s |
| Installer | `scripts/build_installer.py --build` |
| Payload | 8,748 files / 444.0 MB |
| **Artifact** | `build/installer/AI-Workbench-Setup.exe` |
| **Bytes** | **97,311,474** (92.8 MB) |
| **SHA-256** | `698db63662cc24e42873884077ada51b7b97c5ccd45ab3616acd15ad2df0d3d7` |
| Version in payload | `VERSION` = 2.2.0 |
| Config template in payload | `.env.example` present, no key value |
| Shell in payload | `AI Workbench.exe` present |
| Reproducible | **Yes** — rebuilt from the clean tree, byte-identical hash |
| Signature | **NOT SIGNED.** No certificate on this machine. See below. |

### The artifact is not signed

No Authenticode signature. Windows SmartScreen will warn on first run. This is recorded as
a release blocker in `KNOWN_ISSUES.md` KI-06, not glossed over. A signing certificate is
required to close it; nothing in this repository can substitute for one.

---

## 3 — BLOCKED_NO_API_KEY / NOT_RUN_NO_API_KEY

No real API key exists; the previous one was closed by the user. **No work was stopped
because of this** — everything that does not need a model was completed. These items are
marked, not passed, and none of them was faked with a mock that only returns a success
string.

Mock and offline providers **were** used, and they verify real business state: the agent
run in the PoC is checked against `ai_runs` and is required to report `INCOMPLETE` /
`max_steps` rather than a success. What a mock cannot do is prove the vendor's behaviour.

| # | Not verified | Why it cannot be | Minimum test when a key arrives |
|---|---|---|---|
| 1 | Real provider returns a valid completion | No key | One `POST /chat/rag` against DeepSeek; assert a non-empty answer, `ai_runs` row `SUCCEEDED`, token counts non-zero |
| 2 | Provider timeout handling | No key | Point `base_url` at a black-hole address; assert `TIMEOUT`, not a hang, and that the row records it |
| 3 | Provider network failure | No key | Point `base_url` at a closed port; assert a classified error and no retry storm |
| 4 | Invalid / malformed provider response | No key | Point `base_url` at a stub returning non-JSON; assert a handled error, not a crash |
| 5 | Tool-calling against a real model | No key | One agent run whose goal requires a tool; assert the tool ran and the observation was fed back |
| 6 | RAG grounding with a real model | No key | Ask a question answerable from an uploaded document; assert `grounded` is true **and** every claim carries a valid citation |
| 7 | Empty-response handling | No key | Stub returning empty content; assert `INCOMPLETE` / `empty_response` |
| 8 | Loop detection against a real model | No key | Stub repeating one identical tool call; assert `INCOMPLETE` / `loop_detected` |
| 9 | Max-steps against a real model | No key | Stub that never concludes; assert `INCOMPLETE` / `max_steps` |
| 10 | Task interruption mid-run with a real model | No key | Kill the process during a real run; assert `INTERRUPTED` on restart and that produced files are still on disk |
| 11 | Embedding with a real provider | No key | Configure a real embedding endpoint; assert the collection dimension, and that switching dimensions does not mix old vectors |
| 12 | Key rotation through the Settings UI | No key | Enter a new key in the UI, save, restart, confirm it persists and the old one is gone |
| 13 | Whether the old key was revoked | No key, and no live call permitted | Check the vendor dashboard |

**Items 2, 3, 4, 7, 8 and 9 do not actually need a real key** — they need a controllable
endpoint, which can be a local stub server. They are listed here because they were not run
this round, not because they are impossible. That distinction matters for the next round:
six of the thirteen can be closed without any key at all.

## 4 — BLOCKED by hardware or system environment

| # | Item | Blocked by | What would close it |
|---|---|---|---|
| 1 | Multi-monitor DPI: 100/125/150/175%, mixed DPI, hot-plug | One display, fixed scale | A machine with two displays at different scale factors, or a VM with controllable DPI |
| 2 | `docker compose` actually starting | No Docker installed; registry unreachable | A host with Docker and registry access |
| 3 | Installer Authenticode signature | No certificate | A trusted code-signing certificate |
| 4 | Symlink/junction escape at SYSTEM level | Requires elevation | An elevated shell on NTFS with real reparse points |
| 5 | Clean-room install on a machine that never had the dev tree | This machine has the dev tree | A fresh Windows image |
| 6 | Qt packaging (PyInstaller/NSIS for the native UI) | Not started — see `NATIVE-UI-MIGRATION.md` | The W6 workstream |

## 5 — Unfixed defects

| # | Defect | Severity | Status |
|---|---|---|---|
| RF-01 | The desktop UI is a WebView2 page, not a native interface | **Release blocker** | Not fixed. Runnable PoC built, migration estimated. See `NATIVE-UI-MIGRATION.md`. |
| KI-02 | `tauri.conf.json` CSP is `null` | Medium | Not fixed. Deliberate — the layer is being replaced; a guessed policy is worse than none. |
| KI-03 | `ruff format --check` fails on 103 files | Low | Pre-existing, not introduced here. |
| — | `verify_installer_acceptance.py` leaves ~50 MB of test data in `%LOCALAPPDATA%\AI Workbench Data TEST` | Low | Non-blocking. Registered for the next version per the discipline rule; the residue from this round's runs was removed. |

## Summary

| Bucket | Count |
|---|---|
| Verified — source and automated | 17 checks, 1204 + 127 + 130 script assertions |
| Verified — local integration | 32 + 28 + 6 runtime checks |
| BLOCKED_NO_API_KEY | 13 (6 of which need only a local stub) |
| BLOCKED — hardware / environment | 6 |
| Unfixed defects | 2 real (one a release blocker) + 1 pre-existing + 1 registered |

**Nothing in the BLOCKED buckets was written as a pass, and no assertion was relaxed to
make a suite green.** The one pre-existing assertion that changed asserted the defect it
was meant to catch; that is documented in `RELEASE-FREEZE-REVIEW.md` under RF-04.
