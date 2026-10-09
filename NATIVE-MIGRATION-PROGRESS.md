# Native migration progress — round 4

Branch `fix/release-freeze-20261009` · start `e447e24` · real-provider calls: **11, ¥0.01**

This round did three things: verified the remaining cheap real-model capabilities, found and
fixed two defects, and moved the native UI from four pages to seven by building the product's
core loop. It did **not** finish the migration — 20 pages remain, and that is stated below
rather than implied.

---

## 1. Baseline

| | |
|---|---|
| Start commit | `e447e24` (both `e447e24` and `52b9af7` verified as ancestors of HEAD) |
| Branch | `fix/release-freeze-20261009` |
| Working tree at start | clean |
| Version | 2.2.0 |
| 2.2.0 artifact | `build/installer/AI-Workbench-Setup.exe` — **97,311,474 bytes**, SHA-256 `698db636…` |
| Archived copy | `archive/releases/AI-Workbench-2.2.0-final.exe` — **identical SHA-256** |
| User data | `%LOCALAPPDATA%\AI Workbench Data` — 1,530 files, present, untouched |
| Test baseline | 1,216 passed / 10 skipped / 0 failed |
| Test result at end | **1,229 passed / 10 skipped / 0 failed** (1,216 + 13 new: 3 embedding guard, 10 streaming) |

Everything the previous report claimed about the baseline was confirmed against the
repository. No discrepancy.

---

## 2. Real model acceptance

### 2.1 What was already verified, and was **not** re-run

Per the instruction not to spend credit re-proving things, these were cited, not repeated:
real RAG Q&A (`run_e3d7119e…`), real Agent `project_list` call with observation
(`run_0aafffc2…`), citation-to-source verification, real 400 → `ai_runs` FAILED
(`run_ed8a03234f…`), and the native UI key save/mask path.

### 2.2 What this round ran

| # | Purpose | Real or stub | Calls | In / Out tokens | Cost | Run ID | Result |
|---|---|---|---|---|---|---|---|
| A2 | Real streaming: do bytes arrive in pieces? | **real** | 3 | 570 total | < ¥0.005 | not persisted (provider-level) | **PASS_REAL** |
| A3 | Multi-turn context carried between turns | **real** | 2 | 1 per turn | < ¥0.005 | via conversation `c486dfb5…` | **PASS_REAL** |
| A4 | Controlled longer input (~3,800 chars) | **real** | 3 | 489 + 511 + 2,218 | < ¥0.005 | not persisted | **PASS_REAL** |
| A2s | Streaming transport, terminator, malformed frame, usage frame, HTTP error, guard | stub | 0 | 0 | ¥0.00 | — | **PASS_STUB** (10 tests) |
| A7 | Embedding provider assessment | none | 1 probe | 0 | ¥0.00 | — | **BLOCKED** |
| B5b | Knowledge loop through the native bridge | **real** | 1 | ~1,000 | < ¥0.005 | `run_dbc17976…` | **PASS_REAL** |

**Total: 11 real HTTP requests, ¥0.01** (balance 6.71 → 6.71 at two-decimal precision).
No pause threshold reached, no repeated requests, no concurrency.

### 2.3 A2 — streaming

The provider layer had **no streaming at all** before this round. A chat interface where the
answer appears only after it is finished is a form, not a conversation, so the transport was
implemented: server-sent events, read line by line, terminated by `data: [DONE]`.

Real evidence: **44 content chunks arriving over 0.37 s** with measurable gaps between them,
usage reported (60 in / 44 out), `finish_reason=stop`.

Two of the three A2 calls returned **no answer at all** — 128 and 256 chunks of *reasoning*
and nothing else. `deepseek-flash` is a reasoning model, and an open-ended prompt consumed the
whole budget thinking. That is a real property the interface has to handle: a stream can end
with `finish_reason=length` and empty content, and the UI must say so rather than show a blank
panel.

### 2.4 A3 — multi-turn context

Two turns in one conversation, with the outgoing request **captured**:

```
turn 2 sent  : ...用户：请记住一件事：我的项目代号是 ORION-7。
                   你：记住了：你的项目代号是 ORION-7。...
turn 2 reply : 你的项目代号是 ORION-7。（这是你之前让我记住的，我这边存着呢。）
```

