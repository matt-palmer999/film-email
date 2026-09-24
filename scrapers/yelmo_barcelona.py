"""
Yelmo La Maquinista (Barcelona) scraper.

Uses the same Yelmo API as scrapers/yelmo.py but with cityKey "barcelona"
and targets the Westfield La Maquinista cinema.
"""

import json
import logging
import re
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import requests

log = logging.getLogger(__name__)

BARCELONA_TZ = ZoneInfo("Europe/Madrid")
CINEMA_KEY   = "westfield-la-maquinista"
CINEMA_ID    = "yelmo_maquinista"
CINEMA_NAME  = "Yelmo La Maquinista"
API_URL      = "https://www.yelmocines.es/now-playing.aspx/GetNowPlaying"
HEADERS      = {
    "User-Agent":      "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Content-Type":    "application/json; charset=UTF-8",
    "X-Requested-With": "XMLHttpRequest",
    "Referer":         "https://www.yelmocines.es/cartelera/westfield-la-maquinista",
    "Accept":          "application/json, text/javascript, */*; q=0.01",
    "Origin":          "https://www.yelmocines.es",
}

_DOTNET_TS = re.compile(r"/Date\((\d+)\)/")


def _parse_dotnet_ts(ts_str: str) -> str:
    m = _DOTNET_TS.search(ts_str)
    if not m:
        return ts_str
    ms = int(m.group(1))
    dt = datetime.fromtimestamp(ms / 1000, tz=timezone.utc).astimezone(BARCELONA_TZ)
    return dt.strftime("%Y-%m-%dT%H:%M:%S")


def _is_vose(language: str) -> bool:
    lang = language.upper()
    return "VOSE" in lang or "SUBTITULADO" in lang


def _lang_code(language: str) -> str:
    lang = language.upper()
    if "INGL" in lang:   return "EN"
    if "FRANC" in lang:  return "FR"
    if "JAPON" in lang:  return "JA"
    if "ITAL" in lang:   return "IT"
    if "ALEM" in lang:   return "DE"
    if "PORT" in lang:   return "PT"
    return "ES"


def scrape_yelmo_barcelona() -> list[dict]:
    """Fetch Yelmo La Maquinista listings and return a list of film dicts."""
    log.info("Fetching Yelmo Barcelona now-playing data …")
    r = requests.post(
        API_URL,
        data=json.dumps({"cityKey": "barcelona"}),
        headers=HEADERS,
        timeout=20,
    )
    r.raise_for_status()
    payload = r.json()

    cinemas = payload["d"]["Cinemas"]
    cinema  = next((c for c in cinemas if c["Key"] == CINEMA_KEY), None)
    if cinema is None:
        raise RuntimeError(f"Cinema {CINEMA_KEY!r} not found in Yelmo Barcelona API response")

    log.info("Found cinema: %s (id=%s)", cinema["Name"], cinema["Id"])

    films_map: dict[int, dict] = {}

    for date_entry in cinema["Dates"]:
        filter_date_raw = date_entry.get("FilterDate", "")
        date_str = _parse_dotnet_ts(filter_date_raw)[:10] if filter_date_raw else ""

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
                language   = fmt.get("Language", "")
                fmt_name   = fmt.get("Name", "2D")
                is_vose    = _is_vose(language)
                audio_lang = _lang_code(language)

                for st in fmt.get("Showtimes", []):
                    time_str = st.get("Time", "")
                    if not time_str or not date_str:
                        continue
                    local_dt = f"{date_str}T{time_str}:00"

                    films_map[fid]["showtimes"].append({
                        "date":           date_str,
                        "time":           time_str,
                        "datetime_local": local_dt,
                        "format":         fmt_name,
                        "language":       language,
                        "is_vose":        is_vose,
                        "audio_lang":     audio_lang,
                        "is_3d":          "3D" in fmt_name.upper(),
                        "is_imax":        "IMAX" in fmt_name.upper(),
                        "is_4dx":         "4DX" in fmt_name.upper(),
                    })

    results = []
    for film in films_map.values():
        showtimes = film.pop("showtimes")
        any_vose  = any(s["is_vose"] for s in showtimes)
        vose_langs = {s["audio_lang"] for s in showtimes if s["is_vose"]}
        audio_lang = next(iter(vose_langs), "ES") if any_vose and len(vose_langs) == 1 else (
            "EN" if any_vose else "ES"
        )
        results.append({
            "cinema":         CINEMA_ID,
            "cinema_name":    CINEMA_NAME,
            "title_es":       film["title_es"],
            "original_title": film["original_title"],
            "is_vose":        any_vose,
            "audio_lang":     audio_lang,
            "imdb_id":        "",
            "is_film":        True,
            "duration_mins":  film["runtime_mins"],
            "poster_url":     film["poster_url"],
            "synopsis_es":    film["synopsis_es"],
            "yelmo_key":      film["yelmo_key"],
            "showtimes":      showtimes,
        })

    log.info("Yelmo La Maquinista: %d films, %d sessions",
             len(results), sum(len(f["showtimes"]) for f in results))
    return results


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    films = scrape_yelmo_barcelona()
    total = sum(len(f["showtimes"]) for f in films)
    vose  = [f for f in films if f["is_vose"]]

    print(f"\n{'='*60}")
    print(f"Yelmo La Maquinista  —  {len(films)} films  /  {total} sessions")
    print(f"VOSE: {len(vose)}")
    print(f"{'='*60}")

    for f in sorted(films, key=lambda x: x["title_es"]):
        vose_tag = "VOSE" if f["is_vose"] else "    "
        langs    = sorted({s["language"] for s in f["showtimes"] if s["is_vose"]})
        lang_str = langs[0][:35] if langs else ""
        print(f"  [{vose_tag}] {f['title_es'][:45]:<45}  {lang_str}")
