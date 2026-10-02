"""Versioned prompt registry.

Business code never contains prompt text.  It asks for a prompt **by name** and
receives the currently active version together with its version number, which is
then persisted on the ``ai_runs`` row.  That is what makes an AI answer
reproducible: given a run you can see exactly which prompt produced it.

Cache strategy: ``prompt:<name>:<namespace_version>`` with a 10-minute TTL, and
an explicit namespace bump on every write so edits are visible immediately.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.prompts.builtin import (
    PROMPT_INVENTORY,
    TEMPLATES_BY_NAME,
    PromptTemplate,
    detect_variables,
)
from app.core.cache import bump_namespace, cache_get, cache_set, make_key, namespace_version
from app.core.config import settings
from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.core.logging import get_logger
from app.models.ai import Prompt, PromptVersion

logger = get_logger(__name__)

NS = "prompt"


@dataclass(slots=True)
class ResolvedPrompt:
    name: str
    version: int
    content: str
    variables: list[str]
    task: str
    json_output: bool
    category: str
    source: str  # "database" | "builtin"

    def render(self, **values: object) -> str:
        from app.ai.prompts.builtin import render_template

        return render_template(self.content, values)


def _to_resolved(prompt: Prompt, version: PromptVersion) -> ResolvedPrompt:
    builtin = TEMPLATES_BY_NAME.get(prompt.name)
    return ResolvedPrompt(
        name=prompt.name,
        version=version.version,
        content=version.content,
        variables=list(version.variables or []),
        task=builtin.task if builtin else "",
        json_output=builtin.json_output if builtin else False,
        category=prompt.category,
        source="database",
    )


def _builtin_resolved(name: str) -> ResolvedPrompt:
    template = TEMPLATES_BY_NAME.get(name)
    if template is None:
        raise NotFoundError(f"Prompt '{name}' is not registered")
    return ResolvedPrompt(
        name=name,
        version=0,
        content=template.content,
        variables=template.variables or detect_variables(template.content),
        task=template.task,
        json_output=template.json_output,
        category=template.category,
        source="builtin",
    )


async def ensure_seeded(session: AsyncSession) -> int:
    """Insert any built-in template that has no database row yet."""
    existing = set(
        (await session.execute(select(Prompt.name))).scalars().all()
    )
    created = 0
    for template in PROMPT_INVENTORY:
        if template.name in existing:
            continue
        prompt = Prompt(
            name=template.name,
            description=template.description,
            category=template.category,
            is_active=True,
        )
        session.add(prompt)
        await session.flush()
        session.add(
            PromptVersion(
                prompt_id=prompt.id,
                version=1,
                content=template.content,
                variables=template.variables or detect_variables(template.content),
                change_note="initial seed from built-in template",
                is_current=True,
                created_by="system",
            )
        )
        created += 1
    if created:
        await session.flush()
        logger.info("prompts_seeded", extra={"count": created})
    return created


async def get_prompt(session: AsyncSession, name: str) -> ResolvedPrompt:
    version_stamp = await namespace_version(NS)
    key = make_key(NS, name, version_stamp)
    cached = await cache_get(NS, key)
    if cached:
        return ResolvedPrompt(**cached)

    row = (
        await session.execute(
            select(Prompt, PromptVersion)
            .join(PromptVersion, PromptVersion.prompt_id == Prompt.id)
            .where(Prompt.name == name, PromptVersion.is_current.is_(True))
        )
    ).first()

    resolved = _to_resolved(*row) if row else _builtin_resolved(name)
    await cache_set(
        NS,
        key,
        {
            "name": resolved.name,
            "version": resolved.version,
            "content": resolved.content,
            "variables": resolved.variables,
            "task": resolved.task,
            "json_output": resolved.json_output,
            "category": resolved.category,
            "source": resolved.source,
        },
        settings.cache_ttl_prompt_seconds,
    )
    return resolved


async def list_prompts(session: AsyncSession) -> list[dict[str, object]]:
    rows = (await session.execute(select(Prompt).order_by(Prompt.name))).scalars().all()
    versions = (
        await session.execute(
            select(PromptVersion).where(PromptVersion.is_current.is_(True))
        )
    ).scalars().all()
    current_by_prompt = {v.prompt_id: v for v in versions}
    counts: dict[object, int] = {}
    all_versions = (await session.execute(select(PromptVersion))).scalars().all()
    for v in all_versions:
        counts[v.prompt_id] = counts.get(v.prompt_id, 0) + 1

    out: list[dict[str, object]] = []
    for prompt in rows:
        version = current_by_prompt.get(prompt.id)
        builtin = TEMPLATES_BY_NAME.get(prompt.name)
        out.append(
            {
                "id": str(prompt.id),
                "name": prompt.name,
                "description": prompt.description,
                "category": prompt.category,
                "is_active": prompt.is_active,
                "current_version": version.version if version else 0,
                "version_count": counts.get(prompt.id, 0),
                "variables": list(version.variables or []) if version else (
                    builtin.variables if builtin else []
                ),
                "updated_at": prompt.updated_at,
                "task": builtin.task if builtin else "",
            }
        )
    # Include built-ins that were never seeded so the UI is never empty.
    known = {p.name for p in rows}
    for template in PROMPT_INVENTORY:
        if template.name in known:
            continue
        out.append(
            {
                "id": f"builtin:{template.name}",
                "name": template.name,
                "description": template.description,
                "category": template.category,
                "is_active": True,
                "current_version": 0,
                "version_count": 0,
                "variables": template.variables,
                "updated_at": None,
                "task": template.task,
            }
        )
    return sorted(out, key=lambda item: str(item["name"]))


async def get_prompt_detail(session: AsyncSession, name: str) -> dict[str, object]:
    prompt = (
        await session.execute(select(Prompt).where(Prompt.name == name))
    ).scalar_one_or_none()
    builtin = TEMPLATES_BY_NAME.get(name)
    if prompt is None and builtin is None:
        raise NotFoundError(f"Prompt '{name}' does not exist")

    versions = (
        (
            await session.execute(
                select(PromptVersion)
                .where(PromptVersion.prompt_id == prompt.id)
                .order_by(PromptVersion.version.desc())
            )
        ).scalars().all()
        if prompt
        else []
    )
    return {
        "name": name,
        "description": prompt.description if prompt else builtin.description,
        "category": prompt.category if prompt else builtin.category,
        "task": builtin.task if builtin else "",
        "json_output": builtin.json_output if builtin else False,
        "versions": [
            {
                "version": v.version,
                "content": v.content,
                "variables": list(v.variables or []),
                "change_note": v.change_note,
                "is_current": v.is_current,
                "created_by": v.created_by,
                "created_at": v.created_at,
            }
            for v in versions
        ],
        "builtin_content": builtin.content if builtin else None,
    }


async def create_version(
    session: AsyncSession,
    name: str,
    *,
    content: str,
    change_note: str = "",
    created_by: str = "user",
    activate: bool = True,
) -> dict[str, object]:
    if not content.strip():
        raise ValidationError("Prompt content must not be empty")
    if len(content) > 40_000:
        raise ValidationError("Prompt content is too long (max 40000 chars)")

    prompt = (
        await session.execute(select(Prompt).where(Prompt.name == name))
    ).scalar_one_or_none()
    if prompt is None:
        builtin = TEMPLATES_BY_NAME.get(name)
        prompt = Prompt(
            name=name,
            description=builtin.description if builtin else "custom prompt",
            category=builtin.category if builtin else "general",
        )
        session.add(prompt)
        await session.flush()

    latest = (
        await session.execute(
            select(PromptVersion)
            .where(PromptVersion.prompt_id == prompt.id)
            .order_by(PromptVersion.version.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    next_version = (latest.version + 1) if latest else 1

    if latest and latest.content.strip() == content.strip():
        raise ConflictError("This content is identical to the current version")

    if activate:
        await session.execute(
            update(PromptVersion)
            .where(PromptVersion.prompt_id == prompt.id)
            .values(is_current=False)
        )

    version = PromptVersion(
        prompt_id=prompt.id,
        version=next_version,
        content=content,
        variables=detect_variables(content),
        change_note=change_note or f"version {next_version}",
        is_current=activate,
        created_by=created_by,
    )
    session.add(version)
    await session.flush()
    await bump_namespace(NS)
    logger.info(
        "prompt_version_created",
        extra={"prompt": name, "version": next_version, "activated": activate},
    )
    return {"name": name, "version": next_version, "is_current": activate}


async def activate_version(session: AsyncSession, name: str, version: int) -> dict[str, object]:
    prompt = (
        await session.execute(select(Prompt).where(Prompt.name == name))
    ).scalar_one_or_none()
    if prompt is None:
        raise NotFoundError(f"Prompt '{name}' does not exist")
    target = (
        await session.execute(
            select(PromptVersion).where(
                PromptVersion.prompt_id == prompt.id, PromptVersion.version == version
            )
        )
    ).scalar_one_or_none()
    if target is None:
        raise NotFoundError(f"Prompt '{name}' has no version {version}")

    await session.execute(
        update(PromptVersion)
        .where(PromptVersion.prompt_id == prompt.id)
        .values(is_current=False)
    )
    target.is_current = True
    await session.flush()
    await bump_namespace(NS)
    return {"name": name, "version": version, "is_current": True}


async def reset_to_builtin(session: AsyncSession, name: str) -> dict[str, object]:
    template: PromptTemplate | None = TEMPLATES_BY_NAME.get(name)
    if template is None:
        raise NotFoundError(f"Prompt '{name}' has no built-in definition")
    return await create_version(
        session,
        name,
        content=template.content,
        change_note="reset to built-in template",
        created_by="system",
    )