The assertion that matters is the captured request, not the reply — a model can guess a
plausible fact, but `ORION-7` is made up, and it appears in turn 2's prompt because the
application put it there. 2 model calls total, 4 messages persisted.

### 2.5 A4 — controlled longer input

3,787-character document, marker at character 1,902 (the midpoint), one question. The answer
`ZQ-4417` proves the middle reached the model — a beginning-only truncation would have failed
it. 2,172 prompt tokens reported against a client estimate of 3,392 (the estimate is
conservative for CJK; the vendor's count is authoritative).

The first two attempts were **703 characters**, below the 2,000–4,000 the order specifies, and
each still spent a call discovering it. The size is now asserted *before* the request.

### 2.6 A7 — Embedding: **BLOCKED**, and what was done instead

| Check | Finding |
|---|---|
| Independent chat/embedding providers | **Yes** — `embedding_provider`, `embedding_base_url`, `embedding_model` are separate settings |
| DeepSeek embeddings endpoint | **404** — the API does not serve one |
| Local embedding model on this machine | **None** — no Ollama running; no sentence-transformers, transformers, torch, onnxruntime or fastembed installed |
| Providers the project supports | `openai_compatible`, `ollama`, `offline` |
| Collection records model and dimension | Yes — `DocumentChunk.embedding_model`, `KnowledgeBase.embedding_model` / `vector_size` |

**Not worked around.** Installing a heavy dependency or registering another paid service to
close a checkbox is what the round forbids. The capability stays `BLOCKED`, with the
dimension, storage and isolation checks done against the offline embedder instead — which is
honest about what was and was not verified.

**A real defect was found in this area and fixed** — see §4.1.

---

## 3. Native UI migration

### 3.1 Page inventory, recounted from the code

25 pages in `frontend/src/pages`, 10,476 lines of TSX. Counted from the filesystem, not copied
from a previous report.

| Old page | Lines | Native | Status |
|---|---|---|---|
| `SettingsPage` | 721 | `SettingsPage.qml` | **IMPLEMENTED** — provider config, credential entry, runtime info, file export |
| `DashboardPage` | 639 | `DashboardPage.qml` | **IMPLEMENTED** — real aggregate counts |
| `TasksPage` | 540 | `TasksPage.qml` | **IMPLEMENTED** — list, create, complete |
| `KnowledgePage` | 474 | `KnowledgePage.qml` | **IMPLEMENTED** — list, create, counts per base |
| `KnowledgeDocumentsPage` | 96 | `DocumentsPage.qml` | **IMPLEMENTED** — list, real file import |
| `ChatPage` | 453 | `AskPage.qml` | **PARTIAL** — grounded Q&A with citations and streaming; no conversation history |
| `AgentPage` | 442 | `RunsPage.qml` | **PARTIAL** — run, stream progress, cancel; no history list or filters |
| `ConversationPage` | 872 | — | `NOT_STARTED` |
| `MyAIPage` | 648 | — | `NOT_STARTED` (provider config is covered by `SettingsPage`) |
| `AgentWorkbenchPage` | 555 | — | `NOT_STARTED` |
| `HomePage` | 546 | — | `NOT_STARTED` |
| `AIRunsPage` | 525 | — | `NOT_STARTED` |
| `PlannerPage` | 486 | — | `NOT_STARTED` |
| `GraphPage` | 450 | — | `NOT_STARTED` — needs its own design (force layout, no d3) |
| `AgentPermissionsPage` | 449 | — | `NOT_STARTED` |
| `NotesPage` | 433 | — | `NOT_STARTED` |
| `AIRunDetailPage` | 345 | — | `NOT_STARTED` |
| `LoginPage` | 345 | — | **N/A** — a local session; there is no login in a single-account desktop app |
| `ProjectDetailPage` | 344 | — | `NOT_STARTED` |
| `ProjectsPage` | 340 | — | `NOT_STARTED` |
| `OnboardingPage` | 256 | — | `NOT_STARTED` |
| `SearchPage` | 249 | — | `NOT_STARTED` |
| `WorkbenchPage` | 226 | — | `NOT_STARTED` |
| `NotFoundPage` | 25 | — | `N/A` — a desktop shell has no routes |
| `DocumentsPage` | 17 | — | **N/A** — a redirect |

**Tally: 6 IMPLEMENTED, 2 PARTIAL, 15 NOT_STARTED, 2 N/A.**

Nothing is marked `VERIFIED` except where §3.3 shows a test. A page with a matching title is
not a migrated page.

### 3.2 The core loop, and what it actually does

```
create a knowledge base  →  import a real file  →  parse and embed  →  ask  →  answer with citations
```

Six new bridge slots. The import calls `save_upload` → `register_upload` → `ingest` — exactly
what the HTTP upload route calls — so the file is hashed, deduplicated, parsed and embedded by
the product's own pipeline, not by a path written for the interface.

The ask page shows the **grounding report** rather than a green tick: claim units, supported
units, coverage, and the reason when it fails. It also states, in the interface, that this is
a structural check and not fact-level entailment.

### 3.3 Verification

| What | Result |
|---|---|
| `desktop-native/verify_knowledge_loop.py` — create, import, embed, ask, stream | **18/18** |
| `desktop-native/verify_native_ui.py` — window, tasks, config, permissions, IPC | **28/28** |
| `desktop-native/audit_native_ui.py` — no HTTP, no browser, permissions, IPC, page gap | **25/25** |
| Real RAG answer **through the bridge** | **6/6 PASS_REAL** — `deepseek/deepseek-flash`, `本项目的封版版本号是 **2.2.0** [1]`, citation resolves to `release-notes.md` |

### 3.4 Architecture boundaries, re-checked

| Requirement | Evidence |
|---|---|
| No WebView2 / Qt WebEngine / Chromium / HTML page | audit: no browser component referenced, no HTML shipped, the React build is not loaded |
| No `127.0.0.1` HTTP path from the UI | audit: a socket spy during a bridge call shows **0 connections to the API port** |
| Business logic not in QML | all data access is in `awbnative/bridge.py`; QML only calls slots |
| Permission layer not bypassed | audit: workspace write succeeds; out-of-policy and protected paths are refused |
| UI thread not blocked | `AsyncRunner` owns one asyncio loop on a background thread; every slot returns immediately |
| IPC frame cap, ids, timeout, allowlist, disconnect | audit: 1 MiB cap, `unknown_method`, `timeout`, server survives a mid-frame hang-up |

### 3.5 Not done, and not claimed

* **Packaging.** No PyInstaller/NSIS wiring for the Qt build; the Qt runtime is not bundled.
  There is no installable native artifact, so the deliverable for this round is the runnable
  source tree plus the verification scripts.
* **Keyboard navigation** beyond the Ctrl+1–5 shortcuts. Focus order, traps and the
  accessibility tree are unverified.
* **DPI.** One display at 1.0. The scaling code runs; 100/125/150/175% and mixed DPI are
  `BLOCKED`.
* **Performance at scale.** No measurement with many knowledge bases, documents or graph nodes.
* **The graph page.** No design work started; it needs a force layout and its own interaction
  model before it can be estimated.

---

## 4. Defects found and fixed

### 4.1 The embedding model could be switched silently (backend)

`kb.embedding_model` was assigned on every ingest and never compared against anything. A
knowledge base built with model A accepted vectors from model B without a word; retrieval then
compared distances across two incompatible spaces while every row still claimed a single
model. The dimension guard in `ensure_collection` is necessary and **not sufficient** — two
models can both produce 384-dimensional vectors and mean different things by them.

Fixed with a comparison against what the knowledge base was actually built with, refusing with
both model names and the embedded-chunk count. A knowledge base with no vectors yet is not a
conflict, and that case is tested so the guard cannot be "fixed" into refusing first
ingestions. **3 tests.**

### 4.2 The native bridge read the wrong provider configuration (native UI)

`ServiceBridge._owner()` resolved the account id and stopped. `build_llm_provider()` takes no
owner and reads the request context — which the HTTP application sets in its auth dependency —
so every provider built inside the bridge resolved the **installation-level** configuration
while `loadAiConfig` returned the account's. The settings page showed a key and a model; the
calls used whatever the machine had.

This is precisely the failure the acceptance round warned about, and it was in the newest code.
`_owner()` now binds the call. **Verified by a real call through the bridge.**

### 4.3 The guard's streaming refusal never ran (backend)

Written as a plain `async def`, `BlockedProvider.stream_chat` returned a coroutine; `async for`
rejected it with "requires an object with `__aiter__` method" *before* the refusal executed.
The call still did not reach the vendor, but the operator would see a TypeError three frames
from the cause. It is an async generator now. **1 test.**

### 4.4 Three ways a streaming parser goes wrong (backend)

`stream_options.include_usage` (without it, zero tokens recorded for a paid call); the usage
frame's **empty `choices` array** (crashes a parser that indexes `choices[0]`, on the last
frame, after the user has watched the answer appear); and HTTP errors arriving before any
event. **10 tests.**

