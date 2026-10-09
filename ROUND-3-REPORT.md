# Round 3 — stub-provider tests and an independent audit of `desktop-native`

Branch `fix/release-freeze-20261009` · base commit `be88cb3` · **no API key used, requested
or required**

Two workstreams. The first closed six behaviours that had been carried as
`BLOCKED_NO_API_KEY` and were never actually impossible — they needed a local endpoint, not a
key. Building them found **two real defects**, both fixed. The second is an executable audit
of the native UI proof of concept: 25 checks, all passing, none of them a claim taken on
trust.

---

## 1. The six provider-failure tests

### What was built

`backend/tests/integration/_stub_provider.py` is a **real HTTP server on loopback**, not a
mock object. That distinction is the whole point: every one of the six behaviours lives in
the transport or the parsing — a timeout, a 503, a body that is not JSON, a response with no
`choices` — and a monkeypatched provider would skip all of them. The tests drive the real
`OpenAICompatibleProvider` over a real socket.

`_agent_child.py` is a separate process, because "the process died mid-run" cannot be tested
in-process: there is always a process left to clean up after itself. The parent spawns the
child, waits until the run row says `RUNNING`, kills it, and then runs the same
reconciliation the application runs at startup.

One change was needed in the product to make this possible. `guard()` blocked every
non-offline provider, which blocked the stub. Its own stated rule is *"anything that could
leave the machine"*, and a server on `127.0.0.1` cannot — so the guard now allows loopback
and still blocks everything else. The check is on the **host**, not on a flag the caller
sets, so a provider aimed at a vendor cannot opt itself in. That is asserted.

### Results — 9 cases in `test_release_freeze_stub_provider.py`

| # | Behaviour | Asserted | Result |
|---|---|---|---|
| 1 | Empty response | `INCOMPLETE` / `empty_response` in `ai_runs`, `agent_runs` and the payload; the explanation is still shown to the user | **PASS** |
| 2 | Response with no `choices` | `AIResponseFormatError`; run `FAILED` with an error code; **distinct from an empty answer** | **PASS** |
| 3 | Body that is not JSON | `AIResponseFormatError`; run `FAILED`; distinct from a wrong-shaped body | **PASS** |
| 4 | Same tool call repeatedly | `INCOMPLETE` / `loop_detected`; the stub was asked **twice**, so the guard fired before the ceiling | **PASS** |
| 5 | A different tool call each time | `INCOMPLETE` / `max_steps`; exactly 3 steps | **PASS** |
| 6 | Process killed mid-run | Real child killed; reconcile marks `INTERRUPTED`; scratchpad state agrees with the status | **PASS** |
| 7 | HTTP 503 from the provider | `AIProviderError`; run `FAILED`, not `SUCCEEDED` | **PASS** |
| 8 | Provider that never answers | `AITimeoutError`; gives up in ~1s, does not hang | **PASS** |
| 9 | Guard on the guard | a vendor endpoint is still blocked; a loopback one is not | **PASS** |

Cases 7–9 were on the previous round's "needs no key, only a stub" list. They are closed
rather than carried.

---

## 2. Two defects found, both fixed

Neither was reachable by reading the code. Both needed a real endpoint and, for the second,
a real process.

### D-1 — A failed AI run left **no record at all**

**What happens.** The executor writes `status=FAILED`, an error code and an error message,
then re-raises. Every API route uses `session_scope`, which rolls back on any exception — so
the rollback erased exactly the record that code exists to write.

**Proven, not inferred.** `test_release_freeze_run_record.py` posts to `/agents/run` with a
stub returning 503, then counts rows: **zero**. The success path records exactly once, which
is asserted too, so the fix cannot double-write.

**Why it matters.** The product states the guarantee itself — every AI invocation is recorded
"so the product can answer *what did the AI actually do?*" — and `tracing.py` promises a run
survives partial failure: *"if the model times out at step 4 of an agent loop, we still want
the first three tool calls"*. It was the failure case, the one where the record matters most,
that was being dropped.

**Fix.** `persist_terminal_record(session, context=…)` in `tracing.py`, called from the
executor's and the RAG pipeline's error paths before the exception leaves. The trade-off is
in its docstring rather than left implicit: committing also commits any business write the
tools performed before the failure. The alternative is losing the record of work that
demonstrably happened and leaving the database disagreeing with what the agent already told
the user.

### D-2 — A running run was invisible, so a crash left nothing to reconcile

**What happens.** The run row was only ever `flush()`ed, never committed, until the run
finished. So:

* a live run did not appear in the run history;
* a process killed mid-run left **no row at all** — which made
  `_reconcile_interrupted_agent_runs` unreachable for a real crash. The machinery built to
  mark an interrupted run `INTERRUPTED` had nothing to find, because the row it exists for
  had never been written.

The previous round's `verify_interrupted_runs.py` passed 21/21 because it *seeds* a
committed `RUNNING` row. That tests the reconcile, not the path that produces the row.

