# Autonomous Agent

How AI Workbench turns a goal into finished work on your machine.
For the business-tool agent (knowledge, tasks, notes) see [`agent.md`](./agent.md).

---

## 1. The one design decision that matters

> **The model decides *what* to do. The code decides *whether it worked*.**

Most agent frameworks let the model grade its own homework: it says "I fixed the
bug", the framework believes it, the user finds out later that nothing was fixed.

Here, verification is a **tool call whose result is read**. The agent asks itself
"what tool call would prove this step?", runs it, and the exit code decides:

```
run_tests → exit code 1  →  step FAILED, regardless of what the model says
```

If a step cannot be verified with a tool, it is not considered verified. That
single rule is what makes the output of this system trustworthy.

---

## 2. The loop

```
   PLAN      model produces a structured plan: steps, each with a way to
             verify it, plus success criteria stated up front
     ↓
   SNAPSHOT  workspace captured before any change
     ↓
   ┌─────────────────────────────────────────┐
   │  for each step:                         │
   │    EXECUTE   model calls tools          │
   │    OBSERVE   results recorded           │
   │    VERIFY    tool call; exit code rules │
   │    passed? ──yes──► next step           │
   │      │ no                               │
   │    RECOVER   analyse the failure        │
   │    RETRY     (bounded)                  │
   └────────────────┬────────────────────────┘
                    ↓
   FINAL VERIFY  success criteria checked
                    ↓
   REPORT        honest summary + diff
```

### Run states

One enum, not a set of booleans. `is_running` + `is_paused` + `is_failed` admits
combinations that mean nothing, and that is where bugs live.

| State | Meaning |
|---|---|
| `PLANNING` | producing the plan |
| `READY` | plan exists, nothing executed |
| `EXECUTING` | a tool is running |
| `OBSERVING` | reading the result |
| `VERIFYING` | checking against reality |
| `RECOVERING` | analysing a failure |
| `WAITING_CONFIRMATION` | a human must decide |
| `PAUSED` / `CANCELLED` | user paused / stopped |
| `FAILED` | gave up: unrecoverable or out of retries |
| `LIMIT_REACHED` | hit a resource ceiling |
| `COMPLETED` | success criteria met |

---

## 3. What a plan looks like

The planner is required to produce, for every step, a way to verify it. A plan
whose steps cannot be checked is rejected at parse time rather than accepted and
patched up later.

```json
{
  "goal": "修复这个项目的测试",
  "risk_level": "low",
  "success_criteria": ["pytest 退出码为 0"],
  "steps": [
    { "id": "step-1",
      "description": "列出项目结构，确认测试命令",
      "verification": "directory_list 返回非空",
      "tool": "directory_list" },
    { "id": "step-2",
      "description": "运行测试并记录失败用例",
      "verification": "run_tests 退出码可读",
      "tool": "run_tests" }
  ]
}
```

---

## 4. Control

A run is a live process, not a database row.

| Action | What actually happens |
|---|---|
| **Pause** | blocks at the next checkpoint, between steps. Not mid-tool: stopping a file write halfway would leave the workspace inconsistent. |
| **Resume** | returns to the state it was paused in, not a guessed `EXECUTING`. |
| **Cancel** | cancels the running asyncio task, which propagates into the process runner and **kills the child process tree**. A cancelled build does not keep compiling. |

`POST /agent/runs` returns immediately with a `run_id`; the work continues on a
background task. A synchronous endpoint would leave the user no way to interrupt a
run that had gone wrong.

---

## 5. Resource limits

Every axis is bounded. A runaway agent hits a wall, not the user's patience.

| Limit | Default |
|---|---|
| tool calls | 100 |
| wall clock | 30 minutes |
| retries per step | 3 |
| tool calls per step | 12 |
| output per tool | 200 KB |
| files modified | 50 |
| download size | 200 MB |

Hitting a limit sets `LIMIT_REACHED` and stops. There is no infinite retry.

---

## 6. Failure classification

Retrying a permission refusal is pointless; retrying a network blip is not. The
executor classifies before the loop decides.

