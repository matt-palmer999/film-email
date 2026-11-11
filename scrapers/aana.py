"""
Cines Aana scraper — two venues in the Alicante area:

  Aana Alicante   (city centre, 5 screens)   entradas.com cinema id 3301
  Aana San Juan   (Sant Joan d'Alacant)      entradas.com cinema id 3303

cinesaana.com embeds the cine.entradas.com widget, which runs on the kinoheld
platform. Its GraphQL endpoint takes a plain POST — no auth or key, just an
Origin/Referer of cine.entradas.com:

  POST https://entradas-next-live.kinoheld.de/graphql
  programByMovie(cinemaIds, first, page)
    data[]                      one per film: name, movie{id,title,duration,thumbnailImage}
      showGroups[].shows.data[] beginning (ISO, local offset), urlSlug, auditorium, flags[]
    paginatorInfo.hasMorePages

VOSE = flag code "subtitled" (name "VOSE"). Booking page:
  https://cine.entradas.com/cine/{path}/evento/{urlSlug}

Aana only publishes about two days ahead (today + tomorrow).

Not yet wired into pipeline.py — Alicante scrapers are being built and
checked standalone first. Run directly for a quick test summary.
"""

import logging
from datetime import datetime

import requests

log = logging.getLogger(__name__)

GRAPHQL_URL = "https://entradas-next-live.kinoheld.de/graphql"
BOOKING_BASE = "https://cine.entradas.com/cine"

VENUES = [
    {
        "key":       "aana_alicante",
        "name":      "Cines Aana Alicante",
        "cinema_id": "3301",
        "path":      "alicante-alacant/cines-aana-alicante",
    },
    {
        "key":       "aana_san_juan",
        "name":      "Cines Aana San Juan",
        "cinema_id": "3303",
        "path":      "sant-joan-d-alacant/cines-aana-san-juan",
    },
]

_HEADERS = {
    "Content-Type": "application/json",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Origin":  "https://cine.entradas.com",
    "Referer": "https://cine.entradas.com/",
}

_QUERY = """
query Program($cinemaIds: [ID!], $first: Int, $page: Int) {
  programByMovie(cinemaIds: $cinemaIds, first: $first, page: $page) {
    data {
      name
      movie { ... on MovieInterface { id title duration thumbnailImage { url } } }
      showGroups {
        shows { data { id urlSlug beginning auditorium { name } flags { code name } } }
      }
    }
    paginatorInfo { hasMorePages currentPage }
  }
}
"""

_PAGE_SIZE = 50
_MAX_PAGES = 10


def _fetch_program(sess: requests.Session, cinema_id: str) -> list[dict]:
    films, page = [], 1
    while page <= _MAX_PAGES:
        r = sess.post(GRAPHQL_URL, headers=_HEADERS, timeout=30, json={
            "query": _QUERY,
            "variables": {"cinemaIds": [cinema_id], "first": _PAGE_SIZE, "page": page},
        })
        r.raise_for_status()
        j = r.json()
        if j.get("errors"):
            raise RuntimeError(f"entradas GraphQL error: {j['errors'][:1]}")
        prog = (j.get("data") or {}).get("programByMovie") or {}
        films.extend(prog.get("data") or [])
        if not (prog.get("paginatorInfo") or {}).get("hasMorePages"):
            break
        page += 1
    return films


def _parse_show(show: dict, path: str) -> dict | None:
    try:
        dt = datetime.fromisoformat(show["beginning"])  # already cinema-local time
    except (KeyError, TypeError, ValueError):
        return None
    codes = {(f.get("code") or "").lower() for f in show.get("flags") or []}
    names = [f.get("name") or "" for f in show.get("flags") or []]
    upper = " ".join(names).upper()
    is_vose = "subtitled" in codes or "VOSE" in upper
    date_str, time_str = dt.strftime("%Y-%m-%d"), dt.strftime("%H:%M")
    slug = show.get("urlSlug") or ""
    return {
        "date":           date_str,
        "time":           time_str,
        "datetime_local": f"{date_str}T{time_str}:00",
        "format":         ("Digital VOSE" if is_vose else "Digital"),
        "is_vose":        is_vose,
        "audio_lang":     "EN" if is_vose else "ES",
        "is_3d":          "3d" in codes or "3D" in upper,
        "is_imax":        "IMAX" in upper,
        "is_4dx":         "4DX" in upper,
        "is_screenx":     "SCREENX" in upper.replace(" ", ""),
        "booking_url":    f"{BOOKING_BASE}/{path}/evento/{slug}" if slug else "",
    }


def scrape_entradas_cinema(cinema_id: str, path: str, cinema_key: str,
                           cinema_name: str, sess: requests.Session | None = None) -> list[dict]:
    """Fetch one entradas.com (kinoheld) cinema and return a list of film dicts."""
    sess = sess or requests.Session()
    log.info("Fetching %s (entradas id %s) …", cinema_name, cinema_id)
    raw = _fetch_program(sess, cinema_id)

    results = []
    for f in raw:
        movie = f.get("movie") or {}
        title = (movie.get("title") or f.get("name") or "").strip()
        if not title:
            continue
        seen, sts = set(), []
        for sg in f.get("showGroups") or []:
            for show in ((sg.get("shows") or {}).get("data")) or []:
                s = _parse_show(show, path)
                if s and (s["datetime_local"], s["booking_url"]) not in seen:
                    seen.add((s["datetime_local"], s["booking_url"]))
                    sts.append(s)
        if not sts:
            continue
        sts.sort(key=lambda s: s["datetime_local"])
        any_vose = any(s["is_vose"] for s in sts)
        results.append({
            "cinema":        cinema_key,
            "cinema_name":   cinema_name,
            "title_es":      title,
            "is_vose":       any_vose,
            "audio_lang":    "EN" if any_vose else "ES",
            "imdb_id":       "",
            "is_film":       True,
            "duration_mins": movie.get("duration") or 0,
            "poster_url":    (movie.get("thumbnailImage") or {}).get("url") or "",
            "showtimes":     sts,
        })

    log.info("%s: %d films, %d sessions", cinema_name, len(results),
             sum(len(f["showtimes"]) for f in results))
    return results


def scrape_aana() -> list[dict]:
    """Fetch both Cines Aana venues. Raises if neither returns anything."""
    sess = requests.Session()
    films = []
    for v in VENUES:
        try:
            films.extend(scrape_entradas_cinema(v["cinema_id"], v["path"], v["key"], v["name"], sess))
        except Exception as e:
            log.error("%s failed: %s", v["name"], e)
    if not films:
        raise RuntimeError("Cines Aana: no films from either venue")
    return films


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    films = scrape_aana()
    for v in VENUES:
        vf = [f for f in films if f["cinema"] == v["key"]]
        total = sum(len(f["showtimes"]) for f in vf)
        vose_sessions = sum(1 for f in vf for s in f["showtimes"] if s["is_vose"])
        print(f"\n{'='*60}")
        print(f"{v['name']}  —  {len(vf)} films  /  {total} sessions  /  VOSE {vose_sessions}")
        print(f"{'='*60}")
        for f in sorted(vf, key=lambda x: x["title_es"]):
            v_n = sum(1 for s in f["showtimes"] if s["is_vose"])
            tag = "VOSE" if f["is_vose"] else "    "
            dates = sorted({s["date"] for s in f["showtimes"]})
            print(f"  [{tag}] {f['title_es'][:45]:<45}  {dates[0]} -> {dates[-1]}  "
                  f"({len(f['showtimes'])}, VOSE {v_n})")
