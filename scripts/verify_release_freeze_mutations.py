"""Mutation check: does each regression test actually catch its defect?

Running the new tests against the pre-fix commit only proves they import new modules.
That is coupling, not evidence. This script does the stronger thing: it takes the
**fixed** tree, reintroduces the original defect with a targeted edit, runs the test that
is supposed to catch it, and requires that test to fail. Then it restores the file.

A test that still passes with the defect reintroduced is not a regression test.

Usage::

    .venv/Scripts/python.exe scripts/verify_release_freeze_mutations.py

It exits non-zero if any defect is NOT caught, so it can be wired into a gate.
"""

from __future__ import annotations

import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
PYTHON = ROOT / ".venv" / "Scripts" / "python.exe"

#: (label, file, old_text, new_text, test node id)
CASES: list[tuple[str, str, str, str, str]] = [
    (
        "RF-02 installer copies .env.example",
        "installer/ai-workbench.nsi",
        "  IfFileExists \"$INSTDIR\\.env\" env_present env_absent",
        "  CopyFiles /SILENT \"$INSTDIR\\.env.example\" \"$INSTDIR\\.env\"\n"
        "  IfFileExists \"$INSTDIR\\.env\" env_present env_absent",
        "tests/unit/test_release_freeze_production_env.py::test_installer_no_longer_copies_the_example",
    ),
    (
        "RF-03 placeholder key accepted",
        "backend/app/core/config.py",
        "    markers = sorted({m for m in _PLACEHOLDER_MARKERS if m in lowered})",
        "    markers = []",
        "tests/unit/test_release_freeze_security.py::test_production_rejects_every_placeholder_secret",
    ),
    (
        "RF-04 bcrypt truncation",
        "backend/app/core/security.py",
        "    hashed = bcrypt.hashpw(_prehash(password), bcrypt.gensalt(rounds=_BCRYPT_ROUNDS))",
        "    hashed = bcrypt.hashpw(\n"
        "        password.encode(\"utf-8\")[:_MAX_PASSWORD_BYTES], bcrypt.gensalt(rounds=_BCRYPT_ROUNDS)\n"
        "    )",
        "tests/unit/test_release_freeze_security.py::test_new_scheme_rejects_passwords_that_only_differ_after_byte_72",
    ),
    (
        "RF-05 revocation fails open",
        "backend/app/core/security.py",
        "        return RevocationState.STORAGE_UNAVAILABLE",
        "        return RevocationState.NOT_REVOKED",
        "tests/unit/test_release_freeze_security.py::test_storage_failure_is_not_reported_as_valid",
    ),
    (
        "RF-06 stopped run marked succeeded",
        "backend/app/ai/agent/executor.py",
        "            status = _STOP_REASON_STATUS[reason]",
        "            status = RunStatus.SUCCEEDED if final_answer else RunStatus.FAILED",
        "tests/integration/test_release_freeze_run_state.py::test_agent_hitting_max_steps_is_incomplete_not_succeeded",
    ),
    (
        "RF-07 unbounded job retry",
        "backend/app/repositories/ai_repo.py",
        "            if job.attempts >= job.max_attempts:",
        "            if False:",
        "tests/integration/test_release_freeze_run_state.py::test_stale_job_with_attempts_spent_becomes_terminal_failed",
    ),
    (
        "RF-08 missing policy allowed",
        "backend/app/ai/skills/discovery.py",
        "    if permissions is None:\n        raise PermissionDeniedError(\n            \"该请求没有权限策略，按安全默认值拒绝\",\n            details={\"url\": url, \"reason\": \"no_permission_policy\"},\n        )",
        "    if permissions is None:\n        return",
        "tests/unit/test_release_freeze_skill_network.py::test_missing_policy_is_denied",
    ),
    (
        "RF-09 shared provider config",
        "backend/app/ai/runtime_config.py",
        "    if owner is None:\n        return CONFIG_PATH",
        "    return CONFIG_PATH\n    if owner is None:\n        return CONFIG_PATH",
        "tests/unit/test_release_freeze_provider_isolation.py::test_one_account_cannot_change_another",
    ),
    (
        "RF-10 Qdrant CORS enabled",
        "scripts/infra-start.ps1",
        "  enable_cors: false",
        "  enable_cors: true",
        "tests/unit/test_release_freeze_deployment.py::test_infra_script_does_not_enable_cors",
    ),
    (
        "RF-11 one citation grounds everything",
        "backend/app/ai/rag/pipeline.py",
        "        and supported_units == len(units)",
        "        and supported_units >= 1",
        "tests/unit/test_release_freeze_rag_citations.py::test_one_citation_for_many_claims_is_not_grounded",
    ),
    (
        "RF-12 kill by image name",
        "scripts/verify_installer.py",
        "    stop_app_processes([INSTALL_DIR])",
        "    subprocess.run([\"taskkill\", \"/F\", \"/IM\", EXE_NAME], capture_output=True, check=False)",
        "tests/unit/test_release_freeze_process_safety.py::test_no_verify_script_kills_by_image_name",
    ),
    (
        "RF-13 version drift",
        "desktop/package.json",
        '"version": "2.2.0"',
        '"version": "1.0.0"',
        "tests/unit/test_release_freeze_versioning.py::test_every_file_agrees_with_the_version_file",
    ),
    (
        "RF-14 compose publishes on all interfaces",
        "docker-compose.yml",
        '      - "127.0.0.1:${API_PORT:-8000}:8000"',
        '      - "${API_PORT:-8000}:8000"',
        "tests/unit/test_release_freeze_deployment.py::test_every_published_port_is_bound_to_loopback",
    ),
    (
        "RF-16 unauthenticated delivery states",
        "backend/app/api/v1/delivery.py",
        "async def delivery_states(user: CurrentUser) -> dict[str, Any]:",
        "async def delivery_states() -> dict[str, Any]:",
        "tests/api/test_release_freeze_ownership.py::test_every_route_refuses_an_unauthenticated_request[GET-/api/v1/delivery/states]",
    ),
]


