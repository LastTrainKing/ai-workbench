"""FIND and ASSESS: going out to public sources, and deciding what is worth showing.

The product's claim is that it does not wait for a new user to accumulate behaviour — it
goes and finds the ways of working that fit, on the first day. That is the difference
between a skill library that is useful immediately and one that is empty for months.

    FIND    turn the user's need into search terms, ask GitHub
    ASSESS  drop what cannot work, rank what can, and say why each one fits *this person*

Two things this module is careful about:

* **It reuses the existing permission gate.** `PermissionPolicy.check_domain` already
  decides what this system may talk to; a second allow-list here would be a second
  decision about the same question, and the two would drift. Discovery is a network
  caller like any other, so it goes through the same door.
* **Assessment explains itself.** A shortlist without reasons is a search result. The
  product's assess stage exists so a person chooses, and they cannot choose from a list
  of names — so every candidate carries why it was kept and, when it was dropped, why.
"""

from __future__ import annotations

import base64
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import httpx

from app.ai.skills.schema import Skill, parse_skill
from app.core.errors import PermissionDeniedError, ValidationError
from app.core.logging import get_logger

logger = get_logger(__name__)

GITHUB_SEARCH = "https://api.github.com/search/code"
GITHUB_REPO = "https://api.github.com/repos"

#: GitHub's code search needs a token and is rate-limited hard without one. Repo search is
#: open, so discovery starts there and only reaches for file contents when it can.
#: Recorded as a limitation rather than hidden: without a token the candidate pool is
#: smaller, not absent.
USER_AGENT = "ai-workbench-skill-discovery"

#: Files a skill is likely to live in. Narrowing the search keeps the result set about
#: skills rather than about repositories that happen to contain the word.
SKILL_HINTS = ("SKILL.md", "skill.md", "AGENTS.md", "prompt.md")


@dataclass
class Candidate:
    """One thing worth showing a person, with the reason it was kept."""

    name: str
    repo: str
    path: str
    url: str
    commit: str = ""
    license: str = ""
    stars: int = 0
    updated_at: str = ""
    #: Why this fits *this* user. Filled by `assess`.
    why: str = ""
    #: Set when the candidate was dropped, and why. Kept so the report can show what was
    #: rejected rather than only what survived.
    dropped_because: str = ""
    skill: Skill | None = None


@dataclass
class DiscoveryResult:
    queries: list[str] = field(default_factory=list)
    kept: list[Candidate] = field(default_factory=list)
    dropped: list[Candidate] = field(default_factory=list)
    error: str = ""

    def summary(self) -> str:
        if self.error:
            return f"搜索失败：{self.error}"
        return f"用 {len(self.queries)} 个搜索词找到 {len(self.kept) + len(self.dropped)} 个候选，保留 {len(self.kept)} 个"


# ------------------------------------------------------------------------- FIND --


def build_queries(profile: dict[str, Any], need: str, *, limit: int = 3) -> list[str]:
    """Turn a need plus the user's profile into GitHub search terms that actually work.

    **Measured, not assumed.** The first version searched for the user's own words, and
    returned zero candidates every time:

        '帮我写毕业论文的文献综述 skill'   -> 0 results
        '人工智能 SKILL.md'               -> 0 results

    GitHub's repository search matches repository names and descriptions, which are
    overwhelmingly English. A Chinese phrase cannot match one, so a query built from what
    the user typed is a query that returns nothing — and "found nothing" is
    indistinguishable from "there is nothing", which is the worst possible failure for a
    discovery stage.

    What works, measured against the live API:

        topic:agent-skills      -> 29,319 hits, top anthropics/skills (179,886 stars)
        topic:claude-skills     -> 10,110 hits
        agent skill markdown    ->  1,903 hits

    So the topics are the floor and the user's own ASCII terms are the refinement. The
    personalisation then happens in `assess`, which scores the pool against the profile —
    which is where it belongs anyway, because ranking a pool is a different job from
    finding one, and the profile can judge a candidate it could never have found.
    """
    terms: list[str] = []

    # The topic floor. Always present, so the pool is never empty for a Chinese-language
    # user — who is the expected user, and who would otherwise always get nothing.
    topics = ["topic:agent-skills", "topic:claude-skills"]

    # ASCII words from the need and the profile, which GitHub can match.
    ascii_words = re.findall(r"[A-Za-z][A-Za-z0-9+#.-]{2,}", f"{need} {profile.get('domain', '')}")
    ascii_words = [w for w in ascii_words if w.lower() not in {"the", "and", "for", "with", "skill"}]
    if ascii_words:
        terms.append(" ".join(ascii_words[:4]))

    terms.extend(topics)

    # The user's own language, kept last and only as a refinement. It will usually return
    # nothing; when it returns something it is worth more than the topics, so it goes
    # first rather than being dropped.
    need_words = " ".join(re.findall(r"[\w\u4e00-\u9fff]+", need or ""))[:40].strip()
    if need_words and any("\u4e00" <= c <= "\u9fff" for c in need_words):
        terms.insert(0, f"{need_words} skill")

    return terms[:limit]


