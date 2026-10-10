"""Where does the Qdrant API key come from, and does the store actually work with it?

`scripts/infra-start.ps1` starts Qdrant with a key and writes it to
``<runtime>/qdrant-api-key.txt``. The backend is started by a *different* process and cannot
inherit that environment, so `app.core.config` reads the same file. Two implementations of
one decision — and if they disagree, the launcher enforces a key the client never sends and
every vector call fails with 401 while the interface reports "the knowledge base is empty".

This checks the five things that can go wrong, in separate child processes so each one
reads the configuration exactly the way the application does rather than the way a test
happens to have set it up:

  1  no key configured       → the request is **refused**, and the message says why
  2  key configured          → a real request to the running Qdrant succeeds
  3  after a restart         → the same key is still used, and still works
  4  the logs                → contain no complete key
  5  the installer           → contains no complete key

The key itself is never printed. Everything reports a SHA-256 fingerprint, which is enough
to tell "the same key" from "a different key" and useless to anyone who reads the output.

    .venv\\Scripts\\python.exe scripts\\verify_qdrant_key.py
    .venv\\Scripts\\python.exe scripts\\verify_qdrant_key.py --data-dir <path>
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
SETUP = ROOT / "build" / "installer" / "AI-Workbench-Native-Setup.exe"

RESULTS: list[tuple[str, bool, str]] = []


def check(label: str, ok: bool, detail: str = "") -> bool:
    RESULTS.append((label, bool(ok), detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}" + (f" — {detail}" if detail else ""), flush=True)
    return bool(ok)


def section(title: str) -> None:
    print(f"\n=== {title} ===", flush=True)


#: Run in a child process. Reads the configuration through `app.core.config`, exactly as the
#: application does, and makes one real request.
PROBE = r"""
import asyncio, hashlib, json, os, sys
sys.path.insert(0, os.environ["AW_BACKEND"])
from app.core.config import settings
from app.infrastructure.vector_store import VectorStore

async def main():
    key = settings.qdrant_api_key or ""
    if os.environ.get("QDRANT_API_KEY", "").strip():
        source = "environment"
    elif key:
        source = "runtime file"
    else:
        source = "none"
    store = VectorStore()
    ok, detail = await store.ping()
    await store.close()
    print("PROBE " + json.dumps({
        "ok": ok,
        "detail": detail,
        "key_source": source,
        "key_length": len(key),
        "key_fingerprint": hashlib.sha256(key.encode()).hexdigest()[:12] if key else "",
        "data_dir": os.environ.get("AI_WORKBENCH_DATA_DIR", ""),
        "qdrant_url": settings.qdrant_url,
    }))

