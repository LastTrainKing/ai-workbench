"""Permission model for the computer agent.

This module is the single place that decides whether the agent may touch
something on this machine. It is deliberately independent of the tool
implementations: a tool can claim whatever it likes in its spec, and the executor
still asks here first.

Three ideas carry the whole design:

**Levels, not booleans.** A user does not want to choose between "the agent can
do nothing" and "the agent can do anything". Four levels (observe, workspace,
system, full control) map onto how people actually think about trust, and the
default is the middle of the range: work freely inside the project, ask before
going further.

**Deny beats allow, always.** A path is checked against the denied list before
the allowed list, and the denied list cannot be overridden by a broader allow
entry. That is what keeps ``C:\\Users\\me\\.ssh`` out of reach even when the whole
user directory has been granted.

**Friction is proportional to consequence.** Reading a file in the workspace and
running the test suite never prompt; that is the entire point of having an agent.
Deleting a directory tree, rewriting git history or touching a system path always
prompt, and say what, where, why and what it will cost.
"""

from __future__ import annotations

import ipaddress
import os
import re
import socket
from dataclasses import dataclass, field
from enum import IntEnum, StrEnum
from pathlib import Path
from typing import Any

from app.core.config import runtime_root, settings
from app.core.logging import get_logger

logger = get_logger(__name__)


# --------------------------------------------------------------------------- #
# Vocabulary
# --------------------------------------------------------------------------- #
class PermissionLevel(IntEnum):
    """How much of the machine the agent may reach."""

    OBSERVE = 0  # read files, search, list processes, screenshot
    WORKSPACE = 1  # create/modify files, run tests and dev servers in the workspace
    SYSTEM = 2  # shell commands, install dependencies, manage processes, downloads
    FULL_CONTROL = 3  # broad system access; off by default and user-enabled only

    @classmethod
    def label(cls, value: int) -> str:
        return {
            0: "Observe only",
            1: "Workspace",
            2: "System",
            3: "Full control",
        }.get(int(value), "Unknown")


class RiskLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"

    @property
    def order(self) -> int:
        return {"low": 0, "medium": 1, "high": 2, "critical": 3}[self.value]


class AgentMode(StrEnum):
    """How much autonomy the user has granted."""

    ASSIST = "assist"  # analyse and propose; never execute
    SUPERVISED = "supervised"  # execute; confirm anything risky
    AUTONOMOUS = "autonomous"  # complete the task within the granted scope


class Decision(StrEnum):
    ALLOW = "allow"
    CONFIRM = "confirm"
    DENY = "deny"


@dataclass(slots=True)
class PermissionVerdict:
    """The answer to "may I do this?", with the reasoning kept for the audit log."""

    decision: Decision
    reason: str
    risk: RiskLevel = RiskLevel.LOW
    detail: str = ""
    #: Shown in the confirmation dialog: what will happen, to what, and why.
    operation: str = ""
    target: str = ""
    impact: str = ""

    @property
    def allowed(self) -> bool:
        return self.decision is Decision.ALLOW

    @property
    def needs_confirmation(self) -> bool:
        return self.decision is Decision.CONFIRM

    def as_dict(self) -> dict[str, Any]:
        return {
            "decision": self.decision.value,
            "reason": self.reason,
            "risk": self.risk.value,
            "detail": self.detail,
            "operation": self.operation,
            "target": self.target,
            "impact": self.impact,
        }


def _allow(reason: str, **kw: Any) -> PermissionVerdict:
    return PermissionVerdict(Decision.ALLOW, reason, **kw)


def _confirm(reason: str, **kw: Any) -> PermissionVerdict:
    return PermissionVerdict(Decision.CONFIRM, reason, **kw)


def _deny(reason: str, **kw: Any) -> PermissionVerdict:
    return PermissionVerdict(Decision.DENY, reason, **kw)


