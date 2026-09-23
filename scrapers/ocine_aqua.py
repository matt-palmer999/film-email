"""
Ocine Premium Aqua scraper.

Uses the ticketing API at tickets.ocinepremiumaqua.es — plain POST requests,
no authentication or browser required.

Flow:
  POST /api/v1/init     → token (not actually needed for subsequent calls)
  POST /api/v1/sessions → list of film group IDs
  GET  /api/v1/pelicula/{id}?lang=es → full detail with subPelicules and sessions

Each film group (esGrup=True) contains subPelicules for different formats/rooms.
VOSE sessions are identified by "Versión VOSE" in propietatsDesc.
"""

import logging
import re

import requests

log = logging.getLogger(__name__)

CINEMA_KEY  = "ocine_aqua"
CINEMA_NAME = "Ocine Premium Aqua"
BASE_URL    = "https://tickets.ocinepremiumaqua.es"

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Content-Type": "application/json",
    "Referer": BASE_URL + "/",
}

_FORMAT_RE = re.compile(
    r"\s*\((?:3D|4D|ATMOS|URBAN|KIDS|VOSE|ICE|Premium|Screen\s*X)[^)]*\)",
    re.IGNORECASE,
)


def _clean_title(raw: str) -> str:
    return _FORMAT_RE.sub("", raw).strip()


def _props_to_format(props: list[str]) -> str:
    parts = []
    for p in props:
        p_up = p.upper()
        if "3D" in p_up:
            parts.append("3D")
        if "ATMOS" in p_up:
            parts.append("ATMOS")
        if "ICE" in p_up:
            parts.append("ICE")
        if "4D" in p_up:
            parts.append("4DX")
        if "SCREEN X" in p_up:
            parts.append("ScreenX")
    return " ".join(parts) if parts else "2D"


def scrape_ocine_aqua() -> list[dict]:
    """Scrape Ocine Premium Aqua via the ticketing API and return film dicts."""
    log.info("Fetching Ocine Premium Aqua sessions …")
    session = requests.Session()
    session.headers.update(_HEADERS)

    try:
        r_sessions = session.post(f"{BASE_URL}/api/v1/sessions", json={}, timeout=20)
        r_sessions.raise_for_status()
        film_groups = r_sessions.json().get("pelicules", [])
    except Exception as exc:
        log.error("Ocine Aqua: could not fetch sessions list: %s", exc)
        raise

    results: list[dict] = []

    for group in film_groups:
        group_id = group.get("id")
        if not group_id:
            continue

        try:
            r_detail = session.get(
                f"{BASE_URL}/api/v1/pelicula/{group_id}?lang=es",
                timeout=20,
            )
            r_detail.raise_for_status()
            detail = r_detail.json()
        except Exception as exc:
            log.warning("Ocine Aqua: could not fetch film %s: %s", group_id, exc)
            continue

        base_title = _clean_title(detail.get("titol", "").strip())
        synopsis   = detail.get("sinopsis", "")
        duration   = int(detail.get("durada", 0) or 0)
        genre      = detail.get("genereComercial", "")
        trailer    = detail.get("videoExtern", "")

        sub_films = detail.get("subPelicules") or []
        if not sub_films:
            # Leaf film, not a group
            sub_films = [detail]

        for sub in sub_films:
            sessions_raw = sub.get("sessions") or []
            if not sessions_raw:
                continue

            props       = sub.get("propietatsDesc") or []
            is_vose     = any("vose" in p.lower() for p in props)
            fmt         = _props_to_format(props)
            is_3d       = "3D" in fmt
            is_atmos    = "ATMOS" in fmt
            is_4dx      = "4DX" in fmt

            showtimes: list[dict] = []
            for s in sessions_raw:
                date_str = s.get("data", "")
                hora     = s.get("hora", "")[:5]   # HH:MM
                plan_id  = s.get("planificacio", "")
                if not date_str or not hora:
                    continue

                booking_url = (
                    f"{BASE_URL}/compra/{plan_id}"
                    if plan_id else ""
                )
                showtimes.append({
                    "datetime_local": f"{date_str}T{hora}:00",
                    "date":           date_str,
                    "time":           hora,
                    "format":         fmt,
                    "is_vose":        is_vose,
                    "audio_lang":     "EN" if is_vose else "ES",
                    "is_3d":          is_3d,
                    "is_imax":        False,
                    "is_4dx":         is_4dx,
                    "booking_url":    booking_url,
                })

            if not showtimes:
                continue

            results.append({
                "cinema":         CINEMA_KEY,
                "cinema_name":    CINEMA_NAME,
                "title_es":       base_title,
                "original_title": detail.get("titolOriginal", base_title),
                "is_vose":        is_vose,
                "audio_lang":     "EN" if is_vose else "ES",
                "imdb_id":        "",
                "is_film":        True,
                "duration_mins":  duration,
                "poster_url":     "",
                "synopsis_es":    synopsis,
                "genre":          genre,
                "trailer_url":    trailer,
                "showtimes":      showtimes,
            })

    total = sum(len(f["showtimes"]) for f in results)
    log.info("Ocine Aqua: %d film entries, %d sessions", len(results), total)
    return results


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    films = scrape_ocine_aqua()
    total = sum(len(f["showtimes"]) for f in films)
    vose  = [f for f in films if f["is_vose"]]

    print(f"\n{'='*60}")
    print(f"Ocine Premium Aqua  --  {len(films)} entries  /  {total} sessions")
    print(f"VOSE entries: {len(vose)}")
    print(f"{'='*60}")

    for f in sorted(films, key=lambda x: (x["title_es"], x["is_vose"])):
        tag    = "VOSE" if f["is_vose"] else "    "
        dates  = sorted({s["date"] for s in f["showtimes"]})
        n      = len(f["showtimes"])
        d_range = f"{dates[0]} → {dates[-1]}" if dates else "?"
        print(f"  [{tag}] {f['title_es'][:45]:<45} {n:3d} sessions  {d_range}")
