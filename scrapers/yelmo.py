"""
Yelmo Campanar (Valencia) scraper.

Yelmo was acquired by Cinépolis; the old yelmocines.es API is gone.
Now uses the Cinépolis GraphQL API at api-g.cinepolis.com.

Two queries:
  1. billboardByCinema  — all movies + showtimes for this cinema in one call
  2. movie              — title, original title, duration, poster for each film

Run directly for a quick test summary.
"""

import logging
import re
import time

import requests

log = logging.getLogger(__name__)

CINEMA_KEY   = "mercado-de-campanar"
CINEMA_NAME  = "Yelmo Campanar"
CINEMA_SLUG  = "mercado-de-campanar-valencia"
COUNTRY_ID   = "ES"
TIMEZONE     = "Europe/Madrid"

_API         = "https://api-g.cinepolis.com/v2/billboards/graphql"
_API_KEY     = "lQM6Mkvri1iHksKKCfpAiwGXq0YUZA7Nn6XAXRPr4i13LwXo"
_HEADERS     = {
    "Content-Type": "application/json",
    "x-apikey":     _API_KEY,
    "Origin":       "https://cineyelmo.com",
    "Referer":      "https://cineyelmo.com/",
    "User-Agent":   (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
}

_BILLBOARD_QUERY = """
  query BillboardByCinema(
    $cinemaId: String!
    $countryId: String!
    $timezone: String
    $isAbsoluteExperiencePath: Boolean
  ) {
    billboardByCinema(
      cinemaId: $cinemaId
      countryId: $countryId
      timezone: $timezone
      isAbsoluteExperiencePath: $isAbsoluteExperiencePath
    ) {
      schedules {
        movieId
        dates {
          date
          languages {
            language
            displayLanguage
            showtimes {
              sessionId
              datetime
              format { name }
              experience { name }
            }
          }
        }
      }
    }
  }
"""

_MOVIE_QUERY = """
  query Movies($countryId: String!, $id: String!, $isAbsoluteMediaPath: Boolean) {
    movie(countryId: $countryId, id: $id, isAbsoluteMediaPath: $isAbsoluteMediaPath) {
      id
      name
      originalName
      length
      synopsis
      media {
        resource
        type
        code
        sizes { large medium small }
      }
    }
  }
"""

_LENGTH_RE = re.compile(r"(\d+)")


def _parse_duration(length_str: str) -> int:
    m = _LENGTH_RE.search(length_str or "")
    return int(m.group(1)) if m else 0


def _is_vose(language: str) -> bool:
    lang = language.upper()
    return lang not in ("ESP", "ESPAÑOL")


def _audio_lang(language: str) -> str:
    lang = language.upper()
    if lang in ("ESP", "ESPAÑOL"):
        return "ES"
    if lang in ("COR", "COREANO"):
        return "KO"
    return "EN"


def _poster_url(media: list) -> str:
    for item in media or []:
        if item.get("code") == "poster":
            return item.get("sizes", {}).get("large", "") or item.get("sizes", {}).get("medium", "")
    return ""


def _fetch_billboard(sess: requests.Session) -> list[dict]:
    r = sess.post(_API, headers=_HEADERS, json={
        "query":     _BILLBOARD_QUERY,
        "variables": {
            "cinemaId":                CINEMA_SLUG,
            "countryId":               COUNTRY_ID,
            "timezone":                TIMEZONE,
            "isAbsoluteExperiencePath": True,
        },
    }, timeout=20)
    r.raise_for_status()
    data = r.json()
    if "errors" in data:
        raise RuntimeError(f"billboardByCinema errors: {data['errors']}")
    return data["data"]["billboardByCinema"]["schedules"]


def _fetch_movie(sess: requests.Session, movie_id: str) -> dict:
    r = sess.post(_API, headers=_HEADERS, json={
        "query":     _MOVIE_QUERY,
        "variables": {
            "countryId":            COUNTRY_ID,
            "id":                   movie_id,
            "isAbsoluteMediaPath":  True,
        },
    }, timeout=15)
    r.raise_for_status()
    data = r.json()
    if "errors" in data:
        log.debug("movie query errors for %s: %s", movie_id, data["errors"])
        return {}
    return data["data"].get("movie") or {}


def scrape_yelmo() -> list[dict]:
    """Fetch Yelmo Campanar listings and return a list of film dicts."""
    log.info("Fetching Yelmo Campanar (Cinépolis) billboard …")

    with requests.Session() as sess:
        schedules = _fetch_billboard(sess)
        log.info("Yelmo Campanar: %d movie slots in billboard", len(schedules))

        # Gather unique movie IDs and their combined showtimes
        movies_raw: dict[str, list[dict]] = {}
        for sched in schedules:
            mid = sched["movieId"]
            if mid not in movies_raw:
                movies_raw[mid] = []
            for day in sched.get("dates", []):
                date_str = day["date"]
                for lang_entry in day.get("languages", []):
                    language = lang_entry.get("displayLanguage", lang_entry.get("language", ""))
                    vose     = _is_vose(language)
                    alang    = _audio_lang(language)
                    for st in lang_entry.get("showtimes", []):
                        dt_str   = st.get("datetime", "")
                        time_str = dt_str[11:16] if len(dt_str) >= 16 else ""
                        fmt_name = st.get("format", {}).get("name", "2D") or "2D"
                        if not time_str:
                            continue
                        movies_raw[mid].append({
                            "date":           date_str,
                            "time":           time_str,
                            "datetime_local": dt_str[:19] if len(dt_str) >= 19 else f"{date_str}T{time_str}:00",
                            "format":         fmt_name,
                            "language":       language,
                            "is_vose":        vose,
                            "audio_lang":     alang,
                            "is_3d":          "3D" in fmt_name.upper(),
                            "is_imax":        "IMAX" in fmt_name.upper(),
                            "is_4dx":         "4DX" in fmt_name.upper(),
                            "booking_url":    "",
                        })

        # Fetch metadata for each movie
        results = []
        for mid, showtimes in movies_raw.items():
            if not showtimes:
                continue
            meta = _fetch_movie(sess, mid)
            time.sleep(0.05)

            name          = meta.get("name") or mid.replace("-", " ").title()
            original_name = meta.get("originalName") or ""
            duration      = _parse_duration(meta.get("length", ""))
            synopsis      = meta.get("synopsis") or ""
            poster        = _poster_url(meta.get("media", []))
            any_vose      = any(s["is_vose"] for s in showtimes)
            vose_langs    = {s["audio_lang"] for s in showtimes if s["is_vose"]}
            alang_out     = next(iter(vose_langs), "ES") if any_vose and len(vose_langs) == 1 else (
                "EN" if any_vose else "ES"
            )

            results.append({
                "cinema":         CINEMA_KEY,
                "cinema_name":    CINEMA_NAME,
                "title_es":       name,
                "original_title": original_name,
                "is_vose":        any_vose,
                "audio_lang":     alang_out,
                "imdb_id":        "",
                "is_film":        True,
                "duration_mins":  duration,
                "poster_url":     poster,
                "synopsis_es":    synopsis,
                "showtimes":      showtimes,
            })

    total = sum(len(f["showtimes"]) for f in results)
    log.info("Yelmo Campanar: %d films, %d sessions", len(results), total)
    return results


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    films = scrape_yelmo()
    total = sum(len(f["showtimes"]) for f in films)
    vose  = [f for f in films if f["is_vose"]]

    print(f"\n{'='*60}")
    print(f"Yelmo Campanar  —  {len(films)} films  /  {total} sessions")
    print(f"VOSE: {len(vose)}")
    print(f"{'='*60}")

    for f in sorted(films, key=lambda x: x["title_es"]):
        vose_tag = "VOSE" if f["is_vose"] else "    "
        langs    = sorted({s["language"] for s in f["showtimes"] if s["is_vose"]})
        lang_str = langs[0][:30] if langs else ""
        dates    = sorted({s["date"] for s in f["showtimes"]})
        d_range  = f"{dates[0]} -> {dates[-1]}" if dates else "?"
        print(f"  [{vose_tag}] {f['title_es'][:45]:<45}  {lang_str:<12} {d_range}")
