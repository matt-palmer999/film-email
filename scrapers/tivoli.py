"""
Cine Tívoli scraper.

Small neighbourhood cinema in Burjassot run by Exhicine. The site switched
from server-rendered HTML to a JavaScript/REST approach in mid-2026. Film and
session data now comes from the custom doo/v1 REST API:

  GET https://exhicine.es/wp-json/doo/v1/cinema/8447

VOSE is still indicated by "(V.O.)" in the API title field when present.

Run directly for a quick test summary.
"""

import logging
import re

import requests

log = logging.getLogger(__name__)

CINEMA_KEY   = "tivoli"
CINEMA_NAME  = "Cine Tívoli"
API_URL      = "https://exhicine.es/wp-json/doo/v1/cinema/8447"

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "es-ES,es;q=0.9",
}

_VO_RE = re.compile(r"\s*\(V\.O\.?(?:\s*S\.?)?\)", re.IGNORECASE)


def scrape_tivoli() -> list[dict]:
    """Scrape Cine Tívoli and return a list of film dicts."""
    log.info("Fetching Cine Tívoli cartelera …")
    try:
        r = requests.get(API_URL, headers=_HEADERS, timeout=20)
        r.raise_for_status()
    except Exception as exc:
        log.error("Could not fetch Tívoli API: %s", exc)
        raise

    data = r.json()
    movies = data.get("movies", [])

    results: list[dict] = []
    for movie in movies:
        raw_title = movie.get("title", "").strip()
        if not raw_title:
            continue

        is_film_vose = bool(_VO_RE.search(raw_title))
        title_es = _VO_RE.sub("", raw_title).strip()
        poster = movie.get("image", "")

        showtimes: list[dict] = []
        for session in movie.get("sessions", []):
            date_str = session.get("date", "")
            time_str = session.get("hour", "")
            if not date_str or not time_str:
                continue
            showtimes.append({
                "datetime_local": f"{date_str}T{time_str}:00",
                "date":           date_str,
                "time":           time_str,
                "format":         "VOSE" if is_film_vose else "2D",
                "is_vose":        is_film_vose,
                "audio_lang":     "EN" if is_film_vose else "ES",
                "is_3d":          False,
                "is_imax":        False,
                "is_4dx":         False,
                "booking_url":    "",
            })

        if not showtimes:
            continue

        results.append({
            "cinema":         CINEMA_KEY,
            "cinema_name":    CINEMA_NAME,
            "title_es":       title_es,
            "original_title": "",
            "is_vose":        is_film_vose,
            "audio_lang":     "EN" if is_film_vose else "ES",
            "imdb_id":        "",
            "is_film":        True,
            "duration_mins":  0,
            "poster_url":     poster,
            "synopsis_es":    "",
            "showtimes":      showtimes,
        })

    total = sum(len(f["showtimes"]) for f in results)
    log.info("Tívoli: %d films, %d sessions", len(results), total)
    return results


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    films = scrape_tivoli()
    total = sum(len(f["showtimes"]) for f in films)
    vose  = [f for f in films if f["is_vose"]]

    print(f"\n{'='*60}")
    print(f"Cine Tivoli  --  {len(films)} films  /  {total} sessions")
    print(f"VOSE: {len(vose)}")
    print(f"{'='*60}")

    for f in sorted(films, key=lambda x: x["title_es"]):
        vose_tag = "VOSE" if f["is_vose"] else "    "
        dates    = sorted({s["date"] for s in f["showtimes"] if s["date"]})
        n        = len(f["showtimes"])
        d_range  = f"{dates[0]} -> {dates[-1]}" if dates else "?"
        print(f"  [{vose_tag}] {f['title_es'][:45]:<45} {n:3d} sessions  {d_range}")
