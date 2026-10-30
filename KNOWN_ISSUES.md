# KNOWN ISSUES — AI Workbench 2.2.0

> **2026-10-30 更新**：KI-03（ruff format）已关闭，见该条。

Things that are **not fixed**. Each is either an external block or a defect this round
chose not to fix, with the reason stated. Nothing here is a fixable defect renamed as a
"limitation" — if it could be fixed in this environment, it is fixed and in
`docs/archive/FULL_REMEDIATION_REPORT.md`.

---

## Release blockers

### KI-00 — No API key exists, so no model-dependent behaviour is verified

**What.** The user closed the previous API key. Everything that needs a real model call is
unverified: provider returns, timeouts, network failures, malformed responses, tool calling
against a real model, real RAG grounding, real embeddings, and whether the old key was
revoked at the vendor.

**Why it is not fixed here.** There is no key, and this round is explicitly forbidden from
finding, borrowing or substituting one.

**What was done instead.** Offline providers and a **real local HTTP stub** were used, and
they verify real business state rather than a success string: the agent run is checked
against `ai_runs` and required to report `INCOMPLETE` / `max_steps`. Eight of the items that
were carried as blocked are now **closed** — empty response, malformed response, invalid
JSON, loop detection, step ceiling, mid-run process kill, HTTP 503 and client timeout — all
against the real transport. They never needed a key, only a controllable endpoint. See
`docs/archive/ROUND-3-REPORT.md`.

**What would close it.** A key, and the seven-test list in `docs/archive/FINAL-VERDICT.md`. The entry
point is already in place: Settings → AI Provider, or `POST /api/v1/ai-config`. No source
edit, no rebuild.

### KI-01 — The desktop UI is a WebView2 page, not a native interface

**What.** The window is native; its contents are a React build rendered by WebView2. The
requirement is a native Windows UI (Qt 6 / Qt Quick preferred) with no embedded web page.

**Why it is not fixed here.** It is a rewrite of the presentation layer, not a repair.

**The estimate below is superseded.** A runnable Qt 6 / QML proof of concept was built and
measured in the next round, and the figure derived from it does not agree with the one that
was written from reading the code. Use `NATIVE-UI-MIGRATION.md` — **~134.5 engineer-days,
15–17 weeks for two engineers** — and treat the table below as the withdrawn first guess.

| Phase | Work | Rough effort |
|---|---|---|
| 1 | Service-layer boundary: every capability the UI uses gets an explicit Python API, no HTTP in the production path | 1–2 weeks |
| 2 | IPC: named pipe (or in-process adapter) with message length caps, request ids, timeouts, cancellation, error shape, permission checks, child-process cleanup | 1 week |
| 3 | Qt 6 / QML shell: splash, shell, navigation, focus/controller states, big-card layout | 2–3 weeks |
| 4 | Screen-by-screen port (conversation, knowledge, tasks, runs, delivery, settings) | 3–5 weeks |
| 5 | Packaging: DLLs, Qt plugins, fonts, runtime; NSIS update; delete the Tauri tree | 1–2 weeks |
| 6 | Verification: keyboard navigation, DPI, file dialogs, crash recovery, clean install | 1–2 weeks |

**Rough total: 9–15 weeks of focused work.** The honest scheduling unit is "a quarter", not
"the next round".

**Interim state.** The Tauri shell remains. Its CSP is `null` (KI-02) — see below.

### KI-02 — `tauri.conf.json` CSP is `null`

**What.** `"security": { "csp": null }`, and the frontend keeps its JWT in `localStorage`.

**Why it is not fixed here.** A correct CSP has to be derived from what the app actually
loads — the API origin, the asset scheme, inline styles from Tailwind, the markdown
renderer's images. A guessed policy is either so permissive it adds nothing, or it breaks a
screen and gets loosened until it adds nothing. Both outcomes look like "CSP added" in a
report and neither is a fix.

It is also on the path being deleted: the whole WebView layer goes away in KI-01, and a CSP
tuned for it is work thrown away. The real fix is KI-01, and it removes the need for this
one.

