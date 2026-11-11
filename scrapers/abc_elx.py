"""
Cines ABC Elx scraper (Centro Comercial L'Aljub, Elche, 11 screens).

Same reservaentradas platform as the Valencia ABC cinemas, so this reuses
scrapers.abc. Booking name "abcelx", recinto 174 (found by probing the
reservaentradas index endpoint, 2026-11-11). Booking links keep abc.py's
".../entrada/valencia/abcelx/..." form — the city segment is ignored by
reservaentradas (checked: /valencia/ and /elche/ open the same ABC Elx page).

Kept out of abc.CINEMAS on purpose so it does not join the Valencia run.
Not yet wired into pipeline.py — Alicante-area scrapers are being built and
checked standalone first. Run directly for a quick test summary.
"""

import logging

from scrapers.abc import scrape_abc_cinemas

log = logging.getLogger(__name__)

CINEMA = {
    "key":     "abc_elx",
    "name":    "Cines ABC Elx",
    "base":    "https://www.cinesabc.com/cine/abcelx",
    "booking": "abcelx",
    "recinto": 174,
}


def scrape_abc_elx() -> list[dict]:
    """Fetch Cines ABC Elx listings and return a list of film dicts."""
    return scrape_abc_cinemas([CINEMA])


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    films = scrape_abc_elx()
    total = sum(len(f["showtimes"]) for f in films)
    vose_sessions = sum(1 for f in films for s in f["showtimes"] if s["is_vose"])
    vose = [f for f in films if f["is_vose"]]

    print(f"\n{'='*60}")
    print(f"{CINEMA['name']}  —  {len(films)} films  /  {total} sessions")
    print(f"VOSE films: {len(vose)}   VOSE sessions: {vose_sessions}")
    print(f"{'='*60}")

    for f in sorted(films, key=lambda x: x["title_es"]):
        v = sum(1 for s in f["showtimes"] if s["is_vose"])
        vose_tag = "VOSE" if f["is_vose"] else "    "
        dates = sorted({s["date"] for s in f["showtimes"] if s["date"]})
        d_range = f"{dates[0]} -> {dates[-1]}" if dates else "?"
        print(f"  [{vose_tag}] {f['title_es'][:45]:<45}  {d_range}  ({len(f['showtimes'])}, VOSE {v})")
