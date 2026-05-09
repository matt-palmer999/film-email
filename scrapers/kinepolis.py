"""
Kinépolis Valencia scraper.

Loads https://kinepolis.es/?complex=KVAL&select_theater=KVAL&main_section=cartelera
with a headed Playwright browser (headless is fingerprinted and blocked).

All film and showtime data is embedded in window.Drupal.settings.variables
as current_movies — no separate API call or auth key needed.

Returns a list of film dicts. Run directly for a quick test summary.
"""

import json
import re
import logging
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo

from playwright.sync_api import sync_playwright

log = logging.getLogger(__name__)

VALENCIA_TZ = ZoneInfo("Europe/Madrid")
COMPLEX_CODE = "KVAL"
CINEMA_NAME  = "Kinépolis Valencia"
CARTELERA_URL = (
    "https://kinepolis.es/"
    "?complex=KVAL&select_theater=KVAL&main_section=cartelera"
)
IMAGE_BASE = "https://cdn.kinepolis.es"

# Kinépolis prefixes VOSE titles with "VOSE " — strip it for the clean title
_VOSE_PREFIX = re.compile(r"^VOSE[:\s]+", re.IGNORECASE)


def _clean_title(raw: str) -> str:
    return _VOSE_PREFIX.sub("", raw).strip()


def _to_local(iso_utc: str) -> str:
    """Convert an ISO UTC datetime string to Europe/Madrid local time (ISO format)."""
    dt = datetime.fromisoformat(iso_utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(VALENCIA_TZ).strftime("%Y-%m-%dT%H:%M:%S")


def _parse_session(session: dict) -> dict:
    attrs = session.get("rawSessionAttributes", "")
    parts = [p.strip() for p in attrs.split(",")]
    fmt = session.get("film", {}).get("format", {}).get("name", "2D")
    return {
        "datetime_utc":   session["showtime"],
        "datetime_local": _to_local(session["showtime"]),
        "format":         fmt,
        "is_3d":          any("3D" in p for p in parts),
        "is_imax":        any("IMAX" in p for p in parts),
        "is_4dx":         any("4DX" in p for p in parts),
        "attributes":     attrs,
        "session_id":     session.get("id", ""),
    }


def _build_film(film: dict, sessions: list[dict]) -> dict:
    audio_lang   = film.get("audioLanguage", "ES")
    spoken_lang  = film.get("spokenLanguage", {}).get("code", "")
    is_vose      = audio_lang != "ES"
    raw_title    = film.get("title", "")
    imdb_id      = film.get("imdbCode", "")
    is_film      = imdb_id.startswith("tt")   # TMT* = Kinépolis internal events

    poster_url = ""
    for img in film.get("images", []):
        if img.get("mediaType") == "Poster Graphic":
            poster_url = IMAGE_BASE + img["url"]
            break
    if not poster_url and film.get("images"):
        poster_url = IMAGE_BASE + film["images"][0]["url"]

    return {
        "cinema":       "kinepolis",
        "cinema_name":  CINEMA_NAME,
        "title_es":     _clean_title(raw_title),
        "title_raw":    raw_title,
        "is_vose":      is_vose,
        "audio_lang":   audio_lang,
        "spoken_lang":  spoken_lang,
        "imdb_id":      imdb_id,
        "is_film":      is_film,
        "duration_mins": film.get("duration", 0),
        "poster_url":   poster_url,
        "showtimes":    [_parse_session(s) for s in sessions],
    }


def scrape_kinepolis(playwright=None) -> list[dict]:
    """
    Scrape Kinépolis Valencia and return a list of film dicts.

    Pass an existing Playwright instance to reuse it (e.g. when scraping
    multiple cinemas in one run). If None, a headed browser is launched
    and closed within this call.
    """
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
        log.info("Loading Kinépolis cartelera page …")
        page.goto(CARTELERA_URL, timeout=30_000, wait_until="networkidle")
        log.info("Page loaded: %s", page.title())

        raw = page.evaluate(
            "() => JSON.stringify("
            "  window.Drupal && window.Drupal.settings"
            "  ? window.Drupal.settings.variables"
            "  : null"
            ")"
        )
        browser.close()
    finally:
        if own_playwright:
            p.stop()

    if not raw:
        log.error("Drupal.settings.variables not found on page")
        return []

    variables = json.loads(raw)
    current   = variables.get("current_movies", {})
    all_sessions = current.get("sessions", [])
    all_films    = current.get("films", [])

    # Index films by corporateId
    film_by_id = {f["corporateId"]: f for f in all_films}

    # Group KVAL sessions by film corporateId
    kval_by_film: dict[int, list] = {}
    for s in all_sessions:
        if s.get("complexOperator") != COMPLEX_CODE:
            continue
        corp_id = s["film"]["corporateId"]
        kval_by_film.setdefault(corp_id, []).append(s)

    films = []
    for corp_id, sessions in kval_by_film.items():
        film = film_by_id.get(corp_id)
        if not film:
            log.warning("No film record for corporateId %s", corp_id)
            continue
        films.append(_build_film(film, sessions))

    log.info("Kinépolis: %d films, %d sessions", len(films), sum(len(f["showtimes"]) for f in films))
    return films


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    films = scrape_kinepolis()

    total_sessions = sum(len(f["showtimes"]) for f in films)
    real_films     = [f for f in films if f["is_film"]]
    vose_films     = [f for f in real_films if f["is_vose"]]

    print(f"\n{'='*60}")
    print(f"Kinépolis Valencia  —  {len(films)} entries  /  {total_sessions} sessions")
    print(f"Real films (tt*): {len(real_films)}   VOSE: {len(vose_films)}")
    print(f"{'='*60}")

    for f in sorted(films, key=lambda x: x["title_es"]):
        vose_tag = "VOSE" if f["is_vose"] else "    "
        film_tag = "film " if f["is_film"] else "event"
        times    = sorted(set(s["datetime_local"][:10] for s in f["showtimes"]))
        print(f"  [{vose_tag}] [{film_tag}] {f['title_es'][:45]:<45}  {f['imdb_id']}  dates:{times}")
