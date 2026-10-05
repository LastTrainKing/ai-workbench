# Audit — before productization

Written at tag `pre-productization`, from source and live execution, not from reports.

---

## 1. Environment

| Requirement | State | Evidence |
|---|---|---|
| PostgreSQL / Redis / Qdrant | **READY** | ports 5432 / 6379 / 6333 open |
| API serving `frontend/dist` same-origin | **READY** | `frontend/dist/index.html` present, API returns 200 on `/` |
| Tauri executable builds and opens a native window | **READY** | `ai-workbench-desktop.exe` 3,556,352 B; desktop suite 12/12 |
| DeepSeek provider | **READY** | live call returned "链路正常", `deepseek-flash`, 74 tokens |
| `python-docx` / `python-pptx` / `openpyxl` / `reportlab` / `pypdf` | **READY** | all import |
| LibreOffice | **MISSING** | not installed; being installed via winget |

One finding from the DeepSeek call worth recording: `max_tokens=30` **fails** with
"模型在思考阶段用尽了 token 预算". The default model is a reasoning model and needs
thinking budget on top of the answer. Any code path that sets a small `max_tokens` is
silently broken for this provider.

---

## 2. Database

29 tables, migration head `1867e3419108`.

Live rows:

```
projects=2   tasks=252   task_documents=3   documents=4
concepts=30  document_concepts=15
ai_runs=57   agent_runs=29   memories=1
```

The knowledge → concept → task links exist **and have data**. That matters: the original
product idea is not dead code, it is populated and merely invisible in the interface.

---

## 3. Module status

| Module | Status | Evidence |
|---|---|---|
| Knowledge ingestion (parse → chunk → embed → Qdrant) | **DONE** | 4 documents ingested, 30 concepts extracted |
| Concept extraction and document↔concept links | **DONE** | 15 `document_concepts` rows |
| Task ↔ document association | **DONE (backend)** / **PARTIAL (UI)** | `TaskDocument` populated (3 rows); no page shows it |
| RAG (hybrid retrieval, rerank, citations) | **DONE** | pipeline live, budgeted context assembly |
| AI Task Planner | **DONE** | plans persist as tasks; context injected |
| Tool registry (44 tools) + permissions + audit | **DONE** | 44 registered; audit rows with correct verdicts |
| Autonomous agent loop | **DONE** | PLAN→EXECUTE→VERIFY→FIX→RETRY, pause/resume/cancel, snapshots |
| Snapshot / diff / rollback | **DONE** | conflict protection verified |
| Personal context models (5 tables) | **DONE** | migrated, live |
| Personal context API (12 endpoints) | **DONE** | 31/31 API tests |
| ContextAssembler wiring (RAG, planner, agent, document analysis) | **DONE** | `context_snapshots` carries `rag`/`planner`/`document` purposes |
| MemoryEvaluator | **DONE (wrong trigger)** | works, but fires on conversation turns |
| `document_write` / `document_read` | **DONE** | 6/6 formats round-trip, Excel with formula and chart |
| Desktop shell (shortcut, job object, splash, menu, no-browser) | **DONE** | 12/12 + 21/21 + 10/10 |
| Frontend routes | **DONE** | 27 `<Route>` entries, all 200 |
| **`BuildRun` model + API** | **TODO** | does not exist |
| **`Deliverable` model + API + verification** | **TODO** | does not exist |
| **Accept / reject with feedback** | **TODO** | the loop's entry point; does not exist |
| **Workbench page** | **TODO** | `/agent` exists but is a run viewer, not a delivery view |
| **Home page (4 areas)** | **TODO** | current `/` is a statistics dashboard |
| **Memory trigger on delivery acceptance** | **TODO** | fires on conversation; wrong source |
| **Task ↔ document visible in UI** | **TODO** | data exists, no page |
| **Project view connecting knowledge/tasks/deliverables** | **TODO** | does not exist |
| Memory management page | **PARTIAL** | `/me` tab exists; needs source attribution |
| Onboarding | **DONE** | two doors, both verified |

---

## 4. Tests

| Suite | Count | State |
|---|---|---|
| Backend unit | — | all pass |
| Frontend Vitest | 114 | all pass |
| Desktop acceptance | 12 | 12/12 |
| Agent UI (browser) | 26 | 26/26 |
| Context UI (browser) | 22 | 22/22 |
| Shell behaviour (browser) | 21 | 21/21 |
| Focus grid (browser) | 17 | 17/17 |
| Splash | 10 | 10/10 |
| Sidebar | 10 | 10/10 |
| Context API | 31 | 31/31 |
| Memory loop | 18 | 18/18 |
| Personalisation | 8 | 8/8 |

Total 189 assertions, all passing at freeze.

---

## 5. The diagnosis

Three products live in one repository and none of them is complete as a *product*:

1. **Knowledge → task → execution.** Fully implemented, fully populated, **invisible in
   the interface**. The original idea was never abandoned; it was buried.
2. **Windows autonomous agent.** The most complete and most demonstrable part — and it is
   a *means* that was mistaken for the *end*. It produces files; nothing in the product
   treats those files as the point.
3. **Personal AI OS.** Foundation only: five tables and two components, wired into
   prompts, but with no delivery loop to attach to.

They are not three directions. They are **three layers of one product**:

```
认知层   what it knows        documents · concepts · memory
行动层   what it can do       planning · tools · execution
交付层   what it produced     deliverables · verification · acceptance
```

What is missing is the **交付层** — and with it the reason for the other two to exist.

The single most important consequence: **memory has no correct source.** The evaluator
currently listens to conversation turns, which is guessing. It should listen to delivery
acceptance — accept, reject, revise — because that is the only moment the user states
what they actually wanted.

`Deliverable.accepted` and `Deliverable.feedback` are therefore not two extra columns.
They are the input to the entire learning loop.

---

## 6. Plan

Vertical slices, each ending in a working path a person can walk:

| Stage | Slice |
|---|---|
| 1 | Desktop shell — verify, do not rebuild |
| 2 | Personal context — verify, do not rebuild |
| 3 | **Deliverable + verification** — the value carrier |
| 4 | **BuildRun + workbench** — the visible loop |
| 5 | **Memory on acceptance** — the learning loop |
| 6 | **Home page** — the entry point |
| 7 | Knowledge / task / project surfacing |
| 8 | End-to-end, demos, delivery |

Stages 1 and 2 are already done and are verified rather than rebuilt, because rebuilding
working, tested code is the fastest way to lose the tests that prove it works.
