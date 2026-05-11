"""
Cines Lys scraper.

Arthouse cinema in central Valencia. Uses the reservaentradas.com
platform — all showtime data is server-rendered HTML, no browser needed.

Strategy:
1. GET reservaentradas.com/cine/valencia/cineslys  → collect /sesiones/ links
2. For each film, GET its session page  → tabs = days, session-container = times
3. Title from page <title>; VOSE from <!-- FORMAT --> p tag in each day's pane.

Run directly for a quick test summary.
"""

import logging
import re
from datetime import date

import requests
from bs4 import BeautifulSoup

log = logging.getLogger(__name__)

CINEMA_KEY  = "lys"
CINEMA_NAME = "Cines Lys"
BASE        = "https://www.reservaentradas.com"
LIST_URL    = f"{BASE}/cine/valencia/cineslys"

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "es-ES,es;q=0.9",
}

_TITLE_SUFFIX = re.compile(r"\s+en\s+CINES LYS\b.*$", re.IGNORECASE)


def _parse_tab_date(text: str) -> str:
    """'Do <br/> 10 / 05' → 'YYYY-MM-DD'; bumps year if month already passed."""
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


def _scrape_film(session: requests.Session, ses_url: str) -> dict | None:
    try:
        r = session.get(ses_url, headers=_HEADERS, timeout=15)
        r.raise_for_status()
    except Exception as exc:
        log.warning("Failed to fetch %s: %s", ses_url, exc)
        return None

    soup = BeautifulSoup(r.text, "lxml")

    # Title from page <title> tag
    page_title = soup.title.get_text(strip=True) if soup.title else ""
    title = _TITLE_SUFFIX.sub("", page_title).strip()
    if not title:
        h1 = soup.select_one("h1")
        title = h1.get_text(strip=True) if h1 else ""
    if not title:
        return None

    # Poster: lazy-loaded img with cineslys.reservaentradas.com domain
    poster = ""
    for img in soup.select("img[data-original], img[src]"):
        src = img.get("data-original") or img.get("src", "")
        if "cineslys.reservaentradas.com" in src:
            poster = src
            break

    # Runtime
    runtime = 0
    rt_m = re.search(r"(\d{2,3})\s*min", r.text, re.IGNORECASE)
    if rt_m:
        runtime = int(rt_m.group(1))

    # Date tabs: ul.nav-tabs li a → "Do <br/> 10 / 05"
    tab_dates: list[str] = []
    for li in soup.select("ul.nav-tabs li"):
        a = li.select_one("a")
        if a:
            tab_dates.append(_parse_tab_date(a.get_text()))

    if not tab_dates:
        log.debug("%s: no date tabs found", title)
        return None

    showtimes: list[dict] = []
    for i, date_str in enumerate(tab_dates, start=1):
        if not date_str:
            continue
        tab_div = soup.find("div", id=str(i))
        if not tab_div:
            continue

        # Format label from the <!-- FORMAT --> p tag in this pane
        fmt_p = tab_div.select_one("p")
        fmt   = fmt_p.get_text(strip=True) if fmt_p else ""
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
                "format":         fmt if fmt else "2D",
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


def scrape_lys() -> list[dict]:
    """Scrape Cines Lys and return a list of film dicts."""
    log.info("Fetching Cines Lys film list …")
    with requests.Session() as sess:
        try:
            r = sess.get(LIST_URL, headers=_HEADERS, timeout=15)
            r.raise_for_status()
        except Exception as exc:
            log.error("Could not fetch Cines Lys film list: %s", exc)
            return []

        # Unique /sesiones/ links for this cinema
        ses_urls = list(dict.fromkeys(
            re.findall(
                r"https://www\.reservaentradas\.com/sesiones/valencia/cineslys/[^/\"']+/\d+/",
                r.text,
            )
        ))
        log.info("Found %d film session pages", len(ses_urls))

        results: list[dict] = []
        for url in ses_urls:
            film = _scrape_film(sess, url)
            if film:
                results.append(film)

    total = sum(len(f["showtimes"]) for f in results)
    log.info("Cines Lys: %d films, %d sessions", len(results), total)
    return results


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    films = scrape_lys()
    total = sum(len(f["showtimes"]) for f in films)
    vose  = [f for f in films if f["is_vose"]]

    print(f"\n{'='*60}")
    print(f"Cines Lys  --  {len(films)} films  /  {total} sessions")
    print(f"VOSE: {len(vose)}")
    print(f"{'='*60}")

    for f in sorted(films, key=lambda x: x["title_es"]):
        vose_tag = "VOSE" if f["is_vose"] else "    "
        dates    = sorted({s["date"] for s in f["showtimes"] if s["date"]})
        n        = len(f["showtimes"])
        d_range  = f"{dates[0]} -> {dates[-1]}" if dates else "?"
        print(f"  [{vose_tag}] {f['title_es'][:45]:<45} {n:3d} sessions  {d_range}")
