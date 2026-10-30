"""Deterministic offline provider ("heuristic engine").

Why this exists
---------------
AI Workbench is local-first: a user must be able to run the full pipeline with
**zero external dependencies and zero API keys**.  When no cloud key and no
Ollama daemon are available, this provider keeps every workflow functional
instead of returning fake data.

It is a *real* implementation, not a stub:

* **Embeddings** — hashed bag-of-ngrams vectoriser with sublinear TF weighting
  and L2 normalisation (words for latin scripts, character bigrams for CJK).
  Deterministic, dependency-free, and good enough for lexical-semantic recall.
* **Extractive QA** — sentence-level scoring (query overlap + term salience +
  position prior) produces grounded answers built *only* from retrieved context.
* **Structured extraction** — TF-IDF style keyword/term ranking, TODO and
  question detection, heading-based outline extraction.
* **Planning** — turns a goal plus retrieved material into a phased task tree.

Everything it produces is labelled ``provider=offline`` in the AI run trace, so
the UI and the docs never present it as a hosted LLM.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import time
from collections import Counter
from collections.abc import Iterable, Sequence
from typing import Any

from app.ai.providers.base import EmbeddingProvider, LLMProvider
from app.ai.types import (
    ChatMessage,
    ChatResult,
    EmbeddingResult,
    ToolCallRequest,
    ToolSpec,
    Usage,
    estimate_tokens,
)
from app.core.logging import get_logger

logger = get_logger(__name__)

DEFAULT_DIMENSION = 384

_CJK = re.compile(r"[\u4e00-\u9fff]")
_WORD = re.compile(r"[A-Za-z][A-Za-z0-9_+#.-]*|\d+")
_SENT_SPLIT = re.compile(r"(?<=[。！？!?；;\n])|(?<=[.!?])\s+")
_TASK_MARKER = re.compile(r"task\s*[:=]\s*([a-z_]+)", re.IGNORECASE)

_STOPWORDS = {
    "the",
    "a",
    "an",
    "and",
    "or",
    "of",
    "to",
    "in",
    "is",
    "are",
    "was",
    "were",
    "be",
    "been",
    "for",
    "on",
    "with",
    "as",
    "by",
    "that",
    "this",
    "it",
    "at",
    "from",
    "but",
    "not",
    "you",
    "your",
    "we",
    "our",
    "they",
    "their",
    "will",
    "can",
    "if",
    "then",
    "than",
    "so",
    "such",
    "into",
    "about",
    "which",
    "when",
    "what",
    "how",
    "why",
    "use",
    "using",
    "used",
    "may",
    "must",
    "should",
    "的",
    "了",
    "和",
    "是",
    "在",
    "我",
    "有",
    "就",
    "不",
    "人",
    "都",
    "一",
    "一个",
    "上",
    "也",
    "很",
    "到",
    "说",
    "要",
    "去",
    "会",
    "着",
    "没有",
    "看",
    "好",
    "自己",
    "这",
    "那",
    "为",
    "与",
    "对",
    "可以",
    "并",
    "等",
    "被",
    "把",
    "而",
    "从",
    "以",
}


# ---------------------------------------------------------------------------
# Tokenisation helpers
# ---------------------------------------------------------------------------
def tokenize(text: str) -> list[str]:
    """Language-agnostic tokenisation: latin words + CJK character bigrams."""
    tokens: list[str] = [m.group(0).lower() for m in _WORD.finditer(text)]
    for run in re.findall(r"[\u4e00-\u9fff]+", text):
        if len(run) == 1:
            tokens.append(run)
            continue
        tokens.extend(run[i : i + 2] for i in range(len(run) - 1))
        # Keep the full run as ONE token for exact-phrase matching.
        # NOTE: ``extend(run)`` would iterate the string and emit single
        # characters, flooding the vector with noise — hence ``append``.
        tokens.append(run)
    return [t for t in tokens if (t not in _STOPWORDS and len(t) > 1) or _CJK.search(t)]


def split_sentences(text: str) -> list[str]:
    parts = [p.strip() for p in _SENT_SPLIT.split(text) if p and p.strip()]
    return [p for p in parts if len(p) > 1]


def _hash_bucket(token: str, dim: int) -> tuple[int, float]:
    digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
    value = int.from_bytes(digest, "big")
    return value % dim, 1.0 if (value >> 63) & 1 else -1.0


# ---------------------------------------------------------------------------
# Embedding
# ---------------------------------------------------------------------------
class HeuristicEmbeddingProvider(EmbeddingProvider):
    """Deterministic hashed n-gram embedding — no network, no model download."""

    name = "offline"

    def __init__(
        self,
        *,
        model: str = "heuristic-hash-ngram-v1",
        timeout: float = 5.0,
        dimension: int = DEFAULT_DIMENSION,
    ) -> None:
        super().__init__(model=model, timeout=timeout)
        self._dim = dimension

    @property
    def dimension(self) -> int:
        return self._dim

    def _vector(self, text: str) -> list[float]:
        vec = [0.0] * self._dim
        tokens = tokenize(text)
        if not tokens:
            return vec
        counts = Counter(tokens)
        # Sublinear TF damps the effect of repeated boilerplate words.
        for token, tf in counts.items():
            idx, sign = _hash_bucket(token, self._dim)
            vec[idx] += sign * (1.0 + math.log(tf))
        norm = math.sqrt(sum(v * v for v in vec))
        if norm > 0:
            vec = [v / norm for v in vec]
        return vec

    async def embed(self, texts: Sequence[str]) -> EmbeddingResult:
        started = time.perf_counter()
        vectors = [self._vector(t) for t in texts]
        return EmbeddingResult(
            vectors=vectors,
            model=self.model,
            provider=self.name,
            dimension=self._dim,
            usage=Usage(
                prompt_tokens=sum(estimate_tokens(t) for t in texts),
                total_tokens=sum(estimate_tokens(t) for t in texts),
            ),
            latency_ms=int((time.perf_counter() - started) * 1000),
        )


# ---------------------------------------------------------------------------
# Chat / generation
# ---------------------------------------------------------------------------
class HeuristicLLMProvider(LLMProvider):
    """Extractive, deterministic generator used when no model backend is set."""

    name = "offline"

    def __init__(
        self, *, model: str = "heuristic-extractive-v1", timeout: float = 30.0, max_retries: int = 1
    ) -> None:
        super().__init__(model=model, timeout=timeout, max_retries=max_retries)

    @property
    def supports_tool_calling(self) -> bool:
        return True

    # ------------------------------------------------------------- entrypoint
    async def chat(
        self,
        messages: Sequence[ChatMessage],
        *,
        temperature: float = 0.2,
        max_tokens: int = 2048,
        tools: Sequence[ToolSpec] | None = None,
        json_mode: bool = False,
    ) -> ChatResult:
        started = time.perf_counter()
        system = next((m.content for m in messages if m.role == "system"), "")
        user = next((m.content for m in reversed(messages) if m.role == "user"), "")
        task = self._detect_task(system, user, tools)

        if tools and task in ("agent", "agent_system"):
            content, calls = self._agent_step(messages, user, list(tools))
        elif json_mode or task in _JSON_TASKS:
            content, calls = self._structured(task, user, messages), []
        else:
            content, calls = self._prose(task, user, messages), []

        latency_ms = int((time.perf_counter() - started) * 1000)
        usage = Usage(
            prompt_tokens=sum(estimate_tokens(m.content) for m in messages),
            completion_tokens=estimate_tokens(content),
        )
        usage.total_tokens = usage.prompt_tokens + usage.completion_tokens
        return ChatResult(
            content=content,
            model=self.model,
            provider=self.name,
            usage=usage,
            tool_calls=calls,
            finish_reason="tool_calls" if calls else "stop",
            latency_ms=latency_ms,
        )

    # ---------------------------------------------------------------- helpers
    @staticmethod
    def _detect_task(system: str, user: str, tools: Sequence[ToolSpec] | None) -> str:
        match = _TASK_MARKER.search(system)
        if match:
            return match.group(1).lower()
        if tools:
            return "agent"
        lowered = user.lower()
        if any(k in lowered for k in ("计划", "plan", "roadmap", "schedule")):
            return "task_planner"
        if any(k in lowered for k in ("总结", "summar", "摘要")):
            return "document_summary"
        return "rag_answer"

    def _prose(self, task: str, user: str, messages: Sequence[ChatMessage]) -> str:
        context = self._latest_context(messages)
        if task == "note_generation":
            return self._note(user, context)
        if task == "document_summary":
            return self._summary(context or user)
        return self._answer(user, context)

    def _structured(self, task: str, user: str, messages: Sequence[ChatMessage]) -> str:
        context = self._latest_context(messages)
        payload: dict[str, Any]
        if task == "knowledge_extraction":
            payload = self._extract(context or user)
        elif task == "task_planner":
            payload = self._plan(user, context)
        elif task == "interview_generator":
            payload = self._interview(context or user)
        elif task == "study_plan":
            payload = self._study_plan(user, context)
        elif task == "query_rewrite":
            payload = {"queries": self._rewrite(user)}
        elif task == "agent_system":
            payload = {
                "thought": "offline heuristic agent",
                "final_answer": self._answer(user, context),
            }
        elif task == "document_summary":
            payload = self._summary_payload(context or user)
        else:
            payload = {"answer": self._answer(user, context)}
        return json.dumps(payload, ensure_ascii=False)

    @staticmethod
    def _latest_context(messages: Sequence[ChatMessage]) -> str:
        """Extract the retrieved-context block from the conversation.

        The prompt templates wrap the material in explicit ``<document>`` /
        ``<context>`` tags (see ``app/ai/prompts/builtin.py``).  Reading those
        tags — instead of guessing which message holds the material — is what
        keeps the offline engine honest: it can never mistake the prompt's own
        instructions for retrieved content, and it works no matter which message
        the caller put the material in.
        """
        tagged = re.compile(r"<document>(.*?)</document>", re.DOTALL | re.IGNORECASE)
        context_tag = re.compile(r"<context>(.*?)</context>", re.DOTALL | re.IGNORECASE)
        labelled = re.compile(r"(【资料\s*\d+.*)", re.DOTALL)

        for pattern in (tagged, context_tag, labelled):
            for msg in messages:
                if msg.role == "system" and pattern is labelled:
                    continue
                match = pattern.search(msg.content)
                if match:
                    return match.group(1)

        # Last resort: the concatenation of user turns only. The system message
        # is never used as material.
        return "\n".join(m.content for m in messages if m.role == "user")

    @staticmethod
    def _strip_context_noise(text: str) -> str:
        text = re.sub(r"<context[^>]*>|</context>", "", text)
        text = re.sub(r"<document[^>]*>|</document>", "", text)
        text = re.sub(r"【资料\s*\d+[^】]*】", "", text)
        return text.strip()

    # ------------------------------------------------------------- primitives
    def _answer(self, question: str, context: str) -> str:
        clean = self._strip_context_noise(context)
        sentences = [s for s in split_sentences(clean) if len(s) > 8]
        if not sentences:
            return (
                "离线启发式引擎没有检索到可用资料。\n\n"
                "请先上传 Markdown / TXT / PDF 文档到知识库，或在 .env 中配置 "
                "AI_API_KEY 使用云端模型。"
            )
        q_tokens = set(tokenize(question))
        scored: list[tuple[float, int, str]] = []
        for idx, sent in enumerate(sentences):
            s_tokens = tokenize(sent)
            if not s_tokens:
                continue
            overlap = len(q_tokens & set(s_tokens)) / (len(q_tokens) or 1)
            density = len(set(s_tokens)) / (len(s_tokens) or 1)
            # Earlier sentences in a chunk are usually more topical.
            position = 1.0 / (1.0 + idx * 0.08)
            scored.append((overlap * 2.0 + density * 0.4 + position * 0.2, idx, sent))
        scored.sort(key=lambda x: (-x[0], x[1]))
        top = [s for _, _, s in scored[:5]]
        body = "\n".join(f"- {s.strip()}" for s in top)
        return (
            "根据检索到的资料，可以归纳出以下要点（离线启发式抽取）：\n\n"
            f"{body}\n\n"
            "以上内容完全来自知识库检索片段，未引入外部知识。"
        )

    def _summary(self, text: str) -> str:
        clean = self._strip_context_noise(text)
        sentences = [s for s in split_sentences(clean) if len(s) > 10]
        if not sentences:
            return "文档内容过短，无法生成摘要。"
        freq = Counter(tokenize(clean))
        if not freq:
            return clean[:400]
        top_terms = {t for t, _ in freq.most_common(max(8, len(freq) // 12))}
        scored: list[tuple[float, int, str]] = []
        for idx, sent in enumerate(sentences):
            tokens = tokenize(sent)
            if not tokens:
                continue
            salience = sum(freq.get(t, 0) for t in tokens if t in top_terms) / len(tokens)
            position = 1.0 / (1.0 + idx * 0.05)
            length_penalty = 1.0 if 20 <= len(sent) <= 300 else 0.6
            scored.append((salience * length_penalty + position * 0.3, idx, sent))
        scored.sort(key=lambda x: (-x[0], x[1]))
        picked = sorted(scored[:6], key=lambda x: x[1])
        body = "\n".join(f"{i + 1}. {s.strip()}" for i, (_, _, s) in enumerate(picked))
        keywords = "、".join(t for t, _ in freq.most_common(8))
        return f"**核心内容**\n\n{body}\n\n**关键词**：{keywords}"

    def _summary_payload(self, text: str) -> dict[str, Any]:
        """Full ``document_summary`` shape so the API response is identical
        regardless of which provider answered."""
        clean = self._strip_context_noise(text)
        freq = Counter(tokenize(clean))
        sentences = [s for s in split_sentences(clean) if len(s) > 10]
        key_points = [
            s.strip()
            for _, _, s in sorted(
                ((sum(freq.get(t, 0) for t in tokenize(s)), i, s) for i, s in enumerate(sentences)),
                key=lambda x: -x[0],
            )[:8]
        ]
        long_sentences = sum(1 for s in sentences if len(s) > 60)
        ratio = long_sentences / len(sentences) if sentences else 0.0
        difficulty = "高级" if ratio > 0.6 else "进阶" if ratio > 0.3 else "入门"
        return {
            "summary": self._summary(clean),
            "key_points": key_points,
            "keywords": [t for t, _ in freq.most_common(8)],
            "difficulty": difficulty,
            "audience": "具备基础编程经验的读者",
        }

    def _extract(self, text: str) -> dict[str, Any]:
        clean = self._strip_context_noise(text)
        sentences = split_sentences(clean)
        freq = Counter(tokenize(clean))
        concepts = [t for t, c in freq.most_common(15) if len(t) > 2 and c > 1]
        headings = [h.strip() for h in re.findall(r"^#{1,6}\s*(.+)$", clean, re.MULTILINE)]
        terms: list[dict[str, str]] = []
        for term in concepts[:10]:
            source = next((s for s in sentences if term in s.lower()), "")
            terms.append({"term": term, "definition": source[:200]})
        todos: list[dict[str, str]] = []
        todo_re = re.compile(r"(?i)(TODO|FIXME|待办|待完成|下一步|需要|要完成)[:：\s]*(.+)")
        for sent in sentences:
            m = todo_re.search(sent)
            if m:
                todos.append({"title": m.group(2).strip()[:160], "source": sent[:200]})
        if not todos:
            for h in headings[:5]:
                todos.append({"title": f"掌握：{h}", "source": "由文档小节标题推导"})
        key_points = [
            s.strip()
            for _, _, s in sorted(
                ((sum(freq.get(t, 0) for t in tokenize(s)), i, s) for i, s in enumerate(sentences)),
                key=lambda x: -x[0],
            )[:8]
        ]
        return {
            "summary": self._summary(clean),
            "concepts": concepts,
            "key_points": key_points,
            "terms": terms,
            "todos": todos,
            "outline": headings[:20],
        }

    def _plan(self, goal: str, context: str) -> dict[str, Any]:
        clean = self._strip_context_noise(context)
        headings = [h.strip() for h in re.findall(r"^#{1,6}\s*(.+)$", clean, re.MULTILINE)]
        if not headings:
            candidates = [s.strip() for s in split_sentences(clean) if 4 < len(s) < 60]
            headings = candidates[:8]
        if not headings:
            headings = self._goal_topics(goal)

        headings = self._dedupe(headings)[:6]
        phases: list[dict[str, Any]] = []
        for i, topic in enumerate(headings):
            topic = topic.lstrip("#-* ").strip()
            if not topic:
                continue
            phases.append(
                {
                    "name": f"阶段 {i + 1}：{topic}",
                    "description": f"围绕「{topic}」建立可验证的理解与实践能力。",
                    "tasks": [
                        {
                            "title": f"通读并整理 {topic} 的核心概念",
                            "description": f"阅读知识库中与「{topic}」相关的资料，输出结构化笔记。",
                            "estimated_minutes": 90,
                            "priority": "HIGH" if i == 0 else "MEDIUM",
                            "completion_criteria": f"能够用自己的话解释 {topic} 的关键概念，并产出一篇笔记。",
                            "subtasks": [
                                {"title": f"标记 {topic} 中的疑问点", "estimated_minutes": 20},
                                {"title": f"整理 {topic} 的术语表", "estimated_minutes": 30},
                            ],
                        },
                        {
                            "title": f"完成 {topic} 的动手练习",
                            "description": f"针对「{topic}」设计并完成一个最小可运行示例。",
                            "estimated_minutes": 120,
                            "priority": "MEDIUM",
                            "completion_criteria": f"示例代码可运行，并能说明 {topic} 在真实项目中的作用。",
                            "subtasks": [],
                        },
                        {
                            "title": f"复盘 {topic} 并沉淀知识卡片",
                            "description": "把易错点、结论和参考资料整理为知识卡片。",
                            "estimated_minutes": 45,
                            "priority": "LOW",
                            "completion_criteria": "产出至少 3 张知识卡片并关联到知识库文档。",
                            "subtasks": [],
                        },
                    ],
                }
            )
        if not phases:
            phases = [
                {
                    "name": "阶段 1：目标拆解",
                    "description": "把目标拆解为可执行的最小步骤。",
                    "tasks": [
                        {
                            "title": goal.strip()[:160] or "明确目标范围",
                            "description": "定义范围、验收标准与时间盒。",
                            "estimated_minutes": 60,
                            "priority": "HIGH",
                            "completion_criteria": "产出书面的目标与验收标准。",
                            "subtasks": [],
                        }
                    ],
                }
            ]
        return {
            "goal": goal.strip(),
            "strategy": "基于知识库中已有材料的离线启发式规划（按小节标题拆解为阶段与任务）。",
            "phases": phases,
        }

    @staticmethod
    def _goal_topics(goal: str) -> list[str]:
        tokens = [t for t in tokenize(goal) if _CJK.search(t) and len(t) >= 2]
        words = [m.group(0) for m in _WORD.finditer(goal) if len(m.group(0)) > 2]
        topics = HeuristicLLMProvider._dedupe(tokens + words)
        return topics[:5] or ["基础准备", "核心内容", "实践应用", "复盘总结"]

    @staticmethod
    def _dedupe(items: Iterable[str]) -> list[str]:
        seen: set[str] = set()
        out: list[str] = []
        for raw in items:
            key = raw.strip().lower()
            if not key or key in seen:
                continue
            seen.add(key)
            out.append(raw)
        return out

    def _interview(self, text: str) -> dict[str, Any]:
        clean = self._strip_context_noise(text)
        freq = Counter(tokenize(clean))
        topics = [t for t, _ in freq.most_common(10) if len(t) > 2]
        sentences = split_sentences(clean)
        questions: list[dict[str, Any]] = []
        for topic in topics[:8]:
            evidence = next((s for s in sentences if topic in s.lower()), "")
            questions.append(
                {
                    "question": f"请解释「{topic}」是什么，它在什么场景下有价值？",
                    "answer": evidence[:400] or "结合文档内容作答。",
                    "difficulty": "MEDIUM",
                    "topic": topic,
                    "follow_up": f"如果「{topic}」失效或被误用，会出现什么问题？",
                }
            )
        return {"questions": questions}

    def _study_plan(self, goal: str, context: str) -> dict[str, Any]:
        plan = self._plan(goal, context)
        days: list[dict[str, Any]] = []
        day_index = 1
        for phase in plan["phases"]:
            for task in phase["tasks"]:
                days.append(
                    {
                        "day": day_index,
                        "focus": phase["name"],
                        "goal": task["title"],
                        "estimated_minutes": task["estimated_minutes"],
                        "deliverable": task["completion_criteria"],
                    }
                )
                day_index += 1
        return {"goal": goal.strip(), "days": days}

    def _note(self, topic: str, context: str) -> str:
        clean = self._strip_context_noise(context)
        summary = self._summary(clean)
        extracted = self._extract(clean)
        lines = [
            f"# {topic.strip()[:80] or 'AI 生成笔记'}",
            "",
            "> 由 AI Workbench 离线启发式引擎基于知识库检索片段生成。",
            "",
            "## 摘要",
            "",
            summary,
            "",
            "## 关键概念",
            "",
        ]
        lines += [f"- {c}" for c in extracted["concepts"][:10]] or ["- （未提取到概念）"]
        lines += ["", "## 待办", ""]
        lines += [f"- [ ] {t['title']}" for t in extracted["todos"][:8]] or ["- [ ] （未发现待办）"]
        lines += ["", "## 术语", ""]
        for t in extracted["terms"][:8]:
            lines.append(f"- **{t['term']}**：{t['definition']}")
        return "\n".join(lines)

    def _rewrite(self, query: str) -> list[str]:
        base = query.strip()
        tokens = [t for t in tokenize(base) if len(t) > 1][:6]
        variants = [base]
        if tokens:
            variants.append(" ".join(tokens))
            variants.append(f"{base} 定义 原理 示例")
        return self._dedupe(variants)[:3]

    # --------------------------------------------------------------- agent ---
    def _agent_step(
        self, messages: Sequence[ChatMessage], goal: str, tools: list[ToolSpec]
    ) -> tuple[str, list[ToolCallRequest]]:
        """Deterministic tool-selection policy.

        Order of operations mirrors what a competent assistant would do:
        gather knowledge → derive a plan → persist tasks → summarise.
        The policy inspects the transcript so the loop always terminates.
        """
        used = {m.name for m in messages if m.role == "tool" and m.name}
        available = {t.name: t for t in tools}
        lowered = goal.lower()

        wants_knowledge = any(
            k in lowered for k in ("知识", "资料", "文档", "knowledge", "文档库", "根据")
        )
        wants_tasks = any(k in lowered for k in ("计划", "任务", "plan", "task", "安排", "学习"))

        sequence: list[str] = []
        if wants_knowledge or wants_tasks:
            sequence.append("knowledge_search")
        if wants_tasks:
            sequence.append("task_planner")
            sequence.append("task_create_batch")
        sequence.append("note_create")

        for name in sequence:
            if name in used or name not in available:
                continue
            args: dict[str, Any] = {}
            if name == "knowledge_search":
                args = {"query": goal[:200], "limit": 6}
            elif name == "task_planner":
                args = {"goal": goal[:300], "create_tasks": False}
            elif name == "task_create_batch":
                args = {"from_goal": goal[:300], "project_id": None}
            elif name == "note_create":
                args = {
                    "title": goal[:60] or "AI 工作记录",
                    "content_md": "",
                    "from_goal": goal[:300],
                }
            return "", [ToolCallRequest(id=f"call_{name}", name=name, arguments=args)]

        summary = self._answer(goal, self._latest_context(messages))
        return f"离线启发式 Agent 已完成工具调用序列。\n\n{summary}", []


_JSON_TASKS = {
    "knowledge_extraction",
    "task_planner",
    "interview_generator",
    "study_plan",
    "query_rewrite",
    "agent_system",
}
