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
from app.services.artifact_checks import checks_for

logger = get_logger(__name__)

#: Extensions the verification step knows how to read back. Kept as the public name
#: other modules import; the per-kind rules themselves live in `artifact_checks`.
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
        assembled = await assembler.assemble(goal=run.goal)
        snapshot = await assembler.persist(assembled, run_id=str(run.id), purpose=purpose)
        run.context_snapshot_id = snapshot.id
        await self.session.flush()
        return assembled.render()

    async def start_build_run(self, run: BuildRun) -> dict[str, Any]:
        """Create is not start. This is start, and it is the only implementation of it.

        Two callers need it: the ``/execute`` endpoint, and the conversation when the
        understanding layer decides the request is clear enough to work on. Having the
        second one assemble context and hand off on its own would be a second place where
        "started" means something — and this project has already paid for that mistake
        once: create and start were separate endpoints, the frontend called one of them,
        and the run sat in DRAFT while the screen looked like work in progress.
        """
        from app.services.build_executor import execute_build_run

        if run.status not in (BuildStatus.DRAFT.value, BuildStatus.READY.value):
            raise ValidationError(f"当前状态（{run.status}）下不能开始执行")

        # Assemble and record what this run will be told about the user, then hand the
        # same block to the agent — so "what did it know" and "what was it given" are
        # the same string rather than two things that ought to match.
        context = await self.attach_context(run)
        return await execute_build_run(self.session, build_run_id=run.id, context=context)

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

        "The file was generated" is not verification. This is: the file exists, it
        opens with the library that writes that format, it contains what it should,
        and the checks are the ones that format actually has — see
        `artifact_checks.checks_for`. A missing reader yields `unverified` rather
        than a pass or a failure, because neither is true.
        """
        item = await self.session.get(Deliverable, deliverable_id)
        if item is None or item.user_id != owner_id:
            raise NotFoundError("产出物不存在")

        checks = checks_for(item.kind, Path(item.path))

        result = make_verification(checks, run_state=await self._run_state_note(item))
        item.verification = result
        item.verified = result["passed"]
        await self.session.flush()
        return result

    async def _run_state_note(self, item: Deliverable) -> dict[str, Any] | None:
        """The producing run's state, for the column beside the file's own verdict.

        Recomputed here rather than carried over from whatever was stored, so a
        re-verify cannot leave a stale run state behind — and so there is exactly
        one place that decides what a run state means (`describe_run_state`).

        `None` when the file has no run (a deliverable registered by hand). That is
        not the same as "the run failed": it means the question does not apply, and
        the UI is told so by the field's absence rather than by a false value.
        """
        from app.models.delivery import BuildRun, describe_run_state

        if item.build_run_id is None:
            return None
        run = await self.session.get(BuildRun, item.build_run_id)
        if run is None:
            return None
        return describe_run_state(run.status)

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

    async def outcome_for_run(
        self, *, owner_id: uuid.UUID, build_run_id: uuid.UUID
    ) -> dict[str, Any]:
        """The unified result contract for one run — see `run_outcome.build_run_outcome`.

        The deliverables are loaded here, scoped to the owner, rather than through the
        relationship: a projection that could show another account's file would be a
        worse bug than the one it fixes.
        """
        from app.services.run_outcome import build_run_outcome

        run = await self.get_build_run(owner_id=owner_id, build_run_id=build_run_id)
        items = await self.list_deliverables(
            owner_id=owner_id, build_run_id=build_run_id, limit=200
        )
        return build_run_outcome(run, items)

    async def read_text(
        self, *, owner_id: uuid.UUID, deliverable_id: uuid.UUID, max_chars: int = 60_000
    ) -> dict[str, Any]:
        """A bounded, honest preview of a text-like deliverable.

        Three refusals matter as much as the success path, and each says which one it
        is rather than returning an empty string that reads like an empty file:

        * the file is gone → `reason` says so, and the caller stops claiming 「已交付」
        * the kind is not text → `reason` names the format instead of showing mojibake
        * the file is mis-encoded → the strict decoder fails and the reason is the
          encoding, not "empty"

        Truncation is reported with `truncated`, because a preview that silently stops
        at 60,000 characters is indistinguishable from a file that ends there.
        """
        item = await self.get_deliverable(owner_id=owner_id, deliverable_id=deliverable_id)
        target = Path(item.path)
        base: dict[str, Any] = {
            "deliverable_id": str(item.id),
            "title": item.title or target.name,
            "kind": item.kind,
            "path": item.path,
            "text": "",
            "truncated": False,
            "size_bytes": 0,
            "reason": "",
        }

        if not target.is_file():
            base["reason"] = "文件不在了，无法预览。它可能被移动或删除。"
            return base

        base["size_bytes"] = target.stat().st_size
        try:
            text = target.read_text(encoding="utf-8-sig")
        except UnicodeDecodeError:
            base["reason"] = "这个文件不是 UTF-8 文本，应用内不预览它，请用系统程序打开。"
            return base
        except OSError as exc:
            base["reason"] = f"读不了这个文件：{type(exc).__name__}"
            return base

        if len(text) > max_chars:
            base["text"] = text[:max_chars]
            base["truncated"] = True
        else:
            base["text"] = text
        return base

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
