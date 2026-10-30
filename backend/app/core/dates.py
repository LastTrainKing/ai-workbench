"""Date/time parsing helpers shared by services and tools."""

from __future__ import annotations

import re
from datetime import UTC, date, datetime, timedelta

from app.core.errors import ValidationError

_RELATIVE = re.compile(
    r"^\+?(\d+)\s*个?\s*(d|day|days|天|日|w|week|weeks|周|m|month|months|月)$", re.I
)


def utcnow() -> datetime:
    return datetime.now(UTC)


def parse_datetime(
    value: str | datetime | None, *, default: datetime | None = None
) -> datetime | None:
    """Parse an ISO-8601 date/datetime or a simple relative expression.

    Accepted forms: ``2026-11-01``, ``2026-11-01T09:30:00Z``,
    ``+7d``, ``30 days``, ``2w``, ``3个月``.
    """
    if value is None or value == "":
        return default
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)

    text = value.strip()
    relative = _RELATIVE.match(text)
    if relative:
        amount = int(relative.group(1))
        unit = relative.group(2).lower()
        if unit.startswith(("d", "天", "日")):
            return utcnow() + timedelta(days=amount)
        if unit.startswith(("w", "周")):
            return utcnow() + timedelta(weeks=amount)
        return utcnow() + timedelta(days=amount * 30)

    cleaned = text.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(cleaned)
    except ValueError:
        try:
            parsed = datetime.combine(date.fromisoformat(cleaned), datetime.min.time())
        except ValueError as exc:
            raise ValidationError(
                f"Unrecognised date value: '{value}'. Use ISO-8601 (2026-11-01) or '+7d'.",
                details={"value": value},
            ) from exc
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def day_bounds(moment: datetime | None = None) -> tuple[datetime, datetime]:
    moment = moment or utcnow()
    start = moment.replace(hour=0, minute=0, second=0, microsecond=0)
    return start, start + timedelta(days=1)


def week_bounds(moment: datetime | None = None) -> tuple[datetime, datetime]:
    moment = moment or utcnow()
    start = moment.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(
        days=moment.weekday()
    )
    return start, start + timedelta(days=7)


def iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None
