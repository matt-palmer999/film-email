"""
Cinesa LUXE Bonaire scraper.

Uses the Cinesa Vista ticketing API (vwc.cinesa.es/WSVistaWebClient/ocapi/v1/).

The API requires a Bearer JWT that is issued during the initial page load.
Playwright loads the Bonaire page first, intercepts the /films request via
page.route() + route.fetch() to:
  1. Capture the Bearer token from the outgoing request headers.
  2. Capture the films JSON (92 films) via route.fetch().

Once the Bearer token is in hand, all subsequent API calls (film-screening-dates
for the 7-day range, per-date showtimes) are made directly with Python requests.

VOSE detection: attributeId "0000000068" == "Vose" (confirmed from attribute legend).

Returns a list of film dicts in the same format as other scrapers.
Run directly for a quick test summary.
"""

import json
import logging
import requests
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from playwright.sync_api import sync_playwright

log = logging.getLogger(__name__)

VALENCIA_TZ  = ZoneInfo("Europe/Madrid")
SITE_ID      = "028"
CINEMA_KEY   = "cinesa"
CINEMA_NAME  = "Cinesa LUXE Bonaire"
PAGE_URL     = "https://www.cinesa.es/cines/bonaire/"
VWC_BASE     = "https://vwc.cinesa.es/WSVistaWebClient/ocapi/v1"
VOSE_ATTR    = "0000000068"   # confirmed: attribute legend alt="Vose"
DAYS_AHEAD   = 7


def _api_headers(bearer: str, cf_cookie: str | None = None) -> dict:
    h = {
        "Accept":          "application/json",
        "Accept-Language": "es-ES",
        "Authorization":   bearer,
        "Origin":          "https://www.cinesa.es",
        "Referer":         "https://www.cinesa.es/",
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
    }
    if cf_cookie:
        h["Cookie"] = cf_cookie
    return h


