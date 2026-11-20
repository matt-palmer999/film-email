"""`python pipeline.py --rebuild`: rewrite the pages from films_cache.json.

It must never scrape, call TMDB/YouTube/Bing, send any email, or touch the
files that record real runs (carry-forward, stats.json date, films cache,
VOSE baseline). These tests run a real rebuild in a temporary folder with
every outside call booby-trapped. Offline: no network.

Run:  python -m unittest discover -s tests -v
"""
import json
import logging
import os
import sys
import tempfile
import unittest
import urllib.request
from datetime import datetime, timedelta
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
for _k in ("FROM_EMAIL", "SUPABASE_URL", "SUPABASE_KEY"):
    os.environ.setdefault(_k, "test")
logging.disable(logging.CRITICAL)

import mailer  # noqa: E402
import pipeline  # noqa: E402
import requests  # noqa: E402

TODAY = datetime.now(pipeline.VALENCIA_TZ).date()
D0 = TODAY.isoformat()
D1 = (TODAY + timedelta(days=1)).isoformat()
OLD = (TODAY - timedelta(days=20)).isoformat()


def _film(i, dates):
    title = f"Film {i}"
    return title, {
        "title": title, "title_es": title, "title_en": title, "slug": f"film-{i}",
        "meta": "100 min", "synopsis": "", "is_new": False, "rating": "12", "poster": "",
        "any_vose": True, "year": "2026", "origin_country": ["US"], "original_language": "en",
        "cinemas": [{"id": "lys", "name": "Cines Lys", "website": "", "type": "multiplex", "vose": True,
                     "showtimes": {d: {"18:00": "u"} for d in dates},
                     "vose_showtimes": {d: {"21:00": "u"} for d in dates},
                     "format_showtimes": {}}],
    }


# Files that record real runs: a rebuild must leave them byte-for-byte alone
RECORD_FILES = {
    "docs/data/stats.json": '{"film_count": 12, "updated": "%s"}' % OLD,
    "docs/data/cinema_last_ok.json": '{"lys": "%s"}' % OLD,
    "docs/data/vose_counts.json": '{"lys": 3}',
    "docs/data/scraper_counts.json": '{"lys": 12}',
    "docs/llms.txt": "# whatson.movie\n- Last updated: %s (set by each run)\n" % OLD,   # rebuild must not move it
}


def _read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _boom(name):
    def f(*a, **k):
        raise AssertionError(f"--rebuild must not call {name}")
    return f


class Rebuild(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.chdir(self.tmp.name)
        os.makedirs("docs/data")
        os.makedirs("docs/listings")
        films = dict(_film(i, [D0, D1]) for i in range(12))
        k, gone = _film(99, [OLD])          # nothing left to show: must be dropped
        films[k] = gone
        self.cache = json.dumps(films, ensure_ascii=False)
        with open("docs/data/films_cache.json", "w", encoding="utf-8") as fh:
            fh.write(self.cache)
        for path, body in RECORD_FILES.items():
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(body)
        traps = {
            "pipeline.aggregate_scrapers": "scrapers", "pipeline.enrich_with_tmdb": "TMDB",
            "pipeline.enrich_with_trailers": "YouTube", "pipeline.submit_indexnow": "IndexNow",
            "pipeline.carry_forward_failed_cinemas": "carry-forward",
            "pipeline.send_pipeline_summary": "the admin email", "pipeline.send_weekly_emails": "the weekly email",
            "mailer.send_email": "any email", "requests.get": "the network", "requests.post": "the network",
            "requests.Session.request": "the network", "urllib.request.urlopen": "the network",
            "smtplib.SMTP": "SMTP", "smtplib.SMTP_SSL": "SMTP",
        }
        self.patches = [mock.patch(t, _boom(n)) for t, n in traps.items()]
        self.patches += [mock.patch.object(pipeline, "SUPABASE_URL", "https://example.supabase.co"),
                         mock.patch.object(pipeline, "SUPABASE_ANON", "anon-key")]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        os.chdir(ROOT)
        self.tmp.cleanup()

    def test_rebuild_writes_pages_and_touches_nothing_else(self):
        pipeline.rebuild()
        for i in range(12):
            self.assertTrue(os.path.exists(f"docs/listings/film-{i}/index.html"))
        city = _read("docs/listings/valencia/index.html")
        self.assertIn(f'data-v-lys_{D0}="21:00"', city)
        self.assertNotIn("__SUPABASE_URL__", city)        # credentials were injected
        self.assertFalse(os.path.exists("docs/listings/film-99"))   # nothing to show
        self.assertTrue(os.path.exists("docs/sitemap.xml"))
        for path, body in RECORD_FILES.items():
            with self.subTest(path=path):
                self.assertEqual(_read(path), body)
        self.assertEqual(_read("docs/data/films_cache.json"), self.cache)

    def test_refuses_without_supabase_credentials(self):
        with mock.patch.object(pipeline, "SUPABASE_ANON", ""):
            with self.assertRaises(SystemExit):
                pipeline.rebuild()
        self.assertFalse(os.path.exists("docs/listings/valencia"))

    def test_refuses_with_an_empty_cache(self):
        with open("docs/data/films_cache.json", "w", encoding="utf-8") as fh:
            fh.write("{}")
        with self.assertRaises(SystemExit):
            pipeline.rebuild()
        self.assertFalse(os.path.exists("docs/listings/valencia"))


    def _archive(self, slug, age_days):
        os.makedirs(f"docs/listings/{slug}")
        page = pipeline.build_film_archive_page({"title": slug, "slug": slug}, datetime.now(pipeline.VALENCIA_TZ))
        with open(f"docs/listings/{slug}/index.html", "w", encoding="utf-8") as fh:
            fh.write(page)
        with open(f"docs/listings/{slug}/archived_on.txt", "w", encoding="utf-8") as fh:
            fh.write((TODAY - timedelta(days=age_days)).isoformat())

    def test_old_archive_pages_fade_out_before_deletion(self):
        self._archive("recent", 30)
        self._archive("old", pipeline.ARCHIVE_NOINDEX_DAYS + 10)
        self._archive("expired", pipeline.ARCHIVE_MAX_DAYS + 1)
        pipeline.rebuild()
        sitemap = _read("docs/sitemap.xml")
        self.assertIn("/listings/recent/", sitemap)
        self.assertNotIn("noindex", _read("docs/listings/recent/index.html"))
        old = _read("docs/listings/old/index.html")
        self.assertNotIn("/listings/old/", sitemap)                 # out of the sitemap…
        self.assertEqual(old.count(pipeline.NOINDEX_TAG), 1)        # …and noindex, once
        self.assertIn("archive-notice-label", old)                  # still a working page
        self.assertFalse(os.path.exists("docs/listings/expired"))   # deleted at 180 days
        pipeline.rebuild()                                          # a second run doesn't add it twice
        self.assertEqual(_read("docs/listings/old/index.html").count(pipeline.NOINDEX_TAG), 1)


class SharedPageCode(unittest.TestCase):
    def test_run_and_rebuild_use_the_same_page_writer(self):
        import inspect
        self.assertIn("write_site(", inspect.getsource(pipeline.run))
        self.assertIn("write_site(", inspect.getsource(pipeline.rebuild))
        src = inspect.getsource(pipeline.rebuild)
        for name in ("send_", "aggregate_scrapers", "carry_forward", "run_vose_check", "enrich_"):
            self.assertNotIn(name, src.split('"""')[2], f"rebuild() must not call {name}")


if __name__ == "__main__":
    unittest.main()
