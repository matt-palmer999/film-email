"""
Cines Babel scraper.

cinesbabel.com is a WordPress site with server-side rendered HTML protected
by a Cloudflare JS challenge. We load it with a headed Playwright browser,
wait for the challenge to pass (title changes from "Checking your browser…"),
then parse the HTML with BeautifulSoup.

VOSE is identified via the "Idioma:" metadata field on each film
(anything other than "Castellano" = original language + Spanish subtitles).

Returns a list of film dicts in the same format as the other scrapers.
Run directly for a quick test summary.
"""

import logging
import re
import time
from datetime import date
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

log = logging.getLogger(__name__)

VALENCIA_TZ   = ZoneInfo("Europe/Madrid")
CINEMA_NAME   = "Cines Babel"
CARTELERA_URL = "https://cinesbabel.com/cartelera/"

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

_CF_TITLES = {"checking your browser", "just a moment"}


def _parse_date(text: str) -> str:
    """Convert Babel's Spanish date like "Sáb 9 May" to "YYYY-MM-DD"."""
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
        if month < today.month or (month == today.month and day < today.day):
            year += 1
        return f"{year}-{month:02d}-{day:02d}"
    except (ValueError, IndexError):
        return ""


def _lang_code(idioma: str) -> str:
    return _LANG_CODES.get(idioma.lower().strip(), "??")


def _is_vose(idioma: str) -> bool:
    return idioma.lower().strip() not in ("castellano", "español", "")



_RE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "es-ES,es;q=0.9",
}


def _parse_re_date(text: str) -> str:
    """'Ma 10 / 11' -> 'YYYY-MM-DD'; bumps year if month already passed."""
    m = re.search(r"(\d{1,2})\s*/\s*(\d{2})", text)
    if not m:
        return ""
    day, month = int(m.group(1)), int(m.group(2))
    today = date.today()
    year = today.year
    if month < today.month or (month == today.month and day < today.day):
        year += 1
    return f"{year}-{month:02d}-{day:02d}"


def _fetch_session_urls(
    ses_url: str, session: requests.Session
) -> dict[tuple[str, str], str]:
    """Fetch a reservaentradas /sesiones/ page and return {(date, time): entrada_url}."""
    try:
        r = session.get(ses_url, headers=_RE_HEADERS, timeout=15)
        r.raise_for_status()
    except Exception as exc:
        log.warning("Could not fetch %s: %s", ses_url, exc)
        return {}

    soup = BeautifulSoup(r.text, "lxml")

    tab_dates: list[str] = []
    for li in soup.select("ul.nav-tabs li"):
        a = li.select_one("a")
        tab_dates.append(_parse_re_date(a.get_text()) if a else "")

    result: dict[tuple[str, str], str] = {}
    for i, date_str in enumerate(tab_dates, start=1):
        if not date_str:
            continue
        tab_div = soup.find("div", id=str(i))
        if not tab_div:
            continue
        for sc in tab_div.select("div.session-container"):
            a = sc.select_one("a")
            if not a:
                continue
            time_str = a.get_text(strip=True)
            if re.match(r"\d{1,2}:\d{2}", time_str):
                href = a.get("href", "")
                if href:
                    result[(date_str, time_str)] = href
    return result

def _parse_film(block) -> dict | None:
    """Parse one div.pelicula-post into a film dict."""
    title_el = block.select_one("h2")
    if not title_el:
        return None
    title = title_el.get_text(strip=True)

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
        "cinema":        "babel",
        "cinema_name":   CINEMA_NAME,
        "title_es":      title,
        "original_title": "",
        "is_vose":       _is_vose(idioma),
        "audio_lang":    _lang_code(idioma),
        "spoken_lang":   idioma,
        "subtitles":     subtitles,
        "imdb_id":       "",
        "is_film":       True,
        "duration_mins": int(duration.group()) if duration else 0,
        "poster_url":    poster,
        "synopsis_es":   "",
        "showtimes":     showtimes,
    }


def scrape_babel(playwright=None) -> list[dict]:
    """Scrape Cines Babel using a headed Playwright browser."""
    log.info("Fetching Babel cartelera …")

    own_playwright = playwright is None
    if own_playwright:
        p = sync_playwright().start()
    else:
        p = playwright

    try:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context(
            locale="es-ES",
            timezone_id="Europe/Madrid",
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
        )
        page = context.new_page()
        page.goto(CARTELERA_URL, timeout=30_000, wait_until="networkidle")

        # Cloudflare JS challenge: poll until title is no longer the challenge page
        for _ in range(15):
            title_lower = page.title().lower()
            if not any(cf in title_lower for cf in _CF_TITLES):
                break
            time.sleep(2)
            try:
                page.wait_for_load_state("networkidle", timeout=5_000)
            except Exception:
                pass

        html = page.content()
        browser.close()
    finally:
        if own_playwright:
            p.stop()

    soup   = BeautifulSoup(html, "lxml")
    blocks = soup.select("div.pelicula-post")
    log.info("Found %d film blocks", len(blocks))

    films = []
    for block in blocks:
        film = _parse_film(block)
        if film:
            films.append(film)

    # Enrich each film's showtimes with per-session /entrada/ booking URLs
    with requests.Session() as re_sess:
        for film in films:
            ses_urls = list(dict.fromkeys(
                st["booking_url"] for st in film["showtimes"] if st["booking_url"]
            ))
            for ses_url in ses_urls:
                session_map = _fetch_session_urls(ses_url, re_sess)
                for st in film["showtimes"]:
                    key = (st["date"], st["time"])
                    if key in session_map:
                        st["booking_url"] = session_map[key]

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
