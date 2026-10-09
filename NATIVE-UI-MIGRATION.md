# Native UI migration — inventory and re-estimated schedule

Status: **migration not started**. This document is the plan, and the plan is now based on a
running proof of concept rather than on an estimate.

The earlier figure in `KNOWN_ISSUES.md` (KI-01) was **9–15 weeks**, written from reading the
code. It is replaced below by a figure derived from what was actually built and measured.
The two do not agree, and the measured one is the one to use.

---

## 1. What was measured, not estimated

### The proof of concept

`desktop-native/` — runnable, verified, committed.

| | Count |
|---|---|
| Python (bridge, async runner, IPC, entry point, verification) | 1,359 lines |
| QML + JS (shell, 4 pages, 3 components, theme) | 1,193 lines |
| **Total** | **2,552 lines** |
| Pages implemented | 4 |
| Bridge slots exposed to QML | 12 |
| IPC transports proven | 2 (in-process bridge, named pipe) |
| Behavioural checks passing | 28 / 28 |

Pages built: Dashboard (aggregate read), Tasks (read + write + state change), Runs (agent
execution, live progress, cancel), Settings (per-account credential write, file export).

### What is left to migrate

| | Count | Source |
|---|---|---|
| React pages | 25 | `frontend/src/pages/*.tsx` |
| Lines of TSX in those pages | 10,476 | `wc -l` |
| API client code | ~1,832 lines across 5 files | `frontend/src/lib/*.ts` |
| API operations to expose to the UI | 152 operations over 120 paths | `app.openapi()` |

### The honest caveat on the PoC's rate

The PoC pages average ~200 lines each. A production page is not that size, and the
difference is not padding: the PoC pages have no empty state beyond a one-line placeholder,
no error recovery, no loading skeleton, no accessibility work, no keyboard trap handling,
no i18n, and no tests. A production page in this application is **2–4× the PoC page**, and
that factor is what the estimate below applies rather than the raw PoC rate.

---

## 2. The service boundary

### What must not change

The requirement is explicit and the code supports it: everything below is business logic
and is **not** touched by the migration.

| Layer | Where | Why it stays |
|---|---|---|
| API routes | `backend/app/api/v1/*` | The route handlers already do nothing but validate, call a service, and shape a response. They become one of two entry points to the same services. |
| Services | `backend/app/services/*` | The real logic. The PoC calls these directly; that is the whole point. |
| Agent executor, tools, permissions | `backend/app/ai/**` | Independent of the transport. |
| Repositories, models, migrations | `backend/app/repositories`, `models`, `alembic` | Untouched. |
| Worker and job queue | `backend/app/worker`, `repositories/ai_repo.py` | Untouched. |
| Everything fixed this round | auth, revocation, provider isolation, RAG grounding, retention | Untouched; the PoC exercises several of them. |

### What changes

| Item | Now | After | Effort |
|---|---|---|---|
| UI → backend transport | HTTP to `127.0.0.1:8000` | In-process bridge (proven) | **done in the PoC** |
| Second transport, if needed | — | Named pipe (proven) | **done in the PoC** |
| Auth | Bearer JWT, login page, logout, revocation | Local session; no token exists | 2–3 days |
| Error handling | HTTP status → error object | Exception → `failed(id, message, code)` | **done in the PoC** |
| Response shapes | Pydantic schemas over JSON | The same dataclasses, no serialisation | 1 week to retire the duplicate |
| The API itself | The only way in | Kept for automation and developers | 0 (kept deliberately) |

**One concrete consequence worth stating.** With no token, there is no token to steal, no
CORS to configure, no `/docs` to disable in production, and no listener on the machine for
anything else to reach. The fixes this round made to token revocation and CORS stop being
load-bearing on the desktop path — they remain for the API path.

---

## 3. Page inventory

Difficulty is judged from the measured complexity (lines, interaction types, bespoke
rendering), not from the page name. "Est." is in engineer-days for the **port**, and
excludes testing, which is estimated separately.

### Tier 1 — list, form, or detail. Structure is the same on every one.

| Page | Lines | Core function | Notable | Est. |
|---|---|---|---|---|
| `NotFoundPage` | 25 | empty state | — | 0.2 |
| `DocumentsPage` | 17 | redirect | — | 0.1 |
| `KnowledgeDocumentsPage` | 96 | document list | upload | 0.5 |
| `SearchPage` | 249 | hybrid search | result ranking display | 1.0 |
| `NotesPage` | 433 | note CRUD | markdown preview | 1.5 |
| `ProjectsPage` | 340 | project list | create/edit dialog | 1.5 |
| `ProjectDetailPage` | 344 | project detail | nested stats | 1.5 |
| `AIRunsPage` | 525 | run history list | filters, 3 chart marks | 2.0 |
| `AIRunDetailPage` | 345 | run trace | tool-call timeline | 1.5 |
| `AgentPermissionsPage` | 449 | policy editor | many toggles | 2.0 |
| `WorkbenchPage` | 226 | run workbench | live status polling | 1.5 |
| `LoginPage` | 345 | sign-in | **replaced by a local session** | 0.5 |

**Subtotal: 11 pages, ~15 engineer-days.**

### Tier 2 — rich interaction, state machines, or aggregate views.

| Page | Lines | Core function | Notable | Est. |
|---|---|---|---|---|
| `TasksPage` | 540 | task tree | tree, dependencies, bulk ops | 3.5 |
| `PlannerPage` | 486 | goal → plan | editable tree, apply | 3.5 |
| `KnowledgePage` | 474 | knowledge bases | ingest progress | 3.0 |
| `AgentPage` | 442 | agent runs | start/cancel, history | 3.0 |
| `OnboardingPage` | 256 | first-run wizard | multi-step | 2.5 |
| `HomePage` | 546 | landing | aggregates, empty states | 3.0 |
| `DashboardPage` | 639 | metrics | charts, date ranges | 3.5 |
| `MyAIPage` | 648 | provider config | **the credential entry point** | 3.5 |

