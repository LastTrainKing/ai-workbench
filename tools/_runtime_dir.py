"""Where this machine keeps its runtime directory — one implementation, two scanners.

**Why this is a module and not a function copied into each scanner.** The two secret
scanners both need to answer "which local credentials should I compare against?", and the
first version of that answer was a literal path on the build machine
(`C:\\Users\\user\\AppData\\Local\\AI Workbench Data\\.runtime`). On any other machine it did
not exist, the comparison set was empty, and the run printed a zero that read exactly like a
clean result.

The current-tree scanner was fixed first. The history scanner was not, so for one round the
two disagreed: one found the machine's real key, the other silently compared against
nothing. Copying the fix into the second file would have made them agree today and drift
again tomorrow — so the resolution lives here, and neither scanner owns it.

Resolution order, matching `app.core.config`:

1. `--runtime-dir DIR` — taken as the runtime directory itself.
2. `AI_WORKBENCH_DATA_DIR` — taken as the *data* directory, so `.runtime` is appended.
3. `%LOCALAPPDATA%\\AI Workbench Data\\.runtime` — the installed application's location.
4. `<repo>/.runtime` — a development checkout.

`None` means "this machine has no runtime directory that could be found", which callers are
expected to **report** rather than treat as "nothing found".
"""

from __future__ import annotations

import os
import pathlib

#: `<repo>`, for callers that do not pass their own. Correct as long as this file sits in
#: `<repo>/tools/` — callers should pass `repo=` rather than rely on it, because a copy of
#: this file somewhere else would silently resolve to the wrong root.
REPO = pathlib.Path(__file__).resolve().parents[1]


def runtime_dir(
    explicit: str | None = None, *, repo: pathlib.Path | None = None
) -> pathlib.Path | None:
    """This machine's runtime directory, or `None` if there is not one to find."""
    if explicit:
        return pathlib.Path(explicit)
    data = os.environ.get("AI_WORKBENCH_DATA_DIR", "").strip()
    if data:
        return pathlib.Path(data) / ".runtime"
    local = os.environ.get("LOCALAPPDATA", "").strip()
    if local:
        candidate = pathlib.Path(local) / "AI Workbench Data" / ".runtime"
        if candidate.is_dir():
            return candidate
    dev = (repo or REPO) / ".runtime"
    return dev if dev.is_dir() else None


def credential_files(runtime: pathlib.Path | None) -> list[tuple[str, pathlib.Path]]:
    """Every file in `runtime` that could hold a credential, labelled by what it is.

    Both scanners read the same set. An installed copy keeps its key in
    `<runtime>/ai-config/<owner>.json`; a checkout may also have a single
    `<runtime>/ai-config.json`. Neither layout is assumed to be the only one.
    """
    if runtime is None:
        return []
    found: list[tuple[str, pathlib.Path]] = []
    single = runtime / "ai-config.json"
    if single.is_file():
        found.append(("ai-config.json", single))
    config_dir = runtime / "ai-config"
    if config_dir.is_dir():
        for path in sorted(config_dir.glob("*.json")):
            found.append((f"ai-config/{path.name}", path))
    return found
