"""Real junction / symlink escape test against the workspace boundary.

Not a mock. It creates an actual reparse point inside a real workspace and asks the
permission manager whether the path behind it is reachable.

    python scripts/verify_workspace_escape.py

Exits 0 when every escape shape is refused.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from app.ai.tools.permissions import AgentMode, AgentPolicy, PermissionLevel, PermissionManager  # noqa: E402

results: list[tuple[str, bool, str]] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    results.append((label, ok, detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}{f' — {detail}' if detail else ''}")


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="aiw-escape-") as tmp:
        root = Path(tmp)
        workspace = root / "workspace"
        workspace.mkdir()
        secret = root / "outside" / "secret"
        secret.mkdir(parents=True)
        (secret / "id_rsa").write_text("PRIVATE", encoding="utf-8")
        (workspace / "normal.txt").write_text("ok", encoding="utf-8")

        # **Level matters, and the first version of this script used the wrong one.**
        #
        # At `SYSTEM`, reading outside the workspace is permitted *by design* — that is
        # what the level means. The workspace boundary is enforced at `WORKSPACE`, so that
        # is where an escape has to be refused. Running the test at SYSTEM and calling the
        # result a failure would have been a false alarm about correct behaviour.
        policy = AgentPolicy.from_settings(
            mode=AgentMode.SUPERVISED,
            level=PermissionLevel.WORKSPACE,
            workspace=workspace,
            allowed_paths=[workspace],
        )
        manager = PermissionManager(policy)
        print(f"  workspace = {workspace}")
        print(f"  secret    = {secret}\n")

        # 1. A normal file inside the workspace is allowed.
        check("normal file inside the workspace", manager.check_path(workspace / "normal.txt").allowed)

        # 2. `..` traversal out of the workspace.
        escape = workspace / ".." / "outside" / "secret" / "id_rsa"
        check("`..` traversal is refused", not manager.check_path(escape).allowed, str(escape))

        # 3. Absolute path outside the workspace.
        check("absolute path outside is refused", not manager.check_path(secret / "id_rsa").allowed)

        # 4. Directory junction pointing out of the workspace. This is the Windows shape:
        #    `mklink /J` needs no elevation, which is exactly why it is the one that
        #    matters. A symlink needs developer mode or admin, so it is the lesser risk.
        junction = workspace / "link-out"
        made = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(junction), str(secret)],
            capture_output=True,
            text=True,
            # mklink writes its confirmation in the console code page, not UTF-8.
            encoding="mbcs",
            errors="replace",
            check=False,
        )
        if made.returncode == 0 and junction.exists():
            check(
                "junction created for real (mklink /J)",
                True,
                (made.stdout or "").strip()[:60],
            )
            verdict = manager.check_path(junction / "id_rsa")
            check(
                "reading through a junction is refused",
                not verdict.allowed,
                f"allowed={verdict.allowed} reason={verdict.reason[:60]}",
            )
            check(
                "writing through a junction is refused",
                not manager.check_path(junction / "new.txt", write=True).allowed,
            )
        else:
            check(
                "junction created for real (mklink /J)",
                False,
                f"mklink failed rc={made.returncode}: {(made.stderr or made.stdout or '').strip()[:80]}",
            )

        # 5. Symlink, if this machine allows creating one.
        symlink = workspace / "link-sym"
        try:
            symlink.symlink_to(secret, target_is_directory=True)
            check("symlink created for real", True)
            check(
                "reading through a symlink is refused",
                not manager.check_path(symlink / "id_rsa").allowed,
            )
        except OSError as exc:
            # Not a failure of the product: creating a symlink needs a privilege this
            # shell does not have. Recorded as not verified rather than as a pass.
            print(f"  [NOT VERIFIED] symlink could not be created here: {type(exc).__name__}")

    failed = [r for r in results if not r[1]]
    print(f"\n=== {len(results) - len(failed)}/{len(results)} passed ===")
    for label, _ok, detail in failed:
        print(f"  [FAIL] {label} — {detail}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
