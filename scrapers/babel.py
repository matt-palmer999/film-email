"""
Cines Babel scraper.

cinesbabel.com is a WordPress site with server-side rendered HTML.
A plain requests GET + BeautifulSoup parse is all that's needed —
no browser, no JS rendering.

VOSE is identified via the "Idioma:" metadata field on each film
(anything other than "Castellano" = original language + Spanish subtitles).

Returns a list of film dicts in the same format as the other scrapers.
Run directly for a quick test summary.
"""

import logging
import re
from datetime import date, datetime
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

log = logging.getLogger(__name__)

VALENCIA_TZ  = ZoneInfo("Europe/Madrid")
CINEMA_NAME  = "Cines Babel"
CARTELERA_URL = "https://cinesbabel.com/cartelera/"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "es-ES,es;q=0.9",
}

_MONTHS_ES = {
    "ene": 1, "feb": 2, "mar": 3, "abr": 4, "may": 5, "jun": 6,
    "jul": 7, "ago": 8, "sep": 9, "oct": 10, "nov": 11, "dic": 12,
}
_LANG_CODES = {
    "castellano": "ES", "español": "ES",
    "inglés": "EN", "ingles": "EN",
    "francés": "FR", "frances": "FR",
    "japonés": "JA", "japones": "JA",
    "italiano": "IT", "alemán": "DE", "aleman": "DE",
    "portugués": "PT", "portugues": "PT",
    "catalán": "CA", "catalan": "CA",
}


def _parse_date(text: str) -> str:
    """
    Convert Babel's Spanish date like "Sáb 9 May" to "YYYY-MM-DD".
    Uses the current year, bumping to next year if the month is in the past.
    """
    parts = text.strip().split()
    if len(parts) < 3:
        return ""
    try:
        day   = int(parts[1])
        month = _MONTHS_ES.get(parts[2].lower()[:3], 0)
        if not month:
            return ""
        today = date.today()
        year  = today.year
        # If this month already passed this year, it must be next year
        if month < today.month or (month == today.month and day < today.day):
            year += 1
        return f"{year}-{month:02d}-{day:02d}"
    except (ValueError, IndexError):
        return ""


def _lang_code(idioma: str) -> str:
    return _LANG_CODES.get(idioma.lower().strip(), "??")


def _is_vose(idioma: str) -> bool:
    key = idioma.lower().strip()
    return key not in ("castellano", "español", "")


def _parse_film(block) -> dict | None:
    """Parse one div.pelicula-post into a film dict."""
    title_el = block.select_one("h2")
    if not title_el:
        return None
    title = title_el.get_text(strip=True)

    # Metadata: Director, Duración, Idioma, Subtítulos
    meta = {}
    for div in block.select("div.pelicula-title div"):
        text = div.get_text(strip=True)
        if ":" in text:
            k, _, v = text.partition(":")
            meta[k.strip()] = v.strip()

    idioma    = meta.get("Idioma", "Castellano")
    subtitles = meta.get("Subtítulos", meta.get("Subtitulos", ""))
    duration  = re.search(r"\d+", meta.get("Duración", meta.get("Duracion", "")))

    poster_el = block.select_one("img")
    poster    = poster_el.get("src", "") if poster_el else ""

    # Showtimes table: each <tr> has date cell + one or more time cells
    showtimes = []
    for row in block.select("table.tabla-sesiones tr"):
        cells = row.find_all("td")
        if not cells:
            continue
        date_str = _parse_date(cells[0].get_text(strip=True))
        for cell in cells[1:]:
            link = cell.find("a")
            if not link:
                continue
            time_text = link.get_text(strip=True)
            if not re.match(r"\d{1,2}:\d{2}", time_text):
                continue
            booking_url = link.get("href", "")
            # Extract session ID from reservaentradas URL
            m = re.search(r"/(\d+)/?$", booking_url)
            session_id = m.group(1) if m else ""
            showtimes.append({
                "datetime_local": f"{date_str}T{time_text}:00" if date_str else "",
                "date":           date_str,
                "time":           time_text,
                "is_vose":        _is_vose(idioma),
                "audio_lang":     _lang_code(idioma),
                "format":         "2D",
                "is_3d":          False,
                "is_imax":        False,
                "is_4dx":         False,
                "booking_url":    booking_url,
                "session_id":     session_id,
            })

    if not showtimes:
        return None

    return {
        "cinema":       "babel",
        "cinema_name":  CINEMA_NAME,
        "title_es":     title,
        "original_title": "",
        "is_vose":      _is_vose(idioma),
        "audio_lang":   _lang_code(idioma),
        "spoken_lang":  idioma,
        "subtitles":    subtitles,
        "imdb_id":      "",
        "is_film":      True,
        "duration_mins": int(duration.group()) if duration else 0,
        "poster_url":   poster,
        "synopsis_es":  "",
        "showtimes":    showtimes,
    }


def scrape_babel() -> list[dict]:
    """Scrape Cines Babel and return a list of film dicts."""
    log.info("Fetching Babel cartelera …")
    r = requests.get(CARTELERA_URL, headers=HEADERS, timeout=20)
    r.raise_for_status()

    soup   = BeautifulSoup(r.text, "lxml")
    blocks = soup.select("div.pelicula-post")
    log.info("Found %d film blocks", len(blocks))

    films = []
    for block in blocks:
        film = _parse_film(block)
        if film:
            films.append(film)

    total = sum(len(f["showtimes"]) for f in films)
    log.info("Babel: %d films, %d sessions", len(films), total)
    return films


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    films = scrape_babel()
    total = sum(len(f["showtimes"]) for f in films)
    vose  = [f for f in films if f["is_vose"]]

    print(f"\n{'='*60}")
    print(f"Cines Babel  —  {len(films)} films  /  {total} sessions")
    print(f"VOSE: {len(vose)}")
    print(f"{'='*60}")

    for f in sorted(films, key=lambda x: x["title_es"]):
        vose_tag  = "VOSE" if f["is_vose"] else "    "
        lang      = f["spoken_lang"]
        dates     = sorted(set(s["date"] for s in f["showtimes"] if s["date"]))
        n         = len(f["showtimes"])
        print(f"  [{vose_tag}] {f['title_es'][:45]:<45}  {lang:<15}  {n:2d} sessions  {dates[0] if dates else '?'} -> {dates[-1] if dates else '?'}")
