# Memory — what it remembers, and why so little of it

> `MEMORY.md` describes the *design*. The runtime store is the `memory_entries` and
> `memory_candidates` tables; the page that shows them is `/me`.

---

## The trigger is delivery acceptance, not conversation

This is the decision the rest of the design follows from.

A conversation turn is a **guess** about what someone prefers: they said something and
the system infers. A delivery decision is a **statement**: they looked at a real
artifact and said yes or no, and when they said no they said why. That is a fact about
a standard, held by the only person who holds it.

So the primary input to memory is:

```
用户退回了《毕业设计答辩.pptx》第 1 版，理由是：「图表太少，文字堆得太满，页数也偏多」
```

and the failure mode this avoids is the one that makes memory products useless:
accumulating inferences from chatter until the store is full of things the user never
actually said. A run's *goal* is also offered to the evaluator (it is the user's own
words), and a run's *transcript* is not (those are mechanics).

---

## Purity: recording something wrong is worse than recording nothing

Five stages, in order, and the order is load-bearing:

| Stage | What it does | Why it is here |
|---|---|---|
| 1. Rule pre-filter | drops greetings, one-off transactions, and anything sensitive — **before the model is called** | cheap, and a safety rule must not depend on a model's cooperation |
| 2. Structured verdict | asks specific questions (worth remembering? which kind? confidence? does it contradict something?) | "is this interesting?" gets an opinion; these get answers |
| 3. Dedup | a restatement bumps an existing row instead of adding one | the same preference said twice is one preference |
| 4. Conflict detection | a contradiction becomes a **question**, never an overwrite | silently replacing a belief the user stated is worse than asking |
| 5. Routing | ≥ 0.8 lands active; 0.5–0.8 becomes a candidate; below 0.5 is dropped | the middle band is where "probably true" lives, and it belongs to the user |

Sensitive content never auto-lands at any confidence. That is a rule, not a threshold.

The measured effect: nine evaluations wrote zero memories in one measured run. A store
that rejects most of what it sees is doing its job.

---

## Every memory says where it came from

A memory with no origin is an assertion from nowhere. "You prefer more charts" is only
reviewable if the user can see *which* delivery taught it — so
`MemoryEntry.structured.origin` records the deliverable, the build run, the version,
the decision and the user's own words:

```json
{"origin": {"kind": "delivery", "decision": "rejected",
            "deliverable_title": "毕业设计答辩", "version": 1,
            "feedback": "图表太少，文字堆得太满，页数也偏多",
            "build_run_id": "…", "decided_at": "2026-10-06T02:33:10+00:00"}}
```

The memory page renders exactly that, with a link back to the delivery. The origin is
carried on the *candidate* as well as the entry, so it survives the approval step
instead of being reconstructed later — and when a restatement matches a memory that
predates this field, the origin is attached, which is how an older belief becomes
reviewable rather than staying unfalsifiable forever.

---

## Preference is a constraint, not an instruction

The failure this rule exists for is small and specific:

```
目标：做一份 15 分钟的答辩 PPT
偏好：页数更少
结果：2 页
```

Nothing crashed. Every check passed. "页数更少" was executed as *the thing to do*
rather than as *a standard to apply while doing it* — and a 15-minute talk has a
physical floor that no preference can argue with.

The order is not negotiable:

```
任务目标（硬约束）  >  长期偏好（软约束）  >  默认行为
```

**Hard constraints are derived in code, not by the model.** `derive_constraints` reads
the duration out of the goal and turns it into a page range — 15 minutes is 10–14
slides, 3 minutes is 3–5 — from a documented table of how presentations are actually
paced. If this were left to the model, a preference saying "fewer pages" would be
weighed against a duration the model had merely been *told about*, and a model asked
to satisfy both will sometimes satisfy the wrong one. A number derived in code cannot
be negotiated away.

**Preferences are resolved against those constraints, per intent.** A single learned
memory can read "用户偏好演示文稿图表更多、页数更少" — two preferences in one sentence
— and matching the sentence as a whole would honour whichever was tested first and
silently drop the other. Each intent gets its own outcome:

```
[applied]      「图表更多」  —— 会为内容页生成图表（matplotlib 可用）
[bounded]      「页数更少」  —— 只能部分满足：本次是 15 分钟的汇报，硬约束要求至少 10 页，
                              因此取 10 页（区间下限），不能再少
[unsatisfiable]「用公司模板」—— 做不到：工作区里没有任何模板文件（*.pptx / *.potx / *.docx）
```

**A preference that cannot be executed is reported.** A silently ignored preference is
worse than a refused one, because the user believes it took effect. An `unsatisfiable`
outcome is emitted into the event stream and written into the final report — not
merely stored.