# --------------------------------------------------------------------------- #
# Policy
# --------------------------------------------------------------------------- #
def default_workspace() -> Path:
    """The agent's own scratch area, always writable.

    Resolution order: an explicit environment override, then the configured path,
    then ``<project>/.runtime/workspace``.

    The old default was ``%USERPROFILE%\\Desktop\\AI Workbench Workspace``, and it
    was wrong in a way that showed up as a support problem rather than a bug: a
    product whose whole pitch is "your private workspace" was creating its working
    directory at the root of the user's desktop, next to their shortcuts. That is
    the product tidying up by making a mess in someone else's room. The workspace
    belongs to the product, so it lives inside the product's own directory — and
    the path stays configurable for anyone who wants it elsewhere.
    """
    override = os.environ.get("AI_WORKBENCH_AGENT_WORKSPACE")
    if override:
        return Path(override)
    configured = (getattr(settings, "agent_workspace", "") or "").strip()
    if configured:
        return Path(os.path.expandvars(configured))
    return runtime_root() / "workspace"


def project_root() -> Path:
    """The repository this backend is running from.

    ``__file__`` is ``<repo>/backend/app/ai/tools/permissions.py``, so the
    repository root is five levels up. Getting this wrong would silently put the
    project outside its own allowed paths.
    """
    return Path(__file__).resolve().parents[4]


#: The shape of the agent's workspace.
#:
#: Stated once, in one place, because the previous arrangement had no shape at all:
#: the workspace was flat, so a finished deck, a half-written outline, a cloned
#: project's virtualenv and a stray test artifact all sat at the same level. The
#: user's private area contained 1,864 files belonging to a Python virtualenv — not
#: a tidiness problem, but evidence that nothing had decided what the area was for.
WORKSPACE_DIRS: tuple[str, ...] = ("deliverables", "notes", "sources", ".tmp")

#: Where a file produced *for the user* goes when the caller did not say.
#:
#: Deliverables used to land wherever the model happened to point, including the
#: root of the workspace and — once — the desktop itself. "Which of these files is
#: the thing I asked for?" should not be a question the user has to answer by
#: reading filenames.
DELIVERABLES_DIR = "deliverables"


def ensure_workspace_layout(workspace: Path) -> Path:
    """Create the workspace directories. Idempotent, and safe to call every run."""
    for name in WORKSPACE_DIRS:
        (workspace / name).mkdir(parents=True, exist_ok=True)
    return workspace


def default_output_path(name: str) -> str:
    """Route a bare filename into ``deliverables/``; leave an explicit path alone.

    A caller that wrote ``notes/draft.md`` meant it. A caller that wrote ``a.pptx``
    was not making a statement about location, so the product makes one.
    """
    text = str(name or "").strip()
    if not text:
        return text
    normalised = text.replace("\\", "/")
    if "/" in normalised or Path(normalised).is_absolute():
        return text
    return f"{DELIVERABLES_DIR}/{text}"


#: Paths that are never readable by the agent, whatever else is granted.
#: These hold credentials or control the machine, and no agent task needs them.
BUILTIN_DENIED: tuple[str, ...] = (
    r"%USERPROFILE%\.ssh",
    r"%USERPROFILE%\.aws",
    r"%USERPROFILE%\.azure",
    r"%USERPROFILE%\.kube",
    r"%USERPROFILE%\.gnupg",
    r"%USERPROFILE%\.docker\config.json",
    r"%USERPROFILE%\AppData\Local\Google\Chrome\User Data",
    r"%USERPROFILE%\AppData\Local\Microsoft\Edge\User Data",
    r"%USERPROFILE%\AppData\Roaming\Mozilla\Firefox\Profiles",
    r"%USERPROFILE%\AppData\Local\Microsoft\Credentials",
    r"%USERPROFILE%\AppData\Roaming\Microsoft\Credentials",
    r"%USERPROFILE%\AppData\Local\Microsoft\Vault",
    r"%WINDIR%",
    r"%ProgramFiles%",
    r"%ProgramFiles(x86)%",
    r"%ProgramData%",
    # This project's own secrets.
    r"{PROJECT}\.env",
    r"{PROJECT}\.runtime\ai-config.json",
)


