"""Verifica un server Mosaic in esecuzione, endpoint per endpoint: `python scripts/verify_http.py [http://127.0.0.1:8000]`.

Non modifica nulla (solo richieste di lettura). Esce con codice 1 se un controllo fallisce.
"""

from __future__ import annotations

import sys

import httpx

results: list[tuple[bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((ok, name))
    print(f"{'OK  ' if ok else 'FAIL'} {name}{(' — ' + detail) if detail and not ok else ''}")


def main(base: str) -> int:
    http = httpx.Client(base_url=base, timeout=60)
    status = http.get("/api/status")
    check("GET /api/status", status.status_code == 200 and "photos" in status.json())
    check("interfaccia servita su /", http.get("/").status_code == 200 and "<div id=\"app\">" in http.get("/").text)
    check("host non consentito rifiutato", http.get("/api/status", headers={"host": "evil.example"}).status_code == 400)
    check("POST da un'altra origine rifiutato", http.post("/api/library/index", json={}, headers={"origin": "https://evil.example"}).status_code == 403)

    listing = http.get("/api/photos", params={"count": 100}).json()
    photos = listing["photos"]
    check("GET /api/photos", listing["total"] == len(photos) > 0, f"total={listing['total']}")
    by_type = {}
    for p in photos:
        by_type.setdefault(p["type"], p)
    image = by_type.get("image")
    if image:
        thumb = http.get(f"/t/{image['hash']}/tile_224")
        check("miniatura tile_224 (JPEG, cache immutabile)", thumb.status_code == 200 and thumb.headers["content-type"] == "image/jpeg"
              and "immutable" in thumb.headers.get("cache-control", ""))
        big = http.get(f"/t/{image['hash']}/fit_2560")
        check("miniatura grande creata su richiesta", big.status_code == 200 and len(big.content) > 1000)
        part = http.get(f"/api/files/{image['uid']}/original", headers={"range": "bytes=0-99"})
        check("originale con Range (206)", part.status_code == 206 and len(part.content) == 100)
        detail = http.get(f"/api/photos/{image['uid']}").json()
        check("dettaglio foto", detail["uid"] == image["uid"] and detail["files"] and "labels" in detail)
        sim = http.get(f"/api/photos/{image['uid']}/similar", params={"k": 4}).json()
        check("foto simili con motivi", sim["ready"] and len(sim["hits"]) > 0 and all("reasons" in h for h in sim["hits"]),
              str(sim)[:120])
    video = by_type.get("video")
    if video:
        v = http.get(f"/api/videos/{video['uid']}", headers={"range": "bytes=0-199"})
        check("video con Range", v.status_code in (200, 206) and v.headers["content-type"].startswith("video/"), str(v.status_code))
    live = by_type.get("live")
    if live:
        check("video della Live Photo", http.get(f"/api/live/{live['uid']}").status_code == 200)
    check("ricerca semantica in italiano", http.get("/api/photos", params={"q": "paesaggio con un lago"}).json()["total"] > 0)
    check("ricerca con filtri", http.get("/api/photos", params={"q": "type:video"}).status_code == 200)
    check("query non valida → 400", http.get("/api/photos", params={"order": "boh"}).status_code == 400)
    check("etichette", http.get("/api/labels").status_code == 200)
    check("album", http.get("/api/albums").status_code == 200)
    check("calendario", http.get("/api/calendar").status_code == 200)
    check("persone", http.get("/api/subjects").status_code == 200)
    check("duplicati identici", http.get("/api/duplicates").status_code == 200)
    check("duplicati quasi identici", http.get("/api/duplicates", params={"kind": "near"}).status_code == 200)
    check("anteprima rinomina", http.post("/api/rename/preview", json={}).status_code == 200)
    check("esportazione JSON", http.get("/api/export", params={"format": "json"}).status_code == 200)
    check("suggerimenti di ricerca", http.get("/api/search/suggest", params={"q": "lab"}).status_code == 200)
    check("percorso inesistente → 404 JSON", http.get("/api/non-esiste").status_code == 404)
    failed = [name for ok, name in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} controlli riusciti")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"))