**What would close it.** Either a reviewed policy derived from a recorded request log
(half a day, plus a test that asserts the header), or the Qt migration.

---

## Blocked by this machine

### ~~KI-03 — `ruff format --check app tests` fails on 103 files~~ — **CLOSED**

**What.** The documented check in `docs/testing.md` did not pass.

**Why it was not fixed then.** It was pre-existing and unrelated to that round: an untouched
file (`app/core/logging.py`) also failed it, so the check had never passed on this repository.
Running the formatter during a release freeze would have produced a whitespace diff across a
third of the codebase and buried the actual changes.

**Closed on 2026-10-30** exactly as the "what would close it" note prescribed — as its own
commit, no behaviour change:

```
$ ruff format app tests
136 files reformatted, 98 files left unchanged

$ ruff format --check app tests
234 files already formatted

$ ruff check app tests
All checks passed!

$ python -m compileall -q app tests      # exit 0
```

**How it was checked that nothing changed but whitespace.** For a 40-file sample, the set of
`FunctionDef` / `AsyncFunctionDef` / `ClassDef` names was compared before and after with
`ast` — **0 structural differences**. Combined with `compileall` exit 0, the change is
formatting-only.

Diff: 136 files, +2286 / −1099.

### KI-04 — Multi-monitor DPI is unverified

**What.** 100 / 125 / 150 / 175%, mixed-DPI, display hot-plug, taskbar, off-screen recovery.

**Why.** This machine has one display and a fixed scale factor. The window-clamp fix from an
earlier round was verified at 1536×912 @125% only.

**What would close it.** A machine with two displays at different scale factors, or a VM
where the DPI can be changed. Until then the release verdict stays "not release-ready".

### KI-05 — `docker compose` has never been executed

**What.** The compose file's own header says "provided and reviewed, not executed", and
that is still true.

**Why.** No Docker installation; the registry endpoint is unreachable.

**What was done instead.** The static security defects are fixed and asserted: the data
services are no longer published, the API and frontend bind to loopback, weak/absent
credentials are now required environment variables, Qdrant has CORS off and an API key
wired to both sides. `pytest tests/unit/test_release_freeze_deployment.py` checks the file.

**What would close it.** A host with Docker and registry access: `docker compose config`
showing loopback-only bindings, then up / health / login / ingest / agent / stop / restart.

### KI-06 — The installer is unsigned

**What.** No Authenticode signature.

**Why.** No code-signing certificate on this machine.

**What would close it.** A trusted certificate and a signing step in the build. Until then
the installer will show a SmartScreen warning, and the release notes must say so rather
than implying otherwise.

### KI-07 — Symlink/junction escape is verified only at WORKSPACE level

**What.** Path resolution against reparse points, TOCTOU between check and use.

**Why.** The SYSTEM-level case needs an elevated shell on NTFS with real reparse points.
This session does not hold that. WORKSPACE-level behaviour is verified and passing.

---

### KI-14 — 11 unit tests fail on a clean Windows checkout (pre-existing)

**What.** `pytest tests/unit` on Windows with current dependency versions:
**797 passed, 11 failed, 2 skipped**.

| File | Failing tests |
|---|---|
| `test_release_freeze_skill_network.py` | 5（allow-list / redirect 相关） |
| `test_security_fixes_round15.py` | 5（`test_allowlisted_public_hosts_still_pass` ×4 + `test_posting_to_a_trusted_host_is_confirmed`） |
| `test_user_data_isolation.py` | 1（`REAL_TRAILING_DOT`） |

**Proven not caused by the formatting commit.** The same 11 fail on the pristine `HEAD`
(`git stash` → run → same 11 failed / 59 passed in that subset → `git stash pop` → identical).

**Likely cause.** Two groups:
- The network/allow-list ones look like DNS or egress differences on this machine
  (the redirect tests need a reachable host to resolve).
- `REAL_TRAILING_DOT` is a Windows path-normalisation difference: `Path(".../data.")`
  is normalised away by Windows, so the guard never sees the trailing-dot form it
  expects to reject.

**What would close it.** Run the same suite on Linux (CI does) to see whether it is
platform-only; for the trailing-dot case, decide whether the guard should compare the
*normalised* path (which would make the test assert on a form Windows cannot produce).

