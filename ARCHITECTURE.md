# Architecture — three layers, one loop

This is the short version: what the system is made of, why the boundaries are where
they are, and what each layer is allowed to know. The engineering detail lives in
[`docs/architecture.md`](docs/architecture.md); this document is the shape of the
thing, written for someone who wants the design in ten minutes.

---

## The loop, first

Everything below exists to serve one sentence:

```
资料 → 知识 → 规划 → 产出 → 机器验证 → 用户验收 → 偏好沉淀 → （下一次产出）
```

Read it as a loop, not a pipeline. The last arrow is the point: the product is not
finished when it produces a file, it is finished when the user looks at the file and
says yes or no — and a "no" with a reason becomes something the system holds.

Two properties of that loop decide the whole architecture:

**The verdict must be evidence, not opinion.** A step's success is a tool call whose
exit code is read. `pytest` exiting 1 means the step failed, whatever the model says
about its own work. Without that rule an agent framework is a model complimenting
itself.

**Producing is not delivering.** A file that exists is not a file that was wanted.
So the machine check (`verified`) and the human decision (`accepted`) are separate
fields on separate events, and a file can pass every check and still be rejected.
That combination is the most informative state in the system, which is why it is
representable rather than collapsed.

---

## The three layers

```
┌──────────────────────────────────────────────────────────────────────┐
│  交付层  delivery        what it produced                            │
│  BuildRun · Deliverable · verification · acceptance · version chain   │
│  — the layer the other two exist to serve —                          │
└───────────────────────────────▲──────────────────────────────────────┘
                                │ produces / accepts / rejects
┌───────────────────────────────┴──────────────────────────────────────┐
│  行动层  action          what it can do                              │
│  planner · autonomous loop · tools · permissions · executor · audit   │
│  — decides *what to do*; the code decides *whether it worked* —      │
└───────────────────────────────▲──────────────────────────────────────┘
                                │ supplies facts, receives outcomes
┌───────────────────────────────┴──────────────────────────────────────┐
│  认知层  cognition       what it knows                               │
│  documents · chunks · concepts · RAG · memory · preferences · profile │
│  — the user's material, and what was learned from using it —         │
└──────────────────────────────────────────────────────────────────────┘
```

### 认知层 — what it knows

Documents are parsed, chunked heading-aware, embedded and indexed; concepts are
extracted and linked back to the documents they came from. Retrieval is hybrid
(vector + lexical) with a rerank pass, assembled under a token budget that is
recorded so "what was it told?" is answerable after the fact.

The second half of this layer is not the user's material but the user: memory
entries, preferences, profile. Both halves are *facts*, and both are held to the
same standard — every injected block is persisted with its exact rendered text.

### 行动层 — what it can do

The rule that shapes this layer:

```
Agent → Tool → Permission → Executor → Service → Repository → DB
```

The agent never touches the database. It calls a named tool; the tool is checked
against a policy; the executor runs it, records an audit row and enforces the
resource ceilings; only then does business code run. This is what makes "what did
it do on my machine, and was it allowed to?" a query rather than an investigation.

The loop is `PLAN → EXECUTE → OBSERVE → VERIFY → FIX → RETRY`, bounded on every
axis (steps, tool calls per step, tool calls per run, retries, wall clock, files
modified). A run that cannot finish stops and says why.

### 交付层 — what it produced

A `BuildRun` holds the user's own words, the plan, the context that was injected and
the terminal state. A `Deliverable` is a real file with two independent judgements on
it — `verified` (machine) and `accepted` (user) — plus the version chain that makes
"what changed after I said 图表太少" a walk rather than a guess.

Rejection is the product's primary input. A conversation turn is a *guess* about what
someone prefers; a rejection with a reason is a *statement* about it, made by the only
person who holds it.

---

## Why the boundaries are where they are

**Why not let the agent write to the database?** Because then no operation has a name.
An audit trail of SQL statements answers nothing; an audit trail of named, categorised
tool calls answers "which file, which command, which host, was it allowed".

