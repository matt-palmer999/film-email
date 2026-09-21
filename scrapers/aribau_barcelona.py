"""
Aribau Multicines (MOOBY ARIBAU) Barcelona scraper.

moobycinemas.com server-renders a window.shops JSON blob that contains
all current and upcoming performances for each Mooby venue.  The site
requires a real browser (Cloudflare protection), so Playwright is used
to fetch the initial HTML.

Strategy:
1. Playwright → load https://www.moobycinemas.com/aribau, get page.content()
2. Extract the window.shops JSON literal from the page's inline <script>
3. Find the Aribau venue by slug "/aribau" (theater_id 28)
4. Parse events → showtimes

Version values (field 'version'):
  "VOSE"        → original language, Spanish subtitles
  "VOSI"        → original language, subtitled
  "ESP"         → dubbed in Castilian Spanish
  "CAT"         → dubbed in Catalan
  ""            → opera / ballet / event (version info not applicable)
"""

import json
import logging
import re

from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

log = logging.getLogger(__name__)

CINEMA_KEY  = "aribau_barcelona"
CINEMA_NAME = "Aribau Multicines"
PAGE_URL    = "https://www.moobycinemas.com/aribau"
VENUE_SLUG  = "/aribau"

_VERSION_SUFFIX = re.compile(r"\s*\([^)]+\)\s*$")


def _is_vose(version: str) -> bool:
    return version.upper() in ("VOSE", "VOSI") or "VOSE" in version.upper() or "VOSI" in version.upper()


def _title_clean(locale_title: str) -> str:
    """Strip version suffixes like '(VOSE)', '(Doblada ESP)' from the display title."""
    return _VERSION_SUFFIX.sub("", locale_title).strip()


def _fetch_html(playwright=None) -> str:
    """Use Playwright to load the page and return the HTML content."""
    own_pw = playwright is None
    if own_pw:
        p = sync_playwright().start()
    else:
        p = playwright
    try:
        browser = p.chromium.launch(
            headless=False,
            args=["--disable-blink-features=AutomationControlled"],
        )
        ctx = browser.new_context(
            locale="es-ES",
            timezone_id="Europe/Madrid",
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
        )
        page = ctx.new_page()
        page.goto(PAGE_URL, timeout=60_000, wait_until="domcontentloaded")
        page.wait_for_timeout(3000)
        html = page.content()
        browser.close()
        return html
    finally:
        if own_pw:
            p.stop()


def scrape_aribau_barcelona(playwright=None) -> list[dict]:
    log.info("Fetching Aribau Multicines (moobycinemas.com/aribau) …")
    try:
        html = _fetch_html(playwright)
    except Exception as exc:
        log.error("Could not fetch Aribau page via Playwright: %s", exc)
        return []

    # Extract window.shops JSON from inline <script>
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
        log.error("Could not find window.shops data in Aribau page")
        return []

    # Find the Aribau venue
    venue = None
    for theater in shops_data.values():
        if theater.get("slug", "").rstrip("/") == VENUE_SLUG.rstrip("/"):
            venue = theater
            break

    if not venue:
        log.error("Could not find Aribau venue in window.shops (slug %s)", VENUE_SLUG)
        return []

    theater_id = str(venue.get("id", ""))
    shop_url_tpl = venue.get("shop_url", "")
    events = venue.get("events", [])
    log.info("Aribau: %d events found", len(events))

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
        poster      = event.get("poster") or event.get("poster_url") or ""

        vose = _is_vose(version)

        showtimes: list[dict] = []
        for p in performances:
            raw_time = p.get("time", "")
            raw_date = p.get("schedule_date", "")
            if not raw_time or len(raw_time) < 8:
                continue
            # raw_time: "YYYYMMDDHHmmss"
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
    log.info("Aribau Multicines: %d events, %d sessions", len(results), total)
    return results


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    films = scrape_aribau_barcelona()
    total = sum(len(f["showtimes"]) for f in films)
    vose  = [f for f in films if f["is_vose"]]

    print(f"\n{'='*60}")
    print(f"Aribau Multicines  --  {len(films)} films  /  {total} sessions")
    print(f"VOSE: {len(vose)}")
    print(f"{'='*60}")

    for f in sorted(films, key=lambda x: x["title_es"]):
        vose_tag = "VOSE" if f["is_vose"] else "    "
        dates    = sorted({s["date"] for s in f["showtimes"]})
        n        = len(f["showtimes"])
        d_range  = f"{dates[0]} -> {dates[-1]}" if dates else "?"
        print(f"  [{vose_tag}] {f['title_es'][:45]:<45} {n:3d} sessions  {d_range}")