async def _authorise(permissions: Any, url: str) -> None:
    """The same gate every other network caller uses.

    **A missing policy is a denial, not a pass.** This used to return early when
    ``permissions`` was None, and the one caller that existed — ``POST /skills/discover``
    — never passed a policy, so the domain gate on that path was never executed at all.
    The URLs were hard-coded to GitHub, so it was not exploitable as written; but a check
    that is skipped whenever it is inconvenient is not a check, and the next caller would
    not have had that accident protecting it.
    """
    if permissions is None:
        raise PermissionDeniedError(
            "该请求没有权限策略，按安全默认值拒绝",
            details={"url": url, "reason": "no_permission_policy"},
        )
    verdict = permissions.check_domain(url)
    if not verdict.allowed:
        raise PermissionDeniedError(
            verdict.reason,
            details={"url": url, "host": url, "risk": verdict.risk.value},
        )


#: How many redirects a single fetch may follow. Each one is re-authorised, so this is a
#: loop bound rather than a policy.
_MAX_REDIRECTS = 3
_REDIRECT_CODES = frozenset({301, 302, 303, 307, 308})


async def _get_checked(
    client: httpx.AsyncClient, url: str, *, permissions: Any, headers: dict[str, str]
) -> httpx.Response:
    """GET, following a redirect only after checking the hop it points at.

    ``follow_redirects=True`` checks nothing. The first request is authorised and the
    redirect target never is, so an allowed host can hand the client to ``127.0.0.1`` or
    to a cloud metadata address and the only check that ran is the one that passed. Every
    hop is resolved and authorised here, before it is requested.
    """
    current = url
    for _ in range(_MAX_REDIRECTS + 1):
        await _authorise(permissions, current)
        response = await client.get(current, headers=headers)
        if response.status_code not in _REDIRECT_CODES:
            return response
        location = response.headers.get("location")
        if not location:
            return response
        current = str(httpx.URL(current).join(location))
    raise PermissionDeniedError(
        f"重定向超过 {_MAX_REDIRECTS} 次，已停止",
        details={"url": url},
    )


async def search_github(
    query: str, *, permissions: Any = None, token: str = "", limit: int = 5
) -> list[Candidate]:
    """Search GitHub for skill files matching a query.

    Uses the repository search API, which is open, rather than code search, which needs a
    token. Without a token the pool is smaller — that is recorded as a limitation rather
    than worked around by scraping, which would break the first time GitHub changed its
    HTML.
    """
    url = "https://api.github.com/search/repositories"
    await _authorise(permissions, url)

    headers = {"Accept": "application/vnd.github+json", "User-Agent": USER_AGENT}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    try:
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.get(
                url,
                params={"q": query, "sort": "stars", "order": "desc", "per_page": limit},
                headers=headers,
            )
    except httpx.HTTPError as exc:
        # Reported, not swallowed into an empty list: "the search failed" and "the search
        # found nothing" are different facts and only one of them is actionable.
        logger.warning("skill_search_failed", extra={"query": query, "error": str(exc)})
        raise ValidationError(f"搜索 GitHub 失败：{exc}") from exc

    if response.status_code == 403:
        raise ValidationError("GitHub 限流或需要 token（未配置 GITHUB_TOKEN 时候选池更小）")
    if response.status_code >= 400:
        raise ValidationError(f"GitHub 返回 {response.status_code}")

    payload = response.json()
    candidates: list[Candidate] = []
    for item in payload.get("items", []):
        repo = item.get("full_name", "")
        candidates.append(
            Candidate(
                name=item.get("name", repo.split("/")[-1]),
                repo=repo,
                path="",
                url=item.get("html_url", ""),
                license=(item.get("license") or {}).get("spdx_id", "") or "",
                stars=int(item.get("stargazers_count") or 0),
                updated_at=str(item.get("pushed_at") or ""),
                commit=str(item.get("default_branch") or ""),
            )
        )
    return candidates


