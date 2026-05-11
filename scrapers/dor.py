"""
Cinestudio D'Or scraper.

Single-screen arthouse cinema in Valencia. Uses the reservaentradas.com
platform for ticketing — all showtime data is in plain server-rendered HTML.

Strategy:
1. GET reservaentradas.com/cine/valencia/cinestudiodor  — film list + session-page links
2. For each film, GET its session page — tabs = days, session-container = times
3. No browser needed (plain requests + BeautifulSoup throughout).

Run directly for a quick test summary.
"""

import logging
import re
from datetime import date

import requests
from bs4 import BeautifulSoup

log = logging.getLogger(__name__)

CINEMA_KEY  = "dor"
CINEMA_NAME = "Cinestudio D'Or"
BASE        = "https://www.reservaentradas.com"
LIST_URL    = f"{BASE}/cine/valencia/cinestudiodor"

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "es-ES,es;q=0.9",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_tab_date(text: str) -> str:
    """'Lu  18 / 05' -> 'YYYY-MM-DD' (bumps to next year if month already passed)."""
    m = re.search(r"(\d{1,2})\s*/\s*(\d{2})", text)
    if not m:
        return ""
    day, month = int(m.group(1)), int(m.group(2))
    today = date.today()
    year  = today.year
    if month < today.month or (month == today.month and day < today.day):
        year += 1
    return f"{year}-{month:02d}-{day:02d}"


def _is_vose(fmt: str) -> bool:
    upper = fmt.upper()
    return "VOSE" in upper or "ORIGINAL" in upper or "SUBTITULAD" in upper


# ---------------------------------------------------------------------------
# Per-film scraper
# ---------------------------------------------------------------------------

def _scrape_film(session: requests.Session, title: str, ses_url: str) -> dict | None:
    """Scrape one film's session page. Returns a film dict or None."""
    try:
        r = session.get(ses_url, headers=_HEADERS, timeout=15)
        r.raise_for_status()
    except Exception as exc:
        log.warning("Failed to fetch %s: %s", ses_url, exc)
        return None

    soup = BeautifulSoup(r.text, "lxml")

    # Poster from og:image or film img
    poster = ""
    og_img = soup.find("meta", property="og:image")
    if og_img:
        poster = og_img.get("content", "")
    if not poster:
        img = soup.select_one("img.img-responsive, img[src*=poster], img[src*=pelicula]")
        if img:
            poster = img.get("src", "")

    # Runtime from the film info block
    runtime = 0
    rt_m = re.search(r"(\d{2,3})\s*min", r.text, re.IGNORECASE)
    if rt_m:
        runtime = int(rt_m.group(1))

    # Date tabs: ul.nav-tabs li a  →  "Lu \n 18 / 05"
    tab_dates: list[str] = []
    for li in soup.select("ul.nav-tabs li"):
        a = li.select_one("a")
        if a:
            tab_dates.append(_parse_tab_date(a.get_text()))

    if not tab_dates:
        log.debug("%s: no date tabs found", title)
        return None

    # For each tab, collect sessions
    showtimes: list[dict] = []
    for i, date_str in enumerate(tab_dates, start=1):
        if not date_str:
            continue
        tab_div = soup.find("div", id=str(i))
        if not tab_div:
            continue

        # Format label (e.g. "DIGITAL", "DIGITAL VOSE", "ORIGINAL SUBTITULADA")
        fmt_p = tab_div.select_one("p")
        fmt   = fmt_p.get_text(strip=True) if fmt_p else "DIGITAL"
        is_v  = _is_vose(fmt)

        for sc in tab_div.select("div.session-container"):
            a = sc.select_one("a")
            if not a:
                continue
            time_str = a.get_text(strip=True)
            if not re.match(r"\d{1,2}:\d{2}", time_str):
                continue
            booking_url = a.get("href", "")

            showtimes.append({
                "datetime_local": f"{date_str}T{time_str}:00",
                "date":           date_str,
                "time":           time_str,
                "format":         fmt,
                "is_vose":        is_v,
                "audio_lang":     "EN" if is_v else "ES",
                "is_3d":          "3D" in fmt.upper(),
                "is_imax":        False,
                "is_4dx":         False,
                "booking_url":    booking_url,
            })

    if not showtimes:
        return None

    any_vose = any(s["is_vose"] for s in showtimes)
    return {
        "cinema":         CINEMA_KEY,
        "cinema_name":    CINEMA_NAME,
        "title_es":       title,
        "original_title": "",
        "is_vose":        any_vose,
        "audio_lang":     "EN" if any_vose else "ES",
        "imdb_id":        "",
        "is_film":        True,
        "duration_mins":  runtime,
        "poster_url":     poster,
        "synopsis_es":    "",
        "showtimes":      showtimes,
    }


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def scrape_dor() -> list[dict]:
    """Scrape Cinestudio D'Or and return a list of film dicts."""
    log.info("Fetching Cinestudio D'Or film list …")
    with requests.Session() as sess:
        try:
            r = sess.get(LIST_URL, headers=_HEADERS, timeout=15)
            r.raise_for_status()
        except Exception as exc:
            log.error("Could not fetch D'Or film list: %s", exc)
            return []

        soup = BeautifulSoup(r.text, "lxml")

        # Unique session-page links (skip ?proximamente=true)
        seen: set[str] = set()
        film_links: list[tuple[str, str]] = []  # (url, title)
        for a in soup.select(f'a[href*="/sesiones/valencia/cinestudiodor/"]'):
            href = a["href"]
            if "proximamente" in href:
                continue
            if href in seen:
                continue
            seen.add(href)
            title = a.get_text(strip=True)
            if title:
                film_links.append((href, title))

        log.info("Found %d films", len(film_links))

        results: list[dict] = []
        for url, title in film_links:
            film = _scrape_film(sess, title, url)
            if film:
                results.append(film)

    total = sum(len(f["showtimes"]) for f in results)
    log.info("D'Or: %d films, %d sessions", len(results), total)
    return results


# ---------------------------------------------------------------------------
# CLI test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    films = scrape_dor()
    total = sum(len(f["showtimes"]) for f in films)
    vose  = [f for f in films if f["is_vose"]]

    print(f"\n{'='*60}")
    print(f"Cinestudio D'Or  --  {len(films)} films  /  {total} sessions")
    print(f"VOSE: {len(vose)}")
    print(f"{'='*60}")

    for f in sorted(films, key=lambda x: x["title_es"]):
        vose_tag = "VOSE" if f["is_vose"] else "    "
        dates    = sorted({s["date"] for s in f["showtimes"] if s["date"]})
        n        = len(f["showtimes"])
        d_range  = f"{dates[0]} -> {dates[-1]}" if dates else "?"
        print(f"  [{vose_tag}] {f['title_es'][:45]:<45} {n:3d} sessions  {d_range}")