@dataclass(slots=True)
class AgentPolicy:
    """The effective permission policy, resolved from settings + runtime overrides."""

    mode: AgentMode = AgentMode.SUPERVISED
    level: PermissionLevel = PermissionLevel.WORKSPACE
    workspace: Path = field(default_factory=default_workspace)
    allowed_paths: list[Path] = field(default_factory=list)
    denied_paths: list[Path] = field(default_factory=list)
    allowed_domains: list[str] = field(default_factory=list)
    blocked_domains: list[str] = field(default_factory=list)
    max_tool_calls: int = 100
    max_runtime_seconds: int = 1800
    max_retries: int = 3
    max_output_bytes: int = 200_000
    max_files_modified: int = 50
    max_download_bytes: int = 200_000_000
    confirmation_timeout_seconds: int = 180

    @classmethod
    def from_settings(cls, **overrides: Any) -> AgentPolicy:
        root = project_root()
        expand = {"PROJECT": str(root), "USERPROFILE": os.environ.get("USERPROFILE", "")}
        expand["WINDIR"] = os.environ.get("WINDIR", r"C:\Windows")
        expand["ProgramFiles"] = os.environ.get("ProgramFiles", r"C:\Program Files")  # noqa: SIM112
        expand["ProgramFiles(x86)"] = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")  # noqa: SIM112
        expand["ProgramData"] = os.environ.get("ProgramData", r"C:\ProgramData")  # noqa: SIM112

        def expand_path(raw: str) -> Path:
            text = raw
            for key, value in expand.items():
                text = text.replace(f"{{{key}}}", value)
                text = text.replace(f"%{key}%", value)
            return Path(os.path.expandvars(text))

        allowed = [Path(p) for p in settings.agent_allowed_path_list]
        allowed.append(root)  # the project itself is always in scope

        denied = [expand_path(p) for p in BUILTIN_DENIED]
        denied += [Path(p) for p in settings.agent_denied_path_list]

        policy = cls(
            mode=AgentMode(str(settings.agent_mode).lower()),
            level=PermissionLevel(int(settings.agent_permission_level)),
            workspace=default_workspace(),
            allowed_paths=allowed,
            denied_paths=denied,
            allowed_domains=settings.agent_allowed_domain_list,
            blocked_domains=settings.agent_blocked_domain_list,
            max_tool_calls=settings.agent_max_tool_calls,
            max_runtime_seconds=settings.agent_max_runtime_seconds,
            max_retries=settings.agent_max_retries,
            max_output_bytes=settings.agent_max_output_bytes,
            max_files_modified=settings.agent_max_files_modified,
            max_download_bytes=settings.agent_max_download_bytes,
            confirmation_timeout_seconds=settings.agent_confirmation_timeout_seconds,
        )
        for key, value in overrides.items():
            if value is None or not hasattr(policy, key):
                continue
            # Coerce on assignment. Without this, passing mode="assist" or
            # level=2 would store a bare str/int, and every later `is
            # AgentMode.ASSIST` or `policy.level >= PermissionLevel.X` comparison
            # would quietly be false — the policy would look applied and not be.
            if key == "mode":
                value = value if isinstance(value, AgentMode) else AgentMode(str(value).lower())
            elif key == "level":
                value = value if isinstance(value, PermissionLevel) else PermissionLevel(int(value))
            elif key == "workspace" and not isinstance(value, Path):
                value = Path(str(value))
            setattr(policy, key, value)
        return policy