**Subtotal: 8 pages, ~25.5 engineer-days.**

### Tier 3 — bespoke rendering or long-lived connections. These carry the risk.

| Page | Lines | Why it is hard | Est. |
|---|---|---|---|
| `ConversationPage` | 872 | Token streaming, long transcript, scroll anchoring, interruption. Qt has no DOM; text layout and incremental append must be built. | 8 |
| `SettingsPage` | 721 | The largest page, many independent sections, several of which are their own screens. | 7 |
| `AgentWorkbenchPage` | 555 | Live run control: plan tree, step timeline, diff view, confirmation dialogs, cancel mid-step. | 7 |
| `ChatPage` | 453 | Streaming again, with citation rendering. | 5 |
| `GraphPage` | 450 | d3 force simulation. Qt Quick has no equivalent; the layout must be computed and the interaction (pan/zoom/select) rebuilt. | 6 |

**Subtotal: 5 pages, ~33 engineer-days.**

### Port total

| Tier | Pages | Engineer-days |
|---|---|---|
| 1 | 11 | 15 |
| 2 | 8 | 25.5 |
| 3 | 5 | 33 |
| **Total** | **24** (+1 redirect) | **~73.5** |

---

## 4. Workstreams

| # | Workstream | Depends on | Engineer-days | Parallel with |
|---|---|---|---|---|
| W1 | **Service layer extraction** — one Python API the UI calls, retiring the duplicated response shapes; keep the HTTP routes as a thin adapter over the same services | — | 8 | W2, W3 |
| W2 | **IPC and process model** — decide in-process vs pipe per deployment, session and lifecycle, crash recovery, shutdown ordering | W1 | 5 | W3 |
| W3 | **Shell and design system** — window, navigation, theme, focus model, toasts, dialogs, list/table/tree/editor primitives | — | 10 | W1, W2 |
| W4 | **Page port** — the 24 pages above | W1, W3 | 73.5 | splits across engineers |
| W5 | **Tests** — per-page behaviour tests, the UI↔service contract test, and the headless verification harness extended to every page | W4 | 20 | per page |
| W6 | **Packaging** — bundle the Qt runtime and plugins, NSIS update, install/upgrade/uninstall, clean-machine test | W3 | 8 | W5 |
| W7 | **Acceptance** — keyboard navigation, accessibility, DPI across 100/125/150/175%, crash recovery, data directory migration | W4, W6 | 10 | — |

**Total: ~134.5 engineer-days.**

### What can run in parallel

With **two** engineers: W1+W2 on one, W3 on the other, then W4 splits by tier — roughly
**75–85 working days ≈ 15–17 weeks**.

With **three**: W1/W2, W3, and W6 (packaging can start as soon as the shell exists) run in
parallel, then W4 splits three ways — roughly **55–65 working days ≈ 11–13 weeks**.

One engineer: **~27 weeks**, because W1→W3→W4 is largely sequential.

### Where the estimate can move

| Risk | Direction | Note |
|---|---|---|
| Qt text layout for the conversation view | worse | No DOM. Streaming append and scroll anchoring are genuinely new work, not a port. |
| Graph page | worse | Force simulation has to be written or a library adopted and vetted. |
| Design system | better | If the existing Tailwind tokens map cleanly to a QML palette, W3 shrinks. The PoC suggests they do — `theme.js` is 56 lines and covers it. |
| Service extraction | better | The PoC shows the services are already callable directly; the work is retiring the duplicate shapes, not building an interface. |
| Tests | worse | W5 assumes per-page tests are written alongside. If they are deferred, W4 shortens and W5 grows — the total does not. |

---

## 5. Comparison with the WebView build

| | Tauri + WebView2 (current) | Qt 6 / QML (PoC) |
|---|---|---|
| UI technology | React in an embedded browser | Native Qt Quick scene graph |
| Transport | HTTP on `127.0.0.1:8000` | In-process call, or a named pipe |
| Processes | Shell + Python server | One |
| Token / CORS / `/docs` surface | Required | Does not exist |
| Runtime dependency | WebView2 (ships with Windows 11, separate on 10) | Qt runtime, bundled (~60–80 MB) |
| Installer size | ~97 MB measured | Estimated similar; the Qt runtime replaces the WebView payload |
| Pages to maintain | 25 TSX | 24 QML |
| Frontend test suite | 127 tests, vitest | Must be rebuilt |
| Known blockers | RF-01: not a native UI | None found in the PoC |

---

## 6. Recommendation

**Proceed, and treat it as a quarter, not a sprint.** The PoC answers the questions that
could not be answered by reading:

* A native window, navigation, theming and DPI handling work, and are ~120 lines of shell.
* The service layer is callable directly — no HTTP shim is needed, and the permission layer
  still applies.
* Both IPC options work, including the caps and timeouts the requirements ask for.
* A real business page (write → read back → verify in the database) takes about a day.

What the PoC does **not** answer, and what the estimate is therefore weakest on:

* Streaming text into a native view without a DOM (Conversation, Chat).
* A force-directed graph without d3 (Graph).
* Packaging the Qt runtime and proving it on a machine that has never had the dev tree.

Those three are 19 of the 73.5 port-days, and they are the part most likely to be wrong.

**The one decision that must be made before W1 starts:** does the desktop product keep the
HTTP API at all? If it does, the service layer needs a stable shape that both the routes
and the bridge call, and W1 is 8 days. If it does not, the routes can be deleted with the
React frontend and W1 is 3 days — but automation and the developer workflow lose their
entry point. The PoC assumes the API stays.
