"""Plain-language summaries on city and film pages. They are built from the
same showtime data as the page and must never claim VOSE without a VOSE
session. Offline: no network.

Run:  python -m unittest discover -s tests -v
"""
import json
import logging
import os
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

TODAY = datetime.now(pipeline.VALENCIA_TZ).date()
D0, D2 = TODAY.isoformat(), (TODAY + timedelta(days=2)).isoformat()
LATER = (TODAY + timedelta(days=20)).isoformat()      # outside the 7-day window
ANCHOR = datetime.combine(TODAY, datetime.min.time())


def _c(cid, std=None, vose=None, fmt=None, ensub=None):
    return {"id": cid, "name": pipeline.CINEMA_META[cid]["name"], "website": "", "type": "multiplex",
            "vose": bool(vose), "showtimes": std or {}, "vose_showtimes": vose or {},
            "format_showtimes": fmt or {}, "ensub": ensub or {}}


def _f(title, cinemas, lang="en", year="2026"):
    return {"title": title, "title_es": title, "title_en": title + " (EN)", "slug": title.lower(),
            "original_language": lang, "year": year, "cinemas": cinemas}


class FilmSummary(unittest.TestCase):
    def test_counts_vose_cinemas_without_listing_them(self):
        f = _f("Hamnet", [_c("lys", vose={D0: {"18:00": ""}}), _c("yelmo", std={D2: {"20:00": ""}})])
        es, en = pipeline.film_summary(f)
        self.assertIn("en 2 cines de Valencia, 1 de ellos en VOSE.", es)
        self.assertIn("at 2 cinemas in Valencia, 1 of them in VOSE.", en)
        self.assertNotIn("Lys", es)                         # names are left to the showtimes

    def test_one_cinema_is_named(self):
        f = _f("Porco Rosso", [_c("verdi_barcelona", vose={D0: {"18:00": ""}})], year="1992")
        self.assertTrue(pipeline.film_summary(f)[1].endswith("at Cines Verdi (Barcelona), in VOSE."))

    def test_vose_in_premium_format_counts(self):
        f = _f("Dune", [_c("kinepolis", std={D0: {"18:00": ""}},
                           fmt={"imax": {D0: {"21:00": {"url": "", "is_vose": True}}}})])
        self.assertIn(", en VOSE", pipeline.film_summary(f)[0])

    def test_vose_outside_the_window_is_not_claimed(self):
        f = _f("Late", [_c("lys", std={D0: {"18:00": ""}}), _c("babel", std={D0: {"18:00": ""}},
                                                              vose={LATER: {"18:00": ""}})])
        es, en = pipeline.film_summary(f)
        self.assertTrue(es.endswith(", ninguno en VOSE."))
        self.assertTrue(en.endswith(", none in VOSE."))

    def test_spanish_films_never_mention_vose(self):
        f = _f("La bola negra", [_c("lys", std={D0: {"18:00": ""}}), _c("babel", std={D0: {"18:00": ""}})],
               lang="es")
        es, en = pipeline.film_summary(f)
        self.assertNotIn("VOSE", es + en)

    def test_english_subtitles_and_city_order(self):
        f = _f("Naza", [_c("verdi_barcelona", std={D0: {"18:00": ""}}, ensub={D0: {"18:00": True}}),
                        _c("lys", std={D2: {"20:00": ""}})], lang="es")
        es, en = pipeline.film_summary(f)
        self.assertIn("de Valencia y Barcelona; con subtítulos en inglés en 1 de ellos.", es)

    def test_date_span(self):
        f = _f("One", [_c("lys", vose={D0: {"18:00": ""}})])
        self.assertIn(f"el {pipeline._DAYS_ES[TODAY.weekday()]} {TODAY.day} de", pipeline.film_summary(f)[0])


class CitySummary(unittest.TestCase):
    def test_counts(self):
        films = {
            "A": _f("A", [_c("lys", vose={D0: {"18:00": ""}}), _c("babel", vose={D0: {"20:00": ""}})]),
            "B": _f("B", [_c("lys", std={D0: {"18:00": ""}})]),
            "C": _f("C", [_c("yelmo", std={D0: {"18:00": ""}}), _c("verdi_barcelona", vose={D0: {"18:00": ""}})]),
        }
        es, en = pipeline.city_summary(films, "Valencia", ANCHOR)
        self.assertIn("3 películas en 3 cines de Valencia", es)
        self.assertIn(" 1 en VOSE", es)                     # Verdi is Barcelona, not Valencia
        self.assertIn("3 films at 3 cinemas in Valencia", en)

    def test_join(self):
        self.assertEqual(pipeline._join(["A", "B", "C", "D"], "es", 3), "A, B, C y D")
        self.assertEqual(pipeline._join(["A", "B", "C", "D", "E"], "en", 3, ("cines", "cinemas")),
                         "A, B, C and 2 more cinemas")


