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
    # --- RF-18: the tool-invocation window ---------------------------------- #
    # Each of these reintroduces one half of the ordering that makes a crash survivable.
    (
        "RF-18a intent flushed, not committed, before the tool runs",
        "backend/app/ai/agent/executor.py",
        "                await session.commit()\n\n                result: ToolResult = await registry.execute(\n"
        "                    ctx, request.name, request.arguments\n                )",
        "                await session.flush()\n\n                result: ToolResult = await registry.execute(\n"
        "                    ctx, request.name, request.arguments\n                )",
        "tests/integration/test_release_freeze_tool_invocation.py"
        "::test_the_executor_commits_the_intent_before_the_tool_runs",
    ),
    (
        "RF-18b the outcome never closes the window",
        "backend/app/ai/tracing.py",
        "    call.error_message = (error_message or \"\")[:2000]\n"
        "    call.finished_at = datetime.now(UTC)",
        "    call.error_message = (error_message or \"\")[:2000]",
        "tests/integration/test_release_freeze_tool_invocation.py"
        "::test_window_4_a_committed_outcome_is_decided",
    ),
    (
        "RF-18c recovery only looks at calls that changed nothing",
        "backend/app/ai/tracing.py",
        "    rows = list(\n"
        "        (\n"
        "            await session.execute(\n"
        "                select(ToolCall).where(ToolCall.status == ToolCallStatus.RUNNING.value)\n"
        "            )\n"
        "        ).scalars()\n"
        "    )",
        "    rows = list(\n"
        "        (\n"
        "            await session.execute(\n"
        "                select(ToolCall).where(\n"
        "                    ToolCall.status == ToolCallStatus.RUNNING.value,\n"
        "                    ToolCall.mutates_state.is_(False),\n"
        "                )\n"
        "            )\n"
        "        ).scalars()\n"
        "    )",
        "tests/integration/test_release_freeze_tool_invocation.py"
        "::test_recovery_closes_the_open_windows_and_leaves_decided_ones_alone",
    ),
    (
        "RF-18d the idempotency key is ignored",
        "backend/app/ai/tracing.py",
        "    if invocation_id:\n"
        "        existing = (\n"
        "            await session.execute(select(ToolCall).where(ToolCall.invocation_id == invocation_id))\n"
        "        ).scalar_one_or_none()",
        "    if False:\n"
        "        existing = (\n"
        "            await session.execute(select(ToolCall).where(ToolCall.invocation_id == invocation_id))\n"
        "        ).scalar_one_or_none()",
        "tests/integration/test_release_freeze_tool_invocation.py"
        "::test_the_same_invocation_id_does_not_create_a_second_row",
    ),
    (
        "RF-18e a refusal is recorded as a run",
        "backend/app/ai/tracing.py",
        "        status=ToolCallStatus.SKIPPED.value,\n"
        "        error_message=reason[:2000],\n"
        "        latency_ms=0,\n"
        "        created_at=now,\n"
        "        invocation_id=new_invocation_id(),\n"
        "        started_at=now,\n"
        "        finished_at=now,",
        "        status=ToolCallStatus.RUNNING.value,\n"
        "        error_message=reason[:2000],\n"
        "        latency_ms=0,\n"
        "        created_at=now,\n"
        "        invocation_id=new_invocation_id(),\n"
        "        started_at=now,\n"
        "        finished_at=None,",
        "tests/integration/test_release_freeze_tool_invocation.py"
        "::test_a_duplicate_call_is_recorded_as_skipped_not_as_a_second_execution",
    ),
    (
        "RF-18f an unclassified write is treated as replayable",
        "backend/app/ai/types.py",
        "        return \"idempotent\" if self.idempotent else \"unsafe\"",
        "        return \"idempotent\"",
        "tests/unit/test_release_freeze_tool_replay.py"
        "::test_a_write_that_nobody_classified_is_unsafe",
    ),
    (
        "RF-18g an unknown tool is assumed harmless",
        "backend/app/ai/tracing.py",
        "    if spec is None:\n        return \"unsafe\"",
        "    if spec is None:\n        return \"read_only\"",
        "tests/unit/test_release_freeze_tool_replay.py"
        "::test_an_unknown_tool_is_treated_as_unsafe",
    ),
    (
        "RF-18h the resolver only understands one registry shape",
        "backend/app/ai/tools/registry.py",
        "    lister = getattr(registry, \"specs\", None)\n"
        "    if callable(lister):\n"
        "        for spec in lister():\n"
        "            if getattr(spec, \"name\", \"\") == name:\n"
        "                return spec\n"
        "    return None",
        "    return None",
        "tests/unit/test_release_freeze_tool_replay.py"
        "::test_spec_for_resolves_through_specs_when_there_is_no_get_spec",
    ),
    (
        # `file_write` was marked idempotent, on the argument that the file it leaves behind
        # is a function of `path` + `content`. True of the file it writes, false of the file
        # it finds: a replay writes over whatever is there now.
        "RF-19 a file write claims to be replay-safe",
        "backend/app/ai/tools/builtin/filesystem.py",
        "        mutates_state=True,\n"
        "        # **Not replay-safe, and it used to be marked as if it were.**",
        "        mutates_state=True,\n"
        "        idempotent=True,\n"
        "        # **Not replay-safe, and it used to be marked as if it were.**",
        "tests/unit/test_release_freeze_tool_replay.py"
        "::test_the_writes_that_cannot_be_repeated_are_not_marked[file_write]",
    ),
    (
        # The file on disk does not hold the intended bytes. That is consistent with "the
        # write never happened" **and** with "it happened and something overwrote it", and
        # the two have opposite safe responses.
        "RF-20 a changed file is reported as not applied",
        "backend/app/ai/tools/builtin/filesystem.py",
        "        return ReconcileOutcome(\n"
        "            verdict=ReconcileVerdict.UNDECIDABLE,\n"
        "            detail=(\n"
        "                f\"{target} 存在，但内容与本次调用要写入的不一致——可能是之后被改写，\"",
        "        return ReconcileOutcome(\n"
        "            verdict=ReconcileVerdict.NOT_APPLIED,\n"
        "            detail=(\n"
        "                f\"{target} 存在，但内容与本次调用要写入的不一致——可能是之后被改写，\"",
        "tests/integration/test_release_freeze_tool_reconcile.py"
        "::test_a_file_with_different_content_is_undecidable_not_not_applied",
    ),
    (
        # A tool with no check must not be assumed harmless. `file_append` is the case: it
        # has a real, non-repeatable side effect and no way to observe it from outside.
        "RF-21 a tool with no check claims to be applied",
        "backend/app/ai/agent/tools.py",
        "        return ReconcileOutcome(\n"
        "            verdict=ReconcileVerdict.UNDECIDABLE,\n"
        "            detail=f\"{self.spec.name} 没有对账方法，无法确认这次调用的效果是否已经发生\",",
        "        return ReconcileOutcome(\n"
        "            verdict=ReconcileVerdict.APPLIED,\n"
        "            detail=f\"{self.spec.name} 没有对账方法，无法确认这次调用的效果是否已经发生\",",
        "tests/integration/test_release_freeze_tool_reconcile.py"
        "::test_a_tool_with_no_check_is_undecidable",
    ),
    (
        # A check is a read. One that re-applied the operation would look identical in the
        # database and would have destroyed whatever was in the file.
        "RF-22 reconciliation writes to the target",
        "backend/app/ai/tools/builtin/filesystem.py",
        "        actual = _hash_file(target)\n"
        "        evidence[\"actual_sha256\"] = actual",
        "        target.write_bytes(content.encode(\"utf-8\"))\n"
        "        actual = _hash_file(target)\n"
        "        evidence[\"actual_sha256\"] = actual",
        "tests/integration/test_release_freeze_tool_reconcile.py"
        "::test_reconciliation_does_not_touch_the_file",
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
