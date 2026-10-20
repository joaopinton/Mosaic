"""Parser delle query `chiave:valore` (sul modello di internal/form/search.go di PhotoPrism).

Esempi:  label:moto year:2024 -color:nero "palco rosso" iso:100-800 person:anna|marco favorite:true
"""

from __future__ import annotations

import re
from dataclasses import dataclass

ALIASES = {
    "tag": "keyword", "tags": "keyword", "keywords": "keyword", "etichetta": "label", "labels": "label",
    "categoria": "category", "persona": "person", "people": "person", "anno": "year", "mese": "month",
    "giorno": "day", "preferiti": "favorite", "cartella": "folder", "nome": "name", "fotocamera": "camera",
    "colore": "color", "tipo": "type", "prima": "before", "dopo": "after", "qualita": "quality",
    "privato": "private", "archivio": "archived", "ordine": "order", "mm": "focal", "f": "fnumber",
    "formato": "ratio", "proporzioni": "ratio", "aspect": "ratio", "megapixel": "mp", "mpx": "mp",
    "larghezza": "width", "altezza": "height", "peso": "mb", "radice": "root",
}

BOOL_KEYS = {"favorite", "private", "archived", "review", "live", "video", "nsfw", "geo", "faces", "portrait",
             "landscape", "square", "photo", "animated", "raw", "duplicate", "missing"}
VALUE_KEYS = {"label", "category", "keyword", "person", "face", "album", "folder", "year", "month", "day", "before",
              "after", "camera", "lens", "iso", "focal", "fnumber", "color", "type", "name", "hash", "quality", "title",
              "caption", "uid", "order", "count", "offset", "reverse", "ratio", "mp", "width", "height", "mb", "root"}
KNOWN_KEYS = BOOL_KEYS | VALUE_KEYS

TOKEN_RE = re.compile(r'(?P<neg>[-!]?)(?:(?P<key>[a-zA-Z_]+):)?(?:"(?P<quoted>[^"]*)"|(?P<plain>\S+))')
TRUE = {"true", "yes", "si", "sì", "1", "on", ""}
FALSE = {"false", "no", "0", "off"}


class QueryError(ValueError):
    """Valore non valido in una query."""


@dataclass(frozen=True)
class Filter:
    key: str
    values: tuple[str, ...]
    negate: bool = False


@dataclass(frozen=True)
class Query:
    filters: tuple[Filter, ...] = ()
    text: str = ""
    raw: str = ""

    def get(self, key: str) -> Filter | None:
        return next((f for f in self.filters if f.key == key), None)


def parse(raw: str) -> Query:
    """Scompone la query in filtri noti e testo libero. Le chiavi sconosciute restano testo libero."""
    filters: list[Filter] = []
    words: list[str] = []
    for match in TOKEN_RE.finditer(raw or ""):
        key = (match["key"] or "").lower()
        key = ALIASES.get(key, key)
        value = match["quoted"] if match["quoted"] is not None else match["plain"]
        if key in KNOWN_KEYS:
            values = tuple(v.strip() for v in re.split(r"[|,]", value) if v.strip()) if key not in ("name", "title", "caption", "root") \
                else (value.strip(),)
            filters.append(Filter(key, values or ("",), negate=bool(match["neg"])))
        else:
            token = (match["neg"] + (match["key"] + ":" if match["key"] else "")) + value
            words.append(token if match["plain"] is not None else match["quoted"])
    return Query(tuple(filters), " ".join(w for w in words if w).strip(), (raw or "").strip())


def to_bool(value: str) -> bool:
    lowered = value.lower()
    if lowered in TRUE:
        return True
    if lowered in FALSE:
        return False
    raise QueryError(f"valore booleano non valido: {value!r}")


def to_range(value: str) -> tuple[float | None, float | None]:
    """'100' → (100,100); '100-800' → (100,800); '100-' → (100,None); '-800' → (None,800); '100+' → (100,None)."""
    text = value.strip()
    try:
        if text.endswith("+"):
            return float(text[:-1]), None
        if re.fullmatch(r"-?\d+(\.\d+)?", text):
            return float(text), float(text)
        low, _, high = text.partition("-")
        return (float(low) if low else None), (float(high) if high else None)
    except ValueError:
        raise QueryError(f"intervallo non valido: {value!r}") from None


def to_ratio(value: str) -> float:
    """'4:3', '16/9', '3x2' o '1.5' → larghezza/altezza (4:3 è orizzontale, 3:4 verticale)."""
    text = value.strip().lower().replace("×", "x")
    try:
        parts = re.split(r"[:/x]", text)
        if len(parts) == 1:
            ratio = float(parts[0])
        elif len(parts) == 2:
            ratio = float(parts[0]) / float(parts[1])
        else:
            raise ValueError(text)
    except (ValueError, ZeroDivisionError):
        raise QueryError(f"formato non valido: {value!r} (usa per esempio 4:3, 16:9 o 1.5)") from None
    if ratio <= 0 or ratio != ratio or ratio == float("inf"):
        raise QueryError(f"formato non valido: {value!r}")
    return ratio


def to_date(value: str) -> str:
    """Normalizza 'YYYY', 'YYYY-MM' o 'YYYY-MM-DD' in una stringa ISO confrontabile."""
    match = re.fullmatch(r"(\d{4})(?:-(\d{1,2}))?(?:-(\d{1,2}))?", value.strip())
    if not match:
        raise QueryError(f"data non valida: {value!r}")
    year, month, day = match.group(1), match.group(2) or "01", match.group(3) or "01"
    if not (1 <= int(month) <= 12 and 1 <= int(day) <= 31):
        raise QueryError(f"data non valida: {value!r}")
    return f"{year}-{int(month):02d}-{int(day):02d}"
