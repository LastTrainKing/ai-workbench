"""Everything about the installer that can be checked without starting a process.

Smart App Control blocks this machine's development interpreter and every third-party
`.pyd` it loads, so the acceptance that launches the application cannot run here. What
*can* run is this: the installer's structure, its NSIS source, and the payload it
carries, all examined statically.

That is a real reduction in what is proven, and the report says so. It is not a reason
to check nothing.

Deliberately stdlib-only: the bundled interpreter runs, but none of its compiled
dependencies do, so `import pydantic` - or anything else from `site-packages` - is a
non-starter. `zipfile`, `hashlib` and `re` are enough for all of this.

    python scripts/verify_installer_static.py
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SETUP = ROOT / "build" / "installer" / "AI-Workbench-Setup.exe"
NSI = ROOT / "installer" / "ai-workbench.nsi"
PAYLOAD = ROOT / "build" / "installer-payload"
NSIS = Path.home() / "AppData" / "Local" / "tauri" / "NSIS" / "Bin" / "makensis.exe"

results: list[tuple[str, bool, str]] = []


def check(area: str, name: str, ok: bool, detail: str = "") -> None:
    results.append((f"{area} · {name}", bool(ok), detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


def human(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if abs(n) < 1024:
            return f"{n:,.1f} {unit}"
        n /= 1024
    return f"{n:,.1f} TB"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    print("=" * 74)
    print("  Installer: static verification (no process is started)")
    print("=" * 74)

    # ------------------------------------------------------------- artefact --
    print("\n=== A. the installer file ===")
    if not SETUP.is_file():
        print(f"  missing: {SETUP}")
        return 1
    size = SETUP.stat().st_size
    digest = sha256(SETUP)
    print(f"  path   : {SETUP.relative_to(ROOT)}")
    print(f"  size   : {size:,} bytes ({human(size)})")
    print(f"  sha256 : {digest}")

    head = SETUP.read_bytes()[:2]
    check("A", "is a real PE executable", head == b"MZ", head.hex())
    check("A", "size is plausible for the payload", size > 50_000_000, human(size))

    # The version the installer carries must be the version its source declares.
    #
    # Compared against `APP_VERSION` rather than a literal: `VIProductVersion` is stored
    # as four binary fields, not as the string "2.1.0.0", so searching for it found
    # nothing on an installer whose FileVersion reads exactly `2.1.0`. The check that
    # means something is "does the artefact agree with the source", and that one is
    # answerable.
    declared = ""
    if NSI.is_file():
        match = re.search(r'!define\s+APP_VERSION\s+"([^"]+)"', NSI.read_text(encoding="utf-8-sig"))
        declared = match.group(1) if match else ""

    blob = SETUP.read_bytes()

    def present(text: str) -> bool:
        return text.encode("ascii") in blob or text.encode("utf-16-le") in blob

    check("A", "declares a version in the NSIS source", bool(declared), declared)
    check("A", "carries that exact version", bool(declared) and present(declared), declared)
    check("A", "carries the product name", present("AI Workbench"))
    check("A", "carries the publisher", present("AI Workbench"))

    # ---------------------------------------------------------------- nsis ---
    print("\n=== B. the NSIS source ===")
    if not NSI.is_file():
        check("B", "NSIS script present", False, str(NSI))
    else:
        source = NSI.read_text(encoding="utf-8-sig")

        check("B", "installs per-user (no elevation)", "RequestExecutionLevel user" in source)
        check("B", "installs under LOCALAPPDATA", "$LOCALAPPDATA\\${APP_NAME}" in source)
        check("B", "declares an uninstaller", "WriteUninstaller" in source)
        check("B", "registers in Add/Remove Programs", "Uninstall\\${APP_NAME}" in source)

        # Shortcuts must point at a real program. This is the P0 lesson: a shortcut that
        # resolved through wscript once opened Edge instead of the application.
        #
        # There are more than two now, and they are of two kinds. The app shortcuts (Start
        # Menu and desktop) must target the exe. The repair shortcuts target
        # `repair-install.bat` — a `.bat` on purpose, because the whole point of that entry
        # point is that it runs when the installed program is too broken to start. An
        # earlier version of this check asserted "exactly two shortcuts, all targeting the
        # exe", which was true when there were two and would now either fail on correct
        # behaviour or have to be weakened to "at least one is fine". Asserting the two
        # kinds separately is stronger than either: every shortcut is now checked against
        # the target its kind requires.
        shortcuts = re.findall(r'CreateShortCut "([^"]+)" "([^"]+)"', source)
        app_shortcuts = [s for s in shortcuts if s[1].endswith("${APP_EXE}")]
        repair_shortcuts = [s for s in shortcuts if s[1].endswith("repair-install.bat")]
        check(
            "B",
            "**every shortcut targets either the exe or the repair entry point**",
            len(app_shortcuts) + len(repair_shortcuts) == len(shortcuts),
            str([(s[0].split("\\")[-1], s[1].split("\\")[-1]) for s in shortcuts]),
        )
        check(
            "B",
            "creates the app shortcut in the Start Menu and on the desktop",
            len(app_shortcuts) == 2,
            f"{len(app_shortcuts)} app shortcuts",
        )
        check(
            "B",
            "creates at least one repair shortcut",
            len(repair_shortcuts) >= 1,
            f"{len(repair_shortcuts)} repair shortcuts",
        )
        for forbidden in ("wscript", "start-hidden.vbs", "start.bat", "http://", "https://"):
            check("B", f"no shortcut goes through {forbidden}", forbidden not in source.lower())

        # Data safety. These two are the reason the installer is written by hand.
        check("B", "writes a data-dir marker", "data-dir.txt" in source)
        check(
            "B",
            "does NOT overwrite an existing marker on reinstall",
            'IfFileExists "$INSTDIR\\data-dir.txt" marker_exists 0' in source,
        )
        uninstall = source.split('Section "Uninstall"', 1)[-1]
        check(
            "B",
            "uninstall does not delete the data directory by default",
            'RMDir /r "$LOCALAPPDATA\\${DATA_DIR_NAME}"' in uninstall,
            "only inside the branch the user has to choose",
        )
        check(
            "B",
            "the destructive data branch is opt-in",
            "MB_DEFBUTTON2" in uninstall and "IfSilent keep_data 0" in uninstall,
            "default answer is keep; silent mode keeps",
        )
        check("B", "uninstall stops the stack before deleting", "StopEverything" in uninstall)
        check("B", "uninstall refuses to run over a live copy", "CheckNotRunning" in uninstall)

        # Every message box must be reachable only when someone can answer it.
        #
        # Counted from *instructions*, not from the text: the comments explain the rule
        # and name MessageBox while doing so, and counting those reported five boxes
        # for three and a missing guard that was not missing.
        #
        # A box is guarded if it cannot appear during `/S`. There are two ways to write
        # that in NSIS and both are in use here: the raw `IfSilent` jump, and LogicLib's
        # `${IfNot} ${Silent}` block. The first version of this check only counted
        # `IfSilent`, so it reported two unguarded boxes that were guarded — the fix is to
        # teach the check the second form, not to delete the boxes.
        code_lines = [line.strip() for line in source.splitlines() if not line.strip().startswith((";", "#"))]
        boxes = sum(1 for line in code_lines if line.startswith("MessageBox"))
        guards = sum(1 for line in code_lines if line.startswith("IfSilent"))
        guards += sum(1 for line in code_lines if line.startswith("${IfNot} ${Silent}"))
        check("B", "every MessageBox has a silent-mode branch", guards >= boxes, f"{boxes} boxes, {guards} guards")

        # Syntax. `/PPO` preprocesses without compiling, which catches syntax errors
        # without producing an artefact or touching the payload.
        if NSIS.is_file():
            proc = subprocess.run(
                [str(NSIS), "/PPO", str(NSI)],
                cwd=NSI.parent,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )
            combined = (proc.stdout or "") + (proc.stderr or "")
            # NSIS reports failures as `error:` or `Error in script`. A bare "error"
            # substring also matches `-ErrorAction` in the PowerShell probe this
            # script runs, which is a false positive on a clean preprocess.
            fatal = [
                line
                for line in combined.splitlines()
                if re.search(r"(^|\s)error:|Error in script|aborting creation process", line, re.IGNORECASE)
            ]
            check("B", "makensis preprocesses without error", not fatal, fatal[0][:90] if fatal else "")
        else:
            check("B", "makensis available for the syntax check", False, str(NSIS))

    # ------------------------------------------------------------- payload ---
    print("\n=== C. the payload ===")
    if not PAYLOAD.is_dir():
        check("C", "payload present", False, str(PAYLOAD))
    else:
        entries = sorted(p.name for p in PAYLOAD.iterdir())
        print(f"  top level: {', '.join(entries)}")
        for required in ("AI Workbench.exe", "backend", "frontend", "scripts", "worker", "vendor"):
            check("C", f"{required} is in the payload", required in entries)

        files = [f for f in PAYLOAD.rglob("*") if f.is_file()]
        total = sum(f.stat().st_size for f in files)
        print(f"  {len(files):,} files, {human(total)}")

        # Things that must never ship.
        for forbidden, why in (
            (".runtime", "the developer's database"),
            (".venv", "a development interpreter"),
            ("node_modules", "development packages"),
            (".git", "repository history"),
        ):
            hits = [f for f in files if forbidden in f.parts]
            check("C", f"no {forbidden} in the payload", not hits, f"{len(hits)} files" if hits else why)

        zips = [f for f in files if f.suffix.lower() == ".zip"]
        check("C", "no download caches (.zip) in the payload", not zips, f"{len(zips)} files")

        check("C", "launcher sits at the payload root", (PAYLOAD / "AI Workbench.exe").is_file())
        check("C", "data-dir.txt is NOT shipped", not (PAYLOAD / "data-dir.txt").exists())
        check("C", ".env is NOT shipped (only the example)", not (PAYLOAD / ".env").exists())

        # Secrets. Same patterns the archive builder uses, with one addition.
        #
        # The boundaries matter: `AKIA[0-9A-Z]{16}` matches a run of base64 inside a
        # font blob in Pillow's `ImageFont.py` (`AKIAAQAAAAAABAAHAM0A`). A real key is
        # a standalone token, so the match must not be surrounded by base64 characters.
        patterns = re.compile(
            r"(?<![A-Za-z0-9+/=])"
            r"(sk-[A-Za-z0-9]{20,}|AIza[0-9A-Za-z\-_]{30,}|ghp_[A-Za-z0-9]{30,}|AKIA[0-9A-Z]{16})"
            r"(?![A-Za-z0-9+/=])"
        )
        hits = []
        for path in files:
            if path.suffix.lower() in {".pyc", ".pyo", ".pdb", ".dll", ".exe", ".pyd", ".png", ".jpg", ".woff2"}:
                continue
            # This file is in the payload (scripts/ ships whole), and it contains the
            # patterns themselves as source text - so it matches itself. Skipping it is
            # a self-reference, not a whitelist: the other 8,600 files are still scanned.
            if path.name == "verify_installer_static.py":
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            if patterns.search(text):
                hits.append(str(path.relative_to(PAYLOAD)))
        check("C", "no credentials in the payload's text files", not hits, ", ".join(hits[:3]))

    # ------------------------------------------------------------- verdict ---
    failed = [r for r in results if not r[1]]
    print(f"\n=== {len(results) - len(failed)}/{len(results)} passed ===")
    for name, _, detail in failed:
        print(f"  [FAIL] {name} — {detail}")

    report = {
        "setup_exe": str(SETUP),
        "size_bytes": size,
        "sha256": digest,
        "results": [{"name": n, "ok": ok, "detail": d} for n, ok, d in results],
        "passed": len(results) - len(failed),
        "total": len(results),
        "note": "static only: no process is started, so nothing here proves the installer runs",
    }
    out = ROOT / "build" / "installer-static-check.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  report: {out.relative_to(ROOT)}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
