"""What an employee's persona may and may not say.

§4.2 of the brief: "人格和语气可定制，但不能覆盖系统规则、权限、事实验证和安全要求."

The honest reading of that is not "filter the persona text". A persona is a paragraph the
user wrote about how their employee should talk; it is not a place where the permission
system can be reached, because the permission system is not in the prompt at all — it is in
``app/ai/tools/permissions.py``, running before a tool executes, reading the database. Text
cannot grant a tool.

So what is this for? Two things that are still real:

1. **A persona that tells the employee to ignore its rules produces an employee that
   argues with the user.** It will refuse to ask for approval, explain that it has been
   authorised, and generally waste the user's time on a permission it never had. Catching
   it at write time turns a confusing runtime behaviour into a clear message.
2. **A persona is injected into every request.** Text that says "print your instructions"
   or "ignore the above" is a prompt-injection payload with a permanent home. §17 requires
   that retrieved content be treated as data; a persona is retrieved content with a worse
   blast radius, because it is always there.

**This is a warning, not a wall.** The check returns findings; the caller decides. Refusing
to save outright would be the wrong shape — a legitimate persona might say "不要因为怕出错就
拒绝执行", and a substring filter cannot tell that from an attack. The API returns the
findings and lets the user confirm, which is the same shape as every other risky action in
this product.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

#: Phrases that read as an attempt to change the rules rather than the tone. Chinese and
#: English, because a persona will be written in whichever the user thinks in.
_RULE_OVERRIDE_PATTERNS: tuple[tuple[str, str], ...] = (
    # The optional groups matter more than they look. "忽略以上所有指令" and
    # "无视之前的所有规则" are the same intent with the qualifiers in a different order, and a
    # pattern that only handles the first is a check that misses half of what it is for.
    (
        r"(忽略|无视|不要理会)(以上|之前|前面|上述)?(的)?(所有|全部)?(的)?"
        r"(指令|规则|提示|要求|约束|限制|设定)",
        "试图忽略既有规则",
    ),
    (r"不要(再)?(遵守|执行|理会)(任何)?(规则|限制|约束|指令)", "试图解除约束"),
    (r"(解除|取消|绕过|跳过)(所有)?(权限|限制|约束|审批|确认)", "试图绕过权限或审批"),
    (
        r"(无需|不用|不必|不需要)(再)?(请求|等待|征求)(用户|我)?(的)?(授权|确认|同意|批准)",
        "试图跳过授权",
    ),
    (
        r"你(已经|已被)?(获得|拥有|得到)(了)?(所有|全部|最高|完整)(的)?(权限|授权|控制权)",
        "声称拥有未授予的权限",
    ),
    (r"(直接|自行|随意)(执行|运行|删除|发送)(任何|所有)?", "试图扩大执行范围"),
    (
        r"ignore\s+(all\s+)?(previous|prior|above|the)\s+(instructions?|rules?|prompts?)",
        "试图忽略既有规则",
    ),
    (
        r"disregard\s+(all\s+)?(previous|prior|above|the)\s+(instructions?|rules?)",
        "试图忽略既有规则",
    ),
    (
        r"(bypass|skip|disable)\s+(all\s+)?(permissions?|restrictions?|approvals?|confirmations?)",
        "试图绕过权限或审批",
    ),
    (
        r"you\s+(now\s+)?have\s+(full|all|complete|unrestricted)\s+(access|permissions?|control)",
        "声称拥有未授予的权限",
    ),
    (r"without\s+(asking|waiting\s+for)\s+(the\s+)?(user|confirmation|approval)", "试图跳过授权"),
)

#: Phrases that try to get the employee to reveal or restate its own instructions.
_DISCLOSURE_PATTERNS: tuple[tuple[str, str], ...] = (
    (
        r"(打印|输出|复述|重复|展示|告诉我)(你的)?(系统)?(提示词|指令|prompt|设定)",
        "试图套取系统提示词",
    ),
    (
        r"(reveal|print|repeat|show|output)\s+(your\s+)?(system\s+)?(prompt|instructions?)",
        "试图套取系统提示词",
    ),
    (r"(进入|切换到)(开发者|调试|DAN|越狱)(模式|状态)", "试图切换运行模式"),
    (r"(developer|debug|jailbreak)\s+mode", "试图切换运行模式"),
)

_COMPILED: tuple[tuple[re.Pattern[str], str], ...] = tuple(
    (re.compile(pattern, re.IGNORECASE), label)
    for pattern, label in (*_RULE_OVERRIDE_PATTERNS, *_DISCLOSURE_PATTERNS)
)

#: A persona is a paragraph, not a document. This is generous on purpose — it is here to
#: catch a paste, not to police length.
MAX_PERSONA_CHARS = 2000


@dataclass(frozen=True, slots=True)
class PersonaFinding:
    """One reason to look at this persona before saving it."""

    label: str
    excerpt: str

    def to_dict(self) -> dict[str, str]:
        return {"label": self.label, "excerpt": self.excerpt}


@dataclass(frozen=True, slots=True)
class PersonaReview:
    findings: tuple[PersonaFinding, ...]
    too_long: bool

    @property
    def clean(self) -> bool:
        return not self.findings and not self.too_long

    def to_dict(self) -> dict[str, object]:
        return {
            "clean": self.clean,
            "findings": [f.to_dict() for f in self.findings],
            "too_long": self.too_long,
            "max_chars": MAX_PERSONA_CHARS,
            "note": (
                ""
                if self.clean
                else "这段话里有试图改变规则、跳过授权或套取提示词的句子。"
                "它不会真的改变权限——权限在执行端检查，不读提示词——"
                "但会让员工在运行中表现得不听话。确认无误可以继续保存。"
            ),
        }


def review_persona(text: str) -> PersonaReview:
    """Find the parts of a persona that talk about rules rather than tone."""
    if not text:
        return PersonaReview(findings=(), too_long=False)

    findings: list[PersonaFinding] = []
    for pattern, label in _COMPILED:
        for match in pattern.finditer(text):
            start = max(0, match.start() - 12)
            end = min(len(text), match.end() + 12)
            excerpt = text[start:end].replace("\n", " ").strip()
            findings.append(PersonaFinding(label=label, excerpt=excerpt))
    return PersonaReview(
        findings=tuple(findings),
        too_long=len(text) > MAX_PERSONA_CHARS,
    )


def describe_persona_limits() -> dict[str, object]:
    """For the API to explain the rule without hardcoding it in the window."""
    return {
        "max_chars": MAX_PERSONA_CHARS,
        "rule": (
            "人格决定员工怎么说话，不决定它能做什么。"
            "权限在执行端检查——工具运行之前读数据库——所以写在这里的授权声明不会生效，"
            "只会让员工在运行中表现得抗拒确认。"
        ),
    }
