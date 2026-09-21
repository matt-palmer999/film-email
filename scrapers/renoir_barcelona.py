"""
Renoir Floridablanca Barcelona scraper.

The official site (cinesrenoir.com) is server-rendered HTML — no JS runtime
needed.  Showtimes for a given date are at:
  https://www.cinesrenoir.com/cine/renoir-floridablanca/cartelera/?fecha=YYYY-MM-DD

Strategy: fetch 7 days (today + 6), aggregate showtimes per film slug.

Version labels:
  "Versión Original subtitulada a Castellano"  →  VOSE
  "Versión Original Castellano"                →  dubbed
"""

import logging
import re
from datetime import date, timedelta

import requests
from bs4 import BeautifulSoup

log = logging.getLogger(__name__)

CINEMA_KEY  = "renoir_barcelona"
CINEMA_NAME = "Renoir Floridablanca"
BASE_URL    = "https://www.cinesrenoir.com/cine/renoir-floridablanca/cartelera/"
DAYS_AHEAD  = 7

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "es-ES,es;q=0.9",
}

_DURATION_RE = re.compile(r"(\d+)\s*minutos?", re.IGNORECASE)
_DIR_PREFIX  = re.compile(r"^\s*de\s+", re.IGNORECASE)


def _is_vose(version_text: str) -> bool:
    v = version_text.upper()
    return "ORIGINAL" in v and ("SUBTITULAD" in v or "V.O" in v or "VOSE" in v)


def _fetch_day(session: requests.Session, fecha: str) -> list[dict]:
    """Fetch one day's listing; return list of raw film dicts."""
    try:
        r = session.get(BASE_URL, params={"fecha": fecha}, headers=_HEADERS, timeout=15)
        r.raise_for_status()
    except Exception as exc:
        log.warning("Renoir: failed to fetch %s: %s", fecha, exc)
        return []

    soup = BeautifulSoup(r.text, "lxml")
    films: list[dict] = []

    # Use only the desktop blocks to avoid counting each showtime 3×
    for block in soup.select(".my-account-content.mb-15.d-none.d-lg-block"):
        title_el = block.select_one("a[href*='/pelicula/']")
        if not title_el:
            continue
        title    = title_el.get_text(strip=True)
        film_url = title_el.get("href", "")
        slug_m   = re.search(r"/pelicula/([^/]+)/", film_url)
        slug     = slug_m.group(1) if slug_m else ""

        info_col = block.select(".col-4 small")
        director = ""
        version  = ""
        duration = 0
        if len(info_col) >= 1:
            director = _DIR_PREFIX.sub("", info_col[0].get_text(strip=True))
        if len(info_col) >= 2:
            version = info_col[1].get_text(strip=True)
        if len(info_col) >= 3:
            m = _DURATION_RE.search(info_col[2].get_text())
            if m:
                duration = int(m.group(1))

        poster_el = block.select_one("img")
        poster    = poster_el.get("src", "") if poster_el else ""

        vose = _is_vose(version)

        showtimes: list[dict] = []
        for pase in block.select(".pase-cartelera"):
            time_el = pase.select_one("a.btn")
            if not time_el:
                continue
            time_str = time_el.get_text(strip=True)
            if not re.match(r"\d{1,2}:\d{2}", time_str):
                continue
            booking_url = time_el.get("href", "")
            showtimes.append({
                "datetime_local": f"{fecha}T{time_str}:00",
                "date":           fecha,
                "time":           time_str,
                "format":         version or "2D",
                "is_vose":        vose,
                "audio_lang":     "EN" if vose else "ES",
                "is_3d":          "3D" in (version or "").upper(),
                "is_imax":        False,
                "is_4dx":         False,
                "booking_url":    booking_url,
            })

        if showtimes:
            films.append({
                "slug":      slug,
                "title":     title,
                "director":  director,
                "version":   version,
                "duration":  duration,
                "poster":    poster,
                "vose":      vose,
                "showtimes": showtimes,
            })

    return films


def scrape_renoir_barcelona() -> list[dict]:
    log.info("Fetching Renoir Floridablanca Barcelona …")
    today = date.today()
    dates = [(today + timedelta(days=i)).isoformat() for i in range(DAYS_AHEAD)]

    # Aggregate films by slug across all days
    by_slug: dict[str, dict] = {}

    with requests.Session() as sess:
        for fecha in dates:
            day_films = _fetch_day(sess, fecha)
            for film in day_films:
                slug = film["slug"] or film["title"]
                if slug not in by_slug:
                    by_slug[slug] = film.copy()
                    by_slug[slug]["showtimes"] = []
                by_slug[slug]["showtimes"].extend(film["showtimes"])

    results: list[dict] = []
    for slug, film in by_slug.items():
        if not film["showtimes"]:
            continue
        vose = film["vose"]
        results.append({
            "cinema":         CINEMA_KEY,
            "cinema_name":    CINEMA_NAME,
            "title_es":       film["title"],
            "original_title": "",
            "is_vose":        vose,
            "audio_lang":     "EN" if vose else "ES",
            "imdb_id":        "",
            "is_film":        True,
            "duration_mins":  film["duration"],
            "poster_url":     film["poster"],
            "synopsis_es":    "",
            "showtimes":      film["showtimes"],
        })

    total = sum(len(f["showtimes"]) for f in results)
    log.info("Renoir Floridablanca: %d films, %d sessions", len(results), total)
    return results


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    films = scrape_renoir_barcelona()
    total = sum(len(f["showtimes"]) for f in films)
    vose  = [f for f in films if f["is_vose"]]

    print(f"\n{'='*60}")
    print(f"Renoir Floridablanca  --  {len(films)} films  /  {total} sessions")
    print(f"VOSE: {len(vose)}")
    print(f"{'='*60}")

    for f in sorted(films, key=lambda x: x["title_es"]):
        vose_tag = "VOSE" if f["is_vose"] else "    "
        dates    = sorted({s["date"] for s in f["showtimes"]})
        n        = len(f["showtimes"])
        d_range  = f"{dates[0]} -> {dates[-1]}" if dates else "?"
        print(f"  [{vose_tag}] {f['title_es'][:45]:<45} {n:3d} sessions  {d_range}")
