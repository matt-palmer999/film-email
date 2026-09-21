"""
Cines Verdi Barcelona scraper.

Uses the official Cines Verdi JSON API:
  https://barcelona.cines-verdi.com/api/get-events

Single HTTP request returns all current films with full performances
(date + time).  Covers both Verdi (C/Verdi 32) and Verdi Park (C/Torrijos 49)
— the API does not separate venues by ID, so both are treated as one cinema.
"""

import logging
from datetime import datetime

import requests

log = logging.getLogger(__name__)

CINEMA_KEY  = "verdi_barcelona"
CINEMA_NAME = "Cines Verdi"
API_URL     = "https://barcelona.cines-verdi.com/api/get-events"

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0.0.0 Safari/537.36"
    ),
    "Referer": "https://barcelona.cines-verdi.com/cartelera/",
}


def _is_vose(film: dict) -> bool:
    version  = (film.get("version")  or "").upper()
    language = (film.get("language") or "").lower()
    return (
        "V.O" in version
        or "VOSE" in version
        or "ORIGINAL" in version
        or language not in ("castellano", "español", "es", "")
    )


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
                "audio_lang":     "EN" if vose else "ES",
                "is_3d":          is_3d,
                "is_imax":        False,
                "is_4dx":         False,
                "booking_url":    p.get("booking_url") or p.get("url") or "",
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

    total = sum(len(f["showtimes"]) for f in results)
    log.info("Cines Verdi Barcelona: %d films, %d sessions", len(results), total)
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
        print(f"  [{vose_tag}] {f['title_es'][:45]:<45} {n:3d} sessions  {d_range}")