**Status.** Open. Not fixed — fixing the trailing-dot case changes a security guard's
semantics, which is not a formatting-round decision.

---

## Accepted trade-offs (not defects)

These are deliberate design positions, recorded so they are not mistaken for gaps.

### KI-08 — Embedding is an offline hash n-gram without a provider key

The default with no embedding service configured is a deterministic hash/term retriever. It
is a usable local fallback; it is **not** a trained semantic embedding, and offline
measurements must not be presented as semantic retrieval quality. `docs/` says so. A real
provider can be configured, and the health check reports which mode is active.

### KI-09 — The Skill Scanner is a rule list, not a classifier

It cannot identify every malicious skill, and the UI must not claim it can. Installation
always shows source, licence and required capabilities and always requires an explicit
`confirmed: true`. The scanner is a filter, not a boundary; the permission layer is the
boundary.

### KI-10 — Provider retry is bounded but not rate-limited

HTTP providers have timeout, retry and maximum-response-body policies. There is no global
rate limiter: a user who configures a provider and hammers a button can still generate
requests. Acceptable for a single-user desktop product; it would not be for a shared
service.

### KI-11 — The development `.runtime` grew during these rounds

24,156 → 59,430 files. The growth is the test suite's own footprint (`pgdata` test database,
`test-storage`, `logs`) plus the Qdrant API key file the launcher now writes there. No user
document, snapshot, memory entry or configuration file was deleted or rewritten. The
**installed** data directory is byte-identical (1,530 files / 51,775,575 bytes / same mtime,
before and after both rounds). Recorded because "the file count changed" is exactly the kind
of thing a report should say rather than leave for someone to find.

### KI-12 — DeepSeek key rotation is unconfirmed

A live-looking key sat in a commented line of the development `.env`. It has been removed
and is not reproduced anywhere. Whether it was revoked at the vendor **could not be
checked** — this round permits no real model calls. Treat it as "assume compromised until
the vendor dashboard says otherwise".

### KI-13 — `verify_installer_acceptance.py` leaves ~50 MB of test data behind

**What.** The script wipes `%LOCALAPPDATA%\AI Workbench Data TEST` at the start of a run and
does not remove it at the end. It is clearly named as test data and is separate from the
real directory, but it is 50 MB of residue on the user's machine.

**Why it is not fixed here.** Non-blocking tidiness, and the discipline rule for this round
is that non-blocking improvements are registered for the next version rather than expanding
the current scope. The residue from this round's own runs was removed.

**What would close it.** A cleanup step at the end of the script with a `--keep` opt-out.

---

## Open items carried from earlier rounds (not re-verified this round)

These were open before this round and this round did not re-test them. They are listed so
the list is complete, not because they were checked.

| Item | Status |
|---|---|
| `PostgreSQL FAILED - shutdown timeout` on elevated close | Not reproduced, not disproved. The two contributing defects (exit code discarded, 60s wait on a pid file) were fixed in an earlier round; the fix is not verified against a real failure. |
| Startup-screen draft reaching the input box, end to end | Not tested end to end. |
| `RARITY_FLOOR` / `RELEVANCE_FLOOR` measured on a small corpus | Thresholds work; they have no margin. Not re-measured. |
| Elevated launch cannot kill PostgreSQL via job object | Known limitation, root cause identified (the process is already in a job). Not fixable without changing the launch mechanism. |
| `start.sh` / `stop.sh` | Never executed on this machine. |
| Browser-driven UI flows (focus, keyboard, full-page, DPI) | Not performed. |

---

## What a "pass freeze" verdict would require

1. KI-01 complete — a native desktop UI with no WebView2 in the production path.
2. KI-04 verified on real hardware or a controllable VM.
3. KI-05 executed on a machine with Docker.
4. KI-06 signed, or the release notes stating plainly that it is not.
5. KI-03 closed with a formatting commit reviewed on its own.
6. The installer and ZIP rebuilt from the freeze commit, with hashes, and a clean-room
   install performed on a machine that has never seen the development tree.
