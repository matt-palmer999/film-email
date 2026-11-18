"""English city pages at /en/listings/{city}/.

The same page as the Spanish one, but with the English text written into the
HTML (crawlers that don't run JavaScript read English), its own URL and
English titles. Both versions point at each other with hreflang.

Run:  python -m unittest discover -s tests -v
"""
import json
import logging
import os
import re
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
for _k in ("SMTP_HOST", "SMTP_USER", "SMTP_PASSWORD", "FROM_EMAIL", "SUPABASE_URL", "SUPABASE_KEY"):
    os.environ.setdefault(_k, "test")
os.environ.setdefault("SMTP_PORT", "587")
logging.disable(logging.CRITICAL)

import pipeline  # noqa: E402

TODAY = datetime.now(pipeline.VALENCIA_TZ).date()
D0 = TODAY.isoformat()
ANCHOR = datetime.now(pipeline.VALENCIA_TZ)


def _films():
    return {
        "El cielo": {
            "title": "El cielo", "title_es": "El cielo", "title_en": "The Sky", "slug": "the-sky",
            "meta": "ES · 2026 · 100 min", "meta_en": "ES · 2026 · Drama · 100 min",
            "synopsis": "Una película.", "synopsis_es": "Una película.", "synopsis_en": "A film.",
            "is_new": True, "rating": "12", "poster": "", "any_vose": True, "year": "2026",
            "origin_country": ["ES"], "original_language": "en",
            "cinemas": [{"id": "lys", "name": "Cines Lys", "website": "", "type": "multiplex", "vose": True,
                         "showtimes": {D0: {"18:00": "u"}}, "vose_showtimes": {D0: {"21:00": "u"}},
                         "format_showtimes": {}}],
        },
    }


class RenderLanguage(unittest.TestCase):
    def test_each_bilingual_element_gets_the_english_content(self):
        page = ('<title data-es="Cartelera" data-en="Listings">Cartelera</title>'
                '<h1 data-es="Cartelera&lt;br&gt;Valencia" data-en="Cinema&lt;br&gt;Valencia">Cartelera<br>Valencia</h1>'
                '<div data-es="Hola" data-en="Hello &amp; welcome"><span>Hola</span></div>'
                '<p>Sin traducir</p>'
                '<script>var x = \'<span data-es="no" data-en="no">no</span>\';</script>')
        out = pipeline.render_language(page, "en")
        self.assertIn("<title data-es=\"Cartelera\" data-en=\"Listings\">Listings</title>", out)
        self.assertIn(">Cinema<br>Valencia</h1>", out)
        self.assertIn(">Hello & welcome</div>", out)
        self.assertIn("<p>Sin traducir</p>", out)              # untranslated text is left alone
        self.assertIn("var x = '<span data-es=\"no\"", out)    # script text untouched

    def test_nested_bilingual_elements_are_replaced_once(self):
        page = '<div data-es="A <b>uno</b>" data-en="B <b>one</b>">A <span data-es="uno" data-en="one">uno</span></div>'
        self.assertEqual(pipeline.render_language(page, "en"),
                         '<div data-es="A <b>uno</b>" data-en="B <b>one</b>">B <b>one</b></div>')