**Fix.** The executor commits when the run starts and after each step. The start commit makes
the run visible and reconcilable; the per-step commit is what makes the recorder's own
promise true.

**Evidence.** The kill test only became possible after this: before the fix the child never
produced a `RUNNING` row for the parent to find.

---

## 3. Independent audit of `desktop-native`

`desktop-native/audit_native_ui.py` — **25/25 passed**, exit code 0. It is a script, not a
narrative: every claim in the PoC's README is checked by execution where execution is
possible.

### 3.1 Does it really go through the in-process service layer?

| Check | Result |
|---|---|
| No HTTP client imported anywhere in the PoC | **PASS** — 6 Python files, AST-scanned for `httpx`, `requests`, `aiohttp`, `urllib.request`, `http.client` |
| Nothing targets the old API port or `/api/v1` prefix | **PASS** — scanned over **code**, with comments and docstrings stripped |
| A real bridge call opens no connection to the API port | **PASS** — `socket.connect` is spied during the call: 1 connection total (the database), 0 to `:8000` |

The socket spy is the check that matters. A README saying "no HTTP" is worth nothing next to
a test that fails if a socket opens.

### 3.2 Does it depend on WebView2, a browser, or the old web page?

| Check | Result |
|---|---|
| No browser or WebView component referenced | **PASS** — scanned for `QWebEngine`, `WebView`, `WebEngineView`, `QtWebKit`, `selenium`, `playwright` |
| The React build is not loaded by the native shell | **PASS** |
| No HTML page is shipped as the interface | **PASS** |
| The bridge is a plain Python object, not a server | **PASS** — 35 methods, none of them `do_*` |

### 3.3 Do file operations go through `PermissionManager`?

| Check | Result |
|---|---|
| A workspace path is written | **PASS** |
| A path outside the allowed roots is refused | **PASS** — `PermissionError: outside the workspace and the allowed directories` |
| …and nothing was written | **PASS** |
| A protected path is refused | **PASS** — `This location is on the denied list` |
| Opening a missing file is reported, not pretended | **PASS** — `FileNotFoundError` |

The UI is not a bypass: the same policy the agent's own file tools run through.

### 3.4 Named pipe IPC

| Check | Result |
|---|---|
| A request round-trips | **PASS** |
| An oversized frame is refused, not truncated | **PASS** — `message_too_large`, cap 1,048,576 bytes, enforced by `recv_bytes(maxlength)` |
| The pipe survives a refused frame | **PASS** — a bad frame does not poison the connection |
| An unknown method is refused, twice | **PASS** — `unknown_method`; the registry is closed, so the pipe is not an arbitrary-code channel |
| A handler error is structured, not a traceback | **PASS** — `handler_error` |
| A slow call times out | **PASS** — `timeout` after 1.0s |
| The server survives a client that hangs up mid-frame | **PASS** |
| Request ids are echoed | **PASS** — 6 served |

### 3.5 Page-by-page comparison with the old interface

Measured, not estimated: **25 pages** in `frontend/src/pages`, **4** in the PoC.

| Status | Count | Pages |
|---|---|---|
| Ported | 3 | `DashboardPage`, `SettingsPage`, `TasksPage` |
| Started but not equivalent | 1 | `RunsPage` — the PoC runs an agent and shows the outcome; the old page has filters, pagination and per-step tool detail |
| **Not started** | **21** | `AIRunDetailPage`, `AIRunsPage`, `AgentPage`, `AgentPermissionsPage`, `AgentWorkbenchPage`, `ChatPage`, `ConversationPage`, `DocumentsPage`, `GraphPage`, `HomePage`, `KnowledgeDocumentsPage`, `KnowledgePage`, `LoginPage`, `MyAIPage`, `NotFoundPage`, `NotesPage`, `OnboardingPage`, `PlannerPage`, `ProjectDetailPage`, `ProjectsPage`, `SearchPage`, `WorkbenchPage` |

**What the PoC does not have, stated plainly** — this is a prototype, and the audit asserts
that the README says so rather than implying coverage:

* **No mock data.** Every number on every PoC page comes from a real query. There is no
  placeholder JSON and no hardcoded list.
* **No placeholder pages.** The four pages are implemented, not stubbed.
* **`RunsPage` is not feature-equivalent** to the old `AgentPage`/`AgentWorkbenchPage` — it
  is a working proof that a run can be started, streamed and cancelled, not a port.
* **Not verified in the PoC:** keyboard navigation beyond the shortcut keys, accessibility,
  multi-monitor DPI, streaming text into the conversation view (no page for it), the graph
  page, onboarding, and the login flow — the PoC uses a local session, so there is no login.
* **Not started:** packaging. The Qt runtime is not bundled and there is no installer path.

---

## 4. Delivery safety

