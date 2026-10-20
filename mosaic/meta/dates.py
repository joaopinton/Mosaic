"""Date di scatto: EXIF, nome del file e mtime, con fuso orario quando noto."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

_EXIF_RE = re.compile(
    r"^\s*(?P<y>\d{4})[:\-](?P<mo>\d{2})[:\-](?P<d>\d{2})[ T](?P<h>\d{2}):(?P<mi>\d{2}):(?P<s>\d{2})"
    r"(?:\.\d+)?\s*(?P<tz>Z|[+-]\d{2}:?\d{2})?\s*$"
)
_NAME_FULL = re.compile(
    r"(?<!\d)(?P<y>(?:19|20)\d{2})[-_.]?(?P<mo>[01]\d)[-_.]?(?P<d>[0-3]\d)[-_ T.]?(?P<h>[0-2]\d)[-_.:]?(?P<mi>[0-5]\d)[-_.:]?(?P<s>[0-5]\d)(?!\d)"
)
_NAME_DATE = re.compile(r"(?<!\d)(?P<y>(?:19|20)\d{2})[-_.]?(?P<mo>[01]\d)[-_.]?(?P<d>[0-3]\d)(?!\d)")
ISO_UTC = "%Y-%m-%dT%H:%M:%SZ"
ISO_LOCAL = "%Y-%m-%dT%H:%M:%S"


@dataclass(frozen=True)
class Taken:
    utc: str
    local: str
    tz: str  # offset "+02:00" oppure '' se ignoto


def _build(y: str, mo: str, d: str, h: str, mi: str, s: str) -> datetime | None:
    try:
        return datetime(int(y), int(mo), int(d), int(h), int(mi), int(s))
    except ValueError:
        return None


def _parse_offset(text: str | None) -> timedelta | None:
    if not text:
        return None
    text = text.strip()
    if text.upper() == "Z":
        return timedelta(0)
    match = re.fullmatch(r"([+-])(\d{2}):?(\d{2})", text)
    if not match:
        return None
    delta = timedelta(hours=int(match.group(2)), minutes=int(match.group(3)))
    return delta if match.group(1) == "+" else -delta


def _format_offset(delta: timedelta) -> str:
    total = int(delta.total_seconds() // 60)
    sign = "+" if total >= 0 else "-"
    return f"{sign}{abs(total) // 60:02d}:{abs(total) % 60:02d}"


def parse_exif_datetime(value: str | None, offset: str | None = None) -> Taken | None:
    """Interpreta 'YYYY:MM:DD HH:MM:SS[.ff][+HH:MM]'. Senza fuso, UTC = ora locale e tz vuoto."""
    if not value or not isinstance(value, str) or value.startswith("0000"):
        return None
    match = _EXIF_RE.match(value)
    if not match:
        return None
    local = _build(match["y"], match["mo"], match["d"], match["h"], match["mi"], match["s"])
    if local is None or local.year < 1900:
        return None
    delta = _parse_offset(match["tz"]) or _parse_offset(offset)
    if delta is None:
        return Taken(local.strftime(ISO_LOCAL) + "Z", local.strftime(ISO_LOCAL), "")
    utc = (local - delta).replace(tzinfo=UTC)
    return Taken(utc.strftime(ISO_UTC), local.strftime(ISO_LOCAL), _format_offset(delta))


def parse_filename_datetime(name: str) -> Taken | None:
    """Data dal nome (IMG_20230501_123456.jpg, 2023-05-01 12.34.56.png). Solo data → mezzogiorno."""
    stem = name.rsplit(".", 1)[0]
    match = _NAME_FULL.search(stem)
    local = _build(match["y"], match["mo"], match["d"], match["h"], match["mi"], match["s"]) if match else None
    if local is None:
        match = _NAME_DATE.search(stem)
        local = _build(match["y"], match["mo"], match["d"], "12", "00", "00") if match else None
    if local is None or not (1990 <= local.year <= datetime.now().year + 1):
        return None
    return Taken(local.strftime(ISO_LOCAL) + "Z", local.strftime(ISO_LOCAL), "")


def from_mtime(mtime: float) -> Taken:
    local = datetime.fromtimestamp(mtime)
    utc = datetime.fromtimestamp(mtime, UTC)
    return Taken(utc.strftime(ISO_UTC), local.strftime(ISO_LOCAL), "")
