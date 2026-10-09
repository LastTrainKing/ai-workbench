# Final verdict — AI Workbench 2.2.0

| | |
|---|---|
| Branch | `fix/release-freeze-20261009` |
| **Final commit** | **`8c0270b4b17c7af0d31dcf2dd5f9904eb36561fc`** |
| Working tree | clean |
| Version | 2.2.0, consistent across five carriers |
| Installer | `build/installer/AI-Workbench-Setup.exe` |
| Bytes | **97,311,474** |
| SHA-256 | `698db63662cc24e42873884077ada51b7b97c5ccd45ab3616acd15ad2df0d3d7` |
| Signed | **No** — no certificate on this machine |

---

## Verdict: **D — NATIVE_UI_MIGRATION_IN_PROGRESS**

with the current build conditionally releasable as a **transitional** version (B) under the
conditions below.

### Why not A (`RELEASE_READY`)

A requires "all confirmed blocking defects fixed". **RF-01 is a confirmed blocking defect
and it is not fixed**: the desktop interface is a React page rendered by WebView2, and the
requirement is a native Windows UI. Nothing in this round changed that, and nothing could
have — it is a rewrite of the presentation layer, not a repair.

### Why D rather than C (`BLOCKED`)

C is for "a clear blocking defect, or key release evidence missing". RF-01 is a clear
blocking defect, so C is defensible. D is more precise, and D is the one that fits, because
D's three requirements are all met:

| D requires | Status |
|---|---|
| A runnable technical-validation version | `desktop-native/` — real Qt window, 4 pages, 2 IPC transports, **28/28 checks passing**, screenshot in `desktop-native/docs/` |
| A page migration inventory | `NATIVE-UI-MIGRATION.md` — all 25 pages, tiered by measured complexity |
| An estimate based on actual validation | `NATIVE-UI-MIGRATION.md` — derived from the PoC's measured size, not from reading the code |

The difference matters for the next decision. C would say "stop"; D says "the current build
is verified and archivable, and the rewrite is now a costed project with a running
prototype".

### Why the current build is only *conditionally* releasable

If you ship 2.2.0 as it stands, it is a **transitional** release, and B requires that to be
said plainly:

* it still uses WebView2, and the native UI is not done;
* the installer is **unsigned** — SmartScreen will warn, and that is not fixable here;
* six verification items are blocked by this machine (see `RELEASE-ACCEPTANCE.md` §4);
* the user has to accept it as transitional, not as the finished product.

It must not be described as the final product.

---

## What is verified

**Source and automated** — 1204 backend tests pass (10 skipped, 0 failed); 127 frontend
tests pass; lint clean on backend and PoC; typecheck 0 errors; Rust release build succeeds;
secret scan clean on 8 surfaces and across all of Git history; five version carriers agree.

**Every fix catches its defect** — 14 defects were each reintroduced by a targeted edit and
the test that is supposed to catch it was run. **14/14 caught.** A test that passes with its
defect back is not a regression test, and this is the only way to tell.

**Local integration** — a fresh install ran end to end: install, first start (schema built
from the migrations at `6068bb13c474`), account and run created over the installed API,
close, restart, run survived, repair, uninstall, ports released. **32/32.** The native UI
PoC: **28/28**. Qdrant authentication verified at runtime: unauthenticated **401**,
authenticated **200**, and the backend reads the same key the launcher wrote.

**Real data untouched** — `%LOCALAPPDATA%\AI Workbench Data` is 1,530 files / 51,775,575
bytes with an identical mtime, before and after everything in this round.

**Four defects were found by running things rather than reading them**, three of them
introduced by this round's own changes:

| | What | Consequence if shipped |
|---|---|---|
| N-1 | The Qdrant key was written into the program directory instead of the runtime directory | Every vector call would have returned 401, and the uninstaller could not remove the install directory |
| N-2 | `VERSION` was in the payload but not in the uninstall list | A fresh uninstall left the install directory behind |
| N-3 | The installer runner passed the command line through `cmd /C` and the quotes were escaped | The install silently did not happen |
| N-4 | A verification script kept its own list of terminal statuses | Correct behaviour reported as a failure |

## What is not verified, and why

**No API key exists** (you closed it). 13 items are marked `BLOCKED_NO_API_KEY`. No work
stopped because of it, and nothing was faked — the mock and offline paths verify real
business state (the agent run is checked against `ai_runs` and required to report
`INCOMPLETE` / `max_steps`, not a success string).

**Six items are blocked by this machine**: multi-monitor DPI, Docker, code signing,
SYSTEM-level symlink behaviour, a clean-room machine, and Qt packaging.

---

## Minimum test list for when the new API key arrives

**Seven remain.** Six were closed in the next round using a local stub endpoint, because they
never needed a key — only a controllable endpoint. See `ROUND-3-REPORT.md`.

**Closed without a key** (all passing, against a real HTTP stub on loopback):
empty response, malformed response, invalid JSON, loop detection, step ceiling, mid-run
process kill, plus HTTP 503 and client timeout.

| # | Test | Pass condition |
|---|---|---|
| 1 | One real `POST /chat/rag` against DeepSeek | Non-empty answer; `ai_runs` row `SUCCEEDED`; token counts non-zero |
| 2 | One real agent run whose goal needs a tool | The tool actually ran; the observation reached the next turn |
| 3 | RAG grounding on a real answer | `grounded=true` **and** every claim-bearing sentence carries a citation that resolves to a retrieved source |
| 4 | Key entry through the native UI Settings page | Key saves, survives a restart, reads back as `api_key_set` with a mask and never in plaintext |
| 5 | Embedding with a real provider | Collection dimension matches; switching provider does not mix old vectors into the new collection |
| 6 | Vendor-specific error semantics | A real 401/429 from DeepSeek is classified as the code claims, not only a synthetic one |
| 7 | Whether the old key was revoked | The vendor dashboard says so |

The provider entry point is in place for when the key arrives: **Settings → AI Provider** in
the native UI, or `POST /api/v1/ai-config` in the WebView build. Either writes to the
account's own configuration file and takes effect on the next call. **No source edit, no
rebuild, no re-hardcoding.**

---

## The native UI, next

`NATIVE-UI-MIGRATION.md` carries the detail. The short version:

* **~134.5 engineer-days** total: service extraction 8, IPC 5, shell and design system 10,
  the 24 remaining pages 73.5, tests 20, packaging 8, acceptance 10.
* **15–17 weeks** for two engineers, **11–13** for three, ~27 for one.
* The previous 9–15 week figure is **withdrawn** — it was written from reading the code and
  the measured one does not agree with it.
* The estimate is weakest on three pages worth 19 of the 73.5 port-days: the conversation
  and chat views (streaming text into a native view with no DOM) and the graph page (a force
  simulation without d3).
* **One decision is needed before work starts**: does the desktop product keep the HTTP API?
  The PoC assumes it does.

## What I would not sign off on

* **RF-01.** Not done, and it is the difference between this being the product you asked for
  and a transitional build.
* **The unsigned installer.** Fixable only with a certificate.
* **`ruff format --check`.** Pre-existing and unrelated; I did not reformat 103 files during
  a freeze, and I am not claiming that check passes.
* **The 13 unverified model-dependent items.** No key, no verification, no claim.
