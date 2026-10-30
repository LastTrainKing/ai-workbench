"""One shape for "what came out of that run", for every surface that shows one.

The run page, the delivery page and the home screen all answer the same question —
*what did I get?* — and before this module each of them assembled its own answer
from whichever fields it happened to know about. That is how a page ends up showing
a file with no verdict, or a verdict with no file.

**This is a read-side projection, not a second source of truth.** Every field is
derived on demand from `BuildRun`, `Deliverable` and the filesystem; nothing here is
persisted, and nothing here is authoritative. The contract exists so the *shape* is
defined once — field by field, with what is null and when — while the facts keep
living in the tables that already own them.

Two rules the shape encodes:

* **Actions are computed, not assumed.** A button that opens a file is offered only
  when the file is on disk *now*. A deliverable whose file was deleted shows the
  reason instead of a button that would fail silently.
* **Verification is evidence, not a boolean.** `verification[]` carries the checks;
  `artifacts[].verified` is the one-line summary. Collapsing them is what the
  `verification` JSONB column was added to prevent.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.models.delivery import TEXT_PREVIEWABLE_KINDS, BuildRun, Deliverable

#: Alias kept for callers that import it from here. The set itself lives on the model,
#: because `Deliverable.as_dict()` also answers this question and one list is enough.
TEXT_PREVIEWABLE = TEXT_PREVIEWABLE_KINDS


def _file_state(path: str) -> dict[str, Any]:
    """What is actually on disk right now.

    Re-stat'd on every call. A cached "it existed when we registered it" is how a
    deleted file keeps a working 「打开」 button.
    """
    if not path:
        return {"exists": False, "size_bytes": 0, "reason": "这条记录没有文件路径"}
    target = Path(path)
    try:
        stat = target.stat()
    except OSError as exc:
        return {
            "exists": False,
            "size_bytes": 0,
            "reason": f"文件不在了：{type(exc).__name__}",
        }
    if not target.is_file():
        return {"exists": False, "size_bytes": 0, "reason": "路径指向的不是一个文件"}
    return {"exists": True, "size_bytes": stat.st_size, "reason": ""}


def _actions_for(item: Deliverable, state: dict[str, Any], *, goal: str) -> list[dict[str, Any]]:
    """The operations this artifact actually supports, right now.

    §8.2 asks for buttons that are not decoration. So each one carries `enabled` and,
    when it is not, the sentence explaining why — the same reason the click would have
    produced, said before the click instead of after it.

    「导出」 is deliberately absent. This build has no document converter, and a button
    that cannot convert is a button that lies.
    """
    exists = bool(state.get("exists"))
    previewable = item.kind in TEXT_PREVIEWABLE
    gone = state.get("reason") or "文件不存在"

    return [
        {
            "id": "preview",
            "label": "预览",
            "enabled": exists and previewable,
            "reason": gone
            if not exists
            else ("" if previewable else "该格式不在应用内预览，请用系统程序打开"),
        },
        {
            "id": "open",
            "label": "打开",
            "enabled": exists,
            "reason": gone if not exists else "",
        },
        {
            "id": "reveal",
            "label": "在文件夹中显示",
            "enabled": exists,
            "reason": gone if not exists else "",
        },
        {
            "id": "continue",
            "label": "继续修改",
            "enabled": True,
            "reason": "",
        },
        {
            "id": "regenerate",
            "label": "重新生成",
            "enabled": bool(goal),
            "reason": "" if goal else "找不到当初那次请求，无法原样重跑",
        },
    ]


def _verification_entry(item: Deliverable) -> dict[str, Any]:
    """One artifact's evidence, flattened for a list view."""
    raw = item.verification if isinstance(item.verification, dict) else {}
    checks = raw.get("checks") or []
    return {
        "artifact_id": str(item.id),
        "artifact_title": item.title or item.path,
        "passed": bool(raw.get("passed")),
        "check_count": int(raw.get("check_count") or len(checks)),
        "failed_count": int(raw.get("failed_count") or 0),
        #: Present since §10 introduced the third outcome. Absent on rows written
        #: before it, which is why it is read with a default rather than assumed.
        "unverified_count": int(raw.get("unverified_count") or 0),
        "checks": checks,
        "run_state": raw.get("run_state"),
    }


def build_run_outcome(run: BuildRun, deliverables: list[Deliverable]) -> dict[str, Any]:
    """The whole answer for one run: status, summary, artifacts, evidence, next steps.

    `next_actions` is run-level; per-artifact operations live on each artifact, because
    "open *this* file" is not a property of the run.
    """
    goal = run.goal or ""

    artifacts: list[dict[str, Any]] = []
    verification: list[dict[str, Any]] = []
    for item in deliverables:
        state = _file_state(item.path)
        artifacts.append(
            {
                "id": str(item.id),
                "title": item.title or item.path,
                "kind": item.kind,
                "path": item.path,
                "size_bytes": item.size_bytes,
                "version": item.version,
                "verified": item.verified,
                "accepted": item.accepted,
                "feedback": item.feedback,
                "file_state": state,
                "actions": _actions_for(item, state, goal=goal),
            }
        )
        verification.append(_verification_entry(item))

    missing = [a["title"] for a in artifacts if not a["file_state"]["exists"]]
    if not artifacts:
        summary = run.result_summary or "这次运行没有产出文件。"
    elif missing:
        summary = f"产出 {len(artifacts)} 个文件，其中 {len(missing)} 个已经不在了：{'、'.join(missing)}。"
    else:
        summary = run.result_summary or f"产出 {len(artifacts)} 个文件，都已落盘。"

    return {
        "build_run_id": str(run.id),
        "status": run.status,
        "goal": goal,
        "summary": summary,
        "artifacts": artifacts,
        "verification": verification,
        #: Run-level only. Anything that means "act on *this* file" lives on the
        #: artifact, so the two lists never offer the same button twice.
        "next_actions": [
            {
                "id": "verify",
                "label": "重新验证全部产出",
                "enabled": bool(artifacts),
                "reason": "" if artifacts else "这次运行没有产出文件，没有可验证的东西",
            },
            {
                "id": "open_deliverables",
                "label": "查看全部交付物",
                "enabled": True,
                "reason": "",
            },
        ],
        #: Where to look when the summary is not enough. A pointer, not the log —
        #: §8.1 is explicit that the user's result must not be buried under internals.
        "diagnostic_ref": {
            "build_run_id": str(run.id),
            "ai_run_id": str(run.ai_run_id) if run.ai_run_id else None,
        },
    }


__all__ = ["TEXT_PREVIEWABLE", "build_run_outcome"]
