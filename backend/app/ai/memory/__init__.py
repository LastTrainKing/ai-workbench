"""Personal context: profile, memory and preferences.

Three things live here, and they are deliberately not one thing:

* ``evaluator`` decides what is worth remembering, and refuses to guess;
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

__all__ = [
    "ACTIVE_THRESHOLD",
    "CANDIDATE_FLOOR",
    "MEMORY_KINDS",
    "AssembledContext",
    "ContextAssembler",
    "ContextBlock",
    "MemoryEvaluator",
    "Routing",
    "Verdict",
    "is_noise",
    "is_sensitive",
    "record_preference",
]
