"""Parsing of dates as Moodle displays them (German, English, French, Italian)."""

from __future__ import annotations

import re
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

_MONTHS = {
    # de
    "januar": 1, "jänner": 1, "februar": 2, "märz": 3, "maerz": 3, "april": 4, "mai": 5, "juni": 6,
    "juli": 7, "august": 8, "september": 9, "oktober": 10, "november": 11, "dezember": 12,
    # en
    "january": 1, "february": 2, "march": 3, "may": 5, "june": 6, "july": 7, "october": 10,
    "december": 12,
    # fr
    "janvier": 1, "février": 2, "fevrier": 2, "mars": 3, "avril": 4, "juin": 6, "juillet": 7,
    "août": 8, "aout": 8, "septembre": 9, "octobre": 10, "novembre": 11, "décembre": 12,
    # it
    "gennaio": 1, "febbraio": 2, "marzo": 3, "aprile": 4, "maggio": 5, "giugno": 6, "luglio": 7,
    "agosto": 8, "settembre": 9, "ottobre": 10, "dicembre": 12,
}

# "Sonntag, 8. November 2026, 23:59" / "Sunday, 8 November 2026, 11:59 PM" / "8 novembre 2026, 23:59"
_DATE_RE = re.compile(
    r"(?P<day>\d{1,2})\.?\s+(?P<month>[^\W\d_]+)\.?\s+(?P<year>\d{4})"
    r"(?:\D{0,6}?(?P<hour>\d{1,2})[:.](?P<minute>\d{2})\s*(?P<ampm>[AaPp]\.?[Mm]\.?)?)?"
)


def parse_moodle_date(text: str, tz: ZoneInfo) -> datetime | None:
    """Parse the first date in `text`; returns an aware datetime or None."""
    m = _DATE_RE.search(text)
    if not m:
        return None
    month = _MONTHS.get(m["month"].lower())
    if month is None:
        return None
    hour = int(m["hour"]) if m["hour"] else 0
    minute = int(m["minute"]) if m["minute"] else 0
    if m["ampm"]:
        pm = m["ampm"].lower().startswith("p")
        hour = hour % 12 + (12 if pm else 0)
    try:
        return datetime(int(m["year"]), month, int(m["day"]), hour, minute, tzinfo=tz)
    except ValueError:
        return None


def from_timestamp(value: object) -> datetime | None:
    if isinstance(value, str) and value.isdigit():
        value = int(value)
    return datetime.fromtimestamp(value, UTC) if isinstance(value, int) and value > 0 else None


def parse_since(value: str, tz: ZoneInfo) -> datetime:
    """Accepts ISO dates/datetimes ('2026-09-14', '2026-09-14T08:00') or relative
    values like '7d', '12h'. Naive values are interpreted in the Moodle timezone."""
    value = value.strip()
    rel = re.fullmatch(r"(\d+)\s*([dhw])", value.lower())
    if rel:
        amount = int(rel[1])
        unit = {"d": timedelta(days=1), "h": timedelta(hours=1), "w": timedelta(weeks=1)}[rel[2]]
        return datetime.now(UTC) - amount * unit
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        parsed = datetime.combine(date.fromisoformat(value), time())
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=tz)
