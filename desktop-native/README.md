# AI Workbench — native desktop UI (technical validation)

A **runnable** Qt 6 / Qt Quick proof of concept. It is not a mock-up and not a design
document: it opens a real window, renders real QML, and every page calls the real service
layer.

```powershell
# from the repository root

# 1. once
.venv\Scripts\pip.exe install PySide6-Essentials

# 2. run the window
.venv\Scripts\python.exe desktop-native\main.py

# 3. headless self-check (no display needed)
$env:QT_QPA_PLATFORM = "offscreen"
.venv\Scripts\python.exe desktop-native\main.py --self-check

# 4. full verification against the test database — 28 checks
.venv\Scripts\python.exe desktop-native\verify_native_ui.py

# 5. render the window to a PNG
.venv\Scripts\python.exe desktop-native\main.py --screenshot shot.png
```

## What is actually implemented

| Requirement | Where | Status |
|---|---|---|
| Native window create/close | `qml/Main.qml` (`ApplicationWindow`) | working |
| Main navigation | `qml/components/NavRail.qml`, Ctrl+1..4 | working |
| Theme and styling | `qml/theme.js` | working |
| Resize and DPI | `Theme.scale` + `Screen.devicePixelRatio` | working |
| Front/back IPC | `awbnative/bridge.py` (in-process), `awbnative/ipc.py` (named pipe) | both verified |
| Long-task progress and logs | `qml/pages/RunsPage.qml` + `progress` signal | working |
| File select / generate / open | `SettingsPage.qml` + `QFileDialog` + `openPath` | working |
| Agent run and cancel | `RunsPage.qml` + `startAgentRun` / `cancelAgentRun` | working |
| API configuration and credentials | `SettingsPage.qml` + `saveAiConfig` | working |
| Error notification | `Main.qml` toast, `failed(id, message, code)` | working |
| Packaging | not done — see the migration inventory | not started |

## Why there is no HTTP in this shell

The WebView build reached the backend at `127.0.0.1:8000`. That listener exists because a
web page can only speak HTTP. QML has no such constraint, so `awbnative/bridge.py` calls
the service layer **in the same process**.

What that removes: the localhost listener from the production path, CORS, bearer tokens and
token revocation (there is no token), the serialisation boundary, and the second process to
supervise.

What it does not remove: the permission layer. `saveTextFile` and `openPath` run the same
`PermissionManager` check the agent's own file tools do, and the verification asserts that
a protected path and an out-of-policy path are both refused.

`awbnative/ipc.py` implements the other option the requirements allow — a Windows named
pipe for when the backend must be a separate process — with a 1 MiB frame cap enforced by
`recv_bytes(maxlength)`, request-id correlation, a client-side timeout, and a closed method
registry so a compromised UI cannot turn the pipe into an arbitrary-code channel.

## Files

```
desktop-native/
  main.py                     entry point, font selection, self-check, screenshot
  verify_native_ui.py         28 checks against the test database
  awbnative/
    bridge.py                 QML-facing slots -> service layer (in-process)
    async_runner.py           one asyncio loop on a background thread
    ipc.py                    named pipe: framing, caps, ids, timeouts
  qml/
    Main.qml                  window, nav, page loader, toast, shortcuts
    theme.js                  palette and status->tone mapping
    components/               NavRail, Card, StatusPill
    pages/                    Dashboard, Tasks, Runs, Settings
```

## Verification output

`verify_native_ui.py` uses the test database and a temporary account, and removes both
afterwards. It never touches the development database or the installed data directory.

The checks are behavioural, not cosmetic — for example `createTask` is followed by a
`SELECT COUNT(*)` to prove a row appeared, and `completeTask` is followed by a read of the
stored status. The agent run is checked against `ai_runs`, and the run is required to
report `INCOMPLETE` / `max_steps` rather than a success, because the offline engine cannot
finish that goal.

## Known limitations of this PoC

* **Packaging is not done.** There is no PyInstaller/NSIS wiring for a Qt build, and the
  Qt runtime is not bundled. That is estimated separately in the migration inventory.
* **Four pages, not twenty.** The inventory in `NATIVE-UI-MIGRATION.md` lists every screen
  the WebView build has and what each needs.
* **No accessibility audit.** Qt exposes an accessibility tree; whether it is correct here
  has not been checked with a screen reader.
* **DPI is verified at one ratio.** This machine has a single display at 1.0; the scaling
  code is exercised but not across 100/125/150/175%.
