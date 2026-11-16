"""Pipeline safety nets that were once lost by accident.

On 2026-11-09 a commit made from an out-of-date copy of pipeline.py silently
removed the carry-forward for failed scrapers and the cinema id check, and
put back the wrong Yelmo Campanar id. These tests make sure that can't happen
quietly again. Offline: no network, no browser.

Run:  python -m unittest discover -s tests -v
"""
import importlib
import json
import logging
import os
import pkgutil
import re
import sys
import tempfile
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
import scrapers  # noqa: E402

TODAY = datetime.now(pipeline.VALENCIA_TZ).date()
D_PAST = (TODAY - timedelta(days=1)).isoformat()
D0 = TODAY.isoformat()
D1 = (TODAY + timedelta(days=1)).isoformat()


def _pref_ids() -> set:
    with open(pipeline.PREFERENCES_PAGE, encoding="utf-8") as fh:
        return set(re.findall(r"\{\s*id:\s*'([^']+)'", fh.read()))


class CinemaIds(unittest.TestCase):
    def test_yelmo_campanar_keeps_the_saved_preferences_id(self):
        from scrapers import yelmo
        self.assertEqual(yelmo.CINEMA_KEY, "yelmo")
        self.assertIn("yelmo", pipeline.CINEMA_META)
        self.assertIn("yelmo", _pref_ids())

    def test_every_scraper_cinema_key_is_known_everywhere(self):
        prefs = _pref_ids()
        for mod in pkgutil.iter_modules(scrapers.__path__):
            m = importlib.import_module(f"scrapers.{mod.name}")
            key = getattr(m, "CINEMA_KEY", None)
            if isinstance(key, str):
                with self.subTest(scraper=mod.name, key=key):
                    self.assertIn(key, pipeline.CINEMA_META)
                    self.assertIn(key, prefs)

    def test_check_flags_an_id_users_cannot_have_saved(self):
        films = {"X": {"cinemas": [{"id": "mercado-de-campanar"}]}}
        problems = pipeline.check_cinema_ids(films)
        self.assertTrue(any("mercado-de-campanar" in p for p in problems))

    def test_check_is_clean_for_known_ids(self):
        films = {"X": {"cinemas": [{"id": "yelmo"}, {"id": "lys"}]}}
        self.assertEqual(pipeline.check_cinema_ids(films), [])


class CarryForward(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "last_ok.json")

    def tearDown(self):
        self.tmp.cleanup()

    def _prev(self):
        lys = {
            "id": "lys", "name": "Cines Lys", "vose": True,
            "showtimes": {D_PAST: {"16:00": ""}, D0: {"18:00": "u1"}},
            "vose_showtimes": {D1: {"22:00": "u2"}},
            "format_showtimes": {"imax": {D_PAST: {"20:00": {"url": "", "is_vose": True}}}},
            "ensub": {D_PAST: {"16:00": True}},
        }
        return {
            "Film A": {"title": "Film A", "tmdb_id": 1, "slug": "film-a", "cinemas": [lys]},
            "Old Film": {"title": "Old Film", "tmdb_id": 2, "slug": "old-film", "cinemas": [{
                "id": "lys", "showtimes": {D_PAST: {"16:00": ""}}, "vose_showtimes": {}}]},
        }

    def _today(self):
        return {"Film A": {"title": "Film A", "tmdb_id": 1, "slug": "film-a",
                           "cinemas": [{"id": "babel", "showtimes": {D0: {"17:00": ""}}, "vose_showtimes": {}}]}}

    def test_failed_cinema_is_carried_with_future_sessions_only(self):
        films = self._today()
        carried = pipeline.carry_forward_failed_cinemas(films, self._prev(), TODAY, D_PAST, path=self.path)
        self.assertEqual(set(carried), {"lys"})
        lys = next(c for c in films["Film A"]["cinemas"] if c["id"] == "lys")
        self.assertEqual(lys["showtimes"], {D0: {"18:00": "u1"}})
        self.assertEqual(lys["vose_showtimes"], {D1: {"22:00": "u2"}})
        self.assertEqual(lys["format_showtimes"], {})
        self.assertEqual(lys["ensub"], {})
        self.assertTrue(lys["vose"])
        self.assertNotIn("Old Film", films)   # nothing left from today on

    def test_dubbed_only_cinema_is_not_made_vose(self):
        prev = self._prev()
        prev["Film A"]["cinemas"][0]["vose_showtimes"] = {}
        prev["Film A"]["cinemas"][0]["vose"] = True    # stale flag must not survive
        films = self._today()
        pipeline.carry_forward_failed_cinemas(films, prev, TODAY, D_PAST, path=self.path)
        lys = next(c for c in films["Film A"]["cinemas"] if c["id"] == "lys")
        self.assertFalse(lys["vose"])

    def test_stops_after_max_days(self):
        old = (TODAY - timedelta(days=pipeline.MAX_CARRY_DAYS + 1)).isoformat()
        with open(self.path, "w", encoding="utf-8") as fh:
            json.dump({"lys": old}, fh)
        films = self._today()
        carried = pipeline.carry_forward_failed_cinemas(films, self._prev(), TODAY, D_PAST, path=self.path)
        self.assertEqual(carried, {})

    def test_records_successes_and_pins_first_failure(self):
        films = self._today()
        pipeline.carry_forward_failed_cinemas(films, self._prev(), TODAY, D_PAST, path=self.path)
        with open(self.path, encoding="utf-8") as fh:
            last_ok = json.load(fh)
        self.assertEqual(last_ok["babel"], D0)
        self.assertEqual(last_ok["lys"], D_PAST)

    def test_unknown_ids_are_not_carried(self):
        prev = self._prev()
        prev["Film A"]["cinemas"][0]["id"] = "mercado-de-campanar"
        films = self._today()
        carried = pipeline.carry_forward_failed_cinemas(films, prev, TODAY, D_PAST, path=self.path)
        self.assertEqual(carried, {})

    def test_run_still_calls_the_safety_nets(self):
        import inspect
        src = inspect.getsource(pipeline.run)
        self.assertIn("carry_forward_failed_cinemas(", src)
        self.assertIn("check_cinema_ids(", src)


class TmdbOverrides(unittest.TestCase):
    def test_overrides_file_is_valid(self):
        with open(pipeline.TMDB_OVERRIDES_PATH, encoding="utf-8") as fh:
            data = json.load(fh)
        for k, v in data.items():
            if not k.startswith("_"):
                self.assertTrue(v is None or isinstance(v, int), k)
        self.assertEqual(data.get("La Violinista"), 748681)


if __name__ == "__main__":
    unittest.main()
