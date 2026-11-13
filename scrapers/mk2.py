"""
mk2 cinemas (ex-Cinesur) in the Málaga area.

mk2cines.es is a server-rendered listings site, but every session's buy link
goes to cine.entradas.com — the same kinoheld GraphQL platform as Cines Aana —
so this reuses scrapers.aana.scrape_entradas_cinema.

entradas.com cinema id = the number right after `"cinemaStrings":N},` in the
cine.entradas.com/cine/{path}/sesiones page payload.

mk2 Málaga Nostrum Premium (id 1760, path malaga/cinesur-malaga-nostrum) has
been closed since ~14 Oct 2026 ("incidencia técnico-administrativa"). It's
listed below with enabled=False — flip it on once it reopens.
"""

import logging

import requests

from scrapers.aana import scrape_entradas_cinema

log = logging.getLogger(__name__)

VENUES = [
    {"key": "mk2_miramar", "name": "mk2 Miramar", "cinema_id": "1746",
     "path": "fuengirola/cinesur-miramar-fuengirola-3d", "enabled": True},
    {"key": "mk2_el_ingenio", "name": "mk2 El Ingenio", "cinema_id": "1690",
     "path": "velez-malaga/cinesur-el-ingenio", "enabled": True},
    {"key": "mk2_malaga_nostrum", "name": "mk2 Málaga Nostrum", "cinema_id": "1760",
     "path": "malaga/cinesur-malaga-nostrum", "enabled": False},
]


def scrape_mk2_malaga() -> list[dict]:
    """Fetch the enabled mk2 Málaga-area venues. Raises if none returns anything."""
    sess = requests.Session()
    films = []
    for v in VENUES:
        if not v["enabled"]:
            continue
        films += scrape_entradas_cinema(v["cinema_id"], v["path"], v["key"], v["name"], sess)
    if not films:
        raise RuntimeError("mk2: no films returned from entradas.com")
    return films


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    films = scrape_mk2_malaga()
    for v in VENUES:
        fs = [f for f in films if f["cinema"] == v["key"]]
        st = [s for f in fs for s in f["showtimes"]]
        print(f"{v['name']}: {len(fs)} films, {len(st)} sessions, {sum(s['is_vose'] for s in st)} VOSE")
