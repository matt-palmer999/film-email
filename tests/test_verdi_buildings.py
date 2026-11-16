"""Cines Verdi is two buildings (Verdi, C/ Verdi 32, screens 1-5; Verdi Park,
C/ Torrijos 49, screens A-D) listed as one cinema. Each session carries its
building; an unknown building means no label, never a lost session.
Offline: no network.

Run:  python -m unittest discover -s tests -v
"""
import json
import logging
import os
import re
import sys
import unittest
from datetime import datetime, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
for _k in ("SMTP_HOST", "SMTP_USER", "SMTP_PASSWORD", "FROM_EMAIL", "SUPABASE_URL", "SUPABASE_KEY"):
    os.environ.setdefault(_k, "test")
os.environ.setdefault("SMTP_PORT", "587")
logging.disable(logging.CRITICAL)

import pipeline  # noqa: E402
from scrapers import verdi_barcelona  # noqa: E402

TODAY = datetime.now(pipeline.VALENCIA_TZ).date()
D0 = TODAY.isoformat()
D1 = (TODAY + timedelta(days=1)).isoformat()
ANCHOR = datetime.combine(TODAY, datetime.min.time())

_P = '<p class="font-display text-left text-sm text-gray-600">Cines Verdi Barcelona | {}</p>'


class ParseBuilding(unittest.TestCase):
    def test_screens(self):
        self.assertEqual(verdi_barcelona.parse_building(_P.format("Sala D V.Park")), "park")
        self.assertEqual(verdi_barcelona.parse_building(_P.format("Sala A")), "park")
        self.assertEqual(verdi_barcelona.parse_building(_P.format("Sala 3")), "verdi")
        self.assertEqual(verdi_barcelona.parse_building(_P.format("Sala 5 ")), "verdi")

    def test_unreadable_is_none(self):
        self.assertIsNone(verdi_barcelona.parse_building(""))
        self.assertIsNone(verdi_barcelona.parse_building("<title>Cines Verdi Barcelona</title>"))
        self.assertIsNone(verdi_barcelona.parse_building(_P.format("Sala X")))


def _raw():
    st = lambda t, b, vose=True: {"date": D0, "time": t, "is_vose": vose, "booking_url": "u" + t,
                                  "building": b}
    return [{"cinema": "verdi_barcelona", "title_es": "Wild Horse Nine", "is_film": True,
             "showtimes": [st("16:00", "verdi"), st("18:00", "park"), st("20:00", None),
                           st("22:00", "park", vose=False)]}]


def _page_film(films):
    f = films["Wild Horse Nine"]
    f.update({"title": "Wild Horse Nine", "title_en": "Wild Horse Nine", "slug": "wild-horse-nine",
              "meta": "", "synopsis": "", "is_new": False, "rating": "12", "poster": "",
              "any_vose": True, "year": "2026", "origin_country": ["US"], "original_language": "en"})
    return f


def _row(html, cid):
    part = html.split(f'<div class="showtime-row" data-cinema-id="{cid}"', 1)[1]
    return part.split('<div class="showtime-row"', 1)[0].split('<div class="day-panel', 1)[0]


