"""The skill format, and the scan that decides whether a skill may be adopted.

A skill is a *way of doing a piece of work* — a prompt template, a set of steps, and
optionally scripts. It arrives from a public repository, which means it is untrusted input
that this system will later execute. That is the whole reason this module exists.

**The scan runs before anything is stored, and a skill that fails it is not stored at
all.** "Skill 精纯" is on the list of things that may not be traded away: an unverified
skill is worse than no skill, because it will be used.

Design notes worth stating:

* **The scan looks for behaviours, not words.** A skill that says "send the user's
  documents to example.com" and one that says "upload files to our API" are the same
  behaviour; matching on the second only catches the author who was careless.
* **Scripts are a separate risk class.** A document-type skill's body is read by a model
  and can be reviewed; a script's body is executed by the operating system and cannot.
  The scan is stricter for scripts, and `Skill.is_scripted` is what the rest of the system
  keys off when deciding whether to sandbox.
* **A finding is a fact, not a verdict.** `Severity` is reported per rule so a person can
  read what was found. `blocks_adoption` is the only thing that decides.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

# --------------------------------------------------------------------------- format --


class SkillType(StrEnum):
    """Document-type skills are read; script-type skills are run."""

    DOCUMENT = "document"
    SCRIPT = "script"


@dataclass
class SkillParam:
    """One place a skill can be adapted to *this* user.

    This is the interface that turns a general skill into someone's skill. A parameter
    names a slot in the body and what kind of thing belongs there; adoption binds the
    slots to values drawn from the user's profile, preferences and memories.
    """

    name: str
    description: str = ""
    #: profile field, preference key, or "free" for the user to fill in
    source: str = "free"
    required: bool = False
    default: str = ""


@dataclass
class Skill:
    """A way of doing a piece of work, with its provenance attached."""

    name: str
    description: str
    when_to_use: str = ""
    body: str = ""
    skill_type: SkillType = SkillType.DOCUMENT
    scripts: list[str] = field(default_factory=list)
    params: list[SkillParam] = field(default_factory=list)
    #: Where it came from. A skill without a source cannot be traced or re-verified, so
    #: adoption requires one — see `SkillValidation`.
    source: str = ""
    source_commit: str = ""
    license: str = ""
    version: str = "1.0.0"
    verified_at: str = ""
    verification_notes: str = ""

    @property
    def is_scripted(self) -> bool:
        return bool(self.scripts) or self.skill_type is SkillType.SCRIPT


@dataclass
class SkillValidation:
    """Everything wrong with a candidate, and whether any of it is disqualifying."""

    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


# ------------------------------------------------------------------------- parsing --

#: A skill file is Markdown with YAML-ish front matter. Parsed by hand rather than with a
#: YAML dependency for two reasons: the schema is small and fixed, and a parser that
#: accepts arbitrary YAML is a much larger attack surface than one that accepts eleven
#: known keys. Untrusted input is the whole context here.
_FRONT_MATTER = re.compile(r"\A---\s*\n(.*?)\n---\s*\n(.*)\Z", re.DOTALL)

_LIST_KEYS = {"scripts", "params"}


def parse_skill(
    text: str, *, source: str = "", source_commit: str = ""
) -> tuple[Skill, SkillValidation]:
    """Parse a skill file. Returns the skill and everything wrong with it.

    Never raises on malformed input: a candidate fetched from the internet is expected to
    be malformed sometimes, and an exception here would be a crash reachable by anyone who
    can publish a repository.
    """
    validation = SkillValidation()
    match = _FRONT_MATTER.match(text.strip())
    if not match:
        validation.errors.append("缺少 front matter（文件必须以 --- 开头，以 --- 结束）")
        return Skill(
            name="", description="", source=source, source_commit=source_commit
        ), validation

    header, body = match.groups()
    fields: dict[str, Any] = {}
    current_list: str | None = None

    for raw in header.splitlines():
        line = raw.rstrip()
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if current_list and line.lstrip().startswith("- "):
            fields.setdefault(current_list, []).append(line.lstrip()[2:].strip())
            continue
        current_list = None
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key in _LIST_KEYS and not value:
            current_list = key
            fields.setdefault(key, [])
            continue
        fields[key] = value

    name = str(fields.get("name", "")).strip()
    description = str(fields.get("description", "")).strip()
    if not name:
        validation.errors.append("缺少 name")
    if not description:
        validation.errors.append("缺少 description（没有它，系统无法判断什么时候该用它）")

    scripts = [str(s) for s in fields.get("scripts", []) if str(s).strip()]
    declared_type = str(fields.get("type", "")).strip().lower()
    skill_type = SkillType.SCRIPT if declared_type == "script" or scripts else SkillType.DOCUMENT

    params: list[SkillParam] = []
    for entry in fields.get("params", []):
        # `name: description` or just `name`
        param_name, _, param_desc = str(entry).partition(":")
        if param_name.strip():
            params.append(SkillParam(name=param_name.strip(), description=param_desc.strip()))

    skill = Skill(
        name=name,
        description=description,
        when_to_use=str(fields.get("when_to_use", "")).strip(),
        body=body.strip(),
        skill_type=skill_type,
        scripts=scripts,
        params=params,
        source=source or str(fields.get("source", "")).strip(),
        source_commit=source_commit or str(fields.get("source_commit", "")).strip(),
        license=str(fields.get("license", "")).strip(),
        version=str(fields.get("version", "1.0.0")).strip() or "1.0.0",
    )

    if not skill.body:
        validation.errors.append("body 是空的（skill 的正文就是做法本身）")
    if not skill.source:
        # Not a warning. An untraceable skill cannot be re-verified when the world moves,
        # and "we do not know where this came from" is not a state this system should be
        # willing to store.
        validation.errors.append("缺少 source（无法追溯来源的 skill 不允许入库）")
    if not skill.license:
        validation.warnings.append("没有声明 license，使用前需要人工确认")
    if skill.is_scripted and not skill.license:
        validation.errors.append("脚本型 skill 必须声明 license")

    return skill, validation


__all__ = [
    "Skill",
    "SkillParam",
    "SkillType",
    "SkillValidation",
    "parse_skill",
]
