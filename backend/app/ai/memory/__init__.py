"""Personal context: profile, memory and preferences.

Four things live here, and they are deliberately not one thing:

* ``evaluator`` decides what is worth remembering, and refuses to guess;
* ``relevance`` decides whether a stored memory bears on the request at hand — the
  question both the retrieval ladder and the assembler have to answer, answered once;
* ``assembler`` builds the context block a model call actually receives, under a
  hard token budget;
* the models themselves are in ``app.models.context``, because the rest of the
  system reads them too.

Nothing in this package talks to the operating system. It reads and writes the
database through the session it is given, like every other service.
"""

from __future__ import annotations

from app.ai.memory.assembler import AssembledContext, ContextAssembler, ContextBlock
from app.ai.memory.evaluator import (
    ACTIVE_THRESHOLD,
    CANDIDATE_FLOOR,
    MEMORY_KINDS,
    MemoryEvaluator,
    Routing,
    Verdict,
    is_noise,
    is_sensitive,
    record_preference,
)
from app.ai.memory.relevance import RARITY_FLOOR, rank

__all__ = [
    "ACTIVE_THRESHOLD",
    "CANDIDATE_FLOOR",
    "MEMORY_KINDS",
    "RARITY_FLOOR",
    "AssembledContext",
    "ContextAssembler",
    "ContextBlock",
    "MemoryEvaluator",
    "Routing",
    "Verdict",
    "is_noise",
    "is_sensitive",
    "rank",
    "record_preference",
]