asyncio.run(main())
"""


def probe(*, data_dir: Path | None, with_env_key: bool) -> dict:
    """One request to Qdrant, from a fresh process, configured the way the app would be."""
    env = os.environ.copy()
    env["AW_BACKEND"] = str(BACKEND)
    env["AI_PROVIDER"] = "offline"
    env["REDIS_URL"] = "redis://127.0.0.1:6379/1"
    env.pop("QDRANT_API_KEY", None)
    if with_env_key:
        # The one legitimate alternative: an operator set the variable. `app.core.config`
        # gives it priority over the file, and this checks that it does.
        key_file = (data_dir / "qdrant-api-key.txt") if data_dir else None
        if key_file and key_file.is_file():
            env["QDRANT_API_KEY"] = key_file.read_text(encoding="utf-8").strip()
    if data_dir is not None:
        env["AI_WORKBENCH_DATA_DIR"] = str(data_dir)

    completed = subprocess.run(
        [sys.executable, "-c", PROBE],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        cwd=str(ROOT),
        check=False,
        timeout=180,
    )
    for line in (completed.stdout or "").splitlines():
        if line.startswith("PROBE "):
            return json.loads(line.removeprefix("PROBE "))
    return {
        "ok": False,
        "detail": f"探针没有输出（exit {completed.returncode}）：{(completed.stderr or '')[-400:]}",
        "key_source": "?",
        "key_length": 0,
        "key_fingerprint": "",
        "data_dir": str(data_dir or ""),
        "qdrant_url": "",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="verify_qdrant_key.py")
    parser.add_argument("--data-dir", default="", help="the runtime directory to read the key from")
    parser.add_argument("--json", default="", help="write the result as JSON here")
    args = parser.parse_args(argv)

    real_data = Path(args.data_dir) if args.data_dir else ROOT / ".runtime"
    key_file = real_data / "qdrant-api-key.txt"
    print(f"运行时目录   {real_data}")
    print(f"key 文件     {key_file}  {'存在' if key_file.is_file() else '不存在'}")
    if key_file.is_file():
        value = key_file.read_text(encoding="utf-8").strip()
        print(f"key 指纹     {hashlib.sha256(value.encode()).hexdigest()[:12]}（长度 {len(value)}）")

    # ------------------------------------------------- 1. no key configured --
    section("1. 没有配置 key：请求被拒绝，并且说清楚原因")
    empty = Path(tempfile.mkdtemp(prefix="aw-qdrant-nokey-"))
    missing = probe(data_dir=empty, with_env_key=False)
    check(
        "没有 key 时真实请求被拒绝",
        missing["ok"] is False,
        f"{missing['key_source']} / {missing['detail'][:180]}",
    )
    check(
        "拒绝的原因可以诊断（提到鉴权，而不是含糊的失败）",
        any(
            marker in missing["detail"].lower()
            for marker in ("401", "unauthorized", "forbidden", "403", "api key", "api_key", "authentication")
        ),
        missing["detail"][:180],
    )

    # ------------------------------------------------- 2. key configured --
    section("2. 配置了 key：真实请求成功")
    if not key_file.is_file():
        check("运行时目录里有 key 文件", False, f"{key_file} 不存在")
        check("配置 key 后真实请求成功", False, "没有 key 可测；先运行 scripts\\infra-start.ps1")
        configured: dict = {}
    else:
        check("运行时目录里有 key 文件", True, str(key_file))
        configured = probe(data_dir=real_data, with_env_key=False)
        check(
            "配置 key 后真实请求成功",
            configured["ok"] is True,
            f"{configured['key_source']} / {configured['detail'][:180]}",
        )
        check(
            "key 是从运行时文件读到的（不是环境变量）",
            configured["key_source"] == "runtime file",
            configured["key_source"],
        )

    # ------------------------------------------------- 3. it survives a restart --
    section("3. 重启后仍然有效")
    if configured:
        again = probe(data_dir=real_data, with_env_key=False)
        check("第二个进程里真实请求同样成功", again["ok"] is True, again["detail"][:180])
        check(
            "两个进程用的是同一个 key",
            again["key_fingerprint"] == configured["key_fingerprint"] != "",
            f"{configured['key_fingerprint']} / {again['key_fingerprint']}",
        )
        # The environment variable is the documented override, so it has to win.
        via_env = probe(data_dir=real_data, with_env_key=True)
        check(
            "显式设置 QDRANT_API_KEY 时优先于文件",
            via_env["key_source"] == "environment" and via_env["ok"] is True,
            f"{via_env['key_source']} / {via_env['detail'][:120]}",
        )
    else:
        check("第二个进程里真实请求同样成功", False, "上一步没有 key 可测")
        check("两个进程用的是同一个 key", False, "上一步没有 key 可测")
        check("显式设置 QDRANT_API_KEY 时优先于文件", False, "上一步没有 key 可测")

    # ------------------------------------------------- 4. no key in the logs --
    section("4. 完整 key 只出现在它该在的地方")
    if key_file.is_file():
        secret = key_file.read_text(encoding="utf-8").strip()

        #: Two files are *supposed* to contain it, and a check that flagged them would be a
        #: check that fails on every correct installation — which is how a security check
        #: gets switched off. `qdrant/config/config.yaml` is how Qdrant is told its own key;
        #: the key file is how the backend is told to send it. Both are on the local disk,
        #: inside the user's data directory, readable only by that user.
        ALLOWED = {"qdrant-api-key.txt", "qdrant/config/config.yaml"}
        offenders: list[str] = []
        for entry in real_data.rglob("*"):
            if not entry.is_file():
                continue
            try:
                if entry.stat().st_size > 32 * 1024 * 1024:
                    continue
                blob = entry.read_bytes()
            except OSError:
                continue
            if secret.encode() in blob:
                offenders.append(str(entry.relative_to(real_data)).replace("\\", "/"))
        unexpected = [name for name in offenders if name not in ALLOWED]
        check(
            "除 key 文件与 Qdrant 自己的配置外，运行时目录里没有它",
            not unexpected,
            f"出现在：{unexpected}" if unexpected else f"只在 {sorted(offenders)}",
        )
        # The logs are named separately because they are the one place a secret leaks
        # without anyone deciding to put it there.
        log_hits = [name for name in offenders if name.startswith("logs/")]
        check("日志里没有完整 key", not log_hits, f"出现在：{log_hits}" if log_hits else "无")
    else:
        check("除 key 文件与 Qdrant 自己的配置外，运行时目录里没有它", False, "没有 key 可测")
        check("日志里没有完整 key", False, "没有 key 可测")

    # --------------------------------------------------- 4b. not in the source --
    #
    # The key is read from the file and never written back into the repository. Checked by
    # reading the tracked files here rather than by `git grep <key>`: passing the secret as a
    # command-line argument would put it in the process list, which is one of the places the
    # brief says it must not appear.
    section("4b. 源码与 Git 跟踪的文件里没有完整 key")
    if key_file.is_file():
        secret = key_file.read_text(encoding="utf-8").strip()
        tracked = subprocess.run(
            ["git", "ls-files"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=str(ROOT),
            check=False,
        ).stdout.splitlines()
        binary_suffixes = {".png", ".jpg", ".jpeg", ".gif", ".ico", ".zip", ".exe", ".dll", ".pyd", ".so", ".whl"}
        in_source: list[str] = []
        for name in tracked:
            path = ROOT / name
            if path.suffix.lower() in binary_suffixes:
                continue
            try:
                if path.stat().st_size > 2 * 1024 * 1024:
                    continue
                blob = path.read_bytes()
            except OSError:
                continue
            if secret.encode() in blob:
                in_source.append(name)
            if len(in_source) >= 4:
                break
        check(
            f"Git 跟踪的 {len(tracked)} 个文件里没有这个 key",
            not in_source,
            f"出现在：{in_source}" if in_source else "无",
        )
    else:
        check("Git 跟踪的文件里没有这个 key", False, "没有 key 可测")

    # ------------------------------------------------- 5. not in the installer --
    section("5. 安装包里没有完整 key")
    if SETUP.is_file() and key_file.is_file():
        secret = key_file.read_text(encoding="utf-8").strip()
        found = False
        with SETUP.open("rb") as handle:
            while chunk := handle.read(1 << 20):
                if secret.encode() in chunk:
                    found = True
                    break
        check("安装包里没有这个 key", not found, "未出现" if not found else "出现了")
    else:
        check(
            "安装包里没有这个 key",
            False,
            f"安装包 {'存在' if SETUP.is_file() else '不存在'}；key 文件 {'存在' if key_file.is_file() else '不存在'}",
        )

    passed = sum(1 for _l, ok, _d in RESULTS if ok)
    total = len(RESULTS)
    print()
    print("-" * 74)
    print(f"结果：{passed}/{total} 通过")
    for label, ok, detail in RESULTS:
        if not ok:
            print(f"  FAIL  {label} — {detail}")

    if args.json:
        Path(args.json).write_text(
            json.dumps(
                {
                    "data_dir": str(real_data),
                    "key_file": str(key_file),
                    "passed": passed,
                    "total": total,
                    "checks": [
                        {"label": label, "ok": ok, "detail": detail} for label, ok, detail in RESULTS
                    ],
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