| Requirement | How it was held |
|---|---|
| Do not overwrite the only 2.2.0 archive | The artifact is untouched and a second copy exists at `archive/releases/AI-Workbench-2.2.0-final.exe`, same SHA-256 `698db636…`. No installer was rebuilt this round. |
| Do not delete or reset user data | `%LOCALAPPDATA%\AI Workbench Data` verified unchanged. Every test used the test database and an isolated data directory. |
| No key in source, tests, reports, logs, packages or Git history | No key exists or was used. The single credential-shaped string in the new tests is `sk-fake-stub-0000000000-…`, marked synthetic, and the repository's own scanner passes. |
| No unrelated large refactor | Four product files changed, each for a stated defect. No formatting sweep, no dependency change. |
| Do not delete, skip or weaken tests | No test was removed or skipped. Three new test files were added; the only test edits were adding assertions. |
| Do not claim unverified work as done | DPI, real model calls and signing are still `BLOCKED` and are still listed as such. |

---

## 5. Files changed

| File | Change |
|---|---|
| `backend/app/ai/tracing.py` | `persist_terminal_record()` — the terminal record survives the caller's rollback |
| `backend/app/ai/agent/executor.py` | commit at run start and per step; commit the failure record before re-raising |
| `backend/app/ai/rag/pipeline.py` | commit the failure record before re-raising |
| `backend/app/ai/providers/guard.py` | allow a loopback endpoint; still block everything else; assert the distinction |
| `backend/tests/integration/_stub_provider.py` | new — a scripted OpenAI-compatible endpoint on loopback |
| `backend/tests/integration/_agent_child.py` | new — a run in its own process, for the kill test |
| `backend/tests/integration/test_release_freeze_stub_provider.py` | new — 9 cases |
| `backend/tests/api/test_release_freeze_run_record.py` | new — 3 HTTP-level cases for D-1 |
| `desktop-native/audit_native_ui.py` | new — the audit, 25 checks |

## 6. Commands and results

```
cd backend
..\.venv\Scripts\python.exe -m pytest tests/integration/test_release_freeze_stub_provider.py
  → 9 passed
..\.venv\Scripts\python.exe -m pytest tests/api/test_release_freeze_run_record.py
  → 3 passed
..\.venv\Scripts\python.exe -m pytest tests/unit tests/api
  → 986 passed, 4 skipped, 0 failed
..\.venv\Scripts\ruff.exe check app tests
  → All checks passed

cd ..
.venv\Scripts\python.exe desktop-native\audit_native_ui.py
  → 25/25 passed
.venv\Scripts\python.exe desktop-native\verify_native_ui.py
  → 28/28 passed
```

## 7. Failures encountered and how each was resolved

| # | Failure | Cause | Resolution |
|---|---|---|---|
| 1 | 5 of 9 stub tests failed with "no run row" | **Real defect D-1** — the caller's rollback discarded the record | `persist_terminal_record`; the tests then passed |
| 2 | The kill test could not find a `RUNNING` row | **Real defect D-2** — the row was never committed mid-run | Commit at start and per step |
| 3 | The guard blocked the stub endpoint | The guard blocked every non-offline provider | Loopback exemption, with the vendor case still asserted |
| 4 | 3 audit checks failed on the PoC's own explanatory prose | The scanner searched raw text, so it found the sentences documenting what was removed | Scan code with comments and docstrings stripped |
| 5 | The stripper itself silently matched nothing | `isinstance(x, A \| B \| C \| D)` compiled to something that did not match its own source here | Rewritten without the union form; verified against a known input. An audit whose scanner matches nothing reports PASS, which is the worst outcome, so it is now checked |

## 8. Remaining risks

| Risk | Severity | Note |
|---|---|---|
| **D-1's trade-off** | Medium | Committing the failure record also commits business writes the tools made before the failure. Chosen deliberately over losing the record; if per-tool atomicity is wanted, the tools need their own transaction boundary — a design change, not a bug fix. |
| **RAG runs are still not reconciled** | Low | `_reconcile_interrupted_agent_runs` handles `run_type == AGENT` only. A process killed during a RAG call (up to 90s) leaves no record. Not fixed: it is outside this round's scope and the window is short. |
| **Per-step commit cost** | Low | One extra commit per step, bounded by the step ceiling (≤16). Not measured under load. |
| **The audit's scanner** | Low | Failure 5 means a scanner bug can look like a pass. The stripper now has a known-input check inside the audit, but that class of bug is worth remembering. |
| **21 pages unported, packaging not started** | — | Unchanged. See `NATIVE-UI-MIGRATION.md`. |
| **DPI, real model calls, signing** | — | Still `BLOCKED`. Nothing this round changes that. |

## 9. Effect on the verdict

**The verdict is unchanged: D — `NATIVE_UI_MIGRATION_IN_PROGRESS`.**

What changed is the blocked list. Of the 13 items previously carried as
`BLOCKED_NO_API_KEY`, **six are now closed** (empty response, malformed response, invalid
JSON, loop detection, step ceiling, mid-run kill) plus two adjacent ones (HTTP error,
timeout). Seven remain, and they all genuinely need a real provider: a real completion, tool
calling against a real model, real RAG grounding, real embeddings, key rotation through the
UI, provider-specific error semantics, and whether the old key was revoked.

RF-01 is still the release blocker and is still not fixed.