def scrape_cinesa(playwright=None) -> list[dict]:
    """Scrape Cinesa LUXE Bonaire and return a list of film dicts."""
    own_playwright = playwright is None
    if own_playwright:
        p = sync_playwright().start()
    else:
        p = playwright

    # ── Data collected via Playwright ──────────────────────────────────────────
    films_data:   dict = {}           # from /films (via route.fetch)
    dates_data:   dict = {}           # from /film-screening-dates
    bearer_token: list = [None]       # extracted from request headers
    cf_cookie:    list = [None]

    # ── Route handler: intercept /films, capture Bearer token + film data ───────
    def handle_films_route(route):
        req = route.request
        hdr = req.headers
        if not bearer_token[0] and "authorization" in hdr:
            bearer_token[0] = hdr["authorization"]
            cf_cookie[0]    = hdr.get("cookie")
            log.debug("Bearer token captured from films request")
        try:
            resp = route.fetch()
            body = resp.body()
            if body and body[:1] in (b"{", b"["):
                data = json.loads(body)
                if "films" in data:
                    nonlocal films_data
                    films_data = data
                    log.info("  Films captured via route.fetch(): %d films", len(data["films"]))
            route.fulfill(response=resp)
        except Exception as exc:
            log.warning("  route.fetch() for /films failed: %s", exc)
            route.continue_()

    # ── Response handler: capture film-screening-dates ─────────────────────────
    def on_response(response):
        url = response.url
        if "vwc.cinesa.es" not in url:
            return
        try:
            body = response.body()
            if not body or body[:1] not in (b"{", b"["):
                return
            data = json.loads(body)
            if "film-screening-dates" in url and "filmScreeningDates" in data:
                nonlocal dates_data
                dates_data = data
                log.info("  Dates captured: %d date-entries", len(data["filmScreeningDates"]))
        except Exception:
            pass

    try:
        browser = p.chromium.launch(headless=True)
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
        page.on("response", on_response)
        page.route("**/vwc.cinesa.es/**/films", handle_films_route)

        log.info("Loading Cinesa Bonaire page ...")
        page.goto(PAGE_URL, timeout=60_000, wait_until="networkidle")
        page.wait_for_timeout(4000)
        log.info("Page loaded | films=%d  dates=%d",
                 len(films_data.get("films", [])),
                 len(dates_data.get("filmScreeningDates", [])))

        browser.close()

    finally:
        if own_playwright:
            p.stop()

    # ── Fetch per-date showtimes directly with Bearer token ────────────────────
    showtimes_by_date: dict[str, list] = {}

    if bearer_token[0]:
        hdrs  = _api_headers(bearer_token[0], cf_cookie[0])
        today = datetime.now(VALENCIA_TZ)
        for i in range(DAYS_AHEAD):
            date_str = (today + timedelta(days=i)).strftime("%Y-%m-%d")
            url = f"{VWC_BASE}/showtimes/by-business-date/{date_str}?siteIds={SITE_ID}"
            try:
                r = requests.get(url, headers=hdrs, timeout=20)
                if r.status_code == 200:
                    data = r.json()
                    sessions = [s for s in data.get("showtimes", [])
                                if s.get("siteId") == SITE_ID]
                    bd = data.get("businessDate", date_str)
                    if sessions:
                        showtimes_by_date[bd] = sessions
                        log.info("  Showtimes %s: %d sessions", bd, len(sessions))
                else:
                    log.warning("  Showtimes %s: HTTP %d", date_str, r.status_code)
            except Exception as exc:
                log.warning("  Showtimes %s error: %s", date_str, exc)
    else:
        log.error("No Bearer token captured — cannot fetch showtimes")

    # ── Build lookups ──────────────────────────────────────────────────────────
    all_films   = films_data.get("films", [])
    film_by_id  = {f["id"]: f for f in all_films}

    # Which film IDs are actually showing at Bonaire (from screening-dates)?
    bonaire_ids: set[str] = set()
    # Per-film, per-date VOSE flag from screening-dates
    dates_vose: dict[str, dict[str, bool]] = {}   # film_id -> {date -> is_vose}
    for d in dates_data.get("filmScreeningDates", []):
        date_str = d.get("businessDate", "")
        for fs in d.get("filmScreenings", []):
            film_id = fs.get("filmId", "")
            for site in fs.get("sites", []):
                if site.get("siteId") == SITE_ID:
                    bonaire_ids.add(film_id)
                    attr_ids = site.get("showtimeAttributeIds", [])
                    is_vose  = VOSE_ATTR in attr_ids
                    dates_vose.setdefault(film_id, {})[date_str] = is_vose

    # ── Aggregate per-session showtimes ────────────────────────────────────────
    film_sessions: dict[str, dict] = {}

    for date_str, sessions in showtimes_by_date.items():
        for st in sessions:
            film_id  = st.get("filmId", "")
            if film_id not in bonaire_ids:
                continue
            attr_ids = st.get("attributeIds") or []
            is_vose  = VOSE_ATTR in attr_ids
            starts_at = st.get("schedule", {}).get("startsAt", "")
            if not starts_at:
                continue
            try:
                dt       = datetime.fromisoformat(starts_at).astimezone(VALENCIA_TZ)
                time_str = dt.strftime("%H:%M")
            except Exception:
                time_str = starts_at[11:16]

            fs = film_sessions.setdefault(film_id, {"any_vose": False, "all": {}, "vose": {}})
            fs["all"].setdefault(date_str, set()).add(time_str)
            if is_vose:
                fs["any_vose"] = True
                fs["vose"].setdefault(date_str, set()).add(time_str)

    # Ensure every Bonaire film has an entry (even if no per-session data yet)
    for film_id in bonaire_ids:
        if film_id not in film_sessions:
            film_sessions[film_id] = {"any_vose": False, "all": {}, "vose": {}}
        fs = film_sessions[film_id]
        if any(dates_vose.get(film_id, {}).values()):
            fs["any_vose"] = True

    # ── Build output list ──────────────────────────────────────────────────────
    results: list[dict] = []

    for film_id, fs in film_sessions.items():
        info = film_by_id.get(film_id)
        if not info:
            log.debug("  No film info for %s — skipping", film_id)
            continue

        # Skip events (concerts, live screenings, etc.)
        if info.get("eventId") is not None:
            continue

        any_vose  = fs["any_vose"]
        title_es  = info.get("title", {}).get("text", "")
        synopsis  = (info.get("synopsis",      {}) or {}).get("text", "") or \
                    (info.get("shortSynopsis", {}) or {}).get("text", "")
        runtime   = info.get("runtimeInMinutes", 0)

        showtime_list = []
        for date_str, times in fs["all"].items():
            for t in sorted(times):
                showtime_list.append({
                    "datetime_local": f"{date_str}T{t}:00",
                    "is_vose":        any_vose,
                    "audio_lang":     "EN" if any_vose else "ES",
                })

        if not showtime_list:
            continue

        results.append({
            "cinema":        CINEMA_KEY,
            "cinema_name":   CINEMA_NAME,
            "title_es":      title_es,
            "is_vose":       any_vose,
            "audio_lang":    "EN" if any_vose else "ES",
            "is_film":       True,
            "duration_mins": runtime or 0,
            "poster_url":    "",   # TMDB enrichment fills this
            "synopsis_es":   synopsis,
            "showtimes":     showtime_list,
        })

    log.info(
        "Cinesa Bonaire: %d films  (%d VOSE)",
        len(results), sum(1 for f in results if f["is_vose"])
    )
    return results


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    films = scrape_cinesa()

    total  = sum(len(f["showtimes"]) for f in films)
    vose_f = [f for f in films if f["is_vose"]]

    print(f"\n{'='*60}")
    print(f"Cinesa LUXE Bonaire  --  {len(films)} films  /  {total} sessions")
    print(f"VOSE: {len(vose_f)}")
    print(f"{'='*60}")

    for f in sorted(films, key=lambda x: x["title_es"]):
        vose_tag = "VOSE" if f["is_vose"] else "    "
        dates    = sorted({s["datetime_local"][:10] for s in f["showtimes"]})
        print(f"  [{vose_tag}] {f['title_es'][:50]:<50}  {dates}")
