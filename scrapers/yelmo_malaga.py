"""
Yelmo cinemas in Málaga: Vialia (María Zambrano station, 13 screens) and
Plaza Mayor (near the airport), plus Rincón de la Victoria (~15 km east).

Same Cinépolis GraphQL API as Yelmo Campanar — reuses scrapers.yelmo. The API
cinemaIds were found by trial on 2026-11-13: Vialia is "vialia-malaga-malaga"
(not "vialia-malaga"), Plaza Mayor is "plaza-mayor-malaga". Yelmo Rincón de la
Victoria is "rincon-de-la-victoria-malaga".
"""

import logging

from scrapers.yelmo import scrape_yelmo_cinema

log = logging.getLogger(__name__)

VENUES = [
    ("vialia-malaga-malaga", "yelmo_vialia",      "Yelmo Vialia Málaga"),
    ("plaza-mayor-malaga",   "yelmo_plaza_mayor", "Yelmo Plaza Mayor"),
    ("rincon-de-la-victoria-malaga", "yelmo_rincon", "Yelmo Rincón de la Victoria"),
]


def scrape_yelmo_malaga() -> list[dict]:
    """Fetch both Málaga Yelmos. A failure in one doesn't lose the other; raises if both fail."""
    films, errors = [], []
    for slug, key, name in VENUES:
        try:
            films += scrape_yelmo_cinema(slug, key, name)
        except Exception as exc:
            log.error("%s failed: %s", name, exc)
            errors.append(f"{name}: {exc}")
    if errors and not films:
        raise RuntimeError("; ".join(errors))
    return films


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    films = scrape_yelmo_malaga()
    for _slug, key, name in VENUES:
        fs = [f for f in films if f["cinema"] == key]
        st = [s for f in fs for s in f["showtimes"]]
        print(f"{name}: {len(fs)} films, {len(st)} sessions, {sum(s['is_vose'] for s in st)} VOSE")
