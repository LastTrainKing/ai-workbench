"""The skill layer's entry point.

Step 4 builds this. Until it does, the module exists so that **tier 2 of the retrieval
ladder can report a truthful miss** rather than being absent from the list.

The distinction matters and is the reason this file is not simply omitted: a tier that is
missing and a tier that was consulted and found nothing look identical in a report, and
only one of them is true. `SKILL_LAYER_BUILT` is what lets the tier say which.
"""

from __future__ import annotations

from dataclasses import dataclass

#: False until step 4 lands. Read by the retrieval ladder so that "no skills yet" is not
#: reported as "no matching skills" — those are different facts, and the second one would
#: be a lie told by an empty list.
SKILL_LAYER_BUILT = False


@dataclass
class SkillMatch:
    """One skill that bears on a question. Filled in by step 4."""

    id: str
    name: str
    score: float = 0.0


async def find_matching_skills(question: str) -> list[SkillMatch]:
    """Skills that fit this question, best first.

    Returns nothing today. The caller checks `SKILL_LAYER_BUILT` before reading meaning
    into the empty list.
    """
    return []


__all__ = ["SKILL_LAYER_BUILT", "SkillMatch", "find_matching_skills"]
