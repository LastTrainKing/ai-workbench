# Agent — the loop, and the retry that actually changes

> The engineering detail is in [`docs/agent.md`](docs/agent.md) and
> [`docs/autonomous-agent.md`](docs/autonomous-agent.md). This document is about the
> one mechanism this project got wrong for a whole round and then had to fix
> properly: **recovery**.

---

## The loop

```
PLAN → EXECUTE → OBSERVE → VERIFY → FIX → RETRY
```

with the split that matters:

- **the model decides what to do** — it writes the plan, chooses tools, reads results
  and proposes fixes;
- **the code decides whether it worked** — verification is a tool call whose result is
  checked, never the model's opinion of its own output.

A step is verified by asking the model for *evidence* (a tool call), not for a
verdict. The exit code decides. `file_exists` answering "no" is a failure even though
the call succeeded — a tool that succeeds at telling you the file is missing has told
you the file is missing.

Every axis is bounded: steps, tool calls per attempt, tool calls per step, tool calls
per run, retries per step, wall clock, files modified. A run that cannot finish stops
and says why, rather than grinding until someone notices.

---

## Recovery: the bug that made "retry" a lie

### What it looked like

A run ended in `LIMIT_REACHED`. The trace showed the agent burning **105 tool calls in
two steps** — the whole run budget — while the recovery mechanism sat there doing
nothing.

### What it actually was

`_recover` asked the model for a root-cause analysis and returned it:

```python
async def _recover(self, step, observations) -> str:
    ...
    analysis = (chat.content or "").strip()
    self.controller.emit("state", message=analysis[:400])
    return analysis                       # ← returned

# the caller
await self._recover(step, observations)   # ← and thrown away
step.status = StepStatus.RUNNING
```

The analysis went into the event stream, where a human could read it, and **nowhere
else**. The next attempt was built from the same step description, the same tool list
and the same context — so the model reached the same decision and failed the same way.
"Recover" meant "do it again".

This is not a retry limit that was set too low. It is a retry mechanism that never
existed. Raising `max_tool_calls` would have moved the wall, not removed it.

### The fix

Four things, and each one is needed for the others to mean anything.

**1. Attempts are recorded, not just counted.** `PlanStep.attempts` holds, per attempt,
the tools used, their arguments, a signature of the pair, the error and the analysis:

```python
{"attempt": 2, "tools_used": ["document_write"],
 "signature": "document_write:8ef5d05e22f4", "error": "页数不达标：…",
 "analysis": "sections 数量不足，需要至少 10 项", "same_as_previous": false}
```

**2. The analysis reaches the next attempt.** This is the actual repair. The history is
rendered into the retry's system prompt:

```
你之前已经尝试过以下做法，它们都失败了：
  第 1 次：调用 file_write → 失败：产出格式不是 .pptx
           上次的分析：应该直接使用 document_write
不要重复上面这些做法。必须换一个明显不同的方式…
```

**3. An identical retry is refused.** After each attempt a signature is computed —
tool names plus a digest of their arguments — and compared with the previous one. Two
consecutive attempts with the same signature are not a retry, they are a loop:

```
[error] 第 step-2 步的第 3 次尝试与第 2 次使用了完全相同的工具和参数，
        判定为无效重试，停止该步骤
```

**4. One step cannot spend the run.** A step may use at most
`max(MAX_TOOLS_PER_STEP, max_tool_calls / 3)` calls. Hitting it fails *that step* and
lets the rest of the run continue — "one step is stuck" and "the run is out of budget"
are different facts and are reported differently.

**5. A failed recovery stops the step.** `_recover` used to catch everything and
substitute a sentence like `（分析失败：TimeoutError）`, then retry anyway. Now it
reports and returns empty, and the caller stops — because no new strategy means the
next attempt would be the same attempt.

### What it now looks like

Scenario B of the acceptance run: a step that must fail, and the second attempt
differs from the first:

```
第 1 次：['file_exists', 'file_metadata', 'directory_list']
第 2 次：['directory_list']                      ← different tools
```

Scenario C: every tool refused, and the step stops instead of burning the run:

```
停止原因：['第 step-2 步的第 2 次尝试与第 1 次使用了完全相同的工具和参数，判定为无效重试，停止该步骤',
          '第 step-4 步的第 4 次尝试与第 3 次使用了完全相同的工具和参数，判定为无效重试，停止该步骤']
该步骤实际花费：[6, 0, 4, 2, 4] 次工具调用       ← 全运行 33 次，远低于 100 的上限
```

The three questions the trace has to be able to answer, and now can:

| Question | Where the answer is |
|---|---|
| Which step had the first retry? | The `step` events carry `attempt` and `step.id` |
| What did the second attempt change? | `attempts[n].tools_used` / `arguments`, and the `（与上一次不同）` marker |
| If two attempts were identical, why did the rule not fire? | It always fires — `same_as_previous` is recorded per attempt, and a `true` value breaks the loop |

---

## A second failure found by the same work

Recovery kept failing with `AIError`, which turned out to be
`ai_reasoning_budget_exhausted`: the default provider is a **reasoning model**, and a
completion budget too small for its thinking produces *no answer at all* rather than a
truncated one. At 600 tokens the recovery call — the longest prompt in the loop,
because it carries the whole attempt history — failed every time, so "recover" quietly
degraded into "give up".

Two changes: the history block is capped, and every call goes through one entry point
that retries once with the ceiling raised when the provider says the budget ran out.
Measured on a realistic executor prompt: 3,000 tokens is *reached and truncated*;
6,000 completes in ~20s. `max_tokens` is a cap, not a target — a model that thinks for
300 tokens costs the same either way — so the cap is set generously and the small-budget
guessing is gone.

---

## What the agent may do

Permissions are a policy, not a prompt. The tool list is what the model *sees*; the
policy is what the executor *enforces*, and the model ignoring it changes nothing.
Paths are checked twice — once for the tool, once for the concrete argument — which is
what stops `file_read("C:\Users\me\.ssh\id_rsa")`. Every call writes an audit row with
the decision, the reason and the target, so "what did it touch, and was it allowed to?"
is a query.

---

## Related documents

- [ARCHITECTURE.md](ARCHITECTURE.md) — the three layers
- [WORKSPACE.md](WORKSPACE.md) — where the agent works
- [docs/agent.md](docs/agent.md) — the tool contract and the catalogue
- [docs/autonomous-agent.md](docs/autonomous-agent.md) — states, snapshots, rollback
- [docs/permissions.md](docs/permissions.md) — the policy model
