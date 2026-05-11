"""
Ocine Premium Aqua scraper.

Uses a public JSON endpoint served by the Joomla/com_cines component.
No authentication or browser required — a plain GET returns the full
cartelera for the current week, including session times and IDs.

Format tags (VOSE, 3D, ATMOS, URBAN, IMAX) appear inside parentheses
at the end of peli_titol and are stripped to produce title_es.

Run directly for a quick test summary.
"""

import logging
import re

import requests

log = logging.getLogger(__name__)

CINEMA_KEY   = "ocine_aqua"
CINEMA_NAME  = "Ocine Premium Aqua"
BASE_URL     = "https://www.ocinepremiumaqua.es"
CARTELERA_URL = f"{BASE_URL}/components/com_cines/json/es_cartellera.json"

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "es-ES,es;q=0.9",
}

_FORMAT_TAG_RE = re.compile(
    r"\s*\((?:VOSE|3D|ATMOS|URBAN|IMAX|4DX|3D\s+VOSE)[^)]*\)",
    re.IGNORECASE,
)


def _clean_title(raw: str) -> str:
    return _FORMAT_TAG_RE.sub("", raw).strip()


def _detect_formats(raw: str) -> dict:
    upper = raw.upper()
    return {
        "is_vose":  "(VOSE)"  in upper,
        "is_3d":    "(3D)"    in upper,
        "is_atmos": "(ATMOS)" in upper,
        "is_imax":  "(IMAX)"  in upper,
        "is_4dx":   "(4DX)"   in upper,
    }


def _make_format_label(flags: dict) -> str:
    parts = []
    if flags["is_3d"]:
        parts.append("3D")
    if flags["is_atmos"]:
        parts.append("ATMOS")
    if flags["is_imax"]:
        parts.append("IMAX")
    if flags["is_4dx"]:
        parts.append("4DX")
    if flags["is_vose"]:
        parts.append("VOSE")
    return " ".join(parts) if parts else "2D"


def scrape_ocine_aqua() -> list[dict]:
    """Scrape Ocine Premium Aqua and return a list of film dicts."""
    log.info("Fetching Ocine Premium Aqua cartelera …")
    try:
        r = requests.get(CARTELERA_URL, headers=_HEADERS, timeout=20)
        r.raise_for_status()
        data = r.json()
    except Exception as exc:
        log.error("Could not fetch Ocine Aqua cartelera: %s", exc)
        return []

    results: list[dict] = []

    for film in data.get("data", []):
        sessions_raw = film.get("Planificacions") or []
        if not sessions_raw:
            continue

        raw_title = film.get("peli_titol", "").strip()
        flags     = _detect_formats(raw_title)
        title_es  = _clean_title(raw_title)

        film_id   = film.get("peli_pelicula", "")
        dur_raw   = film.get("peli_durada", 0)
        duration  = int(dur_raw) if str(dur_raw).isdigit() else 0

        synopsis  = (film.get("Pelicules2") or {}).get("pel2_sinopsis") or ""
        genre     = film.get("peli_generacomercial", "")

        showtimes: list[dict] = []
        for s in sessions_raw:
            date_str = (s.get("plan_data") or "").strip()       # YYYY-MM-DD
            time_raw = (s.get("plan_horainici") or "").strip()   # HH:MM:SS
            plan_id  = s.get("plan_planificacio", "")

            if not date_str or not time_raw:
                continue

            time_str = time_raw[:5]  # HH:MM

            booking_url = (
                f"{BASE_URL}/?option=com_cines&task=compra&id={plan_id}"
                if plan_id else ""
            )

            showtimes.append({
                "datetime_local": f"{date_str}T{time_str}:00",
                "date":           date_str,
                "time":           time_str,
                "format":         _make_format_label(flags),
                "is_vose":        flags["is_vose"],
                "audio_lang":     "EN" if flags["is_vose"] else "ES",
                "is_3d":          flags["is_3d"],
                "is_imax":        flags["is_imax"],
                "is_4dx":         flags["is_4dx"],
                "booking_url":    booking_url,
            })

        if not showtimes:
            continue

        results.append({
            "cinema":         CINEMA_KEY,
            "cinema_name":    CINEMA_NAME,
            "title_es":       title_es,
            "original_title": "",
            "is_vose":        flags["is_vose"],
            "audio_lang":     "EN" if flags["is_vose"] else "ES",
            "imdb_id":        "",
            "is_film":        True,
            "duration_mins":  duration,
            "poster_url":     "",
            "synopsis_es":    synopsis,
            "genre":          genre,
            "showtimes":      showtimes,
        })

    total = sum(len(f["showtimes"]) for f in results)
    log.info("Ocine Aqua: %d films, %d sessions", len(results), total)
    return results


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    films = scrape_ocine_aqua()
    total = sum(len(f["showtimes"]) for f in films)
    vose  = [f for f in films if f["is_vose"]]

    print(f"\n{'='*60}")
    print(f"Ocine Premium Aqua  --  {len(films)} films  /  {total} sessions")
    print(f"VOSE: {len(vose)}")
    print(f"{'='*60}")

    for f in sorted(films, key=lambda x: x["title_es"]):
        fmt    = f["showtimes"][0]["format"] if f["showtimes"] else "?"
        dates  = sorted({s["date"] for s in f["showtimes"] if s["date"]})
        n      = len(f["showtimes"])
        d_range = f"{dates[0]} -> {dates[-1]}" if dates else "?"
        print(f"  [{fmt:<10}] {f['title_es'][:45]:<45} {n:3d} sessions  {d_range}")
