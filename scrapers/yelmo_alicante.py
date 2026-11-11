"""
Yelmo Puerta de Alicante scraper (13 screens; cineyelmo.com lists it as
"Alicante Gate" / "Puerta de Alicante").

Same Cinépolis GraphQL API as Yelmo Campanar, so this reuses scrapers.yelmo.
The API cinemaId is "puerta-de-alicante-albacete" — Yelmo's own data has the
wrong city in the slug; found by picking the cinema on cineyelmo.com
(2026-11-11). Coordinates in that record (38.342, -0.518) are Alicante.

Not yet wired into pipeline.py — Alicante scrapers are being built and
checked standalone first. Run directly for a quick test summary.
"""

import logging

from scrapers.yelmo import scrape_yelmo_cinema

log = logging.getLogger(__name__)

CINEMA_SLUG = "puerta-de-alicante-albacete"
CINEMA_KEY  = "yelmo_alicante"
CINEMA_NAME = "Yelmo Puerta de Alicante"


def scrape_yelmo_alicante() -> list[dict]:
    """Fetch Yelmo Puerta de Alicante listings and return a list of film dicts."""
    return scrape_yelmo_cinema(CINEMA_SLUG, CINEMA_KEY, CINEMA_NAME)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    films = scrape_yelmo_alicante()
    total = sum(len(f["showtimes"]) for f in films)
    vose_sessions = sum(1 for f in films for s in f["showtimes"] if s["is_vose"])
    vose  = [f for f in films if f["is_vose"]]

    print(f"\n{'='*60}")
    print(f"{CINEMA_NAME}  —  {len(films)} films  /  {total} sessions")
    print(f"VOSE films: {len(vose)}   VOSE sessions: {vose_sessions}")
    print(f"{'='*60}")

    for f in sorted(films, key=lambda x: x["title_es"]):
        vose_tag = "VOSE" if f["is_vose"] else "    "
        langs    = sorted({s["language"] for s in f["showtimes"] if s["is_vose"]})
        lang_str = langs[0][:30] if langs else ""
        dates    = sorted({s["date"] for s in f["showtimes"]})
        d_range  = f"{dates[0]} -> {dates[-1]}" if dates else "?"
        print(f"  [{vose_tag}] {f['title_es'][:45]:<45}  {lang_str:<12} {d_range} ({len(f['showtimes'])})")