# --------------------------------------------------------------------------- #
# Command classification
# --------------------------------------------------------------------------- #
#: Commands that destroy data or change the machine. Each entry carries the
#: reason it is flagged, so the confirmation dialog can explain itself.
_DANGEROUS_COMMANDS: tuple[tuple[re.Pattern[str], RiskLevel, str], ...] = (
    # --- destructive filesystem operations ---------------------------------
    (re.compile(r"\brm\s+(-[a-z]*[rf][a-z]*\s+)+", re.I), RiskLevel.HIGH, "recursive or forced delete"),
    (re.compile(r"\brmdir\s+/s", re.I), RiskLevel.HIGH, "recursive directory removal"),
    (re.compile(r"\bdel\s+/[fsq]", re.I), RiskLevel.HIGH, "forced file deletion"),
    (re.compile(r"\bRemove-Item\b.*-Recurse\b", re.I), RiskLevel.HIGH, "recursive delete"),
    (re.compile(r"\bRemove-Item\b.*-Force\b", re.I), RiskLevel.MEDIUM, "forced delete"),
    (re.compile(r"\bformat\s+[a-z]:", re.I), RiskLevel.CRITICAL, "disk format"),
    (re.compile(r"\bmkfs\b", re.I), RiskLevel.CRITICAL, "filesystem creation"),
    (re.compile(r"\bdiskpart\b", re.I), RiskLevel.CRITICAL, "disk partitioning"),

    # --- process termination -----------------------------------------------
    #
    # Absent until round 15, and the omission was measured rather than guessed: a live
    # call to `/agent/tools/classify-command` returned `risk=low` for
    # `taskkill /F /IM postgres.exe`, while `rm -rf /` and `del /S /Q C:\Windows` were
    # correctly `high`. The classifier modelled file destruction and network access but
    # not killing a process, so an Autonomous run could force-kill a database at `low`.
    #
    # `taskkill /F` without a filter kills by image name — every process with that name,
    # including ones this product did not start. That is the same defect the installer had.
    (re.compile(r"\btaskkill\b", re.I), RiskLevel.HIGH, "terminates processes"),
    (re.compile(r"\bStop-Process\b", re.I), RiskLevel.HIGH, "terminates processes"),
    (re.compile(r"\bkill\s+(-[a-z]*9|-[a-z]*KILL|-[a-z]*TERM)", re.I), RiskLevel.HIGH, "terminates processes"),
    (re.compile(r"\bpkill\b|\bkillall\b", re.I), RiskLevel.HIGH, "terminates processes by name"),
    (re.compile(r"\btskill\b|\bwmic\b.*\bdelete\b", re.I), RiskLevel.HIGH, "terminates processes"),
    (re.compile(r"\btaskkill\b.*\s/IM\s", re.I), RiskLevel.CRITICAL, "kills every process with that image name"),

    # --- git history rewriting ---------------------------------------------
    (re.compile(r"\bgit\s+reset\s+--hard\b", re.I), RiskLevel.HIGH, "discards uncommitted work"),
    (re.compile(r"\bgit\s+clean\s+-[a-z]*[fd]", re.I), RiskLevel.HIGH, "deletes untracked files"),
    (re.compile(r"\bgit\s+push\b.*--force", re.I), RiskLevel.HIGH, "rewrites remote history"),
    (re.compile(r"\bgit\s+filter-branch\b", re.I), RiskLevel.HIGH, "rewrites history"),
    (re.compile(r"\bgit\s+branch\s+-D\b", re.I), RiskLevel.MEDIUM, "force-deletes a branch"),

    # --- system configuration ----------------------------------------------
    (re.compile(r"\breg\s+(add|delete|import)\b", re.I), RiskLevel.HIGH, "modifies the registry"),
    (re.compile(r"\bSet-ItemProperty\b.*HKLM", re.I), RiskLevel.HIGH, "modifies the registry"),
    (re.compile(r"\bsetx\b", re.I), RiskLevel.MEDIUM, "changes persistent environment variables"),
    (re.compile(r"\bicacls\b|\btakeown\b|\bcacls\b", re.I), RiskLevel.HIGH, "changes file permissions"),
    (re.compile(r"\bnet\s+user\b|\bnet\s+localgroup\b", re.I), RiskLevel.CRITICAL, "changes user accounts"),
    (re.compile(r"\bschtasks\b.*\/create", re.I), RiskLevel.HIGH, "creates a scheduled task"),
    (re.compile(r"\bsc\s+(create|delete|config)\b", re.I), RiskLevel.HIGH, "changes a Windows service"),

    # --- power and privilege ------------------------------------------------
    (re.compile(r"\bshutdown\b|\brestart-computer\b|\bstop-computer\b", re.I), RiskLevel.CRITICAL, "powers off the machine"),
    (re.compile(r"\brunas\b", re.I), RiskLevel.CRITICAL, "runs as another user"),
    (re.compile(r"-Verb\s+RunAs", re.I), RiskLevel.CRITICAL, "requests administrator elevation"),
    (re.compile(r"\bStart-Process\b.*-Verb\s+RunAs", re.I), RiskLevel.CRITICAL, "requests administrator elevation"),

    # --- piping a download straight into an interpreter ---------------------
    (re.compile(r"\b(iwr|Invoke-WebRequest|curl|wget)\b.*\|\s*(iex|Invoke-Expression|bash|sh|python)", re.I),
     RiskLevel.CRITICAL, "executes downloaded code"),
    (re.compile(r"\bInvoke-Expression\b", re.I), RiskLevel.HIGH, "evaluates a string as code"),
)

