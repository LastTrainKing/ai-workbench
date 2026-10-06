"""How many completion tokens to ask for, and why the number is not a knob to fiddle.

The default provider is a **reasoning** model: it spends tokens thinking before it
emits a single character, and `max_tokens` covers the thinking as well as the answer.
A budget too small for the thinking produces **no answer at all** — the provider raises
`ai_reasoning_budget_exhausted` rather than returning something truncated.

That asymmetry is what makes a small budget dangerous rather than merely tight. A
truncated answer is visibly incomplete; a `reasoning_budget_exhausted` exception looks
like a network problem, and every caller that catches broadly turns it into something
plausible:

* the agent's recovery call failed at 600 tokens, so "recover" silently became "give
  up" — the step stopped instead of retrying with a new strategy;
* the memory evaluator's judgement call failed at 500 tokens, so **every** delivery
  rejection was recorded as "not worth remembering". The learning loop was not
  degraded; it was off, and it looked like the filter working.

`max_tokens` is a **cap, not a target**. A model that thinks for 300 tokens costs the
same whether the cap is 1,000 or 8,000. Setting it low buys nothing and costs a wasted
round trip every time the thinking is long. So the number lives here, once, generous
enough that the failure mode does not occur, and it is shared rather than re-guessed in
each module.
"""

from __future__ import annotations

from app.core.config import settings

#: Completion cap for a reasoning-model call. Generous on purpose — see above.
REASONING_TOKENS = 6000


def reasoning_budget() -> int:
    """The cap to pass as `max_tokens`, respecting a deliberately lowered setting."""
    return min(settings.ai_max_tokens, REASONING_TOKENS)


__all__ = ["REASONING_TOKENS", "reasoning_budget"]
