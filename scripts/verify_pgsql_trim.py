"""Prove the trimmed PostgreSQL still does everything the product asks of it.

`trim_runtime.py` removes 800 MB from the vendored distribution. Deleting files from a
database server is the kind of change that looks fine until the first launch, so the
trim is only allowed to stand if the server still does the four things this product
actually uses:

    initdb          create a cluster from nothing (first launch)
    pg_ctl start    launch the server
    psql SELECT 1   accept a query
    pg_ctl stop     shut down cleanly

All four run against a **temporary cluster on a spare port**, so the developer's own
database is never touched and the check is honest about what it proves: the trimmed
binaries work, not merely that a server that was already running stayed up.

    python scripts/verify_pgsql_trim.py
"""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PG_BIN = ROOT / "vendor" / "runtime" / "pgsql" / "pgsql" / "bin"
PORT = 55432  # deliberately not 5432: the real stack may be running

results: list[tuple[str, bool, str]] = []


def check(step: str, name: str, ok: bool, detail: str = "") -> None:
    results.append((f"{step} {name}", bool(ok), detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


def run(*args: str, timeout: int = 120) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
    )


#: The password the throwaway cluster is created with. `psql` would otherwise stop
#: and ask for it — with no terminal attached it waits forever, which is how this
#: check first failed: not a wrong answer, no answer.
PG_PASSWORD = "trimcheck"


def psql(*args: str, timeout: int = 60) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PGPASSWORD"] = PG_PASSWORD
    return subprocess.run(
        args,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
        env=env,
    )


def port_open(port: int) -> bool:
    with socket.socket() as sock:
        sock.settimeout(1.0)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def main() -> int:
    print("=" * 72)
    print("  Trimmed PostgreSQL: does it still work?")
    print("=" * 72)

    for tool in ("initdb.exe", "pg_ctl.exe", "psql.exe", "postgres.exe", "pg_isready.exe"):
        check("binaries", f"{tool} present", (PG_BIN / tool).is_file())

    if not all(ok for _, ok, _ in results):
        print("\n  the distribution is incomplete — re-fetch with scripts/fetch-runtime.ps1")
        return 1

    # Refuse to run against somebody else's server.
    #
    # A leftover cluster from an interrupted run once squatted this port, and the
    # check reported "rejecting connections" — a FAIL that was entirely about the
    # squatter, not about the trim. A test that can silently measure the wrong
    # process is worse than no test.
    if port_open(PORT):
        print(f"\n  port {PORT} is already in use. Something else is listening there;")
        print("  this check would be measuring that process, not the trimmed server.")
        print("  Stop it first, or change PORT at the top of this script.")
        return 1

    data_dir = Path(tempfile.mkdtemp(prefix="aiw-pgtrim-"))
    log_file = data_dir.parent / (data_dir.name + ".log")
    started = False
    try:
        # ------------------------------------------------------------- initdb --
        # Same invocation the launcher uses, including `--pwfile`: scram-sha-256
        # without a password is refused, and testing a weaker auth mode would be
        # testing something the product never does.
        print("\n=== initdb: create a cluster from nothing ===")
        pwfile = data_dir.parent / (data_dir.name + ".pw")
        pwfile.write_text(PG_PASSWORD, encoding="ascii")
        result = run(
            str(PG_BIN / "initdb.exe"),
            "-D", str(data_dir),
            "-U", "postgres",
            "-A", "scram-sha-256",
            f"--pwfile={pwfile}",
            "-E", "UTF8",
            "--locale=C",
        )
        pwfile.unlink(missing_ok=True)
        last_line = (result.stdout or result.stderr).strip().splitlines()
        check("initdb", "created the cluster", result.returncode == 0, last_line[-1] if last_line else "")
        check("initdb", "PG_VERSION written", (data_dir / "PG_VERSION").is_file())

        # -------------------------------------------------------------- start --
        # `postgres.exe` directly, NOT `pg_ctl start`.
        #
        # On Windows `pg_ctl` keeps a handle on the stdout pipe it inherits, so
        # capturing its output makes the caller block until the *server* exits —
        # which is forever. This script hung for thirteen minutes on exactly that
        # before the cause was found, and the launcher's own comment records the
        # same discovery. Output goes to a log file instead of a pipe.
        print("\n=== start the server ===")
        server = subprocess.Popen(
            [
                str(PG_BIN / "postgres.exe"),
                "-D", str(data_dir),
                "-p", str(PORT),
                "-c", "listen_addresses=127.0.0.1",
            ],
            stdout=log_file.open("w", encoding="utf-8"),
            stderr=subprocess.STDOUT,
            cwd=str(data_dir),
        )
        for _ in range(30):
            if port_open(PORT):
                break
            time.sleep(1)
        started = port_open(PORT)
        check("start", f"listening on {PORT}", started, "" if started else f"see {log_file}")

        # ------------------------------------------------------------ pg_isready --
        result = run(str(PG_BIN / "pg_isready.exe"), "-h", "127.0.0.1", "-p", str(PORT))
        check("pg_isready", "reports accepting connections", result.returncode == 0, (result.stdout or "").strip())

        # ---------------------------------------------------------------- query --
        print("\n=== psql: run a real query ===")
        result = psql(
            str(PG_BIN / "psql.exe"),
            "-h", "127.0.0.1", "-p", str(PORT), "-U", "postgres", "-d", "postgres",
            "-t", "-A", "-c", "SELECT 1;",
        )
        check("psql", "SELECT 1 returns 1", result.returncode == 0 and result.stdout.strip() == "1", (result.stdout or result.stderr).strip())

        # Something with real work in it: the server writes, reads and reports.
        result = psql(
            str(PG_BIN / "psql.exe"),
            "-h", "127.0.0.1", "-p", str(PORT), "-U", "postgres", "-d", "postgres",
            "-t", "-A", "-c",
            "CREATE TABLE t(id serial primary key, note text);"
            "INSERT INTO t(note) VALUES ('trimmed');"
            "SELECT count(*) FROM t;",
        )
        # Three statements, three lines of output; the count is the last one.
        # Asserting on the whole stdout would be asserting on psql's echo, not on
        # whether the server stored the row.
        last = result.stdout.strip().splitlines()[-1].strip() if result.stdout.strip() else ""
        check(
            "psql",
            "create + insert + count works",
            result.returncode == 0 and last == "1",
            (result.stdout or result.stderr).strip().replace("\n", " | "),
        )

        # -------------------------------------------------------------- stop ---
        print("\n=== stop the server ===")
        result = run(str(PG_BIN / "pg_ctl.exe"), "stop", "-D", str(data_dir), "-m", "fast", "-w", timeout=60)
        server.wait(timeout=30)
        started = False
        check("stop", "stopped cleanly", result.returncode == 0, (result.stdout or result.stderr).strip().splitlines()[-1] if (result.stdout or result.stderr).strip() else "")
        time.sleep(2)
        check("stop", f"port {PORT} released", not port_open(PORT))

    except subprocess.TimeoutExpired as exc:
        check("timeout", "a step exceeded its budget", False, str(exc))
    finally:
        if started:
            run(str(PG_BIN / "pg_ctl.exe"), "stop", "-D", str(data_dir), "-m", "immediate", timeout=30)
        shutil.rmtree(data_dir, ignore_errors=True)
        log_file.unlink(missing_ok=True)

    failed = [r for r in results if not r[1]]
    print(f"\n=== {len(results) - len(failed)}/{len(results)} passed ===")
    for name, _, detail in failed:
        print(f"  [FAIL] {name} — {detail}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