class OnThePages(unittest.TestCase):
    def test_city_page_summary_is_visible_and_bilingual(self):
        f = _f("Hamnet", [_c("lys", vose={D0: {"18:00": "u"}})])
        f.update({"meta": "", "synopsis": "", "is_new": False, "rating": "12", "poster": "",
                  "any_vose": True, "origin_country": ["GB"]})
        page = pipeline.build_film_detail_page(f, ANCHOR)
        self.assertNotIn('class="hero-summary"', page)    # city pages only, for now
        city = pipeline.build_html({"Hamnet": f}, ANCHOR, city="Valencia")
        self.assertIn('<div class="header-summary" data-es="Esta semana', city)


class LandingFaq(unittest.TestCase):
    def _films(self):
        return {
            "A": _f("A & B", [_c("lys", vose={D0: {"18:00": ""}}), _c("babel", vose={D0: {"20:00": ""}})]),
            "B": _f("B", [_c("yelmo", std={D0: {"18:00": ""}})]),
            "C": _f("Naza", [_c("verdi_barcelona", std={D0: {"18:00": ""}}, ensub={D0: {"18:00": True}})],
                    lang="es"),
        }

    def test_numbers_match_the_city_pages(self):
        films = self._films()
        st = pipeline.city_stats(films, "Valencia")
        self.assertEqual((st["films"], st["cinemas"], st["vose_films"]), (2, 3, 1))
        faq = pipeline.build_landing_faq(films, ANCHOR)
        self.assertIn("Esta semana hay 1 película en VOSE", faq)
        self.assertIn("This week there is 1 film in VOSE", faq)
        self.assertIn("no hay sesiones en VOSE en Barcelona", faq)
        self.assertIn('href="./listings/valencia/"', faq)
        self.assertIn("«Naza»", faq)                         # English subtitles, by name

    def test_answers_are_escaped_and_marked_as_html(self):
        faq = pipeline.build_landing_faq(self._films(), ANCHOR)
        self.assertIn('<p data-html data-es="', faq)
        self.assertNotIn("A & B", faq)                       # raw & never reaches the page

    def test_only_the_marked_block_is_replaced(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "index.html")
            page = "<p>top</p>\n" + pipeline.FAQ_START + "old" + pipeline.FAQ_END + "\n<p>bottom</p>"
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(page)
            pipeline.write_landing_faq(self._films(), ANCHOR, path)
            with open(path, encoding="utf-8") as fh:
                out = fh.read()
            self.assertTrue(out.startswith("<p>top</p>") and out.endswith("<p>bottom</p>"))
            self.assertNotIn("old", out)
            self.assertIn("Preguntas frecuentes", out)
            pipeline.write_landing_faq(self._films(), ANCHOR, path)   # idempotent
            with open(path, encoding="utf-8") as fh:
                self.assertEqual(fh.read().count(pipeline.FAQ_START), 1)
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("<p>no markers</p>")
            pipeline.write_landing_faq(self._films(), ANCHOR, path)   # left alone, no crash
            with open(path, encoding="utf-8") as fh:
                self.assertEqual(fh.read(), "<p>no markers</p>")

    def test_live_landing_page_has_the_markers(self):
        with open(os.path.join(ROOT, "docs", "index.html"), encoding="utf-8") as fh:
            page = fh.read()
        self.assertEqual(page.count(pipeline.FAQ_START), 1)
        self.assertIn("el.hasAttribute('data-html')", page)   # language switch keeps links


class LandingStats(unittest.TestCase):
    def test_counts_are_written_into_the_page(self):
        import tempfile
        films = {"A": _f("A", [_c("lys", vose={D0: {"18:00": ""}}), _c("babel", std={D0: {"20:00": ""}})]),
                 "B": _f("B", [_c("lys", std={D0: {"18:00": ""}}), _c("dor", std={LATER: {"18:00": ""}})])}
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "index.html")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write('<span id="stat-cinemas" data-count="27" style="x">27</span> '
                         '<span id="stat-films" data-count="111" style="y">0</span>+')
            self.assertEqual(pipeline.write_landing_stats(films, path), (2, 2))   # D'Or only later
            with open(path, encoding="utf-8") as fh:
                out = fh.read()
        self.assertIn('<span id="stat-cinemas" data-count="2" style="x">2</span>', out)
        self.assertIn('<span id="stat-films" data-count="2" style="y">2</span>+', out)

    def test_live_landing_page_reads_both_counts(self):
        with open(os.path.join(ROOT, "docs", "index.html"), encoding="utf-8") as fh:
            page = fh.read()
        self.assertRegex(page, r'<span id="stat-cinemas" data-count="\d+"[^>]*>\d+</span>')
        self.assertRegex(page, r'<span id="stat-films" data-count="\d+"[^>]*>\d+</span>')
        self.assertNotIn("'stat-cinemas'), 27,", page)          # no hard-coded count


if __name__ == "__main__":
    unittest.main()
