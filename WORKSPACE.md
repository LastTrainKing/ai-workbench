# Workspace — where the agent works, and what shape that area has

---

## It used to be the desktop

The default agent workspace was:

```
%USERPROFILE%\Desktop\AI Workbench Workspace
```

A product whose entire pitch is *your private workspace* was creating its working
directory at the root of the user's desktop, next to their shortcuts. That is the
product emptying its pockets onto the one screen the user looks at all day, and it is
how `答辩PPT.pptx` ended up sitting on the desktop next to a game launcher.

Inside it were 1,993 files, 1,864 of them belonging to a Python virtualenv — not a
tidiness problem, but evidence that nothing had ever decided what the area was for.

## Where it is now

```
<project>/.runtime/workspace/
├── deliverables/   files produced for the user — the only place a deliverable goes
├── notes/          drafts, outlines, working notes
├── sources/        material imported or staged by the user
└── .tmp/           scratch; safe to empty at any time
```

The workspace belongs to the product, so it lives inside the product's own directory.
It is still configurable — a user may legitimately want it elsewhere — but the default
has to be a place the product owns.

| Setting | Effect |
|---|---|
| `AGENT_WORKSPACE` (in `.env`) | path to use |
| `AI_WORKBENCH_AGENT_WORKSPACE` (environment) | overrides the setting, for tests and one-off runs |

Resolution order: environment override → configured path → `<project>/.runtime/workspace`.

## The layout is created, not assumed

`ensure_workspace_layout()` runs on every entry point that builds an agent, so the shape
never depends on which code path happened to run first. The directories exist before
anything writes to them, which is what makes "where does a deliverable go?" a question
with one answer.

## Deliverables land in `deliverables/`

`document_write` routes a bare filename into `deliverables/`:

```
"答辩PPT.pptx"        →  deliverables/答辩PPT.pptx
"notes/draft.md"      →  notes/draft.md          (an explicit path is respected)
"C:\elsewhere\x.pptx" →  C:\elsewhere\x.pptx     (an absolute path is respected)
```

A caller that wrote `notes/draft.md` meant it. A caller that wrote `a.pptx` was not
making a statement about location, so the product makes one. "Which of these files is
the thing I asked for?" should not be a question the user has to answer by reading
filenames.

## Housekeeping

`.runtime/` is gitignored — it holds the database data directory, uploaded files, logs,
snapshots and the workspace itself. `archive/` is gitignored too: it is a filing cabinet
for old release ZIPs, runtime backups and the pre-convention workspace, hundreds of
megabytes of binaries that nothing in the build reads.

Nothing in the workspace is deleted by the product. `.tmp/` is documented as safe to
empty because that is what makes it useful; everything else is meant to persist.

## Related documents

- [ARCHITECTURE.md](ARCHITECTURE.md) — the three layers
- [AGENT.md](AGENT.md) — what the agent may do inside this area
- `backend/app/ai/tools/permissions.py` — `default_workspace`, `WORKSPACE_DIRS`,
  `ensure_workspace_layout`, `default_output_path`
