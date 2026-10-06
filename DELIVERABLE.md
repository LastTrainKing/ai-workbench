# Deliverable — the layer the other two exist to serve

> The user-facing side of this is in [DELIVERY.md](DELIVERY.md) (what ships, how to run
> it, what was verified). This document is about the *model*.

---

## The two judgements

Two independent things are true about a produced file, and they are stored separately
on purpose:

| Field | Who decides | What it means |
|---|---|---|
| `verified` | the **machine** | the file exists, it opens, it has the page/row/paragraph count it claims |
| `accepted` | the **user** | this is what I wanted |

`accepted` is `null` until the user says something. That is not "no" — it is "they have
not looked yet", and it is a distinct state.

**A file can pass every machine check and still be rejected.** That combination is the
single most informative event in the system: the machine has no opinion about whether a
deck is *good*, and the user does. Collapsing the two flags into one would throw that
away, which is why they are never collapsed.

Rejection requires a reason. An empty reason is refused with a 422, because a rejection
without a reason teaches nothing and the whole loop exists so that it learns.

---

## Verification is a readback, not a claim

`verify_deliverable` opens the file with an independent library and reads what is
inside:

| Kind | Check | Example detail |
|---|---|---|
| pptx | slide count | `11 slides` |
| docx | paragraph count | `214 paragraphs` |
| xlsx | sheet and chart count | `2 sheets, 1 charts` |
| pdf | page count | `10 pages` |
| csv | row count | `312 rows` |

A failed readback is a failed check, and a check list with any failure makes
`passed=false` — derived, never passed in. A verification result that claimed to pass
while containing a failed check is the exact failure this structure exists to prevent.

The machine check is deliberately *shallow*. It can prove a file is real and has the
shape it claims. It cannot prove the content is any good, and it does not pretend to.

---

## What the machine cannot check

Stated plainly, because the gap is the interesting part:

- **Whether the content is correct.** A deck with eleven slides of nonsense passes
  every check above.
- **Whether it is what was asked for.** Only the user knows.
- **Page count for .docx.** A `.docx` does not store its own page count. Where a real
  page count is needed, the file is converted with LibreOffice and the PDF is counted
  — an estimate is not printed as if it were a measurement.
- **Chart *quality*.** The writer reports how many images it placed; it cannot tell you
  whether the chart says anything.

---

## Images and charts: what is actually supported

The honest version, because this is where a silent failure lived for a round.

**Supported.** `document_write` places a chart on a slide when a section carries a
`chart` spec, rendered with matplotlib to PNG and inserted as a picture. Series can be
`[{"name": …, "data": […]}]` or `{name: [values]}`, with `categories` / `labels` / `x`
aliases. For `.xlsx`, a chart is a native Excel chart object.

**Reported.** The tool returns `images` (how many were placed), `chart_failures` (each
failure with its reason) and `slides_dropped` (when an upper bound trimmed content).
A deck that came out with zero visuals says so.

**What used to happen.** `_render_chart` returned `None` on failure and logged a
warning. A chart that was requested and did not appear was therefore indistinguishable
from a section that never asked for one — the deck came out with zero images, the
result looked successful, and the user's stated preference for more charts was not
honoured. That is the exact failure mode the product is supposed to prevent, sitting
inside the product.

**What happens now.** The renderer raises with the real reason; the writer records it
per section and still produces the rest of the deck; and `document_write` returns
`ok=False` naming each failure. Failing loudly is what made the underlying contract bug
findable — the model was sending `series` as a list of `{name, data}`, the natural
reading of the field, and the implementation expected a private shape.

**Not supported.** Images supplied by URL, animated charts, and any visual that is not
a matplotlib figure or a native Excel chart. There is no image *generation*.

---

## The version chain

A redo is the most informative event in the loop — something changed between version
*n* and *n+1* because the user said so. So it is a link, not a timestamp comparison:

```
毕业设计答辩 v1  (11 slides, 0 charts, 70,538 B, COMPLETED)  ← rejected
      ↑ parent_id
毕业设计答辩 v2  (10 slides, 10 charts, …, COMPLETED)         ← the preference applied
```

`version_chain()` walks the links, so "show me what changed after I said 图表太少" is a
walk rather than a guess. Each version also stores `applied_preferences` — the hard
constraints and the resolved preference outcomes in force when it was produced — so the
comparison can say *what was different*, not just *that something was*.

---

## Producing is not delivering

A run that ends without producing a file is not a completed delivery, and the run's
state says so. The three cases are kept distinct:

| Situation | Run status | Deliverable |
|---|---|---|
| Finished, produced files | `COMPLETED` | registered, `verified` from the readback |
| Finished, produced nothing | `WAITING_USER` | none |
| Did not finish, but left files behind | `PARTIAL` | registered, **not** verified |

`PARTIAL` exists because the previous version reported `FAILED` for a run that had
produced a 70 KB deck *and* registered that deck with `verified=true`. Two facts that
contradict each other, both written down, and the one the user reads is the verdict. A
file from a run that did not succeed now carries a failing `run_state` check, so the
record stops disagreeing with itself.

---

## Related documents

- [ARCHITECTURE.md](ARCHITECTURE.md) — the three layers
- [MEMORY.md](MEMORY.md) — the acceptance event, and what it teaches
- [DELIVERY.md](DELIVERY.md) — installing and running the shipped build
- `backend/app/models/delivery.py` — the model
- `backend/app/services/delivery_service.py` — verification, acceptance, version chain
- `backend/app/services/build_executor.py` — the run driver and artifact collection
