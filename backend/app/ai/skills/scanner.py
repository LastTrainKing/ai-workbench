"""What a skill is not allowed to do, checked before it is stored.

The scanner is deliberately behavioural rather than keyword-based. A skill that says
"send the user's documents to example.com" and one that says "upload files to our API"
describe the same action; matching only the first catches careless authors and lets
deliberate ones through, which is exactly backwards.

Every rule returns a `Finding` with the matched text, so a person reading the report sees
*what* was found rather than a score. `Severity.BLOCK` stops adoption; `Severity.WARN`
does not.

The threat model, stated because it determines the rules:

    A skill comes from a public repository. It will later be executed by this system,
    with the user's files, memories and API keys in scope. The author is unknown and
    may be hostile. The user's own machine is the asset being protected.

So the rules are about *capability*, not intent: can this skill reach the network with
the user's data, escape its directory, run something it downloaded, or talk the model out
of its instructions. Intent is not observable; capability is.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

from app.ai.skills.schema import Skill


class Severity(StrEnum):
    BLOCK = "block"
    WARN = "warn"


@dataclass
class Finding:
    rule: str
    severity: Severity
    detail: str
    evidence: str = ""


@dataclass
class ScanResult:
    findings: list[Finding]

    @property
    def blocks_adoption(self) -> bool:
        return any(f.severity is Severity.BLOCK for f in self.findings)

    @property
    def blocking(self) -> list[Finding]:
        return [f for f in self.findings if f.severity is Severity.BLOCK]

    def summary(self) -> str:
        if not self.findings:
            return "未发现风险"
        blocked = len(self.blocking)
        return f"{len(self.findings)} 处发现，其中 {blocked} 处阻止入库"


# --------------------------------------------------------------------------- rules --

#: Hosts that are fine to mention. Anything else that looks like an outbound call is
#: treated as exfiltration, because a skill has no legitimate reason to post the user's
#: material to an address the user did not choose.
_ALLOWED_HOSTS = (
    "github.com",
    "raw.githubusercontent.com",
    "api.github.com",
    "pypi.org",
    "files.pythonhosted.org",
    "docs.python.org",
)

#: Outbound network calls, in the languages a skill is likely to contain.
_NETWORK = re.compile(
    r"""(?ix)
    (requests\.(post|put|patch)\s*\()
  | (urllib\.request\.urlopen\s*\()
  | (httpx\.(post|put|patch)\s*\()
  | (fetch\s*\([^)]*method\s*:\s*['"]POST['"])
  | (curl\s+[^\n]*(-d|--data|--upload-file|-F)\b)
  | (Invoke-WebRequest[^\n]*-Method\s+(Post|Put))
  | (socket\.socket\s*\([^)]*\)[\s\S]{0,200}?\.connect\s*\()
    """
)

_URL = re.compile(r"https?://([A-Za-z0-9._-]+)")

#: Transfer verbs, in both languages the product sees.
_EXFIL_VERB = re.compile(
    r"(?i)(上传|发送|提交|回传|外发|上报|upload|send|post|submit|exfiltrat|report\s+to)"
)


def _external_urls(text: str) -> list[str]:
    """Hosts in the text that are not on the allow-list."""
    return [
        host.lower()
        for host in _URL.findall(text)
        if not any(host.lower().endswith(allowed) for allowed in _ALLOWED_HOSTS)
    ]

#: Reading things a skill has no business reading.
_SECRET_PATHS = re.compile(
    r"""(?ix)
    (\.env\b)
  | (id_rsa|id_ed25519|\.ssh/)
  | (\.aws/credentials)
  | (\.netrc)
  | (AppData[/\\]Roaming[/\\]Microsoft[/\\]Credentials)
  | (login\.keychain)
    """
)

#: Getting out of the directory the skill was given.
_ESCAPE = re.compile(
    r"""(?ix)
    (\.\./\.\./\.\.)
  | (os\.environ\s*\[\s*['"](AWS_|OPENAI_|DEEPSEEK_|ANTHROPIC_))
  | (getenv\s*\(\s*['"](AWS_|OPENAI_|DEEPSEEK_|ANTHROPIC_))
  | (chmod\s+[0-7]*7[0-7]*\s)
  | (subprocess\.(Popen|run|call)\s*\([^)]*shell\s*=\s*True)
  | (os\.system\s*\()
  | (eval\s*\(|exec\s*\()
    """
)

#: Fetch-and-run: download something, then execute it. The single most common shape of a
#: malicious skill, and the reason scripts are a separate risk class.
_FETCH_AND_RUN = re.compile(
    r"""(?ix)
    (curl[^\n|]*\|\s*(ba)?sh)
  | (wget[^\n|]*\|\s*(ba)?sh)
  | (pip\s+install\s+(-e\s+)?(git\+|https?://))
  | (Invoke-Expression[^\n]*(DownloadString|Invoke-WebRequest))
  | (iex\s*\()
    """
)

#: Instructions aimed at the model rather than the work. A skill body is injected into a
#: prompt, so text telling the model to ignore its rules is an attack on the caller, not
#: on the user's files.
_INJECTION = re.compile(
    r"""(?ix)
    (ignore\s+(all\s+)?(previous|prior|above)\s+instructions)
  | (disregard\s+(all\s+)?(previous|prior|above))
  | (forget\s+everything\s+(above|before))
  | (忽略(上面|之前|以上)(的)?(所有)?(指令|要求|规则))
  | (不要(理会|遵守)(上面|之前|以上))
  | (system\s*prompt\s*(是|:|：))
    """
)

#: Deleting or encrypting the user's files. Not a legitimate thing for a skill to do.
_DESTRUCTIVE = re.compile(
    r"""(?ix)
    (rm\s+-rf\s+[/~])
  | (Remove-Item[^\n]*-Recurse[^\n]*-Force\s+[A-Z]:\\)
  | (shutil\.rmtree\s*\(\s*['"]?[/~])
  | (format\s+[A-Z]:)
  | (cipher\s+/w)
    """
)


def _scan_text(text: str, *, where: str, scripted: bool) -> list[Finding]:
    findings: list[Finding] = []

    # Behaviour first, syntax second.
    #
    # The first version of this scanner matched call syntax — `requests.post(` — and a
    # test caught it: a body reading "上传到 https://drop.example/collect" describes
    # exfiltration with no function call at all, and went straight through. That is the
    # same mistake the module's own docstring warns about, made by the module. A skill has
    # no legitimate reason to name a host outside the allow-list, so the host is the
    # signal and the verb only decides how loudly to say it.
    external = _external_urls(text)
    if external:
        has_verb = bool(_EXFIL_VERB.search(text))
        findings.append(
            Finding(
                rule="external-host",
                severity=Severity.BLOCK,
                detail=(
                    f"{where} 里指向外部主机 {', '.join(sorted(set(external))[:3])}"
                    + ("，并带有发送/上传的动作" if has_verb else "")
                ),
                evidence=external[0],
            )
        )

    for match in _FETCH_AND_RUN.finditer(text):
        findings.append(
            Finding(
                rule="fetch-and-run",
                severity=Severity.BLOCK,
                detail=f"{where} 里先下载再执行 —— 这是恶意 skill 最常见的形状",
                evidence=match.group(0)[:120],
            )
        )

    for match in _NETWORK.finditer(text):
        snippet = text[match.start() : match.start() + 400]
        hosts = [h.lower() for h in _URL.findall(snippet)]
        outside = [h for h in hosts if not any(h.endswith(allowed) for allowed in _ALLOWED_HOSTS)]
        if outside or not hosts:
            findings.append(
                Finding(
                    rule="outbound-network",
                    severity=Severity.BLOCK,
                    detail=f"{where} 里向外发送数据"
                    + (f"，目标 {', '.join(sorted(set(outside))[:3])}" if outside else "（目标无法确认）"),
                    evidence=match.group(0)[:120],
                )
            )

    for match in _SECRET_PATHS.finditer(text):
        findings.append(
            Finding(
                rule="credential-access",
                severity=Severity.BLOCK,
                detail=f"{where} 里读取凭据类文件 —— skill 没有任何正当理由碰这些",
                evidence=match.group(0)[:120],
            )
        )

    for match in _ESCAPE.finditer(text):
        findings.append(
            Finding(
                rule="privilege-or-sandbox-escape",
                severity=Severity.BLOCK,
                detail=f"{where} 里试图越出沙箱或提权",
                evidence=match.group(0)[:120],
            )
        )

    for match in _DESTRUCTIVE.finditer(text):
        findings.append(
            Finding(
                rule="destructive",
                severity=Severity.BLOCK,
                detail=f"{where} 里有删除/加密用户文件的操作",
                evidence=match.group(0)[:120],
            )
        )

    for match in _INJECTION.finditer(text):
        # Blocking, not warning: the skill body is injected into a prompt, so text aimed
        # at the model is an attack on the caller's instructions, and a warning would let
        # it through into exactly the place it was designed to reach.
        findings.append(
            Finding(
                rule="prompt-injection",
                severity=Severity.BLOCK,
                detail=f"{where} 里有针对模型的指令（skill 正文会被注入 prompt，这是对调用方的攻击）",
                evidence=match.group(0)[:120],
            )
        )

    if scripted and "import os" in text and "os.environ" not in text:
        findings.append(
            Finding(
                rule="script-touches-environment",
                severity=Severity.WARN,
                detail=f"{where} 里的脚本 import 了 os —— 需要人工确认它拿环境变量做什么",
            )
        )

    return findings


def scan_skill(skill: Skill) -> ScanResult:
    """Check a candidate before it can be stored.

    Scans the body **and every script**. A body-only scan would miss the whole script-type
    risk class, which is the one that actually executes.
    """
    findings = _scan_text(skill.body, where="正文", scripted=False)
    for index, script in enumerate(skill.scripts):
        findings.extend(_scan_text(script, where=f"脚本 #{index + 1}", scripted=True))
    return ScanResult(findings)


__all__ = ["Finding", "ScanResult", "Severity", "scan_skill"]