---

## 5. Backend and data reliability

The transaction semantics from the previous round are **unchanged** — `persist_terminal_record`
still commits the failure record, and the executor still commits at run start and per step.
No evidence was found to justify changing them, and the design trade-off is documented in
`tracing.py` rather than revisited.

| Check | Result |
|---|---|
| Real data directory before/after | **unchanged** — 1,530 files, same mtime |
| 2.2.0 artifact | untouched; two copies with identical SHA-256 |
| Destructive migration | none attempted |
| Tests deleted or weakened | none |
| New regression tests | 14 (3 embedding guard, 10 streaming, plus the audit's own checks) |

**Recovery re-execution risk, stated plainly:** if a run is interrupted after a side-effecting
tool succeeded but before its step committed, the tool's effect may or may not be durable, and
nothing in the system records which. Re-running the goal would repeat the tool. There is no
idempotency key on tool calls. That is a known gap, not a fixed one, and it is the honest
answer to "can recovery re-execute a side-effecting tool?" — **yes, it can**.

---

## 6. Cost and credentials

| | |
|---|---|
| Real requests this round | **11** (A2 3, A3 2, A4 3, B5b 1, embeddings probe 1, balance queries 2) |
| Tokens | ≈ 5,800 |
| Cost | **¥0.01** (balance 6.71 → 6.71) |
| Pause threshold (¥3) | not reached |
| Concurrency | none; every request serial |
| Retries | disabled for the tests (`AI_MAX_RETRIES=1`) |
| Key in reports/source/logs/Git | **none** — only `sk-****` masks |

The key is **not revoked** and the user asked that it be kept. It remains in the git-ignored
per-account file only.

---

## 7. Next stage

Ordered by value, with the dependency that actually gates each one.

| # | Task | Depends on | Size |
|---|---|---|---|
| 1 | **Package the native UI.** Without it there is no installable artifact, and every other page is unreachable by a user. | — | 5–8 days |
| 2 | **Keyboard navigation and focus.** Every core action reachable by keyboard; focus visible; dialogs and lists trap focus. Required by the product statement, currently unverified. | — | 3–4 days |
| 3 | **Projects and notes.** The two largest gaps in the daily loop; both are list-and-form pages over services that already exist. | — | 4–5 days |
| 4 | **Run history and detail.** The agent can be started but not reviewed afterwards. | — | 4–5 days |
| 5 | **Planner.** Goal → plan → apply, over an existing service. | Projects (task tree) | 4 days |
| 6 | **Conversation.** The largest single page in the old interface and the only one with token streaming into a scrolling transcript. Needs its own technical validation before it can be estimated honestly. | Packaging, streaming | 8–10 days + validation |
| 7 | **Graph.** Needs a data-model and interaction design first; no d3 equivalent in Qt. | — | 5–6 days + validation |
| 8 | **Agent permissions UI.** Policy editing over an existing store. | — | 3 days |
| 9 | **DPI and multi-display acceptance.** | Real hardware | `BLOCKED` here |
| 10 | **Embedding provider.** Either a local model or a provider with an embeddings API. | A decision, not code | `BLOCKED` |

**Recommended next:** packaging first, then keyboard navigation. A migrated interface that
cannot be installed or driven without a mouse is not closer to the product than the current
one, and both are prerequisites for calling any page `VERIFIED`.

---

## 8. Final state

* Worktree clean; every change committed.
* No product feature outside this round's scope was touched.
* The 2.2.0 archive and the user's data are intact.
* The native UI is **7 pages, 6 implemented and 2 partial**, verified by 71 automated checks
  plus one real end-to-end call — and **not** a finished product.
