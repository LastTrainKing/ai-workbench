# Delivery — AI Workbench 2.0.0

The single authoritative delivery document. Two older files described delivery
(`PROJECT_DELIVERY.md`, `FINAL_CHECKLIST.md`) and claimed versions 1.0.0 and 1.1.0, which
contradicted each other and the application. They are kept under `docs/archive/` as
historical records rather than deleted, because what the system *was* is worth keeping.

---

## 1. What ships

| Artifact | Path | Size |
|---|---|---|
| Desktop application | `desktop/src-tauri/target/release/ai-workbench-desktop.exe` | 3,556,352 B |
| Windows installer | `desktop/src-tauri/target/release/bundle/nsis/AI Workbench_2.0.0_x64-setup.exe` | 1,123,285 B |
| Source archive | `AI-Workbench-2.0.0-final.zip` | 5.5 MB, 373 entries |
| SHA256 | `969cee258a694a6757aacc22e0e8791267e1997ee03ee06d0486219265950986` | |

The archive contains the backend, the frontend, the Tauri shell, the scripts, the
documentation and the tests. It contains no build output, no virtual environment, no
runtime data, no user data and no credentials.

---

## 2. Running it

### The desktop application

Double-click `AI Workbench.lnk` on the desktop. The application starts PostgreSQL, Redis,
Qdrant and the API itself, then opens its own window.

There is no browser, no address bar, no console window and no port 5173.

If the shortcut is missing, recreate it:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\create-shortcut.ps1
```

The script fails loudly if the executable has not been built, and refuses to fall back to
a browser launcher. Build it with:

```bash
cd desktop
npm install
npm run tauri build
```

### Closing it

Closing the window stops the backend. This is guaranteed at the operating-system level by
a Windows job object (`KILL_ON_JOB_CLOSE`), so a crash or a force-kill cannot leave
services running. Shutdown never deletes data.

### From source

```bash
scripts\start.bat              # infrastructure, or: scripts\start.bat --hidden

cd backend
..\.venv\Scripts\python.exe -m app.cli serve