class EnglishCityPage(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.es = pipeline.build_html(_films(), ANCHOR, city="Valencia")
        cls.en = pipeline.build_html(_films(), ANCHOR, city="Valencia", lang="en")

    def test_english_page_has_its_own_url_and_english_head(self):
        en = self.en
        self.assertIn('<html lang="en" id="html-root">', en)
        self.assertIn('<link rel="canonical" href="https://whatson.movie/en/listings/valencia/">', en)
        title = re.search(r"<title[^>]*>([^<]*)</title>", en).group(1)
        self.assertTrue(title.startswith("Valencia cinema listings: VOSE & original-language films"), title)
        self.assertNotIn("films in English", en)                  # the safer wording, as on the FAQ
        self.assertRegex(en, r'<meta name="description" content="1 films? at 1 Valencia cinemas? this week')
        self.assertIn('<meta property="og:locale" content="en_GB">', en)
        self.assertIn('class="lang-btn active" id="btn-en"', en)
        self.assertIn('window.PAGE_LANG     = "en";', en)

    def test_english_text_is_in_the_html(self):
        self.assertIn(">The Sky</a>", self.en)
        self.assertIn(">A film.</div>", self.en)
        self.assertIn(">Subscribe</a>", self.en)
        self.assertNotIn(">Suscribirse</a>", self.en)

    def test_links_work_one_folder_deeper(self):
        self.assertIn('href="/listings/the-sky/" class="grid-title"', self.en)
        self.assertIn('href="/preferences/"', self.en)
        self.assertNotIn('href="../the-sky/"', self.en)

    def test_both_versions_point_at_each_other(self):
        for page in (self.es, self.en):
            self.assertIn('<link rel="alternate" hreflang="es" href="https://whatson.movie/listings/valencia/">', page)
            self.assertIn('<link rel="alternate" hreflang="en" href="https://whatson.movie/en/listings/valencia/">', page)
            self.assertIn('<link rel="alternate" hreflang="x-default" href="https://whatson.movie/listings/valencia/">', page)

    def test_spanish_page_is_still_spanish(self):
        es = self.es
        self.assertIn('<html lang="es" id="html-root">', es)
        self.assertIn('<link rel="canonical" href="https://whatson.movie/listings/valencia/">', es)
        self.assertIn('window.PAGE_LANG     = "";', es)
        self.assertIn(">El cielo</a>", es)
        self.assertIn('href="../the-sky/" class="grid-title"', es)

    def test_vose_times_are_the_same_on_both(self):
        attr = f'data-v-lys_{D0}="21:00"'
        self.assertIn(attr, self.es)
        self.assertIn(attr, self.en)


class EnglishCityChooser(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cities = ["Valencia", "Barcelona"]
        cls.es = pipeline.build_city_chooser_html(cities, _films(), ANCHOR)
        cls.en = pipeline.build_city_chooser_html(cities, _films(), ANCHOR, lang="en")

    def test_english_chooser_is_english_at_its_own_url(self):
        en = self.en
        self.assertIn('<html lang="en" id="html-root">', en)
        self.assertIn('<link rel="canonical" href="https://whatson.movie/en/listings/">', en)
        self.assertIn("<title>Cinema listings in Barcelona and Valencia: VOSE & original-language films", en)
        self.assertIn("Choose your <span>city</span>", en)
        self.assertIn(">CINEMA LISTINGS</div>", en)
        self.assertIn(">1 films this week</div>", en)
        self.assertIn('class="lang-btn active" id="btn-en"', en)

    def test_city_cards_open_the_english_city_pages(self):
        # relative ./valencia/ from /en/listings/ is /en/listings/valencia/
        self.assertIn('<a href="./valencia/" class="city-card"', self.en)
        self.assertIn('href="/privacy/"', self.en)
        self.assertNotIn('href="../privacy/"', self.en)

    def test_both_choosers_point_at_each_other(self):
        for page in (self.es, self.en):
            self.assertIn('<link rel="alternate" hreflang="es" href="https://whatson.movie/listings/">', page)
            self.assertIn('<link rel="alternate" hreflang="en" href="https://whatson.movie/en/listings/">', page)

    def test_spanish_chooser_unchanged_for_visitors(self):
        self.assertIn('<html lang="es" id="html-root">', self.es)
        self.assertIn("<title>Elige tu ciudad · whatson.movie</title>", self.es)
        self.assertIn("Elige tu <span>ciudad</span>", self.es)
        self.assertIn('href="../privacy/"', self.es)
        self.assertIn("if (saved === 'en' && !false) setLang('en');", self.es)

    def test_landing_footer_link_follows_the_language(self):
        with open(os.path.join(ROOT, "docs", "index.html"), encoding="utf-8") as fh:
            page = fh.read()
        self.assertIn('data-href-es="./listings/" data-href-en="./en/listings/"', page)
        self.assertIn("querySelectorAll('[data-href-es][data-href-en]')", page)


class WrittenBySite(unittest.TestCase):
    def test_rebuild_writes_english_pages_into_the_sitemap(self):
        with tempfile.TemporaryDirectory() as d:
            os.chdir(d)
            try:
                os.makedirs("docs/data")
                os.makedirs("docs/listings")
                films = {f"F{i}": dict(_films()["El cielo"], title=f"F{i}", slug=f"f-{i}") for i in range(12)}
                with open("docs/data/films_cache.json", "w", encoding="utf-8") as fh:
                    json.dump(films, fh)
                with mock.patch.object(pipeline, "SUPABASE_URL", "https://example.supabase.co"), \
                        mock.patch.object(pipeline, "SUPABASE_ANON", "anon-key"):
                    pipeline.rebuild()
                with open("docs/en/listings/valencia/index.html", encoding="utf-8") as fh:
                    en = fh.read()
                with open("docs/sitemap.xml", encoding="utf-8") as fh:
                    sitemap = fh.read()
            finally:
                os.chdir(ROOT)
        self.assertNotIn("__SUPABASE_URL__", en)                  # sign-in works on the English page
        self.assertIn("<loc>https://whatson.movie/en/listings/valencia/</loc>", sitemap)
        self.assertIn("<loc>https://whatson.movie/listings/valencia/</loc>", sitemap)
        self.assertIn("<loc>https://whatson.movie/en/listings/</loc>", sitemap)


if __name__ == "__main__":
    unittest.main()
