"""
Cinesa Barcelona scraper — covers two venues:
  • Cinesa Diagonal      (siteId "012", /cines/diagonal/)
  • Cinesa Diagonal Mar  (siteId "032", /cines/diagonal-mar/)

Uses the same Vista ticketing API (vwc.cinesa.es) as cinesa.py (Bonaire).
Playwright loads the Diagonal page to capture the Bearer JWT, then plain
requests hit the API for both sites.

VOSE detection: attributeId "0000000068" == "Vose".
"""

import json
import logging
import requests
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from playwright.sync_api import sync_playwright

log = logging.getLogger(__name__)

BARCELONA_TZ = ZoneInfo("Europe/Madrid")
VWC_BASE     = "https://vwc.cinesa.es/WSVistaWebClient/ocapi/v1"
VOSE_ATTR    = "0000000068"
DAYS_AHEAD   = 7

CINEMAS = [
    {"site_id": "012", "key": "cinesa_diagonal",     "name": "Cinesa Diagonal",     "page_url": "https://www.cinesa.es/cines/diagonal/"},
    {"site_id": "032", "key": "cinesa_diagonal_mar",  "name": "Cinesa Diagonal Mar",  "page_url": "https://www.cinesa.es/cines/diagonal-mar/"},
]


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


def scrape_cinesa_barcelona(playwright=None) -> list[dict]:
    """Scrape Cinesa Diagonal and Diagonal Mar; return combined list of film dicts."""
    own_playwright = playwright is None
    if own_playwright:
        p = sync_playwright().start()
    else:
        p = playwright

    films_data:    dict = {}
    bearer_token:  list = [None]
    cf_cookie:     list = [None]

    def handle_films_route(route):
        req = route.request
        hdr = req.headers
        if not bearer_token[0] and "authorization" in hdr:
            bearer_token[0] = hdr["authorization"]
            cf_cookie[0]    = hdr.get("cookie")
        try:
            resp = route.fetch()
            body = resp.body()
            if body and body[:1] in (b"{", b"["):
                data = json.loads(body)
                if "films" in data:
                    nonlocal films_data
                    films_data = data
                    log.info("  Films captured: %d films", len(data["films"]))
            route.fulfill(response=resp)
        except Exception as exc:
            log.warning("  route.fetch() for /films failed: %s", exc)
            route.continue_()

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
        page.route("**/vwc.cinesa.es/**/films", handle_films_route)

        log.info("Loading Cinesa Diagonal page to capture Bearer token …")
        page.goto(CINEMAS[0]["page_url"], timeout=60_000, wait_until="networkidle")
        page.wait_for_timeout(5000)
        log.info("Page loaded | bearer=%s  films=%d",
                 bool(bearer_token[0]), len(films_data.get("films", [])))
        browser.close()

    finally:
        if own_playwright:
            p.stop()

    if not bearer_token[0]:
        log.error("No Bearer token captured — cannot fetch Cinesa Barcelona showtimes")
        return []

    # ── Fetch per-date showtimes for each cinema ──────────────────────────────
    hdrs  = _api_headers(bearer_token[0], cf_cookie[0])
    today = datetime.now(BARCELONA_TZ)

    # site_id → date → list[showtime_dict]
    showtimes_by_site: dict[str, dict[str, list]] = {c["site_id"]: {} for c in CINEMAS}
    film_vose_by_site: dict[str, dict[str, bool]] = {c["site_id"]: {} for c in CINEMAS}

    for i in range(DAYS_AHEAD):
        date_str = (today + timedelta(days=i)).strftime("%Y-%m-%d")
        for cinema in CINEMAS:
            site_id = cinema["site_id"]
            url = f"{VWC_BASE}/showtimes/by-business-date/{date_str}?siteIds={site_id}"
            try:
                r = requests.get(url, headers=hdrs, timeout=20)
                if r.status_code == 200:
                    data = r.json()
                    sessions = [s for s in data.get("showtimes", [])
                                if s.get("siteId") == site_id]
                    bd = data.get("businessDate", date_str)
                    if sessions:
                        showtimes_by_site[site_id][bd] = sessions
                        log.info("  %s %s: %d sessions", cinema["name"], bd, len(sessions))
            except Exception as exc:
                log.warning("  %s %s error: %s", cinema["name"], date_str, exc)

    # ── Build film lookup from /films response ─────────────────────────────────
    all_films  = films_data.get("films", [])
    film_by_id = {f["id"]: f for f in all_films}

    all_results: list[dict] = []

    for cinema in CINEMAS:
        site_id = cinema["site_id"]
        film_sessions: dict[str, dict] = {}

        for date_str, sessions in showtimes_by_site[site_id].items():
            for st in sessions:
                film_id  = st.get("filmId", "")
                attr_ids = st.get("attributeIds") or []
                is_vose  = VOSE_ATTR in attr_ids
                starts_at = st.get("schedule", {}).get("startsAt", "")
                if not starts_at:
                    continue
                try:
                    dt       = datetime.fromisoformat(starts_at).astimezone(BARCELONA_TZ)
                    time_str = dt.strftime("%H:%M")
                except Exception:
                    time_str = starts_at[11:16]

                fs = film_sessions.setdefault(film_id, {"any_vose": False, "dates": {}})
                if is_vose:
                    fs["any_vose"] = True
                fs["dates"].setdefault(date_str, []).append((time_str, is_vose))

        for film_id, fs in film_sessions.items():
            info = film_by_id.get(film_id)
            if not info:
                continue
            if info.get("eventId") is not None:
                continue

            any_vose = fs["any_vose"]
            title_es = info.get("title", {}).get("text", "")
            synopsis = (info.get("synopsis",      {}) or {}).get("text", "") or \
                       (info.get("shortSynopsis", {}) or {}).get("text", "")
            runtime  = info.get("runtimeInMinutes", 0)

            showtime_list = []
            for date_str, times in fs["dates"].items():
                for t, is_v in sorted(times):
                    showtime_list.append({
                        "datetime_local": f"{date_str}T{t}:00",
                        "date":           date_str,
                        "time":           t,
                        "format":         "2D",
                        "is_vose":        is_v,
                        "audio_lang":     "EN" if is_v else "ES",
                        "is_3d":          False,
                        "is_imax":        "IMAX" in (info.get("title", {}).get("text", "") or "").upper(),
                        "is_4dx":         False,
                        "booking_url":    "",
                    })

            if not showtime_list:
                continue

            all_results.append({
                "cinema":         cinema["key"],
                "cinema_name":    cinema["name"],
                "title_es":       title_es,
                "original_title": "",
                "is_vose":        any_vose,
                "audio_lang":     "EN" if any_vose else "ES",
                "imdb_id":        "",
                "is_film":        True,
                "duration_mins":  runtime or 0,
                "poster_url":     "",
                "synopsis_es":    synopsis,
                "showtimes":      showtime_list,
            })

        log.info("%s: %d films", cinema["name"], sum(1 for r in all_results if r["cinema"] == cinema["key"]))

    return all_results


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    films = scrape_cinesa_barcelona()
    total = sum(len(f["showtimes"]) for f in films)
    vose  = [f for f in films if f["is_vose"]]

    print(f"\n{'='*60}")
    print(f"Cinesa Barcelona  --  {len(films)} films  /  {total} sessions")
    print(f"VOSE: {len(vose)}")
    print(f"{'='*60}")

    for f in sorted(films, key=lambda x: (x["cinema"], x["title_es"])):
        vose_tag = "VOSE" if f["is_vose"] else "    "
        dates    = sorted({s["date"] for s in f["showtimes"]})
        n        = len(f["showtimes"])
        d_range  = f"{dates[0]} -> {dates[-1]}" if dates else "?"
        print(f"  [{vose_tag}] [{f['cinema'][:14]}] {f['title_es'][:40]:<40} {n:3d} sessions  {d_range}")
