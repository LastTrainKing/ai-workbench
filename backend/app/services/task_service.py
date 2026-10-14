"""Task service: task tree, dependencies, knowledge links and status rules."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dates import parse_datetime
from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.core.logging import get_logger
from app.domain.enums import TaskPriority, TaskSource, TaskStatus
from app.models.task import Task
from app.repositories.ai_repo import ActivityRepository
from app.repositories.knowledge_repo import DocumentRepository
from app.repositories.work_repo import ProjectRepository, TaskRepository

logger = get_logger(__name__)

_VALID_TRANSITIONS: dict[str, set[str]] = {
    TaskStatus.TODO.value: {
        TaskStatus.IN_PROGRESS.value,
        TaskStatus.BLOCKED.value,
        TaskStatus.CANCELLED.value,
        TaskStatus.DONE.value,
    },
    TaskStatus.IN_PROGRESS.value: {
        TaskStatus.TODO.value,
        TaskStatus.BLOCKED.value,
        TaskStatus.DONE.value,
        TaskStatus.CANCELLED.value,
    },
    TaskStatus.BLOCKED.value: {
        TaskStatus.TODO.value,
        TaskStatus.IN_PROGRESS.value,
        TaskStatus.CANCELLED.value,
    },
    TaskStatus.DONE.value: {TaskStatus.IN_PROGRESS.value, TaskStatus.TODO.value},
    TaskStatus.CANCELLED.value: {TaskStatus.TODO.value},
}


class TaskService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.tasks = TaskRepository(session)
        self.projects = ProjectRepository(session)
        self.documents = DocumentRepository(session)
        self.activity = ActivityRepository(session)

    # ------------------------------------------------------------------ create --
    async def create_task(
        self,
        *,
        owner_id: uuid.UUID,
        title: str,
        description: str = "",
        project_id: uuid.UUID | None = None,
        parent_id: uuid.UUID | None = None,
        priority: str = TaskPriority.MEDIUM.value,
        status: str = TaskStatus.TODO.value,
        estimated_minutes: int = 0,
        actual_minutes: int = 0,
        due_date_raw: str | datetime | None = None,
        completion_criteria: str = "",
        ai_suggestion: str = "",
        source: TaskSource | str = TaskSource.MANUAL,
        position: int | None = None,
        document_ids: list[uuid.UUID] | None = None,
        depends_on_ids: list[uuid.UUID] | None = None,
    ) -> Task:
        clean_title = (title or "").strip()
        if not clean_title:
            raise ValidationError("Task title must not be empty", details={"field": "title"})
        if len(clean_title) > 300:
            raise ValidationError("Task title is too long (max 300 chars)")

        if project_id:
            project = await self.projects.get_owned(project_id, owner_id)
            if project is None:
                raise NotFoundError("Project not found", details={"project_id": str(project_id)})

        parent: Task | None = None
        depth = 0
        if parent_id:
            parent = await self.tasks.get_owned(parent_id, owner_id)
            if parent is None:
                raise NotFoundError("Parent task not found")
            depth = parent.depth + 1
            if depth > 4:
                raise ValidationError("Task nesting is limited to 4 levels")

        priority_value = str(priority).upper()
        if priority_value not in {p.value for p in TaskPriority}:
            raise ValidationError(f"Unknown priority '{priority}'")
        status_value = str(status).upper()
        if status_value not in {s.value for s in TaskStatus}:
            raise ValidationError(f"Unknown status '{status}'")

        task = Task(
            owner_id=owner_id,
            project_id=project_id or (parent.project_id if parent else None),
            parent_id=parent_id,
            title=clean_title,
            description=description or "",
            completion_criteria=completion_criteria or "",
            status=status_value,
            priority=priority_value,
            source=str(source),
            ai_suggestion=ai_suggestion or "",
            due_date=parse_datetime(due_date_raw),
            estimated_minutes=max(0, int(estimated_minutes or 0)),
            actual_minutes=max(0, int(actual_minutes or 0)),
            position=position
            if position is not None
            else await self.tasks.next_position(owner_id, parent_id),
            depth=depth,
        )
        self.session.add(task)
        await self.session.flush()

        for document_id in document_ids or []:
            await self.link_document(
                owner_id=owner_id, task_id=task.id, document_id=document_id
            )
        for dependency_id in depends_on_ids or []:
            await self.add_dependency(
                owner_id=owner_id, task_id=task.id, depends_on_id=dependency_id
            )

        await self.activity.record(
            owner_id=owner_id,
            event_type="task.created",
            title=f"新建任务：{task.title}",
            entity_type="task",
            entity_id=str(task.id),
            project_id=task.project_id,
            metadata={"source": str(source), "priority": task.priority},
        )
        logger.info("task_created", extra={"task_id": str(task.id), "source": str(source)})
        return task

    # ------------------------------------------------------------------ update --
    async def update_task(
        self, *, owner_id: uuid.UUID, task_id: uuid.UUID, **changes: Any
    ) -> Task:
        task = await self.tasks.get_owned(task_id, owner_id)
        if task is None:
            raise NotFoundError("Task not found")

        if changes.get("status"):
            new_status = str(changes["status"]).upper()
            if new_status not in {s.value for s in TaskStatus}:
                raise ValidationError(f"Unknown status '{changes['status']}'")
            if new_status != task.status:
                allowed = _VALID_TRANSITIONS.get(task.status, set())
                if new_status not in allowed:
                    raise ConflictError(
                        f"Cannot move a task from {task.status} to {new_status}",
                        details={"from": task.status, "to": new_status},
                    )
                if new_status == TaskStatus.DONE.value:
                    blocking = await self._unfinished_dependencies(task.id)
                    if blocking:
                        raise ConflictError(
                            "This task has unfinished dependencies",
                            details={"blocking": [t.title for t in blocking]},
                        )
                    task.completed_at = datetime.now(UTC)
                else:
                    task.completed_at = None
                task.status = new_status

        if changes.get("priority"):
            value = str(changes["priority"]).upper()
            if value not in {p.value for p in TaskPriority}:
                raise ValidationError(f"Unknown priority '{changes['priority']}'")
            task.priority = value

        if changes.get("title"):
            task.title = str(changes["title"]).strip()[:300]
        if "description" in changes and changes["description"] is not None:
            task.description = str(changes["description"])
        if "completion_criteria" in changes and changes["completion_criteria"] is not None:
            task.completion_criteria = str(changes["completion_criteria"])
        if "ai_suggestion" in changes and changes["ai_suggestion"] is not None:
            task.ai_suggestion = str(changes["ai_suggestion"])
        for field in ("estimated_minutes", "actual_minutes"):
            if field in changes and changes[field] is not None:
                setattr(task, field, max(0, int(changes[field])))
        if "due_date" in changes:
            task.due_date = parse_datetime(changes["due_date"])
        if "is_pinned" in changes and changes["is_pinned"] is not None:
            task.is_pinned = bool(changes["is_pinned"])
        if "position" in changes and changes["position"] is not None:
            task.position = int(changes["position"])
        if "project_id" in changes:
            new_project = changes["project_id"]
            if new_project and await self.projects.get_owned(new_project, owner_id) is None:
                raise NotFoundError("Target project not found")
            task.project_id = new_project

        await self.session.flush()
        if "status" in changes:
            await self.activity.record(
                owner_id=owner_id,
                event_type="task.status_changed",
                title=f"任务状态更新：{task.title} → {task.status}",
                entity_type="task",
                entity_id=str(task.id),
                project_id=task.project_id,
                metadata={"status": task.status},
            )
        return task

    async def complete_task(
        self, *, owner_id: uuid.UUID, task_id: uuid.UUID, actual_minutes: int | None = None
    ) -> Task:
        task = await self.tasks.get_owned(task_id, owner_id)
        if task is None:
            raise NotFoundError("Task not found")
        if task.status == TaskStatus.DONE.value:
            return task
        blocking = await self._unfinished_dependencies(task.id)
        if blocking:
            raise ConflictError(
                "This task has unfinished dependencies",
                details={"blocking": [t.title for t in blocking]},
            )
        task.status = TaskStatus.DONE.value
        task.completed_at = datetime.now(UTC)
        if actual_minutes is not None:
            task.actual_minutes = max(0, int(actual_minutes))
        await self.session.flush()

        # Auto-unblock children whose dependencies are all satisfied.
        for child in await self.tasks.children(task.id):
            if child.status == TaskStatus.BLOCKED.value and not await self._unfinished_dependencies(
                child.id
            ):
                child.status = TaskStatus.TODO.value
        await self.session.flush()

        await self.activity.record(
            owner_id=owner_id,
            event_type="task.completed",
            title=f"完成任务：{task.title}",
            entity_type="task",
            entity_id=str(task.id),
            project_id=task.project_id,
        )
        return task

    async def delete_task(self, *, owner_id: uuid.UUID, task_id: uuid.UUID) -> None:
        task = await self.tasks.get_owned(task_id, owner_id)
        if task is None:
            raise NotFoundError("Task not found")
        title = task.title
        await self.session.delete(task)  # children/dependencies cascade
        await self.session.flush()
        await self.activity.record(
            owner_id=owner_id,
            event_type="task.deleted",
            title=f"删除任务：{title}",
            entity_type="task",
            entity_id=str(task_id),
            project_id=task.project_id,
        )

    # ------------------------------------------------------------- relations ---
    async def add_dependency(
        self, *, owner_id: uuid.UUID, task_id: uuid.UUID, depends_on_id: uuid.UUID
    ) -> None:
        task = await self.tasks.get_owned(task_id, owner_id)
        dependency = await self.tasks.get_owned(depends_on_id, owner_id)
        if task is None or dependency is None:
            raise NotFoundError("Task not found")
        if task.id == dependency.id:
            raise ValidationError("A task cannot depend on itself")
        if await self._creates_cycle(task.id, dependency.id):
            raise ConflictError(
                "This dependency would create a cycle",
                details={"task": task.title, "depends_on": dependency.title},
            )
        await self.tasks.add_dependency(task.id, dependency.id)
        await self.session.flush()

    async def remove_dependency(
        self, *, owner_id: uuid.UUID, task_id: uuid.UUID, dependency_id: uuid.UUID
    ) -> int:
        task = await self.tasks.get_owned(task_id, owner_id)
        if task is None:
            raise NotFoundError("Task not found")
        removed = await self.tasks.remove_dependency(task.id, dependency_id)
        await self.session.flush()
        return removed

    async def _creates_cycle(self, task_id: uuid.UUID, candidate_dependency: uuid.UUID) -> bool:
        """Depth-first walk: is ``task_id`` reachable from ``candidate_dependency``?"""
        stack = [candidate_dependency]
        visited: set[uuid.UUID] = set()
        while stack:
            current = stack.pop()
            if current == task_id:
                return True
            if current in visited:
                continue
            visited.add(current)
            for dep in await self.tasks.dependencies_of(current):
                stack.append(dep.id)
        return False

    async def _unfinished_dependencies(self, task_id: uuid.UUID) -> list[Task]:
        deps = await self.tasks.dependencies_of(task_id)
        return [d for d in deps if d.status != TaskStatus.DONE.value]

    async def link_document(
        self,
        *,
        owner_id: uuid.UUID,
        task_id: uuid.UUID,
        document_id: uuid.UUID,
        relevance: float = 1.0,
    ) -> None:
        task = await self.tasks.get_owned(task_id, owner_id)
        if task is None:
            raise NotFoundError("Task not found")
        document = await self.documents.get_owned(document_id, owner_id)
        if document is None:
            raise NotFoundError("Document not found")
        await self.tasks.link_document(task.id, document.id, relevance)
        await self.session.flush()
        await self.activity.record(
            owner_id=owner_id,
            event_type="task.knowledge_linked",
            title=f"任务《{task.title}》关联资料《{document.title}》",
            entity_type="task",
            entity_id=str(task.id),
            project_id=task.project_id,
            metadata={"document_id": str(document.id)},
        )

    async def unlink_document(
        self, *, owner_id: uuid.UUID, task_id: uuid.UUID, document_id: uuid.UUID
    ) -> int:
        from app.models.task import TaskDocument

        task = await self.tasks.get_owned(task_id, owner_id)
        if task is None:
            raise NotFoundError("Task not found")
        link = await self.session.get(
            TaskDocument, {"task_id": task.id, "document_id": document_id}
        )
        if link is None:
            return 0
        await self.session.delete(link)
        await self.session.flush()
        return 1

    # ------------------------------------------------------------------- tree --
    async def tree(
        self, *, owner_id: uuid.UUID, project_id: uuid.UUID | None = None
    ) -> list[dict[str, Any]]:
        rows = await self.tasks.all_for_project(owner_id, project_id)
        blocked = await self.tasks.blocked_ids(owner_id)
        nodes: dict[uuid.UUID, dict[str, Any]] = {}
        for task in rows:
            nodes[task.id] = {
                "id": str(task.id),
                "title": task.title,
                "description": task.description,
                "status": task.status,
                "priority": task.priority,
                "source": task.source,
                "estimated_minutes": task.estimated_minutes,
                "actual_minutes": task.actual_minutes,
                "due_date": task.due_date.isoformat() if task.due_date else None,
                "completion_criteria": task.completion_criteria,
                "is_blocked": task.id in blocked
                and task.status not in (TaskStatus.DONE.value, TaskStatus.CANCELLED.value),
                "parent_id": str(task.parent_id) if task.parent_id else None,
                "project_id": str(task.project_id) if task.project_id else None,
                "children": [],
            }
        roots: list[dict[str, Any]] = []
        for task in rows:
            node = nodes[task.id]
            if task.parent_id and task.parent_id in nodes:
                nodes[task.parent_id]["children"].append(node)
            else:
                roots.append(node)
        return roots

    async def detail(self, *, owner_id: uuid.UUID, task_id: uuid.UUID) -> dict[str, Any]:
        task = await self.tasks.get_owned(task_id, owner_id)
        if task is None:
            raise NotFoundError("Task not found")
        dependencies = await self.tasks.dependencies_of(task.id)
        dependents = await self.tasks.dependents_of(task.id)
        documents = await self.tasks.documents_of(task.id)
        children = await self.tasks.children(task.id)
        return {
            "task": task,
            "children": children,
            "dependencies": dependencies,
            "dependents": dependents,
            "documents": documents,
            "is_blocked": bool(await self._unfinished_dependencies(task.id)),
        }