async def resolve_commit(repo: str, *, permissions: Any = None, token: str = "") -> str:
    """The commit SHA at the tip of a repository's default branch.

    `ref="HEAD"` is not a commit hash — it is a moving pointer, and a skill recorded
    against it cannot be reproduced, because the next fetch may return different content.
    The product's rule is that the source must be traceable and reproducible, so the SHA
    is resolved and recorded.

    Returns an empty string when it cannot be resolved. That is deliberate and it is the
    honest value: a caller that needs the SHA can see it is missing, whereas a fallback to
    `"HEAD"` would look like a recorded commit and be nothing of the kind.
    """
    url = f"{GITHUB_REPO}/{repo}"
    try:
        await _authorise(permissions, url)
    except PermissionDeniedError:
        return ""

    headers = {"Accept": "application/vnd.github+json", "User-Agent": USER_AGENT}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    try:
        async with httpx.AsyncClient(timeout=20) as client:
            # Two calls, because the repository endpoint returns the branch *name*
            # (`default_branch`), not a commit. The first version read a
            # `default_branch_sha` field that does not exist, which would have produced an
            # empty string and been recorded as "no commit" — a missing value that looks
            # like an honest gap rather than a wrong lookup.
            repo_response = await client.get(url, headers=headers)
            if repo_response.status_code >= 400:
                logger.warning(
                    "commit_resolve_http", extra={"repo": repo, "status": repo_response.status_code}
                )
                return ""
            branch = repo_response.json().get("default_branch") or "main"

            commit_response = await client.get(
                f"{GITHUB_REPO}/{repo}/commits/{branch}", headers=headers
            )
            if commit_response.status_code >= 400:
                logger.warning(
                    "commit_resolve_http",
                    extra={"repo": repo, "branch": branch, "status": commit_response.status_code},
                )
                return ""
            return str(commit_response.json().get("sha") or "")
    except httpx.HTTPError as exc:
        logger.warning("commit_resolve_failed", extra={"repo": repo, "error": _describe(exc, url)})
        return ""


def _describe(exc: Exception, url: str) -> str:
    """A network error with a message, always.

    `httpx.ConnectError` stringifies to the empty string, so logging `str(exc)` produced a
    record that said nothing at all — which is nearly as bad as swallowing it. The type and
    the URL are the two things that make it actionable.
    """
    detail = str(exc).strip()
    return f"{type(exc).__name__} to {url}" + (f": {detail}" if detail else " (no message from httpx)")


async def fetch_skill_file(
    repo: str, path: str, *, ref: str = "", permissions: Any = None, token: str = ""
) -> tuple[Skill, list[str]] | None:
    """Fetch one skill file and parse it.

    Returns None when the file is not a skill this system can use, so the caller can count
    it as dropped rather than as an error.

    `ref` defaults to the resolved commit SHA rather than `"HEAD"`. When it cannot be
    resolved the fetch still runs, and `source_commit` is left empty — visibly missing
    rather than filled with a pointer that looks like a hash.
    """
    commit = ref or await resolve_commit(repo, permissions=permissions, token=token)

    # Fetched through the Contents API on `api.github.com`, not from
    # `raw.githubusercontent.com`.
    #
    # Measured, because the first version used the raw host and failed:
    #
    #     https://raw.githubusercontent.com/...  -> HTTP 000 (unreachable)
    #     https://api.github.com/repos/...       -> HTTP 200
    #
    # The raw host is not reachable from this environment at all, so every fetch failed
    # while every search succeeded — a combination that looks like "the files are missing"
    # rather than "one host is blocked". The API host is the one that works, and it returns
    # the content base64-encoded with the blob SHA, which is better provenance anyway.
    url = f"{GITHUB_REPO}/{repo}/contents/{path}"
    if commit:
        url += f"?ref={commit}"
    await _authorise(permissions, url)

    headers = {"Accept": "application/vnd.github+json", "User-Agent": USER_AGENT}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    try:
        # `follow_redirects=False` on the client, because the redirect target is checked
        # per hop inside `_get_checked` rather than handed to httpx to follow blindly.
        async with httpx.AsyncClient(timeout=20, follow_redirects=False) as client:
            response = await _get_checked(client, url, permissions=permissions, headers=headers)
    except httpx.HTTPError as exc:
        # Reported, not turned into None. "Could not reach it" and "it is not a skill" are
        # different facts, and collapsing them is how a network failure becomes invisible.
        raise ValidationError(f"抓取失败：{_describe(exc, url)}") from exc

    if response.status_code >= 400:
        return None

    payload = response.json()
    if isinstance(payload, list):
        # A directory, not a file. Not a skill.
        return None
    encoded = payload.get("content") or ""
    try:
        text = base64.b64decode(encoded).decode("utf-8", errors="replace")
    except (ValueError, TypeError) as exc:
        raise ValidationError(f"仓库返回的内容无法解码：{exc}") from exc

    # The blob SHA identifies the exact bytes fetched, which the branch tip does not when
    # the file has changed since. Recorded when present.
    blob_sha = str(payload.get("sha") or "")

    skill, validation = parse_skill(
        text, source=f"https://github.com/{repo}", source_commit=commit or blob_sha
    )
    return skill, validation.errors + validation.warnings


