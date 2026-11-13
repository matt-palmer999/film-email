"""
Kinépolis La Cañada (Marbella, CC La Cañada, 8 screens) — formerly Cinesa.

Same source as Kinépolis Valencia/Alicante: the kinepolis.es cartelera page
embeds sessions for every Spanish complex, so this reuses scrapers.kinepolis
and keeps only complex ESMAR ("Kinépolis La Cañada (Marbella)").
"""

import logging

from scrapers.kinepolis import scrape_kinepolis_complex

log = logging.getLogger(__name__)

COMPLEX_CODE = "ESMAR"
CINEMA_KEY   = "kinepolis_marbella"
CINEMA_NAME  = "Kinépolis La Cañada (Marbella)"


def scrape_kinepolis_marbella(playwright=None) -> list[dict]:
    """Scrape Kinépolis La Cañada and return a list of film dicts."""
    return scrape_kinepolis_complex(COMPLEX_CODE, CINEMA_KEY, CINEMA_NAME, playwright)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    films = scrape_kinepolis_marbella()
    st = [s for f in films for s in f["showtimes"]]
    print(f"{CINEMA_NAME}: {len(films)} entries, {len(st)} sessions, "
          f"{sum(1 for f in films if f['is_vose'] for _ in f['showtimes'])} VOSE")
