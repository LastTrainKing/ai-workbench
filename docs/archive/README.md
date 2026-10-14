# Archived documents

**These are records, not guidance.** Each one was accurate when it was written and several now
describe a state that no longer exists — a version that is not the current one, a defect that
has since been fixed, a plan that was carried out or abandoned. Read them to understand how a
decision was made. Do not read them as instructions, and do not take a number from one of them
without checking it against the current tree.

They are kept rather than deleted so the project's history is not rewritten. The live
documents are the ones at the repository root and in `docs/` and `docs/release/`.

## Release freeze — the acceptance rounds

| File | What it was |
|---|---|
| `ACCEPTANCE_MATRIX.md` | The first fix round's matrix. **Says at the top that it is superseded by `RELEASE-ACCEPTANCE.md`.** |
| `RELEASE-ACCEPTANCE.md` | The four-layer test matrix that replaced it. |
| `RELEASE-FREEZE-ISSUES.md` | Every open item at freeze time, with the reason for each. |
| `RELEASE-FREEZE-REVIEW.md` | An independent review of items RF-02 … RF-17. |
| `FINAL-VERDICT.md` | The verdict for 2.2.0 — a version the project has since moved past. |
| `RELEASE_1.0_STATUS.md` | The 1.0 status page. Two versions stale. |

## Fix rounds and audit reports

| File | What it was |
|---|---|
| `FIX-ROUND-15.md` | Security audit, core logic, end-to-end acceptance. |
| `FIX-ROUND-17.md` | The second part of the final repair round. |
| `FIX-ROUND-18.md` | `AgentRun` persistence, Alembic unification, retention policy. |
| `ROUND-3-REPORT.md` | Stub-provider tests and an independent audit of `desktop-native`. |
| `REPORT.md` | The fifth round's acceptance report. |
| `FINAL-ACCEPTANCE-REPORT.md`, `FINAL-REPAIR-REPORT.md`, `FULL_REMEDIATION_REPORT.md` | The closing reports of the repair sequence. |

**The current equivalents live in `docs/release/`** — `RELEASE_NOTES.md`, `TEST_REPORT.md`,
`BUILD_AND_INSTALL.md`, `VERSION_3_REPORT.md`. Those are the ones that describe the shipped
build.

## Working notes and handoffs

| File | What it was |
|---|---|
| `HANDOFF.md` | "Start here in a new conversation" — for a round that ended. |
| `PROGRESS.md` | A crash-recovery checkpoint, not a status page. |
| `NATIVE-MIGRATION-PROGRESS.md` | Round 7 of the native migration. |
| `CLEANUP_PLAN.md` | The A/B classification that `scripts/cleanup_artifacts.py` works from. |
| `REAL-PROVIDER-ACCEPTANCE.md` | A minimal acceptance run against a real provider. |
| `CODE-REVIEW.md` | A review pass, with the issues it found and what was decided about each. |

## Older delivery documents

| File | Claimed version | Why it is here |
|---|---|---|
| `PROJECT_DELIVERY.md` | 1.0.0 | Described the initial delivery. Superseded by `/DELIVERY.md`. |
| `FINAL_CHECKLIST.md` | (none) | A pre-release checklist. Superseded by the verification scripts in `scripts/`, which assert rather than tick. |

`DELIVERY.md` at the repository root is the authoritative delivery document.

---

## Why these moved here

They used to sit in the repository root, alongside `README.md` and `LICENSE` — 36 Markdown
files in one directory, more than half of them round reports. A visitor opening the repository
saw `FIX-ROUND-17.md` next to `README.md` and had no way to tell which of the two described
the current product.

Moving them changed nothing about their content. **References in the live documents and in
code comments were updated to the new paths**; references inside `docs/prompts/` and inside
these archived files were deliberately left alone, because those documents record what someone
was told or observed at the time, and rewriting that would make them describe something that
never happened.