#: Commands that are fine on their own but deserve a note in the audit log.
_NETWORK_COMMANDS = re.compile(r"\b(curl|wget|Invoke-WebRequest|Invoke-RestMethod|git\s+clone|npm\s+install|pip\s+install)\b", re.I)


#: Reasons that are **never** waved through, even in Autonomous mode.
#:
#: Autonomous means "finish the task within the scope I granted". It does not mean "kill
#: my database without telling me". These are the operations whose blast radius reaches
#: outside the task: they affect processes this product did not start, or the machine as a
#: whole. Every one of them still asks.
#:
#: Without this, `taskkill /F /IM postgres.exe` would be HIGH and Autonomous would still
#: auto-allow it — the risk level alone was not enough, because the auto-allow rule is
#: "anything below CRITICAL".
NEVER_AUTOMATIC_REASONS = frozenset(
    {
        "terminates processes",
        "terminates processes by name",
        "kills every process with that image name",
        "modifies the registry",
        "disk format",
        "filesystem creation",
        "disk partitioning",
    }
)


def classify_command(command: str) -> tuple[RiskLevel, str]:
    """Return the risk of a shell command and the reason for it.

    Matching is textual and therefore advisory, not a sandbox: it exists to raise
    the right prompt, not to make an arbitrary command safe. The real limits are
    the permission level, the workspace boundary and the timeout.
    """
    if not command or not command.strip():
        return RiskLevel.LOW, ""

    worst = RiskLevel.LOW
    reasons: list[str] = []
    for pattern, risk, reason in _DANGEROUS_COMMANDS:
        if pattern.search(command):
            if risk.order > worst.order:
                worst = risk
            if reason not in reasons:
                reasons.append(reason)

    if worst is RiskLevel.LOW and _NETWORK_COMMANDS.search(command):
        return RiskLevel.MEDIUM, "downloads from the network"

    return worst, "; ".join(reasons)


