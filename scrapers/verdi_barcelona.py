"""
Cines Verdi Barcelona scraper.

Uses the official Cines Verdi JSON API:
  https://barcelona.cines-verdi.com/api/get-events

Single HTTP request returns all current films with full performances
(date + time).  Covers both Verdi (C/Verdi 32) and Verdi Park (C/Torrijos 49)
— the API does not separate venues by ID, so both are treated as one cinema.

Each session's building is read from its admit-one booking page, which says
e.g. "Cines Verdi Barcelona | Sala D V.Park" (Sala A-D = Verdi Park,
Sala 1-5 = Verdi). It is stored per session as "building": "verdi" | "park",
or None when the page can't be read — the session is always kept.
"""

import logging
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

import requests

log = logging.getLogger(__name__)

CINEMA_KEY  = "verdi_barcelona"
CINEMA_NAME = "Cines Verdi"
API_URL     = "https://barcelona.cines-verdi.com/api/get-events"
SHOP_URL    = "https://verdibcn.admit-one.eu/?p=tickets&s=VERDIBCN&perfCode={code}&language=es"

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0.0.0 Safari/537.36"
    ),
    "Referer": "https://barcelona.cines-verdi.com/cartelera/",
}


def _is_vose(film: dict) -> bool:
    """VOSE only when Verdi's version label says original version, e.g.
    "V.O. SUB. CASTELLANO". The language field is NOT used: it lists every
    language in the film, and "CATALÁN" (dubbed into Catalan) or "VARIOS"
    versions would otherwise be wrongly shown as VOSE."""
    version = (film.get("version") or "").upper()
    return "V.O" in version or "VOSE" in version or "ORIGINAL" in version


_SALA_RE = re.compile(r"Verdi Barcelona\s*\|\s*Sala\s+([0-9A-Za-z]+)(\s+V\.\s*Park)?", re.I)


def parse_building(html: str):
    """'verdi', 'park' or None from a booking page's 'Verdi Barcelona | Sala X' line."""
    m = _SALA_RE.search(html or "")
    if not m:
        return None
    sala = m.group(1).upper()
    if m.group(2) or sala in {"A", "B", "C", "D"}:
        return "park"
    if sala.isdigit():
        return "verdi"
    return None


def _fetch_building(code: str):
    try:
        r = requests.get(SHOP_URL.format(code=code), headers=_HEADERS, timeout=15)
        r.raise_for_status()
        return parse_building(r.text)
    except Exception:
        return None


def lookup_buildings(codes: list[str]) -> dict:
    """{perf code: 'verdi' | 'park' | None}, about 40s for 150 sessions."""
    codes = sorted(set(c for c in codes if c))
    with ThreadPoolExecutor(max_workers=4) as pool:
        found = dict(zip(codes, pool.map(_fetch_building, codes)))
    unknown = [c for c, b in found.items() if not b]
    if unknown:
        log.warning("Verdi Barcelona: building unknown for %d/%d sessions (e.g. %s)",
                    len(unknown), len(codes), ", ".join(unknown[:5]))
    return found


def scrape_verdi_barcelona() -> list[dict]:
    log.info("Fetching Cines Verdi Barcelona API …")
    try:
        resp = requests.get(API_URL, headers=_HEADERS, timeout=15)
        resp.raise_for_status()
    except Exception as exc:
        log.error("Could not fetch Verdi Barcelona API: %s", exc)
        return []

    try:
        data = resp.json()["result"]
    except Exception as exc:
        log.error("Could not parse Verdi Barcelona API response: %s", exc)
        return []

    showing = [f for f in data if f.get("performances") and not f.get("coming_soon")]
    log.info("Verdi Barcelona: %d films with performances", len(showing))

    results: list[dict] = []
    for film in showing:
        title = film.get("locale_title") or film.get("title_name", "")
        if not title:
            continue
        vose     = _is_vose(film)
        en_subs  = "SUB. INGL" in (film.get("version") or "").upper()  # "V.O. SUB. INGLÉS"
        orig     = film.get("title_name") or ""
        imdb_id  = film.get("imdbid") or ""
        synopsis = film.get("synopsis") or ""
        poster   = film.get("poster_url") or film.get("poster") or ""
        runtime  = 0
        try:
            runtime = int(film.get("runtime") or 0)
        except (ValueError, TypeError):
            pass

        fmt   = (film.get("version") or "").strip() or "2D"
        is_3d = "3D" in fmt.upper()

        showtimes: list[dict] = []
        for p in film.get("performances", []):
            raw = p.get("time", "")
            if not raw:
                continue
            try:
                dt = datetime.strptime(raw, "%Y%m%d%H%M")
            except ValueError:
                continue
            date_str = dt.strftime("%Y-%m-%d")
            time_str = dt.strftime("%H:%M")
            showtimes.append({
                "datetime_local": f"{date_str}T{time_str}:00",
                "date":           date_str,
                "time":           time_str,
                "format":         fmt,
                "is_vose":        vose,
                "en_subs":        en_subs,
                "audio_lang":     "EN" if vose else "ES",
                "is_3d":          is_3d,
                "is_imax":        False,
                "is_4dx":         False,
                "booking_url":    SHOP_URL.format(code=p["code"]) if p.get("code") else "",
                "building":       None,
                "_code":          str(p.get("code") or ""),
            })

        if not showtimes:
            continue

        results.append({
            "cinema":         CINEMA_KEY,
            "cinema_name":    CINEMA_NAME,
            "title_es":       title,
            "original_title": orig if orig != title else "",
            "is_vose":        vose,
            "audio_lang":     "EN" if vose else "ES",
            "imdb_id":        imdb_id,
            "is_film":        True,
            "duration_mins":  runtime,
            "poster_url":     poster,
            "synopsis_es":    synopsis,
            "showtimes":      showtimes,
        })

    buildings = lookup_buildings([st["_code"] for f in results for st in f["showtimes"]])
    for f in results:
        for st in f["showtimes"]:
            st["building"] = buildings.get(st.pop("_code"))

    total = sum(len(f["showtimes"]) for f in results)
    n_park = sum(1 for f in results for st in f["showtimes"] if st["building"] == "park")
    log.info("Cines Verdi Barcelona: %d films, %d sessions (%d at Verdi Park)", len(results), total, n_park)
    return results


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    films = scrape_verdi_barcelona()
    total = sum(len(f["showtimes"]) for f in films)
    vose  = [f for f in films if f["is_vose"]]

    print(f"\n{'='*60}")
    print(f"Cines Verdi  --  {len(films)} films  /  {total} sessions")
    print(f"VOSE: {len(vose)}")
    print(f"{'='*60}")

    for f in sorted(films, key=lambda x: x["title_es"]):
        vose_tag = "VOSE" if f["is_vose"] else "    "
        dates    = sorted({s["date"] for s in f["showtimes"]})
        n        = len(f["showtimes"])
        d_range  = f"{dates[0]} -> {dates[-1]}" if dates else "?"
        bl = {"verdi": 0, "park": 0, None: 0}
        for st in f["showtimes"]:
            bl[st["building"]] += 1
        print(f"  [{vose_tag}] {f['title_es'][:45]:<45} {n:3d} sessions  {d_range}  "
              f"verdi={bl['verdi']} park={bl['park']} ?={bl[None]}")
