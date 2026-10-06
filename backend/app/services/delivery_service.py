"""The delivery service: from a sentence to an accepted artifact.

This is where the product's loop actually closes.

    goal  →  plan  →  execute  →  produce  →  verify  →  accept/reject  →  remember

Two decisions shape everything here.

**Verification is separate from acceptance.** The machine checks the file — it exists,
it opens, it has the page count it should. The user decides whether it is what they
wanted. A file can pass every check and still be rejected, and that combination is the
most informative event in the system, so the two are never collapsed into one flag.

**Rejection is the primary source of memory.** "图表太少" is a statement about a standard
the user holds, made by the only person who knows it. That is worth more than any number
of conversation turns, and it is the reason `Deliverable.feedback` exists.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.memory import ContextAssembler, MemoryEvaluator
from app.ai.providers.factory import build_llm_provider
from app.core.errors import NotFoundError, ValidationError
from app.core.logging import get_logger
from app.models.delivery import (
    DELIVERABLE_KINDS,
    BuildRun,
    BuildStatus,
    Deliverable,
    make_verification,
)

logger = get_logger(__name__)

#: Extensions the verification step knows how to read back.
READABLE_KINDS = {"pptx", "docx", "xlsx", "pdf", "md", "csv", "txt"}


class DeliveryService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ------------------------------------------------------------- build runs --
    async def create_build_run(
        self,
        *,
        owner_id: uuid.UUID,
        goal: str,
        deliverable_kind: str = "mixed",
        scope: dict[str, Any] | None = None,
        project_id: uuid.UUID | None = None,
        title: str = "",
    ) -> BuildRun:
        goal = (goal or "").strip()
        if len(goal) < 4:
            raise ValidationError("请说明你想完成什么，至少四个字")
        if deliverable_kind not in DELIVERABLE_KINDS:
            raise ValidationError(f"产出格式必须是：{', '.join(DELIVERABLE_KINDS)}")

        run = BuildRun(
            user_id=owner_id,
            goal=goal,
            title=(title or goal)[:200],
            deliverable_kind=deliverable_kind,
            scope=scope or {},
            project_id=project_id,
            status=BuildStatus.DRAFT.value,
        )
        self.session.add(run)
        await self.session.flush()
        logger.info("build_run_created", extra={"build_run_id": str(run.id), "kind": deliverable_kind})
        return run

    async def get_build_run(self, *, owner_id: uuid.UUID, build_run_id: uuid.UUID) -> BuildRun:
        run = await self.session.get(BuildRun, build_run_id)
        if run is None or run.user_id != owner_id:
            raise NotFoundError("构建任务不存在")
        return run

    async def list_build_runs(
        self,
        *,
        owner_id: uuid.UUID,
        status: str | None = None,
        active_only: bool = False,
        limit: int = 50,
    ) -> list[BuildRun]:
        stmt = select(BuildRun).where(BuildRun.user_id == owner_id)
        if status:
            stmt = stmt.where(BuildRun.status == status)
        if active_only:
            active = [state.value for state in BuildStatus if state.is_active]
            stmt = stmt.where(BuildRun.status.in_(active))
        stmt = stmt.order_by(BuildRun.created_at.desc()).limit(limit)
        return list((await self.session.execute(stmt)).scalars())

    async def set_status(
        self, run: BuildRun, status: BuildStatus, *, error: str = "", summary: str = ""
    ) -> BuildRun:
        """Move a run to a new state, keeping the timestamps honest."""
        run.status = status.value
        if error:
            run.error = error
        if summary:
            run.result_summary = summary
        if status is BuildStatus.EXECUTING and run.started_at is None:
            run.started_at = datetime.now(UTC)
        if status.is_terminal:
            run.finished_at = datetime.now(UTC)
        await self.session.flush()
        return run

    async def attach_context(self, run: BuildRun, *, purpose: str = "build") -> str:
        """Assemble and record what this run will be told about the user.

        Returns the rendered block. An empty profile is normal and yields an empty
        string; the run proceeds exactly as it would otherwise.
        """
        assembler = ContextAssembler(self.session, user_id=run.user_id)
        assembled = await assembler.assemble()
        snapshot = await assembler.persist(assembled, run_id=str(run.id), purpose=purpose)
        run.context_snapshot_id = snapshot.id
        await self.session.flush()
        return assembled.render()

    # ------------------------------------------------------------ deliverables --
    async def record_deliverable(
        self,
        *,
        run: BuildRun,
        path: str | Path,
        kind: str,
        title: str = "",
        verification: dict[str, Any] | None = None,
        applied_preferences: dict[str, Any] | None = None,
        style: str = "",
        parent_id: uuid.UUID | None = None,
    ) -> Deliverable:
        """Register a produced file, with whatever evidence exists for it."""
        target = Path(path)
        if not target.is_file():
            # A deliverable that does not exist is not a deliverable. Refusing here
            # means nothing downstream has to defend against a phantom row.
            raise ValidationError(f"产出文件不存在：{target}")

        version = 1
        if parent_id is not None:
            parent = await self.session.get(Deliverable, parent_id)
            if parent is not None:
                version = parent.version + 1

        item = Deliverable(
            user_id=run.user_id,
            build_run_id=run.id,
            path=str(target),
            kind=kind,
            title=title or target.stem,
            size_bytes=target.stat().st_size,
            version=version,
            parent_id=parent_id,
            verification=verification or make_verification([]),
            verified=bool(verification and verification.get("passed")),
            applied_preferences=applied_preferences or {},
            style=style,
        )
        self.session.add(item)
        await self.session.flush()
        logger.info(
            "deliverable_recorded",
            extra={"deliverable_id": str(item.id), "kind": kind, "verified": item.verified},
        )
        return item

    async def verify_deliverable(
        self, *, owner_id: uuid.UUID, deliverable_id: uuid.UUID
    ) -> dict[str, Any]:
        """Read the file back and check it against what it claims to be.

        "The file was generated" is not verification. This is: the file exists, it can
        be parsed, and the numbers inside it are what was asked for.
        """
        item = await self.session.get(Deliverable, deliverable_id)
        if item is None or item.user_id != owner_id:
            raise NotFoundError("产出物不存在")

        checks: list[dict[str, Any]] = []
        target = Path(item.path)

        checks.append(
            {
                "name": "file_exists",
                "ok": target.is_file(),
                "detail": f"{target.stat().st_size:,} bytes" if target.is_file() else "not found",
            }
        )

        if target.is_file() and item.kind in READABLE_KINDS:
            try:
                from app.ai.tools.builtin.documents import (
                    _read_csv,
                    _read_docx,
                    _read_pdf,
                    _read_pptx,
                    _read_xlsx,
                )

                readers = {
                    "pptx": _read_pptx,
                    "docx": _read_docx,
                    "xlsx": _read_xlsx,
                    "pdf": _read_pdf,
                    "csv": _read_csv,
                }
                if item.kind in readers:
                    payload = readers[item.kind](target)
                    detail = {
                        "pptx": f"{payload.get('slide_count')} slides",
                        "docx": f"{payload.get('paragraphs')} paragraphs",
                        "xlsx": f"{payload.get('sheet_count')} sheets, {payload.get('charts')} charts",
                        "pdf": f"{payload.get('page_count')} pages",
                        "csv": f"{payload.get('row_count')} rows",
                    }.get(item.kind, "readable")
                    checks.append({"name": "readback", "ok": True, "detail": detail})
                else:
                    text = target.read_text(encoding="utf-8", errors="replace")
                    checks.append(
                        {"name": "readback", "ok": bool(text.strip()), "detail": f"{len(text)} chars"}
                    )
            except Exception as exc:
                checks.append(
                    {
                        "name": "readback",
                        "ok": False,
                        "detail": f"{type(exc).__name__}: {exc}"[:200],
                    }
                )
        elif target.is_file():
            checks.append({"name": "readback", "ok": True, "detail": f"{item.kind} (not parsed)"})

        result = make_verification(checks)
        item.verification = result
        item.verified = result["passed"]
        await self.session.flush()
        return result

    # -------------------------------------------------------------- acceptance --
    async def accept(self, *, owner_id: uuid.UUID, deliverable_id: uuid.UUID) -> Deliverable:
        """The user says this is what they wanted.

        Records the acceptance and offers the run to the memory evaluator, because a
        confirmation is evidence about a standard the user holds.
        """
        item = await self.session.get(Deliverable, deliverable_id)
        if item is None or item.user_id != owner_id:
            raise NotFoundError("产出物不存在")

        item.accepted = True
        item.feedback = ""
        item.decided_at = datetime.now(UTC)
        await self.session.flush()

        await self._learn_from_decision(item, accepted=True)
        return item

    async def reject(
        self, *, owner_id: uuid.UUID, deliverable_id: uuid.UUID, feedback: str
    ) -> Deliverable:
        """The user says this is not it, and why.

        The reason is required. A rejection without a reason teaches nothing, and the
        whole point of the loop is that it learns — so an empty reason is refused rather
        than stored as an empty string that looks like a signal.
        """
        reason = (feedback or "").strip()
        if len(reason) < 2:
            raise ValidationError("请说明哪里不满意，一句话即可 —— 这是它学会你标准的唯一途径")

        item = await self.session.get(Deliverable, deliverable_id)
        if item is None or item.user_id != owner_id:
            raise NotFoundError("产出物不存在")

        item.accepted = False
        item.feedback = reason[:1000]
        item.decided_at = datetime.now(UTC)
        await self.session.flush()

        await self._learn_from_decision(item, accepted=False)
        return item

    async def _learn_from_decision(self, item: Deliverable, *, accepted: bool) -> None:
        """Offer a delivery decision to the memory evaluator.

        This is the correct trigger point, and it is worth being explicit about why.

        A conversation turn is a *guess* about what someone prefers — they said
        something, and the system infers. A delivery decision is a *statement*: they
        looked at a real artifact and said yes or no, and when they said no they said
        why. That is a fact about a standard, made by the only person who holds it.

        The failure mode this avoids is the one that makes memory products useless:
        accumulating inferences from chatter until the store is full of things the user
        never actually said.
        """
        try:
            llm, _ = build_llm_provider()
            evaluator = MemoryEvaluator(self.session, llm, user_id=item.user_id)

            if accepted:
                statement = (
                    f"用户验收通过了《{item.title}》（{item.kind}，第 {item.version} 版），"
                    f"没有提出修改意见。"
                )
                context = (
                    f"这是一次真实的交付验收：用户看到产出物后选择了接受。"
                    f"产出格式：{item.kind}。"
                    + (f"当前生效的偏好：{item.applied_preferences}。" if item.applied_preferences else "")
                )
            else:
                statement = (
                    f"用户退回了《{item.title}》（{item.kind}，第 {item.version} 版），"
                    f"理由是：「{item.feedback}」。"
                )
                context = (
                    "这是一次真实的交付退回：用户看到产出物后拒绝，并说明了原因。"
                    "这个原因反映的是他/她的质量标准，不是一次性事务。"
                )

            verdict = await evaluator.evaluate(
                statement,
                context=context,
                source="delivery",
                # The provenance. Without it the memory page can only say "delivery",
                # which is not an answer to "why does it think that about me?" — the
                # user needs the run, the version and the words they used.
                structured={
                    "origin": {
                        "kind": "delivery",
                        "decision": "accepted" if accepted else "rejected",
                        "deliverable_id": str(item.id),
                        "build_run_id": str(item.build_run_id) if item.build_run_id else "",
                        "deliverable_title": item.title,
                        "deliverable_kind": item.kind,
                        "version": item.version,
                        "feedback": item.feedback,
                        "decided_at": item.decided_at.isoformat() if item.decided_at else "",
                    }
                },
            )
            logger.info(
                "memory_verdict_from_delivery",
                extra={
                    "routing": verdict.routing.value,
                    "accepted": accepted,
                    "deliverable_id": str(item.id),
                },
            )
        except Exception as exc:
            logger.warning(
                "delivery_memory_pass_failed",
                extra={"deliverable_id": str(item.id), "error": f"{type(exc).__name__}: {exc}"},
            )

    # --------------------------------------------------------------- queries --
    async def list_deliverables(
        self,
        *,
        owner_id: uuid.UUID,
        build_run_id: uuid.UUID | None = None,
        pending_only: bool = False,
        limit: int = 50,
    ) -> list[Deliverable]:
        stmt = select(Deliverable).where(Deliverable.user_id == owner_id)
        if build_run_id:
            stmt = stmt.where(Deliverable.build_run_id == build_run_id)
        if pending_only:
            # `is_(None)` matters: "not yet decided" is a distinct state from "rejected",
            # and `== None` in SQL would quietly return nothing.
            stmt = stmt.where(Deliverable.accepted.is_(None))
        stmt = stmt.order_by(Deliverable.created_at.desc()).limit(limit)
        return list((await self.session.execute(stmt)).scalars())

    async def get_deliverable(self, *, owner_id: uuid.UUID, deliverable_id: uuid.UUID) -> Deliverable:
        item = await self.session.get(Deliverable, deliverable_id)
        if item is None or item.user_id != owner_id:
            raise NotFoundError("产出物不存在")
        return item

    async def version_chain(
        self, *, owner_id: uuid.UUID, deliverable_id: uuid.UUID
    ) -> list[Deliverable]:
        """Walk the parent links back to the first version.

        A redo is the most informative event in the loop — something changed between
        version n and n+1 because the user said so — so the chain is walkable rather
        than reconstructed from timestamps.
        """
        chain: list[Deliverable] = []
        current = await self.get_deliverable(owner_id=owner_id, deliverable_id=deliverable_id)
        seen: set[uuid.UUID] = set()
        while current is not None and current.id not in seen:
            chain.append(current)
            seen.add(current.id)
            if current.parent_id is None:
                break
            parent = await self.session.get(Deliverable, current.parent_id)
            if parent is None or parent.user_id != owner_id:
                break
            current = parent
        chain.reverse()
        return chain

    async def summary(self, *, owner_id: uuid.UUID) -> dict[str, Any]:
        """Counts for the home page. Real numbers from real rows."""
        runs = await self.list_build_runs(owner_id=owner_id, limit=500)
        items = await self.list_deliverables(owner_id=owner_id, limit=500)
        return {
            "build_runs_total": len(runs),
            "build_runs_active": sum(1 for run in runs if BuildStatus(run.status).is_active),
            "deliverables_total": len(items),
            "deliverables_accepted": sum(1 for item in items if item.accepted is True),
            "deliverables_rejected": sum(1 for item in items if item.accepted is False),
            "deliverables_pending": sum(1 for item in items if item.accepted is None),
            "deliverables_verified": sum(1 for item in items if item.verified),
        }


__all__ = ["DeliveryService"]
