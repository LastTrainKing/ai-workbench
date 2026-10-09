# Native migration progress — round 5

Branch `fix/release-freeze-20261009` · start `aa8e11b` · real-provider calls this round: **0, ¥0.00**

This round closed the two P0 items. It made the agent's tool calls survive a crash, and it
produced a Windows native package that actually launches, reaches the database, and passes a
ten-point acceptance. It also found and fixed a defect that had been in the native UI since
it was written: **no page could talk to the shell**.

It did **not** finish the page migration — 10 of 25 old pages remain `NOT_STARTED`. That is
stated in section D rather than implied.

---

## A. 工程状态

| | |
|---|---|
| Start commit | `aa8e11b` |
| Branch | `fix/release-freeze-20261009` |
| Working tree at start | clean |
| Version | 2.2.0 |
| 2.2.0 artifact | `build/installer/AI-Workbench-Setup.exe` — **97,311,474 bytes**, SHA-256 `698db63662cc24e42873884077ada51b7b97c5ccd45ab3616acd15ad2df0d3d7` |
| Archived copy | `archive/releases/AI-Workbench-2.2.0-final.exe` — **byte-identical**, same SHA-256 |
| User data | `%LOCALAPPDATA%\AI Workbench Data` — **1,530 files, 50 MB, present, not modified** |
| Test baseline (round 4) | 1,229 passed / 10 skipped / 0 failed |
| Test result at end | **1,278 passed / 10 skipped / 0 failed** |
| Mutation check | **22/22 defects caught** by their regression test |

Both copies of the 2.2.0 archive were hashed at the start and at the end of this round and are
unchanged. The user's data directory was read but never written to; the only database this
round modified was the **development** database, and only by `alembic upgrade head` (section F).

### Commits added

