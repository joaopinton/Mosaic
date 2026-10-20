"""JSON di Google Takeout (IMG_1.jpg.json / IMG_1.jpg.supplemental-metadata.json)."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from mosaic.meta.dates import ISO_LOCAL, ISO_UTC, Taken

MAX_BYTES = 1 << 20


@dataclass(frozen=True)
class TakeoutData:
    taken: Taken | None = None
    lat: float | None = None
    lng: float | None = None
    altitude: float | None = None
    title: str = ""
    caption: str = ""


def parse(path: Path) -> TakeoutData | None:
    """Ritorna i dati se il file è un JSON di Takeout, altrimenti None (può essere un JSON qualunque)."""
    try:
        if path.stat().st_size > MAX_BYTES:
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or ("photoTakenTime" not in data and "creationTime" not in data):
        return None
    taken = None
    stamp = (data.get("photoTakenTime") or data.get("creationTime") or {}).get("timestamp")
    try:
        moment = datetime.fromtimestamp(int(stamp), UTC)
        taken = Taken(moment.strftime(ISO_UTC), moment.strftime(ISO_LOCAL), "+00:00")
    except (TypeError, ValueError, OverflowError, OSError):
        pass
    geo = data.get("geoData") or {}
    lat, lng = geo.get("latitude"), geo.get("longitude")
    valid = all(isinstance(v, (int, float)) and math.isfinite(v) for v in (lat, lng)) and not (lat == 0 and lng == 0)
    return TakeoutData(
        taken=taken,
        lat=float(lat) if valid else None,
        lng=float(lng) if valid else None,
        altitude=float(geo["altitude"]) if valid and isinstance(geo.get("altitude"), (int, float)) else None,
        title="" if str(data.get("title", "")).lower().endswith((".jpg", ".jpeg", ".png", ".heic", ".mp4", ".mov")) else str(data.get("title", "")).strip(),
        caption=str(data.get("description", "")).strip(),
    )