**The trade-off note is generated, not requested.** "Did it explain why the deck is
ten pages?" has to be answerable from the output, and a model that simply did not
mention it would make the honest answer "no". So the note is composed deterministically
and appended to the run's report:

```
【取舍说明】
- 硬约束：15 分钟的汇报，按每分钟约 1 页的语速，合理页数是 10~14 页（下限来自「15 分钟」这条任务要求，不是偏好）
- 本次目标页数定为 10 页——这是 15 分钟约束下的最小值，长期偏好希望更少，但再少就讲不满这个时长。
- 图表偏好生效：每个内容页附 1 张图。
```

---

## Where it is injected

One place: `ContextAssembler`, under a token budget, recording the exact rendered text
into `context_snapshots`. Planner, executor, RAG and document analysis all go through
it, so "what did it know when it answered?" is a row rather than a reconstruction. The
rendered block states the precedence rule to the model as well, because a rule the
model has not been told is a rule it can only violate.

---

## Related documents

- [ARCHITECTURE.md](ARCHITECTURE.md) — the three layers
- [DELIVERABLE.md](DELIVERABLE.md) — the acceptance event that triggers all of this
- `backend/app/ai/memory/evaluator.py` — the five stages
- `backend/app/ai/agent/constraints.py` — hard constraints and preference resolution
- `backend/app/ai/memory/assembler.py` — the budget and the render

---

# 生命周期（宜精不宜多）

> 「宜精不宜多，保证记忆库的精与纯净。」
>
> 在此之前：有表、有写入链路、有确认机制，**但没有任何东西会移除任何东西**——
> 所以它只能增长。**一个只增长的记忆库会停止关于这个人，变成一份聊天记录。**

实现：`backend/app/ai/memory/lifecycle.py`

## 四个动作

| 动作 | 做什么 | 不做什么 |
|---|---|---|
| **合并** merge | 语义高度重合 → 合成一条，`use_count` 累加 | **不删除**。被合并的标记 `merged` 并指回存活的那条 |
| **衰减** decay | 长期未用**且**低置信 → 转 `decayed`，退出检索 | **不删除**。再被提到就能回来，删掉的回不来 |
| **冲突** conflict | 矛盾记忆 → 生成候选问用户 | **两条都不改**。系统提议，人决定 |
| **遗忘** purge | 真正删除，`is_forgotten()` 证明它查不到了 | 不允许删别人的 |

**没有任何一个动作会删除。** 只有用户明确操作才删，而删了就必须真的消失——
`purge` 存在就是为了让这件事可验证。

## 阈值

```python
MERGE_THRESHOLD = 0.62          # 词集 Jaccard 重合
DECAY_AFTER_DAYS = 90           # 90 天够长，一个学期/一个项目不会被中途丢掉
DECAY_CONFIDENCE_BELOW = 0.75   # 高置信 = 用户确认过，不按定时器衰减
```

**置信度也把门**：用户确认过的记忆不因为"最近没人提"而过期——**确定和最近不是一回事**。

## 合并不能吃掉冲突（测试抓到的真 bug）

**矛盾的记忆是*相似*的**——那正是它们构成矛盾而不是两个无关事实的原因。
所以合并步骤会在冲突步骤看到它们之前把它们吃掉，存活的是**碰巧被用得多**的那句。

> 那是系统在**悄悄删掉用户的一个信念、保留另一个**——和冲突候选存在的目的正好相反。

实测：「用户喜欢在汇报里用图表」和「用户不喜欢在汇报里用图表」相似度 **0.75**，
高于 0.62 的合并阈值。现在冲突对**被排除在合并之外**。

## 冲突检测是窄的，故意的

规则：**同 kind + 高相似 + 恰好一个带否定**。

"它们矛盾"在一般情况下不可判定，**一个会猜的检测器会把审核队列塞满只是看起来矛盾的配对**
——那比漏掉一些更糟，因为**队列就没人读了**。

## 复盘的真实数字

`memory_stats()` 计算的，不是估的：总数 / 各状态 / 平均 `use_count` / 从没被用过的条数 /
候选待确认 / **用户拒绝占比**（证明确认机制在工作）。

**「可遗忘且真的遗忘」是可证伪的，所以在这里被证伪**：`purge` 之后断言
`is_forgotten()` 为真，以及第二次 purge 报告"本来就没有"而不是声称成功。

## 相关文件

- `backend/app/ai/memory/lifecycle.py` — 合并/衰减/冲突/遗忘
- `backend/tests/integration/test_memory_lifecycle.py` — 20 个测试，每个阈值两侧都断言
