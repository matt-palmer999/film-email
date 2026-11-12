"""
Odeon Multicines Alicante scraper (San Vicente del Raspeig, Av. Alicante 94,
15 screens, next to the University of Alicante).

The sessions page https://odeonmulticines.com/odeon-alicante/peliculas is
server-rendered WordPress HTML (no WAF; plain requests works):

  div.show-info                  one per film: <h5> title, poster, "NNN min"
  div.day-YYYY-MM-DD             following siblings, one per film per day
    div.item-session             one row per format
      div[data-format]           e.g. "DIGITAL", "DIGITAL VOSE", "LASER ATMOS VOSE"
      time[data-time] > a        "HH:MM", href = booking page (sesion?pid=…&sesion=…)

VOSE comes from the data-format tokens ("VOSE", or "VO" for original version).
Premium screens: "CINITY" and "INFINITY" (Infinity Vision) tokens set
is_cinity / is_infinity_vision. Atmos/Laser alone are not flagged.

Not yet wired into pipeline.py — Alicante scrapers are being built and
checked standalone first. Run directly for a quick test summary.
"""

import logging
import re

import requests
from bs4 import BeautifulSoup

log = logging.getLogger(__name__)

CINEMA_KEY   = "odeon_alicante"
CINEMA_NAME  = "Odeon Multicines Alicante"
SESSIONS_URL = "https://odeonmulticines.com/odeon-alicante/peliculas"

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "es-ES,es;q=0.9",
}

_DAY_CLASS = re.compile(r"^day-(\d{4}-\d{2}-\d{2})$")
_TIME_RE   = re.compile(r"^\d{1,2}:\d{2}$")
_MINS_RE   = re.compile(r"(\d+)\s*min")


def _is_film_header(tag) -> bool:
    return tag.name == "div" and "show-info" in (tag.get("class") or [])


def _day_of(tag) -> str | None:
    if tag.name != "div":
        return None
    for c in tag.get("class") or []:
        m = _DAY_CLASS.match(c)
        if m:
            return m.group(1)
    return None


def _parse_header(div) -> dict:
    h5 = div.find("h5")
    title = h5.get_text(" ", strip=True) if h5 else ""
    img = div.select_one(".list-afiche img")
    poster = (img.get("src") or "").split("?")[0] if img else ""
    mins = 0
    m = _MINS_RE.search(div.get_text(" ", strip=True))
    if m:
        mins = int(m.group(1))
    return {"title": title, "poster": poster, "duration": mins}


def _parse_day(div, date_str: str) -> list[dict]:
    showtimes = []
    for row in div.select(".item-session"):
        fmt_div = row.find(attrs={"data-format": True})
        fmt = (fmt_div.get("data-format") if fmt_div else "") or "DIGITAL"
        tokens = set(fmt.upper().split())
        is_vose = bool(tokens & {"VOSE", "VO"})
        for a in row.select("time a"):
            t = a.get_text(strip=True)
            if not _TIME_RE.match(t):
                continue
            t = t.zfill(5)
            showtimes.append({
                "date":           date_str,
                "time":           t,
                "datetime_local": f"{date_str}T{t}:00",
                "format":         fmt,
                "is_vose":        is_vose,
                "audio_lang":     "EN" if is_vose else "ES",
                "is_3d":          "3D" in tokens,
                "is_imax":        "IMAX" in tokens,
                "is_4dx":         "4DX" in tokens,
                "is_screenx":     "SCREENX" in tokens,
                # Odeon premium screens. Labels get truncated ("INFINITY VI"),
                # so match on the INFINITY token. Cinity wins in the pipeline.
                "is_cinity":      "CINITY" in tokens,
                "is_infinity_vision": "INFINITY" in tokens,
                "booking_url":    a.get("href", ""),
            })
    return showtimes


def scrape_odeon_alicante() -> list[dict]:
    """Fetch Odeon Multicines Alicante listings and return a list of film dicts."""
    log.info("Fetching %s sessions page …", CINEMA_NAME)
    r = requests.get(SESSIONS_URL, headers=_HEADERS, timeout=30)
    r.raise_for_status()
    r.encoding = "utf-8"
    soup = BeautifulSoup(r.text, "lxml")

    films: dict[str, dict] = {}
    current = None
    for tag in soup.find_all("div"):
        if _is_film_header(tag):
            hdr = _parse_header(tag)
            if not hdr["title"]:
                current = None
                continue
            current = films.setdefault(hdr["title"], {**hdr, "showtimes": []})
            continue
        date_str = _day_of(tag)
        if date_str and current is not None:
            current["showtimes"].extend(_parse_day(tag, date_str))

    results = []
    for title, f in films.items():
        # De-duplicate (a film can appear in more than one listing block)
        seen, sts = set(), []
        for s in sorted(f["showtimes"], key=lambda s: (s["date"], s["time"], s["format"])):
            k = (s["date"], s["time"], s["booking_url"])
            if k not in seen:
                seen.add(k)
                sts.append(s)
        if not sts:
            continue
        any_vose = any(s["is_vose"] for s in sts)
        results.append({
            "cinema":        CINEMA_KEY,
            "cinema_name":   CINEMA_NAME,
            "title_es":      title,
            "is_vose":       any_vose,
            "audio_lang":    "EN" if any_vose else "ES",
            "imdb_id":       "",
            "is_film":       True,
            "duration_mins": f["duration"],
            "poster_url":    f["poster"],
            "showtimes":     sts,
        })

    if not results:
        raise RuntimeError("Odeon Alicante: no films parsed — page structure may have changed")
    log.info("%s: %d films, %d sessions", CINEMA_NAME, len(results),
             sum(len(f["showtimes"]) for f in results))
    return results


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    films = scrape_odeon_alicante()
    total = sum(len(f["showtimes"]) for f in films)
    vose_sessions = sum(1 for f in films for s in f["showtimes"] if s["is_vose"])
    vose = [f for f in films if f["is_vose"]]

    print(f"\n{'='*60}")
    print(f"{CINEMA_NAME}  —  {len(films)} films  /  {total} sessions")
    print(f"VOSE films: {len(vose)}   VOSE sessions: {vose_sessions}")
    print(f"{'='*60}")

    for f in sorted(films, key=lambda x: x["title_es"]):
        v = sum(1 for s in f["showtimes"] if s["is_vose"])
        vose_tag = "VOSE" if f["is_vose"] else "    "
        dates = sorted({s["date"] for s in f["showtimes"]})
        print(f"  [{vose_tag}] {f['title_es'][:45]:<45}  {dates[0]} -> {dates[-1]}  "
              f"({len(f['showtimes'])}, VOSE {v})")