**Why is verification a tool call rather than a model judgement?** Because a model
asked "did that work?" answers about its own confidence. The exit code does not have
an opinion.

**Why are `verified` and `accepted` separate?** Because collapsing them loses the one
signal the product is built to collect: a file that passed every machine check and was
still rejected is a statement about a standard the machine does not know.

**Why is memory triggered by delivery acceptance rather than by conversation?** A
conversation turn produces inferences; a delivery decision produces facts. A store
that grows from chatter fills with things the user never said.

---

## Where the pieces live

| Concern | Code |
|---|---|
| The loop, retries, recovery | `backend/app/ai/agent/autonomous.py` |
| Plan and step model | `backend/app/ai/agent/plan.py` |
| Hard constraints and preference resolution | `backend/app/ai/agent/constraints.py` |
| Tool interface, context, registry | `backend/app/ai/agent/tools.py` |
| Permission policy and workspace layout | `backend/app/ai/tools/permissions.py` |
| Execution, ceilings, audit | `backend/app/ai/tools/executor.py` |
| Document writers and readers | `backend/app/ai/tools/builtin/documents.py` |
| Delivery model | `backend/app/models/delivery.py` |
| Build run driver, artifact collection | `backend/app/services/build_executor.py` |
| Accept / reject / learn | `backend/app/services/delivery_service.py` |
| Memory evaluation | `backend/app/ai/memory/evaluator.py` |
| Context assembly under budget | `backend/app/ai/memory/assembler.py` |

---

## Related documents

- [AGENT.md](AGENT.md) — the loop, recovery, and how a retry changes strategy
- [DELIVERABLE.md](DELIVERABLE.md) — the delivery model, verification vs acceptance
- [MEMORY.md](MEMORY.md) — what is remembered, when, and why so little of it
- [WORKSPACE.md](WORKSPACE.md) — where the agent works and what shape that area has
- [docs/architecture.md](docs/architecture.md) — the long version, with the bugs that
  produced each decision

---

## The third layer: Skill

Memory is what is true about you. Knowledge is what you have. **Skill is how you work.**

It is the layer that makes the product useful on day one rather than after months of use:
instead of waiting for behaviour to accumulate, **it goes out and finds the ways of working
that fit, verifies them, and installs them.** A new user gets a library on their first
day; a product that waits gets an empty one.

The six stages, and where each lives:

```
FIND      ai/skills/discovery.py   profile + need -> search terms -> GitHub
ASSESS    ai/skills/discovery.py   drop what cannot work, rank, and say why
VERIFY    ai/skills/scanner.py     seven rules, all of them blocking
ADOPT     ai/skills/registry.py    *only on confirmation*, then bind params
USE       ai/skills/registry.py    match, render the personalised body
REVIEW    ai/skills/registry.py    count uses, take a rating, retire
```

### Where personalisation actually happens

**An imported skill is general, not personal.** What makes it yours is the binding step:

```
general skeleton   <- imported from a public source
    + your profile      ({{audience}} = 院学生会指导老师)
    + your preferences  ({{tone}} = 正式、简洁)
    = the work done the way you would do it
```

Which is why importing is not enough on its own: **the memory and knowledge layers have to
be genuinely connected**, or what comes in stays a general template.

### Two gates, deliberately not split

`adopt()` holds both — the user's confirmation and the security scan — because splitting
them across call sites is how one of them eventually gets skipped. **Confirming is not
enough either**: a hostile skill is refused even when the user says yes, because they are
saying yes to something they have not read.

### The library must not become a dumping ground

`installed_skills` records what makes a skill *reviewable* rather than merely stored:
source and commit, the scan result verbatim, use counts, and a rating that is `None` until
a person judges it — **which is a different thing from "it was fine"**.

`record_use()` counts failures too. Counting only successes would make `use_count` a
quality score rather than a usage count, and the review stage needs to see the skill that
gets tried and fails — that is the one to retire.

- [SKILL.md](SKILL.md) — the format, the six stages, the security rules, the injection point