| Commit | Purpose |
|---|---|
| *(this round's first)* | Agent tool invocations: the intent is recorded before the tool runs, recovery distinguishes `UNKNOWN` |
| *(this round's second)* | Standalone Windows native package: build, first-run initialisation, ten-point acceptance |
| *(this round's third)* | Native shell: keyboard navigation, accessibility, Projects and Notes; two defects found in the packaged acceptance |

---

## B. 原生安装包

### B.1 How it is built

```
.venv\Scripts\python.exe desktop-native\build_native.py
```

`build_native.py` runs PyInstaller with `desktop-native/ai-workbench-native.spec` and then
**runs `verify_native_package.py` against the result**, failing if any check fails. A build
that produces a directory full of correctly named files and no working application is a
build that fails here.

One folder, not one file: a one-file build extracts several hundred megabytes of Qt and Python
to a temporary directory on every launch, and gives a different `sys._MEIPASS` each time.

### B.2 The artifact

| | |
|---|---|
| Directory | `desktop-native/dist/AI-Workbench-Native/` |
| Executable | `AI-Workbench-Native.exe` — **20,899,343 bytes** |
| Executable SHA-256 | `3b652ded9c072cbc108b13c8e5f1895730eaf137492b41621511285d80289aed` |
| Total size | **255 MB**, 2,267 files |
| Bundled Python | 3.13.14 (its own; no host Python required) |
| Bundled OpenSSL | 3.5.7 (pinned to the interpreter's own — see B.5) |
| Qt | PySide6 6.12.0, 4 platform plugins including `qwindows` and `qoffscreen` |

**There is no installer yet.** The package is a portable directory that runs from where it
is unpacked. An NSIS installer wrapping it is the next step and is *not* claimed as done.

### B.3 Acceptance — 10 checks, 10 pass

```
  [PASS]  1. 可执行文件已生成 — AI-Workbench-Native.exe, 19.9 MiB
  [PASS]  2. Qt 运行时与平台插件已打包 — Qt libraries, Shiboken binding, 4 platform plugins
  [PASS]  3. QML 界面与 Qt QML 模块已打包 — 13 interface files and the Qt QML modules
  [PASS]  4. Alembic 迁移已打包 — 8 migrations
  [PASS]  5. VERSION 与仓库一致 — 2.2.0
  [PASS]  6. 包内没有密钥与用户数据 — 717 text files and the executable scanned, nothing found
  [PASS]  7. 打包后的应用能启动并自检通过 — 7 UI checks passed in 2.2s (exit 0)
  [PASS]  8. 首次运行会创建用户数据目录 — created AI Workbench Data and used it
  [PASS]  9. 运行不会写入安装目录 — 2267 files unchanged
  [PASS] 10. 不依赖宿主机 Python — ran as AI-Workbench-Native.exe with a sanitized environment
```

Checks 7–10 **run the built executable**. Checks 8 and 9 are the two that matter most:

* **8** runs with `AI_WORKBENCH_DATA_DIR` pointing at a directory that does not exist, and
  requires the application to create it and then report having used it. An application that
  only works once someone has already made its data directory works on the developer's
  machine and nowhere else.
* **9** snapshots all 2,267 files (path, size, mtime) before and after a real run and requires
  that nothing was added, removed or modified. This is the check that catches the class of
  defect this project has already shipped once — a key file and a data directory written next
  to the program, which made the uninstaller unable to remove the install directory.

### B.4 Running environment, and what is blocked

The packaged application reaches the real services on this machine:

```
  database: PASS     — schema=verified, accounts=45
  redis:    PASS     — redis://127.0.0.1:6379/0
  qdrant:   BLOCKED  — HTTP 401: Qdrant is running but refused the request (no API key configured)
```

Qdrant is `BLOCKED`, not failed, and the reason is correct behaviour rather than a defect: the
development `.env` holds `QDRANT_API_KEY` and the packaged build deliberately does not read it.
A packaged application that reads the development `.env` would be shipping development
credentials, which is the opposite of what check 6 is for. Configuring the key through the
application's own settings is the intended path and is not wired to the vector store yet.

**Not verified, and not claimed:**

| Item | State | Why |
|---|---|---|
| Code signing | **BLOCKED** | no certificate on this machine |
| Install / uninstall on a clean machine | **BLOCKED** | no second clean Windows environment available |
| Multi-monitor and DPI behaviour | **BLOCKED** | single display; `Screen.devicePixelRatio` is read and used, but not exercised |
| Qdrant from the packaged build | **BLOCKED** | see above |
| TLS to a *remote* PostgreSQL | **not verified** | the only database here is loopback (see B.5) |

### B.5 Two packaging defects found and fixed

**`shiboken6.Shiboken` was excluded by this build's own spec.** The exclusion read like an
internal detail of a package already being bundled. It is the C++ binding layer every PySide6
class is built on, and the first package died at `import PySide6` with "No module named
'shiboken6.Shiboken'". Because the executable is a GUI-subsystem binary, the failure appeared
as a **modal fatal-error dialog that waits** — so a broken build hung instead of failing.
`verify_native_package.py` now checks for `shiboken6/Shiboken.pyd` by name, and the spec
refuses to build if it is missing.

**The bundled OpenSSL did not match the bundled `_ssl.pyd`.** The second package started,
built its window, and then could not reach PostgreSQL:

```
SSLError: [SSL] system lib (_ssl.c:4105)
```

`_ssl.pyd` in the bundle was byte-identical to the interpreter's, but `libssl-3-x64.dll` and
`libcrypto-3-x64.dll` were **not** — PyInstaller had collected OpenSSL from elsewhere on the
machine. A mismatched OpenSSL pair against a fixed `_ssl.pyd` produces exactly this: contexts
are created (the basic symbols resolve) and handshakes fail at the OpenSSL level. The
diagnostic that settled it asks the question directly and is now part of the report:

```
  "tls_probe": { "plain": "ok", "tls": "SSLError: [SSL] system lib (_ssl.c:4105)" }
```

The spec now takes both libraries from `sys.base_prefix/DLLs`, removes any other copy, and
refuses to build if the pinned files are not the ones in the product. After the fix the
packaged application reports `database: PASS — schema=verified, accounts=45`.

---

## C. Agent 执行安全

### C.1 The defect

`tool_calls` rows were written **after** the tool returned. The window between a tool starting
and its outcome being recorded therefore left no row at all. A process killed inside that
window produced an agent run with no evidence it had ever called anything — and re-running the
goal repeated the tool. For a `file_append` or a `git_commit` that is a duplicated side effect
that nothing in the system could detect, because nothing had been written down.

### C.2 What was implemented

**A durable invocation record, written in two phases.**

| Status | Meaning |
|---|---|
| `PENDING` | the intent is recorded; the tool has not started |
| `RUNNING` | the tool was handed to the executor; its side effect may or may not have happened |
| `SUCCEEDED` | the result was verified and persisted |
| `FAILED` | the tool ran and failed |
| `SKIPPED` | the loop guard refused a duplicate; nothing ran |
| `UNKNOWN` | the process died mid-invocation. **Never auto-retried** |

`begin_tool_call()` writes the row as `RUNNING` with a stable `invocation_id`, `started_at` and
`mutates_state`, and the caller **commits before dispatching**. `finish_tool_call()` sets the
outcome and `finished_at`. A row with `started_at` and no `finished_at` is the definition of
"we do not know".

The commit is not interchangeable with a flush: a flush leaves the row inside the caller's
transaction, invisible to any other session and discarded outright if the process dies — which
is precisely the scenario the row exists for. The cost is one extra commit per tool call.

`invocation_id` doubles as an idempotency key: submitting the same one twice returns the
existing row rather than creating a second.

Both agent engines are covered — `AgentExecutor` (which had the defect) and `AutonomousAgent`
(which recorded nothing at all; it now writes the same rows).

**Recovery.** `resolve_orphan_tool_calls()` turns every `RUNNING` row into `UNKNOWN` at
startup, before the interrupted runs are closed. `finished_at` is deliberately left `NULL`:
stamping it would claim the invocation ended, and the whole point of the row is that its end
was never observed. `unresolved_tool_calls()` returns the mutating ones for a run.

**Tool classification.** `ToolSpec.replay_class()` returns `read_only`, `idempotent` or
`unsafe`. **The default is `unsafe`**, and that direction is deliberate: marking a tool
replayable is a claim about its implementation, and getting the claim wrong is the failure
this mechanism exists to prevent. Three tools are marked `idempotent` because their code was
read — `file_write` (overwrites; the file is a function of path + content), `directory_create`
(`mkdir(exist_ok=True)`), `git_add` (the index records paths) — and 22 writes are `unsafe`,
including `file_append`, `git_commit`, `file_delete` and every terminal tool.

### C.3 Fault injection — eight windows, real processes

`tests/integration/_invocation_child.py` is spawned, allowed to reach a named point, and then
**killed from outside** with `Popen.kill()`. It does not kill itself: an in-process exception
runs the `finally` blocks and disposes the session politely, which is exactly the cleanup a
crash does not get. The child writes a sentinel file and fsyncs it before blocking, so the
parent can never kill a process that announced a window it had not reached.

`file_append` is the tool under test: it mutates state and is **not** idempotent, so a
duplicate shows up in the file itself and not only in the database.

| # | Window | Result |
|---|---|---|
| 1 | killed before the intent was committed | no row; the file does not exist. **This window is not covered by the fix** — a transaction that never committed leaves no trace, and the fix narrows it to the microseconds between flush and commit |
| 2 | killed after the intent committed, before the tool started | one `RUNNING` row, `started_at` set, `finished_at` NULL, file untouched |
| 3 | killed after the side effect, before the outcome | file contains `w3`; one `RUNNING` row — the case the mechanism exists for |
| 4 | killed after the outcome committed | one `SUCCEEDED` row, `finished_at` set |
| 5 | killed after the tool failed, before the failure was written | one `RUNNING` row for `file_delete`; nothing changed |
| 6 | recovery after restart | exactly the three open windows become `UNKNOWN`; the decided one is untouched; `finished_at` stays NULL on the `UNKNOWN` rows |
| 7 | the same invocation id submitted twice | one row, resolved to the same id, not two |
| 8 | two legitimate calls of the same tool | two rows, distinct invocation ids, both terminal |

Two further tests assert the **ordering itself**, which is the part that cannot be checked by
looking at the code: a fake registry opens its **own session** at the moment the tool is about
to run and looks for the invocation. If the executor had only flushed, it would find nothing.

### C.4 Boundaries, stated rather than implied

**This is not exactly-once, and is not described as such.** A database commit and an external
side effect cannot be one atomic transaction, and nothing here pretends otherwise.

* **Window 1 is real and unfixed.** A process killed between `flush()` and `commit()` leaves
  nothing. What the fix does is make that window as small as a commit, instead of as long as
  the tool call.
* **`UNKNOWN` is a report, not a resolution.** For a mutating call killed mid-flight, the
  system can say "this may have happened" and refuses to replay it automatically. It cannot
  say whether it happened. Resolving that needs a human, or a reconciliation the tool itself
  provides — no tool provides one today.
* **RAG and Agent still recover differently.** The RAG pipeline records its run through
  `RunRecorder` and does not execute tools, so it has no invocation window at all; the agent
  paths do. This is a real asymmetry and is recorded here rather than smoothed over.
* **No tool is auto-replayed.** Recovery marks and reports; it never re-runs. So the
  duplicate-execution risk is closed by *not replaying*, not by making replay safe.

---

## D. 原生 UI

### D.1 Page inventory, recounted from the code

25 pages under `frontend/src/pages/` — counted this round, not copied from the previous
report. Status is what the **native** shell can do, verified by reading the bridge's slots
(24 real slots) and the QML:

| Old page | Native status | Evidence |
|---|---|---|
| `DashboardPage` | **IMPLEMENTED** | `DashboardPage.qml`, `loadDashboard` |
| `KnowledgePage` | **IMPLEMENTED** | `KnowledgePage.qml`, `loadKnowledgeBases` / `createKnowledgeBase` |
| `KnowledgeDocumentsPage` | **IMPLEMENTED** | `DocumentsPage.qml`, `loadDocuments` / `importDocument` |
| `DocumentsPage` | **IMPLEMENTED** | same page |
| `TasksPage` | **IMPLEMENTED** | `TasksPage.qml`, `loadTasks` / `createTask` / `completeTask` |
| `ProjectsPage` | **IMPLEMENTED** | `ProjectsPage.qml`, `loadProjects` / `createProject` — **new this round** |
| `NotesPage` | **IMPLEMENTED** | `NotesPage.qml`, `loadNotes` / `createNote` — **new this round** |
| `SettingsPage` | **IMPLEMENTED** | `SettingsPage.qml`, `loadAiConfig` / `saveAiConfig` / `clearAiConfig` |
| `AIRunsPage` | **PARTIAL** | `RunsPage.qml`, `startAgentRun` / `cancelAgentRun`; no run detail |
| `AgentPage` | **PARTIAL** | the runs page covers start and cancel |
| `MyAIPage` | **PARTIAL** | provider configuration lives in Settings |
| `ProjectDetailPage` | NOT_STARTED | — |
| `AIRunDetailPage` | NOT_STARTED | — |
| `AgentWorkbenchPage` | NOT_STARTED | — |
| `AgentPermissionsPage` | NOT_STARTED | the bridge exposes no permission slots |
| `ChatPage` | NOT_STARTED | `streamAnswer` exists for RAG; no chat UI |
| `ConversationPage` | NOT_STARTED | — |
| `GraphPage` | NOT_STARTED | — |
| `PlannerPage` | NOT_STARTED | — |
| `SearchPage` | NOT_STARTED | — |
| `WorkbenchPage` | NOT_STARTED | — |
| `HomePage` | N/A | the dashboard is the entry point |
| `LoginPage` | N/A | single-user desktop, no login |
| `OnboardingPage` | N/A | replaced by first-run initialisation (section B) |
| `NotFoundPage` | N/A | one window, no routes |

**8 IMPLEMENTED · 3 PARTIAL · 10 NOT_STARTED · 4 N/A.**

### D.2 The core loop that works

配置 AI → 创建知识库 → 导入资料 → 检索 → RAG 问答（含引用）→ 创建任务 → 创建项目 → 写笔记 → Agent 运行 → 查看运行记录. The knowledge-base loop was built in round 4 and was **reused, not rewritten**; this round added Projects and Notes.

### D.3 A defect that had been there from the start

**No page could talk to the shell.** The wiring read:

```qml
Component { id: projectsPage; ProjectsPage { shell: shell } }
```

In QML an unqualified name inside an object's binding resolves to that object's **own**
properties before it resolves to an outer id. So the right-hand `shell` was the page's own
(empty) `shell` property, and every page was constructed with `shell = null`.

The pages still rendered — their layout does not depend on it. What did not work was
everything that calls back into the shell: `shell.request` threw
`Cannot call method 'request' of undefined` the moment a button was pressed, so **no page
could load or write anything**. The symptom was a page that looked finished and did nothing,
which is why it survived a screenshot check and a bridge-level test — the bridge-level test
drives the bridge directly and never asks a page to do anything.

Fixed by renaming the window's id to `shellRoot` and binding `shell: shellRoot`, with a
comment at the site because the trap is invisible.

Two more defects surfaced the same way once the pages could run:

* `property bool busy: bool = false` in `DocumentsPage.qml` — C++ initialiser syntax, which
  QML reads as a write to a global called `bool`. The property was never initialised.
* `property var data` in `DashboardPage.qml` — `data` is a built-in `Item` property (the
  default property that owns the item's children), and redeclaring it shadows the engine's
  own member.

The packaged application's self-check now fails on any QML warning or error, which is what
would have caught all three. A QML error does not stop the window from appearing; it makes a
binding evaluate to `undefined` and leaves a control that looks right and does nothing.

### D.4 Keyboard navigation and accessibility — verified by pressing keys

`desktop-native/verify_native_shell.py` sends real key events with `QTest` and then asks the
window where focus went. **29/29 pass.** Nothing in it reads the QML source to decide whether
the keyboard works.

```
  [PASS] shell 暴露了页面清单 — 7 个页面
  [PASS] shell 暴露了导航栏条目 — 9 个条目
  === 1. 快捷键 Ctrl+N ===         7/7   Ctrl+1..7 each open the page the rail's hint names
  === 2. 导航栏方向键 ===           6/6   Down walks 9 entries in order, Up returns,
                                         the ends hold (no wrap), Home/End jump
  === 3. 浏览与激活是两件事 ===      2/2   moving focus does not navigate; Enter opens
  === 4. Esc 关闭提示 ===           1/1
  === 5. Tab 焦点顺序 ===           3/3   name → description → create, Shift+Tab returns
  === 6. 按钮可以用键盘触发 ===      4/4   typing reaches the field; Space fires the button
                                         and a project row appears in PostgreSQL
  === 7. 无障碍名称与角色 ===        3/3   9 rail entries + 3 page controls, read at runtime
  === 8. 焦点可见性 ===             1/1
  结果：29/29 通过
```

Two things about how this is done, because they were both wrong first:

* **Focus is read from `QWindow.focusObject()`, not from a walk over `findChildren`.** Repeater
  delegates are not reachable that way from Python — the rail's nine entries exist, carry their
  names and take focus, and a tree walk finds none of them. The first version of this verifier
  reported "focus never left the window" while focus was on `nav-dashboard` the whole time.
* **The accessible name and role are read while each entry holds focus**, which is both the only
  way to reach them and the only way to read what a screen reader would be given. `Accessible.name`
  is a QML attached property with no Python accessor, so the QML binds it to a mirrored property
  and the test reads that — the same string, not a copy.

Status must not be carried by colour alone: the rail's state is checked to be a non-empty word,
and the same word is what the accessible name carries.

### D.5 Not done

* **Streaming chat UI.** `streamAnswer` exists and real SSE streaming was verified in round 4,
  but no page consumes it. `ChatPage` and `ConversationPage` are `NOT_STARTED`.
* **Graph, Planner, permissions, run detail, search, workbench.** All `NOT_STARTED`; the bridge
  has no slots for them.
* **Dialogs and focus restoration.** Pages use toasts and inline state; there is no modal
  dialog and therefore no focus-restoration behaviour to verify.
* **Multi-monitor / DPI.** `Screen.devicePixelRatio` drives the scale, but only one display
  exists here.

---

## E. 模型与费用

**No real model call was made this round. ¥0.00 spent, cumulative ¥0.01 across the programme.**

The round's work did not need one: the agent-safety work is proved by killing real processes
and reading the database, and the packaging acceptance proves the application starts, reaches
PostgreSQL and writes nothing into its own directory. Adding a paid call to any of those would
not have made the evidence stronger.

| Item | State |
|---|---|
| Real provider round-4 evidence (streaming, multi-turn, long input, agent tool call, RAG) | **PASS_REAL** — unchanged, cited, not re-run |
| Stub provider transport tests | **PASS_STUB** — 9/9 |
| This round's real calls | **NOT_RUN** — deliberately |
| Embedding | **BLOCKED** — unchanged. DeepSeek `/embeddings` returned 404 and no alternative provider is configured; the offline hash/n-gram embedder is **not** a real semantic embedding and is not reported as one |

The API key was not read, not used, and does not appear in any file, commit, command line or
report from this round.

---

## F. 测试与风险

### F.1 What was run

| Suite | Result |
|---|---|
| Backend, full (`pytest tests`) | **1,278 passed / 10 skipped / 0 failed** in 684 s |
| New: `test_release_freeze_tool_invocation.py` | 12 passed |
| New: `test_release_freeze_tool_replay.py` | 37 passed |
| `verify_release_freeze_mutations.py` | **22/22 defects caught** |
| `verify_native_shell.py` | 29/29 |
| `verify_native_package.py` | 10/10 |

The mutation check is the evidence that the new tests are regression tests rather than
couplings: it reintroduces each defect into the fixed tree and requires the test to fail. The
new cases cover the commit-before-dispatch ordering, the outcome closing the window, recovery
scope, the idempotency key, the refusal status, the `unsafe` default, the unknown-tool default,
and the registry-shape resolver.

### F.2 A note on test stability

An earlier full run of the same suite reported **5 failures**, all in agent execution. Two were
real and are fixed (section F.3). Three were **CPU-contention flakes**: that run overlapped a
PyInstaller build that was saturating the machine, and the affected tests spawn subprocesses
and talk to a local stub HTTP server with timeouts. All three pass when nothing else is running,
which is how the final 1,278/0 was obtained. They are timing-sensitive by construction and are
worth noting as a known fragility rather than pretending the first run was noise.

### F.3 A defect this round introduced and then fixed

The executor was changed to call `registry.get_spec(name)`. `get_spec` is a convenience that
the two production registries happen to provide; `specs()` is the contract every registry
satisfies — including the small stand-ins the test suite drives the executor with. Four tests
that had nothing to do with tool classification stopped passing.

Fixed by `app.ai.tools.registry.spec_for()`, which asks `get_spec` when it exists and falls back
to a scan of `specs()`. The answer still has to be *right*, not merely present: the executor
uses it to decide whether a call can be repeated after a crash, so a miss means "unknown tool",
which is treated as unsafe.

### F.4 Data integrity

The development database was at revision `81cce5214260`, two migrations behind head. It was
**backed up first** (`pg_dump -Fc`,
`.runtime/backups/aiworkbench-before-tool-call-invocations-20261010-022306.dump`, 1.2 MB), then
`alembic upgrade head` was run. Both migrations are additive — the first adds `revoked_tokens`,
the second adds four columns to `tool_calls` — and neither drops or rewrites anything. Before
and after:

```
  users 45 · ai_runs 167 · documents 11 · tool_calls 57   (identical before and after)
  revision 81cce5214260 → 6a1884572148
  new columns: finished_at, invocation_id, mutates_state, started_at
```

The test database was migrated the same way. The user's data directory was never touched.

### F.5 Open risks

| Risk | Severity | State |
|---|---|---|
| Window 1: killed between flush and commit leaves no record | medium | **Unfixed by design** — narrowed to a commit, documented in C.4 |
| `UNKNOWN` invocations need a human to resolve | medium | **Unfixed** — the system reports, it does not reconcile |
| No tool provides a reconciliation check | medium | **Unfixed** — no auto-replay exists, so the risk is contained rather than solved |
| 10 old pages not migrated | medium | Planned, section D.1 |
| Streaming chat UI absent | medium | `streamAnswer` exists; no page consumes it |
| Qdrant unreachable from the packaged build | low | Needs the key configured through the application's own settings |
| No installer, no code signing | medium | No certificate; no clean machine |
| Timing-sensitive tests | low | See F.2 |

---

## G. 交付位置

| What | Absolute path | Exists |
|---|---|---|
| Native application | `C:\Users\user\Desktop\ai-workbench\desktop-native\dist\AI-Workbench-Native\AI-Workbench-Native.exe` | yes — 20,899,343 bytes, SHA-256 `3b652ded9c072cbc108b13c8e5f1895730eaf137492b41621511285d80289aed` |
| Package directory | `C:\Users\user\Desktop\ai-workbench\desktop-native\dist\AI-Workbench-Native\` | yes — 255 MB, 2,267 files |
| Build | `C:\Users\user\Desktop\ai-workbench\desktop-native\build_native.py` | yes |
| Package acceptance | `C:\Users\user\Desktop\ai-workbench\desktop-native\verify_native_package.py` | yes |
| Keyboard/accessibility acceptance | `C:\Users\user\Desktop\ai-workbench\desktop-native\verify_native_shell.py` | yes |
| Agent-safety fault injection | `C:\Users\user\Desktop\ai-workbench\backend\tests\integration\_invocation_child.py` + `test_release_freeze_tool_invocation.py` | yes |
| Mutation check | `C:\Users\user\Desktop\ai-workbench\scripts\verify_release_freeze_mutations.py` | yes |
| 2.2.0 archive (unchanged) | `C:\Users\user\Desktop\ai-workbench\archive\releases\AI-Workbench-2.2.0-final.exe` | yes — 97,311,474 bytes, SHA-256 `698db63662cc24e42873884077ada51b7b97c5ccd45ab3616acd15ad2df0d3d7` |
| Database backup taken before migrating | `C:\Users\user\Desktop\ai-workbench\.runtime\backups\aiworkbench-before-tool-call-invocations-20261010-022306.dump` | yes |

**No installer is delivered**, because none was built. The package above runs from its own
directory; turning it into an installer is the next step.

---

## Next stage

1. **NSIS installer** wrapping `desktop-native/dist/AI-Workbench-Native/`, with the same
   install/user-data separation the acceptance already proves, plus an uninstaller that asks
   about the data rather than deciding.
2. **Streaming chat page** consuming the `streamAnswer` slot that already exists.
3. **Run detail and agent permissions pages** — needs bridge slots first.
4. **Graph, Planner, Search, Workbench** — the remaining `NOT_STARTED` pages.
5. **A reconciliation hook for `UNKNOWN` invocations**: let a tool answer "did this already
   happen?" so the one window that currently needs a human can be closed.
