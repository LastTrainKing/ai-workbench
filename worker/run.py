#!/usr/bin/env python3
"""Standalone worker launcher.

Convenience entry point so the worker can be run as ``python worker/run.py``
in addition to ``python -m app.cli worker``.

Running the worker as its own process is the recommended production topology:
it isolates CPU-bound work (parsing, chunking, embedding) from the API event
loop.  For a single-machine demo the API can host the worker in-process
(``WORKER_ENABLED=true``, the default) which keeps "one command to start"
true.
"""

from __future__ import annotations

import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="AI Workbench background worker")
    parser.add_argument("--concurrency", type=int, default=None, help="number of parallel jobs")
    args = parser.parse_args()

    from app.worker.runner import run_worker_blocking

    run_worker_blocking(args.concurrency)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
