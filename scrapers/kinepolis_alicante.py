"""
Kinépolis Alicante scraper (Centro Comercial Plaza Mar 2, 16 screens).

Same source as Kinépolis Valencia: the kinepolis.es cartelera page embeds
sessions for every Spanish complex in window.Drupal.settings, so this reuses
scrapers.kinepolis and keeps only complex KALIC.

Not yet wired into pipeline.py — Alicante scrapers are being built and
checked standalone first. Run directly for a quick test summary.
"""

import logging

from scrapers.kinepolis import scrape_kinepolis_complex

log = logging.getLogger(__name__)

COMPLEX_CODE = "KALIC"
CINEMA_KEY   = "kinepolis_alicante"
CINEMA_NAME  = "Kinépolis Alicante"


def scrape_kinepolis_alicante(playwright=None) -> list[dict]:
    """Scrape Kinépolis Alicante and return a list of film dicts."""
    return scrape_kinepolis_complex(COMPLEX_CODE, CINEMA_KEY, CINEMA_NAME, playwright)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    films = scrape_kinepolis_alicante()

    total_sessions = sum(len(f["showtimes"]) for f in films)
    vose_sessions  = sum(1 for f in films for s in f["showtimes"] if s["is_vose"])
    real_films     = [f for f in films if f["is_film"]]
    vose_films     = [f for f in real_films if f["is_vose"]]

    print(f"\n{'='*60}")
    print(f"{CINEMA_NAME}  —  {len(films)} entries  /  {total_sessions} sessions")
    print(f"Real films (tt*): {len(real_films)}   VOSE films: {len(vose_films)}   "
          f"VOSE sessions: {vose_sessions}")
    print(f"{'='*60}")

    for f in sorted(films, key=lambda x: x["title_es"]):
        vose_tag = "VOSE" if f["is_vose"] else "    "
        film_tag = "film " if f["is_film"] else "event"
        dates    = sorted(set(s["date"] for s in f["showtimes"]))
        print(f"  [{vose_tag}] [{film_tag}] {f['title_es'][:45]:<45}  {f['imdb_id']}  "
              f"{dates[0]}..{dates[-1]} ({len(f['showtimes'])})")
