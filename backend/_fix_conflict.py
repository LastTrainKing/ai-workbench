"""Let the model decide contradictions, instead of a lexical heuristic.

The heuristic compared character overlap and looked for negation or switch words.
That works when the new statement is a near-copy of the old one, and fails when
the two are semantically opposite but lexically different:

    stored : 用户希望所有项目默认使用 React + TypeScript，无需再次确认技术选型
    new    : 我以后所有项目都改用 Vue 了，不要 React

Those say the opposite thing and share little vocabulary. A character-overlap
threshold cannot see that, and a missed conflict is the expensive kind: the user
ends up with two contradictory memories and no idea which one the system believes.

Semantic contradiction is exactly the kind of judgement the model is for, and it
is already being called — so it costs nothing extra to ask.
"""

from __future__ import annotations

from pathlib import Path

SRC = Path(__file__).resolve().parent / "app" / "ai" / "memory" / "evaluator.py"
text = SRC.read_text(encoding="utf-8")

# 1) The judge now also sees the existing memories and can name a contradiction.
OLD_JUDGE_SIG = '''    async def _judge(self, statement: str, context: str) -> dict[str, Any]:
        """Ask the model the specific questions, not "is this interesting?"."""'''
NEW_JUDGE_SIG = '''    async def _judge(
        self, statement: str, context: str, existing: list[MemoryEntry]
    ) -> dict[str, Any]:
        """Ask the model the specific questions, not "is this interesting?".

        The existing memories are included so the model can answer the one
        question a lexical comparison cannot: does this contradict something we
        already believe? It answers with the index of the memory it conflicts
        with, or -1.
        """'''
if OLD_JUDGE_SIG not in text:
    raise SystemExit("judge signature not found")
text = text.replace(OLD_JUDGE_SIG, NEW_JUDGE_SIG, 1)

OLD_SCHEMA = '''            "{\\n"
            '  "worth_remembering": true/false,\\n'
            '  "reason": "一句话理由",\\n'
            f'  "kind": {"|".join(MEMORY_KINDS)},\\n'
            '  "scope": "global 或 project",\\n'
            '  "confidence": 0.0~1.0,\\n'
            '  "content": "改写成的、对未来有用的陈述句"\\n'
            "}\\n\\n"'''
NEW_SCHEMA = '''            "{\\n"
            '  "worth_remembering": true/false,\\n'
            '  "reason": "一句话理由",\\n'
            f'  "kind": {"|".join(MEMORY_KINDS)},\\n'
            '  "scope": "global 或 project",\\n'
            '  "confidence": 0.0~1.0,\\n'
            '  "content": "改写成的、对未来有用的陈述句",\\n'
            '  "contradicts": 已有记忆中与它矛盾的编号（没有则 -1）\\n'
            "}\\n\\n"'''
if OLD_SCHEMA not in text:
    raise SystemExit("judge schema not found")
text = text.replace(OLD_SCHEMA, NEW_SCHEMA, 1)

OLD_HINT = '''            "- 置信度低于 0.5 就填低分，不要勉强"
        )'''
NEW_HINT = '''            "- 置信度低于 0.5 就填低分，不要勉强\\n"
            "- 如果候选内容与下面某条已有记忆**语义相反**（不是补充，是矛盾），"
            "把那条的编号填进 contradicts；只是相关或更具体，填 -1\\n"
            + (
                "\\n已有记忆：\\n"
                + "\\n".join(f"{i}. {entry.content}" for i, entry in enumerate(existing, start=1))
                if existing
                else "\\n（还没有任何已有记忆）"
            )
        )'''
if OLD_HINT not in text:
    raise SystemExit("judge hint not found")
text = text.replace(OLD_HINT, NEW_HINT, 1)

# 2) evaluate() passes the existing memories in and routes on the model's answer.
OLD_CALL = """        try:
            judged = await self._judge(text, context)
        except Exception as exc:  # noqa: BLE001 - a failed judgement must not crash the caller"""
NEW_CALL = """        existing = await self._existing()

        try:
            judged = await self._judge(text, context, existing)
        except Exception as exc:  # noqa: BLE001 - a failed judgement must not crash the caller"""
if OLD_CALL not in text:
    raise SystemExit("judge call not found")
text = text.replace(OLD_CALL, NEW_CALL, 1)

OLD_DUP = """        existing = await self._existing()

        # Dedup: a restatement bumps the existing row rather than adding one."""
NEW_DUP = """        # Dedup: a restatement bumps the existing row rather than adding one."""
if OLD_DUP not in text:
    raise SystemExit("duplicate existing() lookup not found")
text = text.replace(OLD_DUP, NEW_DUP, 1)

# 3) Prefer the model's contradiction over the lexical one, falling back to it.
OLD_CONFLICT = """        # Conflict: a contradiction becomes a question, never an overwrite.
        for entry in existing:
            if entry.kind != kind:
                continue
            if _is_contradiction(content, entry.content):"""
NEW_CONFLICT = """        # Conflict: a contradiction becomes a question, never an overwrite.
        #
        # The model's answer is authoritative when it names a memory; the lexical
        # check remains as a fallback for the near-copy case the model sometimes
        # treats as a restatement.
        named = -1
        try:
            named = int(judged.get("contradicts", -1))
        except (TypeError, ValueError):
            named = -1

        for index, entry in enumerate(existing, start=1):
            if named != index and not (
                entry.kind == kind and _is_contradiction(content, entry.content)
            ):
                continue
            if True:"""
if OLD_CONFLICT not in text:
    raise SystemExit("conflict loop not found")
text = text.replace(OLD_CONFLICT, NEW_CONFLICT, 1)

SRC.write_text(text, encoding="utf-8")
print("conflict detection now asks the model")