def run_test(node: str) -> tuple[bool, str]:
    """Returns (passed, summary line)."""
    result = subprocess.run(
        [str(PYTHON), "-m", "pytest", node, "--no-header", "-p", "no:cacheprovider", "-q"],
        cwd=BACKEND,
        capture_output=True,
        text=True,
        timeout=600,
    )
    tail = [line for line in result.stdout.strip().splitlines() if line.strip()]
    return result.returncode == 0, (tail[-1] if tail else "<no output>")


def main() -> int:
    print("baseline: every test must PASS on the fixed tree")
    print("-" * 78)
    failures: list[str] = []

    for label, rel, old, new, node in CASES:
        path = ROOT / rel
        original = path.read_bytes()
        # Anchors are written with LF. Several files in this repository are CRLF, so the
        # text is normalised before matching and the original convention is restored on
        # write — otherwise a multi-line anchor silently never matches.
        crlf = b"\r\n" in original
        text = original.decode("utf-8", errors="surrogateescape").replace("\r\n", "\n")
        if old not in text:
            print(f"  ??  {label}: anchor not found in {rel}")
            failures.append(f"{label}: anchor not found")
            continue
        try:
            mutated = text.replace(old, new, 1).encode("utf-8", errors="surrogateescape")
            if crlf:
                mutated = mutated.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")
            path.write_bytes(mutated)
            passed, summary = run_test(node)
        finally:
            path.write_bytes(original)

        verdict = "CAUGHT" if not passed else "MISSED"
        if passed:
            failures.append(f"{label}: test still passed with the defect reintroduced")
        print(f"  {verdict:6}  {label}")
        print(f"          {summary[:110]}")

    print("-" * 78)
    if failures:
        print(f"NOT PROVEN ({len(failures)}):")
        for item in failures:
            print(f"  - {item}")
        return 1
    print(f"All {len(CASES)} defects are caught by their regression test.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