class MergeAndPage(unittest.TestCase):
    def test_merge_keeps_every_session_and_its_building(self):
        c = pipeline.merge_scraper_results(_raw())["Wild Horse Nine"]["cinemas"][0]
        self.assertEqual(sorted(c["vose_showtimes"][D0]), ["16:00", "18:00", "20:00"])
        self.assertEqual(sorted(c["showtimes"][D0]), ["22:00"])
        self.assertEqual(c["building"], {D0: {"16:00": "verdi", "18:00": "park", "22:00": "park"}})

    def test_other_cinemas_get_no_building_map(self):
        raw = [{"cinema": "lys", "title_es": "X", "is_film": True,
                "showtimes": [{"date": D0, "time": "18:00", "is_vose": False}]}]
        self.assertNotIn("building", pipeline.merge_scraper_results(raw)["X"]["cinemas"][0])

    def test_dedup_merge_keeps_buildings(self):
        dst = {"id": "verdi_barcelona", "showtimes": {}, "vose_showtimes": {}, "building": {D0: {"16:00": "verdi"}}}
        src = {"id": "verdi_barcelona", "showtimes": {}, "vose_showtimes": {D0: {"18:00": ""}},
               "building": {D0: {"18:00": "park"}}}
        pipeline._merge_cinema_times(dst, src)
        self.assertEqual(dst["building"], {D0: {"16:00": "verdi", "18:00": "park"}})

    def test_film_page_groups_times_by_building(self):
        html = pipeline.build_film_detail_page(_page_film(pipeline.merge_scraper_results(_raw())), ANCHOR)
        row = _row(html, "verdi_barcelona")
        self.assertIn(">Cines Verdi<", row)            # still one cinema
        groups = row.split('<div class="bldg-group">')[1:]
        self.assertEqual(len(groups), 3)               # Verdi, Verdi Park, unlabelled
        self.assertIn("C/ Verdi 32", groups[0])
        self.assertEqual(re.findall(r'data-time="([^"]+)"', groups[0]), ["16:00"])
        self.assertIn("Verdi Park", groups[1])
        self.assertIn("C/ Torrijos 49", groups[1])
        self.assertEqual(re.findall(r'data-time="([^"]+)"', groups[1]), ["18:00", "22:00"])
        self.assertNotIn("bldg-label", groups[2])      # unknown building: no label
        self.assertEqual(re.findall(r'data-time="([^"]+)"', groups[2]), ["20:00"])
        self.assertIn("Torrijos", re.findall(r'href="([^"]+maps[^"]+)"', groups[1])[0])

    def test_no_building_data_means_plain_row(self):
        raw = _raw()
        for st in raw[0]["showtimes"]:
            st["building"] = None
        html = pipeline.build_film_detail_page(_page_film(pipeline.merge_scraper_results(raw)), ANCHOR)
        row = _row(html, "verdi_barcelona")
        row = row.split('class="showtime-legend"', 1)[0]
        self.assertNotIn('class="bldg-', row)
        self.assertIn("cinema-map-link", row)

    def test_jsonld_uses_each_sessions_building(self):
        f = _page_film(pipeline.merge_scraper_results(_raw()))
        ld = pipeline._build_jsonld(f, "wild-horse-nine")
        data = json.loads(re.search(r"<script[^>]*>(.*?)</script>", ld, re.S).group(1)) if "<script" in ld else json.loads(ld)
        nodes = data.get("@graph", [data]) if isinstance(data, dict) else data
        evs = {e["startDate"][11:16]: e["location"] for e in nodes if e.get("@type") == "ScreeningEvent"}
        self.assertEqual(evs["16:00"]["address"]["streetAddress"], "C/ Verdi 32")
        self.assertEqual(evs["18:00"]["address"]["streetAddress"], "C/ Torrijos 49")
        self.assertIn("Verdi Park", evs["18:00"]["name"])
        self.assertEqual(evs["20:00"]["name"], "Cines Verdi")    # unknown: the cinema itself


class UnknownFlag(unittest.TestCase):
    def test_counts_sessions_without_building(self):
        films = pipeline.merge_scraper_results(_raw())
        self.assertEqual(pipeline.unknown_buildings(films, D0), {"verdi_barcelona": (1, 4)})

    def test_carry_forward_trims_buildings(self):
        e = {"showtimes": {}, "vose_showtimes": {}, "building": {"2000-01-01": {"10:00": "park"}, D1: {"10:00": "verdi"}}}
        pipeline._future_only(e, D0)
        self.assertEqual(e["building"], {D1: {"10:00": "verdi"}})


if __name__ == "__main__":
    unittest.main()