# ------------------------------------------------------------------------ ASSESS --


def _days_since(stamp: str) -> int | None:
    if not stamp:
        return None
    try:
        when = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except ValueError:
        return None
    return (datetime.now(UTC) - when).days


def assess(
    candidate: Candidate, profile: dict[str, Any], *, max_age_days: int = 730
) -> Candidate:
    """Decide whether to keep a candidate, and say why.

    The reasons are the point. "Why this one" is what turns a search result into something
    a person can choose from, and the dropped list is what lets them see the filter was
    real rather than decorative.
    """
    age = _days_since(candidate.updated_at)

    if age is not None and age > max_age_days:
        candidate.dropped_because = f"超过 {max_age_days} 天没更新（{age} 天）"
        return candidate

    if not candidate.license:
        # Not fatal for a document-type skill, but it has to be visible: the licence
        # question is the user's to answer, and hiding it would make the decision for them.
        candidate.dropped_because = "没有声明 license，无法确认能否合法使用"
        return candidate

    if candidate.stars < 2:
        candidate.dropped_because = f"star 太少（{candidate.stars}），可信度不足"
        return candidate

    reasons = [f"{candidate.stars} star"]
    if age is not None:
        reasons.append(f"{age} 天前更新")
    reasons.append(f"license {candidate.license}")

    domain = str(profile.get("domain") or "").strip()
    stage = str(profile.get("stage") or "").strip()
    haystack = f"{candidate.name} {candidate.repo}".lower()
    for field_name, value in (("领域", domain), ("阶段", stage)):
        if value and value.lower() in haystack:
            reasons.append(f"与你的{field_name}「{value}」吻合")

    candidate.why = "；".join(reasons)
    return candidate


def shortlist(candidates: list[Candidate], profile: dict[str, Any], *, keep: int = 5) -> tuple[list[Candidate], list[Candidate]]:
    """Assess everything, then split into kept and dropped, best first."""
    assessed = [assess(c, profile) for c in candidates]
    kept = sorted([c for c in assessed if not c.dropped_because], key=lambda c: c.stars, reverse=True)
    dropped = [c for c in assessed if c.dropped_because]
    return kept[:keep], dropped


async def discover(
    *, profile: dict[str, Any], need: str, permissions: Any = None, token: str = "", per_query: int = 5
) -> DiscoveryResult:
    """The whole FIND + ASSESS pass, with failures reported rather than hidden."""
    result = DiscoveryResult(queries=build_queries(profile, need))

    pool: list[Candidate] = []
    errors: list[str] = []
    for query in result.queries:
        try:
            pool.extend(await search_github(query, permissions=permissions, token=token, limit=per_query))
        except ValidationError as exc:
            # One query failing is not the whole search failing; both are reported.
            errors.append(str(exc))

    if not pool and errors:
        result.error = errors[0]
        return result

    result.kept, result.dropped = shortlist(pool, profile)
    if errors:
        result.error = f"部分搜索词失败：{errors[0]}"
    return result


__all__ = [
    "Candidate",
    "DiscoveryResult",
    "assess",
    "build_queries",
    "discover",
    "fetch_skill_file",
    "search_github",
    "shortlist",
]
