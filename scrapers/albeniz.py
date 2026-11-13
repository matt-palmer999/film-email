"""
Cine Albéniz scraper (Málaga, C/ Alcazabilla 4 — municipal arthouse cinema run
by the Festival de Málaga; 4 screens).

cinealbeniz.com is WordPress. There's no sessions API, but every film page
(/pelicula/{slug}/) carries Yoast JSON-LD with a Movie and one ScreeningEvent
per session: startDate (ISO with offset) and offers.url (the Unientradas page
for that film — tickets are sold per film, not per session).

Film pages are found from the links on the homepage and /cartelera/.
Language is a heading on the film page: "VOSE" for foreign films, "Castellano"
for Spanish ones (which are in their original version anyway).
"""

import html
import json
import logging
import re
from datetime import datetime

import requests

log = logging.getLogger(__name__)

CINEMA_KEY  = "albeniz"
CINEMA_NAME = "Cine Albéniz"
BASE        = "https://cinealbeniz.com"
INDEX_PAGES = [f"{BASE}/", f"{BASE}/cartelera/"]

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "es-ES,es;q=0.9",
}

_FILM_LINK_RE = re.compile(r'href="(https://cinealbeniz\.com/pelicula/[^"/#?]+/)"')
_LANG_RE      = re.compile(r'<h5[^>]*>\s*(VOSE|V\.O\.S\.E\.?|VOS|VO|Castellano|Español)\s*</h5>', re.I)
_DURATION_RE  = re.compile(r'PT(?:(\d+)H)?(?:(\d+)M)?')


def _film_links(sess: requests.Session) -> list[str]:
    links: set = set()
    for url in INDEX_PAGES:
        r = sess.get(url, headers=_HEADERS, timeout=20)
        r.raise_for_status()
        links |= set(_FILM_LINK_RE.findall(r.text))
    links.discard(f"{BASE}/pelicula/feed/")
    return sorted(links)


def _jsonld_nodes(page: str) -> list[dict]:
    nodes = []
    for block in re.findall(r'<script[^>]*application/ld\+json[^>]*>(.*?)</script>', page, re.S):
        try:
            data = json.loads(block)
        except ValueError:
            continue
        for item in (data if isinstance(data, list) else [data]):
            nodes.extend(item.get("@graph", [item]))
    return nodes


def _duration_mins(iso: str) -> int:
    m = _DURATION_RE.fullmatch(iso or "")
    return (int(m.group(1) or 0) * 60 + int(m.group(2) or 0)) if m else 0


def _parse_film(page: str, url: str) -> dict | None:
    nodes  = _jsonld_nodes(page)
    movie  = next((n for n in nodes if n.get("@type") == "Movie"), None)
    events = [n for n in nodes if n.get("@type") == "ScreeningEvent" and n.get("startDate")]
    if not movie or not events:
        return None

    lang = _LANG_RE.search(page)
    is_vose = bool(lang) and lang.group(1).upper().replace(".", "").startswith("VO")

    showtimes, seen = [], set()
    for ev in events:
        try:
            dt = datetime.fromisoformat(ev["startDate"])
        except ValueError:
            continue
        key = dt.strftime("%Y-%m-%dT%H:%M")
        if key in seen:
            continue
        seen.add(key)
        showtimes.append({
            "date":           dt.strftime("%Y-%m-%d"),
            "time":           dt.strftime("%H:%M"),
            "datetime_local": dt.strftime("%Y-%m-%dT%H:%M:00"),
            "format":         "VOSE" if is_vose else "Digital",
            "is_vose":        is_vose,
            "audio_lang":     "EN" if is_vose else "ES",
            "is_3d": False, "is_imax": False, "is_4dx": False, "is_screenx": False,
            "booking_url":    ((ev.get("offers") or {}).get("url") or url),
        })
    if not showtimes:
        return None
    showtimes.sort(key=lambda s: s["datetime_local"])

    image = movie.get("image")
    if isinstance(image, dict):
        image = image.get("url")
    return {
        "cinema":        CINEMA_KEY,
        "cinema_name":   CINEMA_NAME,
        "title_es":      html.unescape(movie.get("name", "")).strip(),
        "is_vose":       is_vose,
        "audio_lang":    "EN" if is_vose else "ES",
        "imdb_id":       "",
        "is_film":       True,
        "duration_mins": _duration_mins(movie.get("duration", "")),
        "poster_url":    image or "",
        "synopsis_es":   html.unescape(movie.get("description", "") or "").strip(),
        "showtimes":     showtimes,
    }


def scrape_albeniz() -> list[dict]:
    """Fetch Cine Albéniz listings and return a list of film dicts."""
    sess = requests.Session()
    links = _film_links(sess)
    log.info("Cine Albéniz: %d film pages", len(links))
    films = []
    for url in links:
        try:
            r = sess.get(url, headers=_HEADERS, timeout=20)
            r.raise_for_status()
        except Exception as exc:
            log.warning("  Albéniz %s: %s", url, exc)
            continue
        film = _parse_film(r.text, url)
        if film and film["title_es"]:
            films.append(film)
    if not films and links:
        raise RuntimeError("Cine Albéniz: film pages found but no sessions parsed (page layout changed?)")
    log.info("Cine Albéniz: %d films, %d sessions", len(films), sum(len(f["showtimes"]) for f in films))
    return films


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    films = scrape_albeniz()
    print(f"\n{CINEMA_NAME}: {len(films)} films, {sum(len(f['showtimes']) for f in films)} sessions, "
          f"{sum(len(f['showtimes']) for f in films if f['is_vose'])} VOSE")
    for f in films:
        d = [s["date"] for s in f["showtimes"]]
        print(f"  [{'VOSE' if f['is_vose'] else '    '}] {f['title_es'][:45]:<45} {d[0]} -> {d[-1]} ({len(d)})")