cd frontend
npm run dev                    # development only
```

---

## 3. What was verified, and how

Every claim below has a command behind it. The scripts live in `scripts/`, run against a
live server, and print PASS or FAIL with the evidence that decided it.

| Claim | Script | Result |
|---|---|---|
| Shortcut opens a native window, not a browser | `verify-desktop-entry.ps1` | 12/12, three exit scenarios |
| The `.lnk` points at the executable | `verify_shortcut.py` | 7/7 |
| The agent workbench is wired to the API | `verify-agent-ui.mjs` | 26/26 |
| The context screens are wired to the API | `verify-context-ui.mjs` | 22/22 |
| The window behaves like an application | `verify-shell-behaviour.mjs` | 21/21 |
| Keyboard navigation works | `verify-focus-grid.mjs` | 17/17 |
| The splash reports per-service readiness | `verify-splash.mjs` | 10/10 |
| Every old route survives the sidebar change | `verify-sidebar.mjs` | 10/10 |
| Every context endpoint behaves | `test_context_api.py` | 31/31 |
| The memory loop is pure | `test_memory_loop.py` | 18/18 |
| Personalisation changes the output | `test_personalisation.py` | 8/8 |
| Backend unit tests | `pytest tests/unit` | all pass |
| Frontend tests | `npm run test` | 114 pass |
| Types, lint, production build | `tsc`, `eslint`, `vite build` | clean |

---

## 4. Installing on another machine

**The installer is not self-contained.** It installs the application, but the application
expects the project directory to exist, because the backend, the vendored PostgreSQL /
Redis / Qdrant runtimes and the startup scripts all live there. This is a real limitation,
stated here rather than discovered later.

To move the project to another Windows machine:

1. Copy the whole project directory (or unzip the source archive).
2. Install Node.js and Python 3.13 if they are not present.
3. `cd backend && python -m venv ..\.venv && ..\.venv\Scripts\pip install -r requirements.txt`
4. `cd frontend && npm install && npm run build`
5. `cd desktop && npm install && npm run tauri build`
6. `powershell -ExecutionPolicy Bypass -File scripts\create-shortcut.ps1`

The vendored runtimes under `vendor/runtime/` are portable — no separate PostgreSQL, Redis
or Qdrant installation is needed.

---

## 5. Configuration

### AI provider

Settings → AI Provider, or `.runtime/ai-config.json` (git-ignored). The API key is
write-only: reads return `api_key_set` and a mask. `.env` is the fallback baseline and is
never committed.

The default is DeepSeek. If a cloud provider is selected but no key is available, the
factory degrades to the offline engine and records `degraded: true` with the reason — the
product keeps working and never pretends a hosted model answered.

### Permissions

Settings → Agent Permissions, or `.runtime/agent-policy.json`. Four levels, three modes.
Protected locations are merged in from the built-in policy on every load, so a settings
file can add protections but never remove them.

### Workspace

`<project>/.runtime/workspace` — the agent's own area, with a fixed layout:

```
deliverables/   files produced for the user
notes/          drafts and working notes
sources/        material imported or staged
.tmp/           scratch; safe to empty
```

Overridable with `AGENT_WORKSPACE` in `.env` or the `AI_WORKBENCH_AGENT_WORKSPACE`
environment variable. Relative paths in tool calls resolve here, and a bare filename
given to `document_write` is routed into `deliverables/`.

It used to be `%USERPROFILE%\Desktop\AI Workbench Workspace`, which is how a deck the
agent produced ended up sitting on the desktop next to a game launcher. See
[WORKSPACE.md](WORKSPACE.md).

---

## 6. Data and where it lives

| What | Where |
|---|---|
| Database | `vendor/runtime/pgsql/data` |
| Vectors | `vendor/runtime/qdrant` |
| Agent workspace | `.runtime/workspace` (`deliverables/`, `notes/`, `sources/`, `.tmp/`) |
| Snapshots | `.runtime/snapshots` |
| Logs | `.runtime/logs`, including `desktop-lifecycle.log` |
| Configuration | `.runtime/ai-config.json`, `.runtime/agent-policy.json` |

None of it is committed. `scripts/stop.bat` stops the services and preserves everything.

### Sample outputs

`samples/demo-output/` holds the real artifacts the acceptance runs produced, kept as
evidence rather than described:

| File | What it is |
|---|---|
| `答辩PPT-第一版-11页.pptx` | 70,538 B / 11 slides, produced from three imported documents (Demo 1, version 1) |
| `答辩PPT-第一版-11页.png` | its cover, rendered by LibreOffice |
| `答辩PPT-第二版-2页-FAILED.pptx` | the 34,355 B / 2-slide deck from a run that ended `FAILED` — the case that produced the `PARTIAL` state |

`archive/` holds what was filed off the desktop rather than deleted: old release ZIPs, a
runtime backup, and the pre-convention agent workspace. It is gitignored — hundreds of
megabytes of binaries that nothing in the build reads.

---

## 7. Known limits

Stated plainly. A project that hides its edges is a project you cannot trust.

- **No browser control.** `screenshot` and `screen_read` observe the screen. There is no
  click, no typing, no page automation.
- **No mouse or keyboard control.**
- **No OCR.** A scanned PDF with no text layer is reported as such.
- **Embeddings are offline hashes**, not a semantic model — DeepSeek exposes no embedding
  endpoint. Retrieval quality is correspondingly limited.
- **Ollama and OpenAI-compatible endpoints are untested against a live server.** The code
  paths exist and degrade correctly when unreachable; no real endpoint was used.
- **The Docker path is unverified.** It is written and documented; it has not been run.
- **The installer is not self-contained** (section 4).

---

## 8. Repository layout

```
backend/          FastAPI, SQLAlchemy, the agent, the tools, the RAG pipeline
frontend/         React + TypeScript + Vite
desktop/          Tauri shell (Rust) and the splash screen
scripts/          Start, stop, verify, build
docs/             Architecture and reference documentation
docs/archive/     Superseded documents, kept as history
vendor/runtime/   Portable PostgreSQL, Redis and Qdrant
.runtime/         Configuration, logs, snapshots (git-ignored)
```
