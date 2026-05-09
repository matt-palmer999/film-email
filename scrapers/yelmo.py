"""
Yelmo Campanar scraper.

A plain POST request to yelmocines.es/now-playing.aspx/GetNowPlaying
returns all films and showtimes as JSON — no browser needed.

Returns a list of film dicts in the same format as scrapers/kinepolis.py.
Run directly for a quick test summary.
"""

import json
import logging
import re
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import requests

log = logging.getLogger(__name__)

VALENCIA_TZ = ZoneInfo("Europe/Madrid")
CINEMA_KEY  = "mercado-de-campanar"
CINEMA_NAME = "Yelmo Campanar"
API_URL     = "https://www.yelmocines.es/now-playing.aspx/GetNowPlaying"
HEADERS     = {
    "User-Agent":      "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Content-Type":    "application/json; charset=UTF-8",
    "X-Requested-With": "XMLHttpRequest",
    "Referer":         "https://www.yelmocines.es/cartelera/campanar",
    "Accept":          "application/json, text/javascript, */*; q=0.01",
    "Origin":          "https://www.yelmocines.es",
}

# /Date(1778374800000)/ — .NET JSON timestamp (ms since Unix epoch)
_DOTNET_TS = re.compile(r"/Date\((\d+)\)/")


def _parse_dotnet_ts(ts_str: str) -> str:
    """Return ISO local datetime string from a .NET /Date(ms)/ value."""
    m = _DOTNET_TS.search(ts_str)
    if not m:
        return ts_str
    ms = int(m.group(1))
    dt = datetime.fromtimestamp(ms / 1000, tz=timezone.utc).astimezone(VALENCIA_TZ)
    return dt.strftime("%Y-%m-%dT%H:%M:%S")


def _is_vose(language: str) -> bool:
    lang = language.upper()
    return "VOSE" in lang or "SUBTITULADO" in lang


def _lang_code(language: str) -> str:
    """Best-effort 2-letter audio language code from the Spanish label."""
    lang = language.upper()
    if "INGL" in lang:   return "EN"
    if "FRANC" in lang:  return "FR"
    if "JAPON" in lang:  return "JA"
    if "ITAL" in lang:   return "IT"
    if "ALEM" in lang:   return "DE"
    if "PORT" in lang:   return "PT"
    return "ES"


def scrape_yelmo() -> list[dict]:
    """Fetch Yelmo Campanar listings and return a list of film dicts."""
    log.info("Fetching Yelmo now-playing data …")
    r = requests.post(
        API_URL,
        data=json.dumps({"cityKey": "valencia"}),
        headers=HEADERS,
        timeout=20,
    )
    r.raise_for_status()
    payload = r.json()

    cinemas = payload["d"]["Cinemas"]
    cinema  = next((c for c in cinemas if c["Key"] == CINEMA_KEY), None)
    if cinema is None:
        log.error("Cinema %r not found in response (available: %s)",
                  CINEMA_KEY, [c["Key"] for c in cinemas])
        return []

    log.info("Found cinema: %s (id=%s)", cinema["Name"], cinema["Id"])

    # Aggregate films across all dates
    # Structure: Dates > Movies > Formats > Showtimes
    films_map: dict[int, dict] = {}

    for date_entry in cinema["Dates"]:
        for movie in date_entry["Movies"]:
            fid = movie["Id"]
            if fid not in films_map:
                films_map[fid] = {
                    "id":             fid,
                    "title_es":       movie["Title"],
                    "original_title": movie.get("OriginalTitle", "") or "",
                    "runtime_mins":   int(movie.get("RunTime") or 0),
                    "poster_url":     movie.get("Poster", ""),
                    "synopsis_es":    movie.get("Synopsis", "") or "",
                    "yelmo_key":      movie.get("Key", ""),
                    "showtimes":      [],
                }

            for fmt in movie.get("Formats", []):
                language  = fmt.get("Language", "")
                fmt_name  = fmt.get("Name", "2D")
                is_vose   = _is_vose(language)
                audio_lang = _lang_code(language)

                for st in fmt.get("Showtimes", []):
                    ts_str    = st.get("TimeFilter", "")
                    time_str  = st.get("Time", "")
                    local_dt  = _parse_dotnet_ts(ts_str) if ts_str else ""

                    films_map[fid]["showtimes"].append({
                        "datetime_local": local_dt,
                        "time":           time_str,
                        "format":         fmt_name,
                        "language":       language,
                        "is_vose":        is_vose,
                        "audio_lang":     audio_lang,
                        "is_3d":          "3D" in fmt_name.upper(),
                        "is_imax":        "IMAX" in fmt_name.upper(),
                        "is_4dx":         "4DX" in fmt_name.upper(),
                    })

    # Build output list — attach top-level VOSE flag if ANY showtime is VOSE
    results = []
    for film in films_map.values():
        showtimes = film.pop("showtimes")
        any_vose  = any(s["is_vose"] for s in showtimes)
        # If ALL showtimes are VOSE, use the first audio lang; else mark ES
        vose_langs = {s["audio_lang"] for s in showtimes if s["is_vose"]}
        audio_lang = next(iter(vose_langs), "ES") if any_vose and len(vose_langs) == 1 else (
            "EN" if any_vose else "ES"
        )
        results.append({
            "cinema":       "yelmo",
            "cinema_name":  CINEMA_NAME,
            "title_es":     film["title_es"],
            "original_title": film["original_title"],
            "is_vose":      any_vose,
            "audio_lang":   audio_lang,
            "imdb_id":      "",          # not provided by Yelmo API
            "is_film":      True,        # Yelmo doesn't mix in events
            "duration_mins": film["runtime_mins"],
            "poster_url":   film["poster_url"],
            "synopsis_es":  film["synopsis_es"],
            "yelmo_key":    film["yelmo_key"],
            "showtimes":    showtimes,
        })

    log.info("Yelmo: %d films, %d sessions",
             len(results), sum(len(f["showtimes"]) for f in results))
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
        dates    = sorted(set(s["datetime_local"][:10] for s in f["showtimes"] if s["datetime_local"]))
        langs    = sorted({s["language"] for s in f["showtimes"] if s["is_vose"]})
        lang_str = langs[0][:35] if langs else ""
        print(f"  [{vose_tag}] {f['title_es'][:45]:<45}  {lang_str}")