# --------------------------------------------------------------------------- #
# Manager
# --------------------------------------------------------------------------- #
class PermissionManager:
    """Answers permission questions. Stateless apart from the policy it holds."""

    def __init__(self, policy: AgentPolicy) -> None:
        self.policy = policy
        # Normalised, case-folded, resolved roots for comparison. Windows paths
        # are case-insensitive, so a raw string comparison would be wrong.
        self._allowed = self._normalise_many([*policy.allowed_paths, policy.workspace])
        self._denied = self._normalise_many(policy.denied_paths)

    # ------------------------------------------------------------- helpers --
    @staticmethod
    def _normalise(path: Path) -> str:
        try:
            resolved = path.expanduser().resolve(strict=False)
        except (OSError, RuntimeError):
            resolved = path.expanduser().absolute()
        return str(resolved).rstrip("\\/").casefold()

    def _normalise_many(self, paths: list[Path]) -> list[str]:
        return [self._normalise(p) for p in paths if str(p).strip()]

    @staticmethod
    def _is_under(candidate: str, root: str) -> bool:
        """True when *candidate* is *root* itself or lives inside it.

        Compared on path components rather than raw string prefixes, so
        ``C:\\work\\app-secrets`` is not treated as being inside ``C:\\work\\app``.
        """
        if candidate == root:
            return True
        return candidate.startswith(root + os.sep) or candidate.startswith(root + "/")

    def _resolve(self, raw: str | Path) -> Path:
        return Path(raw).expanduser()

    # ---------------------------------------------------------------- paths --
    def check_path(self, raw: str | Path, *, write: bool = False) -> PermissionVerdict:
        """Decide whether the agent may read or write a path."""
        try:
            target = self._resolve(raw)
        except (OSError, ValueError) as exc:
            return _deny("The path could not be interpreted", detail=str(exc))

        resolved = self._normalise(target)

        # 1. Denied wins. Checked first and never overridden by an allow entry.
        for denied in self._denied:
            if self._is_under(resolved, denied):
                return _deny(
                    "This location is on the denied list",
                    risk=RiskLevel.HIGH,
                    detail=f"{target} is inside a protected location ({denied}). "
                    "Credentials, system directories and application secrets are never readable.",
                    operation="Access a protected path",
                    target=str(target),
                )

        # 2. Allowed roots.
        inside = any(self._is_under(resolved, root) for root in self._allowed)

        if inside:
            if self.policy.level < PermissionLevel.WORKSPACE and write:
                return _confirm(
                    "Writing requires the Workspace permission level",
                    risk=RiskLevel.LOW,
                    operation="Write a file",
                    target=str(target),
                    impact="The agent is currently restricted to reading.",
                )
            return _allow(
                "Inside an allowed location",
                operation="Write a file" if write else "Read a file",
                target=str(target),
            )

        # 3. Outside every allowed root.
        if self.policy.mode is AgentMode.ASSIST:
            return _deny("Assist mode does not read outside the workspace", target=str(target))

        if not write and self.policy.level >= PermissionLevel.SYSTEM:
            return _allow(
                "Read outside the workspace is permitted at the System level",
                risk=RiskLevel.LOW,
                operation="Read a file",
                target=str(target),
            )

        return _confirm(
            "This path is outside the workspace and the allowed directories",
            risk=RiskLevel.MEDIUM,
            detail=f"{target} is not inside any granted directory.",
            operation="Write a file" if write else "Read a file",
            target=str(target),
            impact="Grant access only if you recognise this location.",
        )

    def is_writable(self, raw: str | Path) -> bool:
        return self.check_path(raw, write=True).allowed

    # -------------------------------------------------------------- domains --
    def check_domain(self, url_or_host: str) -> PermissionVerdict:
        """Decide whether the agent may talk to a host.

        ## Why this is not a string match any more

        It used to be: strip the scheme, take everything before the first `/`, strip
        userinfo, take everything before the first `:`, then suffix-match. That let all of
        the following through, and the first one was **allowlisted by default**:

            http://127.0.0.1:6333/...      localhost was on the default allow-list, so the
                                           agent could reach the app's own vector store —
                                           which has no authentication — without asking
            http://169.254.169.254/...     cloud metadata; not on any list, so it fell to
                                           CONFIRM rather than DENY
            http://[::1]:8000/...          `split(":", 1)[0]` yields "[" — the match then
                                           compares a bracket against domain names
            http://2130706433/...          decimal form of 127.0.0.1; not recognised
            http://10.0.0.5/...            private range; not recognised

        The order is now: **normalise the host, decide whether it is an address we refuse
        outright, and only then consult the lists.** A name that resolves to a loopback or
        private address is refused whatever the allow-list says, because the allow-list is
        written in terms of names and the thing that actually gets connected to is an
        address.

        DNS rebinding is not solved here — the check resolves the name once, and the
        connection resolves it again. That is a known limit, stated rather than implied.
        """
        host = self._normalise_host(url_or_host)
        if not host:
            return _deny("No host could be determined from the request")

        # --- addresses that are never allowed, whatever any list says ---------------
        address = self._as_ip(host)
        if address is not None and self._is_local_address(address):
            return _deny(
                f"{host} is a local or private address ({address}); the agent does not "
                "reach the machine it is running on",
                risk=RiskLevel.HIGH,
                operation="Contact a host",
                target=host,
            )

        for blocked in self.policy.blocked_domains:
            if host == blocked or host.endswith("." + blocked):
                return _deny(
                    "This host is on the blocked list",
                    risk=RiskLevel.HIGH,
                    operation="Contact a host",
                    target=host,
                )

        for allowed in self.policy.allowed_domains:
            allowed_host = self._normalise_host(allowed)
            if host == allowed_host or host.endswith("." + allowed_host):
                # Even an allow-listed name is refused if it points at this machine or the
                # local network. `localhost` in the default list is what made the vector
                # store reachable; a name is not a guarantee about where it goes.
                if self._resolves_to_local(host):
                    return _deny(
                        f"{host} is on the allowed list but resolves to a local or private "
                        "address",
                        risk=RiskLevel.HIGH,
                        operation="Contact a host",
                        target=host,
                    )
                return _allow("Host is on the allowed list", operation="Contact a host", target=host)

        if self.policy.mode is AgentMode.ASSIST:
            return _deny("Assist mode does not make network requests", target=host)

        return _confirm(
            "This host is not on the allowed list",
            risk=RiskLevel.MEDIUM,
            detail=f"{host} has not been granted.",
            operation="Contact a host",
            target=host,
            impact="Only continue if you recognise this domain.",
        )

    @staticmethod
    def _normalise_host(value: str) -> str:
        """Lower-case hostname from a URL, a host, or a host:port.

        Handles the three forms the old one-liner got wrong: bracketed IPv6 (`[::1]`),
        userinfo (`user@host`), and a bare host with no scheme.
        """
        text = (value or "").strip()
        if not text:
            return ""
        if "://" in text:
            text = text.split("://", 1)[1]
        text = text.split("/", 1)[0].split("?", 1)[0]
        if "@" in text:
            text = text.split("@")[-1]
        if text.startswith("["):
            # Bracketed IPv6: the host is inside the brackets, the port after them.
            closing = text.find("]")
            return text[1:closing].strip().lower() if closing != -1 else text.strip("[]").lower()
        # No brackets: a colon separates host from port. An unbracketed IPv6 literal is
        # ambiguous, and refusing to guess is safer than guessing wrong.
        return text.split(":", 1)[0].strip().lower()

    @staticmethod
    def _as_ip(host: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
        """Parse a host that is written as an address, including the odd forms.

        `2130706433` is 127.0.0.1 and `0x7f.1` is 127.0.0.1 — both are accepted by
        browsers and by `urllib`, so a check that only understands dotted quads misses
        them.
        """
        try:
            return ipaddress.ip_address(host)
        except ValueError:
            pass
        # Single decimal integer
        if host.isdigit():
            try:
                return ipaddress.ip_address(int(host))
            except (ValueError, OverflowError):
                return None
        # Dotted forms with hex/octal/decimal parts, e.g. 0x7f.1
        parts = host.split(".")
        if 1 < len(parts) <= 4 and all(parts):
            try:
                numbers = [int(part, 0) for part in parts]
            except ValueError:
                return None
            if all(0 <= number <= 0xFF for number in numbers[:-1]) and numbers[-1] < 256 ** (5 - len(numbers)):
                packed = 0
                for number in numbers:
                    packed = (packed << 8) | number
                try:
                    return ipaddress.ip_address(packed)
                except ValueError:
                    return None
        return None

    @staticmethod
    def _is_local_address(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
        """An address the agent must never reach: this machine, or the local network.

        One predicate, two callers — `check_domain` for a host written as an address, and
        `_resolves_to_local` for a name that resolves to one. Two copies of this list is
        how one of them ends up missing `is_link_local`.
        """
        return bool(
            address.is_loopback
            or address.is_private
            or address.is_link_local
            or address.is_reserved
            or address.is_multicast
            or address.is_unspecified
        )

    @classmethod
    def _resolves_to_local(cls, host: str) -> bool:
        """Does this name resolve to this machine or the local network?

        A resolution failure returns False — the name is then judged by the list alone,
        and the connection itself will fail. Returning True on failure would refuse every
        host whenever DNS is down.
        """
        address = cls._as_ip(host)
        if address is not None:
            return cls._is_local_address(address)
        try:
            infos = socket.getaddrinfo(host, None)
        except OSError:
            return False
        for info in infos:
            try:
                resolved = ipaddress.ip_address(info[4][0])
            except ValueError:
                continue
            if cls._is_local_address(resolved):
                return True
        return False


    # ------------------------------------------------------------- commands --
    def check_command(
        self,
        command: str,
        *,
        cwd: str | Path | None = None,
        interpreter: str = "shell",
    ) -> PermissionVerdict:
        """Decide whether a shell command may run."""
        if self.policy.level < PermissionLevel.SYSTEM:
            return _confirm(
                f"Running {interpreter} commands requires the System permission level",
                risk=RiskLevel.MEDIUM,
                detail="The agent is currently limited to the workspace permission level.",
                operation=f"Run a {interpreter} command",
                target=command[:300],
                impact="Commands can change files and start processes.",
            )

        if cwd is not None:
            cwd_verdict = self.check_path(cwd, write=True)
            if not cwd_verdict.allowed:
                return PermissionVerdict(
                    cwd_verdict.decision,
                    "The working directory is not permitted",
                    risk=cwd_verdict.risk,
                    detail=cwd_verdict.detail,
                    operation=f"Run a {interpreter} command",
                    target=str(cwd),
                    impact=cwd_verdict.impact,
                )

        risk, reason = classify_command(command)
        if risk is RiskLevel.LOW:
            return _allow("Routine command", operation=f"Run a {interpreter} command", target=command[:300])

        # In autonomous mode the user has already accepted the scope, but a
        # critical command still stops: autonomy is not a licence for the
        # handful of operations that can destroy the machine.
        #
        # Neither does it cover the operations in `NEVER_AUTOMATIC_REASONS` — killing
        # processes by name, editing the registry, formatting a disk. Those reach outside
        # the task, so they ask whatever the mode says. The risk level alone was not
        # enough here: `taskkill` was `low`, and even once it became `high` the rule below
        # would have waved it through, because `high < critical`.
        flagged = {part.strip() for part in reason.split(";") if part.strip()}
        if (
            self.policy.mode is AgentMode.AUTONOMOUS
            and risk.order < RiskLevel.CRITICAL.order
            and not (flagged & NEVER_AUTOMATIC_REASONS)
        ):
            return _allow(
                f"Autonomous mode permits this ({reason})",
                risk=risk,
                operation=f"Run a {interpreter} command",
                target=command[:300],
            )

        return _confirm(
            f"Flagged as {risk.value} risk: {reason}",
            risk=risk,
            detail=command[:500],
            operation=f"Run a {interpreter} command",
            target=command[:300],
            impact={
                RiskLevel.MEDIUM: "This changes files or reaches the network.",
                RiskLevel.HIGH: "This can permanently destroy data.",
                RiskLevel.CRITICAL: "This can affect the whole machine or run untrusted code.",
            }[risk],
        )

    # ---------------------------------------------------------------- tools --
    def check_tool(self, spec: Any, arguments: dict[str, Any] | None = None) -> PermissionVerdict:
        """Decide whether a tool may run, given its declared governance."""
        arguments = arguments or {}
        required_level = PermissionLevel(int(getattr(spec, "permission_level", 1)))
        risk = RiskLevel(str(getattr(spec, "risk_level", "low")))
        name = getattr(spec, "name", "unknown")

        if self.policy.mode is AgentMode.ASSIST and getattr(spec, "mutates_state", False):
            return _deny(
                "Assist mode only analyses and proposes",
                risk=risk,
                operation=name,
                target=str(arguments)[:200],
            )

        if self.policy.level < required_level:
            return _confirm(
                f"'{name}' needs the {PermissionLevel.label(required_level)} permission level",
                risk=risk,
                detail=f"Current level: {PermissionLevel.label(int(self.policy.level))}.",
                operation=name,
                target=str(arguments)[:200],
                impact="Raise the level in Settings -> Agent Permissions to allow it.",
            )

        if getattr(spec, "requires_confirmation", False) or risk.order >= RiskLevel.HIGH.order:
            return _confirm(
                f"'{name}' is a {risk.value}-risk operation",
                risk=risk,
                operation=name,
                target=str(arguments)[:200],
                impact="Review the arguments before allowing this.",
            )

        return _allow("Permitted by the current policy", risk=risk, operation=name)

    # ------------------------------------------------------------------ view --
    def describe(self) -> dict[str, Any]:
        """The policy as the Settings screen shows it."""
        return {
            "mode": self.policy.mode.value,
            "level": int(self.policy.level),
            "level_label": PermissionLevel.label(int(self.policy.level)),
            "workspace": str(self.policy.workspace),
            "allowed_paths": [str(p) for p in self.policy.allowed_paths],
            "denied_paths": [str(p) for p in self.policy.denied_paths],
            "allowed_domains": list(self.policy.allowed_domains),
            "blocked_domains": list(self.policy.blocked_domains),
            "limits": {
                "max_tool_calls": self.policy.max_tool_calls,
                "max_runtime_seconds": self.policy.max_runtime_seconds,
                "max_retries": self.policy.max_retries,
                "max_output_bytes": self.policy.max_output_bytes,
                "max_files_modified": self.policy.max_files_modified,
                "max_download_bytes": self.policy.max_download_bytes,
            },
        }


__all__ = [
    "AgentMode",
    "AgentPolicy",
    "Decision",
    "PermissionLevel",
    "PermissionManager",
    "PermissionVerdict",
    "RiskLevel",
    "classify_command",
    "default_workspace",
    "project_root",
]