Retryable: `transient`, `network`, `timeout`, `model`, `tool`.
Not retryable: `permission`, `validation`, `cancelled`, `fatal`, `confirmation`.

---

## 7. Snapshots, diff and rollback

Before a run touches anything, the workspace is captured. Afterwards the system
answers three questions: *what changed*, *can I see it*, *put it back*.

The sequence that breaks naive implementations:

```
1. snapshot A
2. agent edits   → B
3. user edits    → C
4. rollback      → would silently destroy the user's edit at step 3
```

So a snapshot records **two** manifests: the state at capture, and the state the
run left behind. Rollback compares the workspace against the second one. If a file
no longer matches what the run produced, someone else has touched it, and the
restore **stops and reports a conflict** instead of overwriting. Forcing is
possible, but it has to be asked for.

Verified: after a simulated user edit, the unforced restore refused with
*"This file was modified after the agent finished"*; only `force` overrode it.

---

## 8. Audit trail

Every tool invocation writes a row — **including the ones that were refused**. A
log that only contains what ran cannot answer "what did it try to do, and what did
we stop?".

A refusal raised *inside* a tool (a permitted tool handed a denied path) rewrites
the verdict to `deny`. An audit trail that reports "allow" for something it
blocked is worse than no trail at all.

---

## 9. Verified behaviour

Both run against real DeepSeek with real files, not fixtures.

**Demo 1 — create a project from scratch.** The agent planned 6 steps,
snapshotted, created `demo1/`, wrote `demo_math.py` and `test_demo_math.py`, ran
the tests, then handled a git failure by analysing it, discovering the commit had
in fact succeeded, and re-verifying. Result: `COMPLETED`, 38 tool calls, 2
retries, 70s. Independently checked: `pytest` reports `2 passed`, the repository
has commit `f4b6670` with the right three files tracked.

**Failure path.** Asked to read `~/.ssh/id_rsa`, the run records
`decision=deny, risk=high, status=DENIED` with the correct target, and the agent
reports the refusal instead of working around it.

**Two real defects the loop found by running** (not by reading code):

1. `detect_project` required a manifest, so a directory containing `foo.py` and
   `test_foo.py` was not recognised as a Python project — the agent could not run
   tests for a project it had just created itself.
2. `shutil.which("python")` returned an unrelated system Python without the
   project's dependencies, so `python -m pytest` failed with "No module named
   pytest" and the agent concluded the *code* was broken. Interpreters are now
   resolved against the working directory: project venv first, then a parent venv,
   then the backend's own interpreter, then PATH.

---

## 10. What this is not

Stated plainly so nobody has to discover it the hard way:

- **No browser control.** There is `screenshot` and `screen_read` — observation
  only. No click, no typing, no page automation.
- **No mouse or keyboard control.**
- **No OCR.** Scanned PDFs without a text layer fail with a clear error.
- **Not unattended.** A high-risk operation always waits for a human. Autonomy is
  not a licence to skip the permission system.

---

## Update (round 5) — recovery, rewritten

The `FIX → RETRY` transition described above assumed `_recover` did something. It did
not: it returned an analysis that the caller discarded, so every retry re-ran an
identical prompt and reached an identical decision. See [`../AGENT.md`](../AGENT.md) for
the full account, and [`agent.md`](agent.md) for the code-level summary.

What changed, in one table:

| Before | After |
|---|---|
| the analysis went to the event stream only | it is injected into the next attempt's prompt |
| attempts were counted | attempts are recorded (tools, arguments, signature, error, analysis) |
| an identical retry ran again | it is refused as an ineffective retry and the step stops |
| one step could spend the whole run | a step is capped at a third of the run's budget |
| a failed recovery retried anyway | a failed recovery reports and stops the step |

Two related fixes: a run with a failed step can no longer end `COMPLETED`, and a run that
did not finish but left files behind is `PARTIAL` rather than `FAILED`-with-a-verified-
deliverable.

Acceptance: `scripts/verify_recovery.py` — three scenarios, run against the real model,
each asserting on the event stream and the tool-call counts rather than on the code.
