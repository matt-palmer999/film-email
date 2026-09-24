"""
Mooby Glòries (Westfield Glòries, Barcelona) scraper.

Uses the same moobycinemas.com window.shops JSON blob as scrapers/aribau_barcelona.py.
Only the VENUE_SLUG differs.
"""

import json
import logging
import re

from bs4 import BeautifulSoup
from curl_cffi import requests as cffi_requests

log = logging.getLogger(__name__)

CINEMA_KEY  = "mooby_gloeries"
CINEMA_NAME = "Mooby Glòries"
PAGE_URL    = "https://moobycinemas.com"
VENUE_SLUG  = "/glories"

_VERSION_SUFFIX = re.compile(r"\s*\([^)]+\)\s*$")


def _is_vose(version: str) -> bool:
    return version.upper() in ("VOSE", "VOSI") or "VOSE" in version.upper() or "VOSI" in version.upper()


def _title_clean(locale_title: str) -> str:
    return _VERSION_SUFFIX.sub("", locale_title).strip()


def _fetch_html() -> str:
    r = cffi_requests.get(
        PAGE_URL,
        impersonate="chrome",
        headers={"Accept-Language": "es-ES,es;q=0.9"},
        timeout=20,
    )
    r.raise_for_status()
    return r.text


def scrape_mooby_gloeries(playwright=None) -> list[dict]:
    log.info("Fetching Mooby Glòries (moobycinemas.com) …")
    try:
        html = _fetch_html()
    except Exception as exc:
        log.error("Could not fetch Mooby page: %s", exc)
        return []

    soup = BeautifulSoup(html, "lxml")
    shops_data: dict = {}
    for script in soup.find_all("script"):
        text = script.string or ""
        if "window.shops" not in text:
            continue
        idx = text.find("window.shops")
        json_start = text.find("{", idx)
        if json_start < 0:
            continue
        try:
            decoder = json.JSONDecoder()
            shops_data, _ = decoder.raw_decode(text[json_start:])
        except json.JSONDecodeError as exc:
            log.error("Failed to parse window.shops JSON: %s", exc)
        break

    if not shops_data:
        log.error("Could not find window.shops data in Mooby page")
        return []

    venue = None
    for theater in shops_data.values():
        if theater.get("slug", "").rstrip("/") == VENUE_SLUG.rstrip("/"):
            venue = theater
            break

    if not venue:
        log.error("Could not find Mooby Glòries venue in window.shops (slug %s)", VENUE_SLUG)
        return []

    theater_id = str(venue.get("id", ""))
    shop_url_tpl = venue.get("shop_url", "")
    events = venue.get("events", [])
    log.info("Mooby Glòries: %d events found", len(events))

    results: list[dict] = []
    for event in events:
        performances = event.get("performances") or []
        if not performances:
            continue

        orig_title  = event.get("name", "")
        locale_t    = event.get("locale_title", "") or orig_title
        title_es    = _title_clean(locale_t)
        version     = event.get("version", "") or ""
        imdb_id     = event.get("imdbid", "") or ""
        runtime     = 0
        try:
            runtime = int(event.get("runtime") or 0)
        except (ValueError, TypeError):
            pass
        poster = event.get("poster") or event.get("poster_url") or ""
        vose   = _is_vose(version)

        showtimes: list[dict] = []
        for p in performances:
            raw_time = p.get("time", "")
            if not raw_time or len(raw_time) < 8:
                continue
            date_str = f"{raw_time[:4]}-{raw_time[4:6]}-{raw_time[6:8]}"
            time_str = f"{raw_time[8:10]}:{raw_time[10:12]}"
            perf_code = p.get("performance_code", "")
            booking_url = (
                shop_url_tpl
                .replace("%s", perf_code, 1)
                .replace("%s", "esp", 1)
                .replace("%s", theater_id, 1)
            ) if perf_code and shop_url_tpl else ""

            showtimes.append({
                "datetime_local": f"{date_str}T{time_str}:00",
                "date":           date_str,
                "time":           time_str,
                "format":         version or "2D",
                "is_vose":        vose,
                "audio_lang":     "EN" if vose else "ES",
                "is_3d":          "3D" in (version or "").upper(),
                "is_imax":        False,
                "is_4dx":         False,
                "booking_url":    booking_url,
            })

        if not showtimes:
            continue

        results.append({
            "cinema":         CINEMA_KEY,
            "cinema_name":    CINEMA_NAME,
            "title_es":       title_es,
            "original_title": orig_title if orig_title != title_es else "",
            "is_vose":        vose,
            "audio_lang":     "EN" if vose else "ES",
            "imdb_id":        imdb_id,
            "is_film":        True,
            "duration_mins":  runtime,
            "poster_url":     poster,
            "synopsis_es":    "",
            "showtimes":      showtimes,
        })

    total = sum(len(f["showtimes"]) for f in results)
    log.info("Mooby Glòries: %d events, %d sessions", len(results), total)
    return results


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    films = scrape_mooby_gloeries()
    total = sum(len(f["showtimes"]) for f in films)
    vose  = [f for f in films if f["is_vose"]]
    print(f"\n{'='*60}")
    print(f"Mooby Glòries  --  {len(films)} films  /  {total} sessions")
    print(f"VOSE: {len(vose)}")
    print(f"{'='*60}")
    for f in sorted(films, key=lambda x: x["title_es"]):
        vose_tag = "VOSE" if f["is_vose"] else "    "
        print(f"  [{vose_tag}] {f['title_es'][:45]:<45} {len(f['showtimes']):3d} sessions")
