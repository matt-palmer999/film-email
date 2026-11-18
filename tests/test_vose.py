"""VOSE accuracy tests.

Showing a dubbed session as VOSE is the worst mistake the site can make, so
every rule that decides VOSE is pinned down here with real examples taken
from the cinemas' own data (including the cases that were wrong before the
2026-11-16 audit). Offline: no network, no browser.

Run:  python -m unittest discover -s tests -v
"""
import copy
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
import scraper  # noqa: E402
import vose_check  # noqa: E402
from scrapers import (abc, aana, aribau_barcelona, babel, cinesa, dor, kinepolis, lys, mn4,  # noqa: E402
                      mooby_arenas, mooby_gloeries, renoir_barcelona, verdi_barcelona, yelmo,
                      yelmo_barcelona)

TODAY = datetime.now(pipeline.VALENCIA_TZ).date()
D0 = TODAY.isoformat()
D1 = (TODAY + timedelta(days=1)).isoformat()


# ── Scraper label rules ──────────────────────────────────────────────────────

class LabelRules(unittest.TestCase):

    def test_yelmo_labels(self):
        for mod in (yelmo, yelmo_barcelona):
            for label in ("SUB ESP", "INGLÉS", "COR", "JAPONÉS"):
                self.assertTrue(mod._is_vose(label), f"{mod.__name__}: {label} is original version")
            # Dubbed, and labels we've never seen, must never become VOSE
            for label in ("ESPAÑOL", "ESP", "CATALÁN", "CAT", "NO SUB", "VALENCIÀ", "SOMETHING NEW", ""):
                self.assertFalse(mod._is_vose(label), f"{mod.__name__}: {label} must not be VOSE")
            self.assertEqual(mod._audio_lang("JAPONÉS"), "JA")
            self.assertEqual(mod._audio_lang("ESPAÑOL"), "ES")
            mod._warned.clear()
            with self.assertNoLogs(mod.log, level="WARNING"):
                self.assertFalse(mod._is_vose("NO SUB"))   # known dubbed label: no warning

    def test_verdi_uses_version_not_language(self):
        v = verdi_barcelona._is_vose
        self.assertTrue(v({"version": "V.O. SUB. CASTELLANO", "language": "Inglés"}))
        self.assertTrue(v({"version": "V.O. SUB. INGLÉS", "language": ""}))
        # Real Verdi entries that used to be shown as VOSE:
        self.assertFalse(v({"version": "CATALÁN", "language": "Catalán"}))            # "11 (CAT)"
        self.assertFalse(v({"version": "VARIOS", "language": "Catalán, Castellano, Inglés"}))
        self.assertFalse(v({"version": "CASTELLANO", "language": "Castellano"}))

    def test_kinepolis_session_attributes(self):
        k = kinepolis._session_is_vose
        self.assertTrue(k("2D,English,es", False))
        self.assertTrue(k("2D,CineK,English,es,IMAX,IMAX W", True))
        self.assertTrue(k("2D,es,Japanese", True))
        # Kinépolis titles dubbed films "VOSE ..." (audioLanguage EN): sessions decide
        self.assertFalse(k("2D,nosubt,Spanish", True))
        self.assertFalse(k("2D,4DX,nosubt,Spanish", True))
        self.assertFalse(k("2D,Catalan,nosubt", True))
        # No language attribute at all: never guess VOSE from the film flag
        self.assertFalse(k("2D", True))
        self.assertFalse(k("", True))

    def test_babel_language(self):
        self.assertFalse(babel._is_vose("Castellano"))
        self.assertTrue(babel._is_vose("Inglés"))
        self.assertTrue(babel._is_vose("Inglés, español"))
        self.assertFalse(babel._is_vose("Castellano, francés"))     # "Jaleos": a Spanish film

    def test_version_label_scrapers(self):
        for mod in (aribau_barcelona, mooby_arenas, mooby_gloeries):
            self.assertTrue(mod._is_vose("VOSE"))
            self.assertTrue(mod._is_vose("VOSE ATMOS", "Anglès"))
            self.assertTrue(mod._is_vose("VOSI", "Coreano,"))          # Korean audio, English subs
            self.assertFalse(mod._is_vose("VOSI", "Espanyol,"))        # "La bola negra (VOSI)": Spanish audio
            self.assertFalse(mod._is_vose("DOBLADA ESP", "Espanyol,"))
            self.assertFalse(mod._is_vose("ESP"))
            self.assertFalse(mod._is_vose("CAT", "Català"))
        self.assertTrue(renoir_barcelona._is_vose("Versión Original subtitulada a Castellano"))
        self.assertFalse(renoir_barcelona._is_vose("Versión Original Castellano"))
        for mod in (dor, lys, mn4):
            self.assertTrue(mod._is_vose("VOSE"))
            self.assertFalse(mod._is_vose("DIGITAL"))
        self.assertTrue(abc._fmt_is_vose("(VOSE)"))
        self.assertFalse(abc._fmt_is_vose(""))

    def test_entradas_flags_per_session(self):  # Aana and mk2
        base = {"beginning": f"{D0}T19:30:00", "urlSlug": "x"}
        vose = aana._parse_show({**base, "flags": [{"code": "subtitled", "name": "VOSE"}]}, "p")
        dub = aana._parse_show({**base, "flags": [{"code": "2d", "name": "2D"}]}, "p")
        self.assertTrue(vose["is_vose"])
        self.assertFalse(dub["is_vose"])


# ── Cinesa Bonaire: per-session flags (the six-month bug) ────────────────────

class CinesaBonaire(unittest.TestCase):

    def _payloads(self):
        films = {"films": [{"id": "HG", "title": {"text": "Los juegos del hambre"}, "eventId": None,
                            "runtimeInMinutes": 159}]}
        dates = {"filmScreeningDates": [{"businessDate": D0, "filmScreenings": [
            {"filmId": "HG", "sites": [{"siteId": cinesa.SITE_ID, "showtimeAttributeIds": [cinesa.VOSE_ATTR]}]}]}]}

        def st(sid, hhmm, attrs):
            return {"id": sid, "filmId": "HG", "attributeIds": attrs,
                    "schedule": {"startsAt": f"{D0}T{hhmm}:00+01:00"}}
        shows = {D0: [st("a", "17:15", [cinesa.VOSE_ATTR]),   # VOSE
                      st("b", "17:15", []),                   # dubbed, same time, other screen
                      st("c", "19:30", []),                   # dubbed
                      st("d", "21:00", [])]}                  # dubbed
        return films, dates, shows

    def test_only_vose_sessions_are_vose(self):
        res = cinesa._build_results(*self._payloads())
        self.assertEqual(len(res), 1)
        sts = res[0]["showtimes"]
        vose = sorted(s["time"] for s in sts if s["is_vose"])
        dub = sorted(s["time"] for s in sts if not s["is_vose"])
        self.assertEqual(vose, ["17:15"])
        self.assertEqual(dub, ["17:15", "19:30", "21:00"])


# ── Pipeline merge ───────────────────────────────────────────────────────────

class Merge(unittest.TestCase):

    def test_split_and_defaults(self):
        raw = [{
            "cinema": "kinepolis", "title_es": "La Odisea", "is_vose": True, "is_film": True,
            "showtimes": [
                {"date": D0, "time": "17:00", "is_vose": True, "booking_url": "u1", "is_imax": True},
                {"date": D0, "time": "20:45", "is_vose": False, "booking_url": "u2", "is_imax": True},
                {"date": D0, "time": "18:00", "is_vose": False, "booking_url": "u3"},
                {"date": D0, "time": "19:00", "is_vose": True, "booking_url": "u4"},
                # No per-session flag on a "VOSE" film: must be treated as dubbed
                {"date": D0, "time": "22:00", "booking_url": "u5"},
            ]}]
        films = pipeline.merge_scraper_results(raw)
        c = films["La Odisea"]["cinemas"][0]
        self.assertEqual(sorted(c["vose_showtimes"][D0]), ["19:00"])
        self.assertEqual(sorted(c["showtimes"][D0]), ["18:00", "22:00"])
        imax = c["format_showtimes"]["imax"][D0]
        self.assertTrue(imax["17:00"]["is_vose"])
        self.assertFalse(imax["20:45"]["is_vose"])


class SpanishOriginal(unittest.TestCase):

    def test_spanish_film_vo_sessions_are_not_vose(self):
        # Verdi lists "La bola negra" as CASTELLANO, "V.O. SUB. CASTELLANO" and
        # "V.O. SUB. INGLÉS": all Spanish audio.
        films = {"LBN": {**_film([_cinema("verdi_barcelona", "Cines Verdi", std={D0: {"18:00": "u"}},
                                          vose={D0: {"20:00": "u", "22:00": "u"}})]),
                         "original_language": "es"},
                 "EN": _film([_cinema("verdi_barcelona", "Cines Verdi", vose={D0: {"19:00": "u"}})])}
        pipeline.demote_spanish_original_vose(films)
        c = films["LBN"]["cinemas"][0]
        self.assertEqual(c["vose_showtimes"], {})
        self.assertEqual(sorted(c["showtimes"][D0]), ["18:00", "20:00", "22:00"])
        self.assertFalse(films["LBN"]["any_vose"])
        self.assertTrue(films["EN"]["cinemas"][0]["vose_showtimes"], "English films keep VOSE")


class EnglishSubtitles(unittest.TestCase):
    """Spanish films shown with English subtitles ("VOSI", "V.O. SUB. INGLÉS"):
    never VOSE, but tagged and offered to English-only users."""

    def _merged(self):
        raw = [{"cinema": "mooby_arenas", "title_es": "La bola negra", "is_film": True, "showtimes": [
                    {"date": D0, "time": "18:00", "is_vose": False, "booking_url": "u"},
                    {"date": D0, "time": "21:00", "is_vose": False, "en_subs": True, "booking_url": "u"}]}]
        films = pipeline.merge_scraper_results(raw)
        f = films["La bola negra"]
        f.update({"title_en": "La Bola Negra", "slug": "la-bola-negra", "original_language": "es",
                  "year": "2026", "origin_country": ["ES"]})
        pipeline.demote_spanish_original_vose(films)
        return f

    def test_merge_keeps_flag_and_not_vose(self):
        f = self._merged()
        c = f["cinemas"][0]
        self.assertEqual(c["ensub"], {D0: {"21:00": True}})
        self.assertEqual(c["vose_showtimes"], {})
        self.assertTrue(pipeline.cinema_has_ensub(c))

    def test_film_page_tag(self):
        f = self._merged()
        html = pipeline.build_film_detail_page(f, datetime.combine(TODAY, datetime.min.time()))
        btns = dict((t, inner) for t, inner in re.findall(r'data-time="([^"]+)"[^>]*>(.*?)</a>', html))
        self.assertIn("ensub-tag", btns["21:00"])
        self.assertNotIn("VOSE", btns["21:00"])
        self.assertNotIn("ensub-tag", btns["18:00"])
        self.assertIn('data-ensub="true"', html)

    def test_email_english_only_includes_it(self):
        f = self._merged()
        prefs = {"city": "Barcelona", "cinemas": ["mooby_arenas"], "vose_only": True, "vose_lang": "en"}
        r = scraper.apply_subscriber_filters({"LBN": f}, prefs)
        self.assertIn("LBN", r)
        self.assertTrue(r["LBN"]["any_ensub"])
        self.assertFalse(r["LBN"]["any_vose"])
        # Plain "VOSE only" (any language) must not include it
        r = scraper.apply_subscriber_filters({"LBN": f}, {**prefs, "vose_lang": "all"})
        self.assertNotIn("LBN", r)


# ── Film page display ────────────────────────────────────────────────────────

def _film(cinemas):
    return {"title": "Prueba", "title_es": "Prueba", "title_en": "Test Film", "slug": "test-film",
            "meta": "100 min", "synopsis": "", "is_new": False, "rating": "12", "poster": "",
            "any_vose": True, "year": "2026", "origin_country": ["US"], "original_language": "en",
            "cinemas": cinemas}


def _cinema(cid, name, std=None, vose=None, fmt=None):
    return {"id": cid, "name": name, "website": "", "type": "multiplex",
            "vose": bool(vose) or any(e["is_vose"] for d in (fmt or {}).values() for t in d.values() for e in t.values()),
            "showtimes": std or {}, "vose_showtimes": vose or {}, "format_showtimes": fmt or {}}


class FilmPage(unittest.TestCase):

    def _row(self, html, cid):
        m = re.search(rf'<div class="showtime-row" data-cinema-id="{cid}".*?</div></div>', html, re.S)
        self.assertIsNotNone(m, f"no row for {cid}")
        return m.group(0)

    def test_mixed_row_tags_each_vose_time(self):
        film = _film([_cinema("kinepolis", "Kinépolis Valencia",
                              std={D0: {"21:00": "u", "18:00": "u"}}, vose={D0: {"20:00": "u"}})])
        anchor = datetime.combine(TODAY, datetime.min.time())
        html = pipeline.build_film_detail_page(film, anchor)
        row = self._row(html, "kinepolis")
        self.assertNotIn("vose-mini", row, "mixed row must not carry a cinema-level VOSE tag")
        btns = re.findall(r'data-time="([^"]+)" data-vose="(true|false)"[^>]*>(.*?)</', row)
        for t, v, inner in btns:
            if v == "true":
                self.assertIn("VOSE", inner, f"VOSE time {t} needs its own tag")
            else:
                self.assertNotIn("VOSE", inner, f"dubbed time {t} must not say VOSE")
        self.assertEqual(sorted(t for t, v, _ in btns if v == "true"), ["20:00"])

    def test_all_vose_row_keeps_cinema_tag(self):
        film = _film([_cinema("babel", "Cines Babel", vose={D0: {"18:00": "u", "20:00": "u"}})])
        anchor = datetime.combine(TODAY, datetime.min.time())
        row = self._row(pipeline.build_film_detail_page(film, anchor), "babel")
        self.assertIn("vose-mini", row)


class ListingCardVoseTimes(unittest.TestCase):
    """City listing cards carry data-v-{cinema}_{date} with the VOSE times
    among the data-t- times (used by the app)."""

    def _attrs(self, film):
        a = pipeline.compute_card_data(film)["showtimes_attrs"]
        return dict(re.findall(r'(data-[tv]-[^=]+)="([^"]*)"', a))

    def test_mixed_cinema_marks_only_vose_times(self):
        # The developer's example: Yelmo Campanar, How to Rob a Bank
        film = _film([_cinema("yelmo", "Yelmo Campanar",
                              std={D0: {"16:55": "u", "19:25": "u", "21:55": "u"}},
                              vose={D0: {"18:25": "u", "21:00": "u"}})])
        a = self._attrs(film)
        self.assertEqual(a[f"data-t-yelmo_{D0}"], "16:55|18:25|19:25|21:00|21:55")
        self.assertEqual(a[f"data-v-yelmo_{D0}"], "18:25|21:00")

    def test_same_time_vose_and_dubbed_counts_as_dubbed(self):
        film = _film([_cinema("kinepolis", "Kinépolis", std={D0: {"18:00": "u"}},
                              fmt={"imax": {D0: {"18:00": {"url": "", "is_vose": True},
                                                 "20:00": {"url": "", "is_vose": True}}}})])
        self.assertEqual(self._attrs(film)[f"data-v-kinepolis_{D0}"], "20:00")

    def test_no_vose_means_no_attribute(self):
        film = _film([_cinema("lys", "Cines Lys", std={D0: {"18:00": "u"}})])
        self.assertFalse([k for k in self._attrs(film) if k.startswith("data-v-")])

    def test_page_scripts_only_read_data_t(self):
        # The listing page's filters scan attributes by the 'data-t-' prefix;
        # data-v- must never be picked up by them.
        import scraper as _s, inspect
        src = inspect.getsource(_s)
        self.assertNotIn("startsWith('data-v", src)
        self.assertIn("startsWith('data-t-')", src)


# ── Weekly email filters ─────────────────────────────────────────────────────

class EmailFilters(unittest.TestCase):

    def _films(self):
        return {
            "A": {**_film([_cinema("kinepolis", "Kinépolis Valencia", std={D0: {"18:00": "u"}}),
                           _cinema("cinesa", "Cinesa", vose={D0: {"19:00": "u"}})]), "title": "A"},
            "Incendies": {**_film([_cinema("kinepolis", "Kinépolis Valencia", vose={D0: {"18:00": "u"}})]),
                          "title": "Incendies", "origin_country": ["CA"], "original_language": "fr"},
        }

    def test_vose_only_checks_subscribers_own_cinemas(self):
        r = scraper.apply_subscriber_filters(self._films(), {"city": "Valencia", "cinemas": ["kinepolis"], "vose_only": True})
        self.assertNotIn("A", r, "VOSE only at another cinema must not count")
        r = scraper.apply_subscriber_filters(self._films(), {"city": "Valencia", "cinemas": ["kinepolis"]})
        self.assertFalse(r["A"]["any_vose"], "badge must reflect the subscriber's cinemas")

    def test_english_only_uses_original_language(self):
        r = scraper.apply_subscriber_filters(
            self._films(), {"city": "Valencia", "cinemas": ["kinepolis"], "vose_only": True, "vose_lang": "en"})
        self.assertNotIn("Incendies", r, "a Canadian film in French is not English")


# ── Daily VOSE check ─────────────────────────────────────────────────────────

class DailyCheck(unittest.TestCase):

    def test_catches_the_cinesa_pattern(self):
        hg = _film([
            _cinema("cinesa", "Cinesa LUXE Bonaire", vose={d: {f"{h}:00": "u" for h in range(12, 19)} for d in (D0, D1)}),
            _cinema("kinepolis", "Kinépolis Valencia", std={D0: {"18:00": "u"}}, vose={D0: {"20:00": "u"}}),
        ])
        counts, issues = vose_check.vose_audit({"HG": hg}, pipeline.CINEMA_META, {"Cinesa LUXE Bonaire": 2},
                                               today=TODAY, label_warnings=[], reviewed={})
        self.assertTrue(any(i.startswith("ALL_VOSE") and "Cinesa LUXE Bonaire" in i for i in issues), issues)
        self.assertTrue(any(i.startswith("VOSE_JUMP") for i in issues), issues)

    def test_reviewed_flags_are_quiet(self):
        hg = _film([
            _cinema("cinesa", "Cinesa LUXE Bonaire", vose={D0: {f"{h}:00": "u" for h in range(12, 20)}}),
            _cinema("kinepolis", "Kinépolis Valencia", std={D0: {"18:00": "u"}}),
        ])
        _, issues = vose_check.vose_audit({"HG": hg}, pipeline.CINEMA_META, None, today=TODAY,
                                          label_warnings=[], reviewed={"Cinesa LUXE Bonaire|Test Film": "ok"})
        self.assertEqual(issues, [])

    def test_unknown_labels_reported(self):
        _, issues = vose_check.vose_audit({}, pipeline.CINEMA_META, None, today=TODAY,
                                          label_warnings=["Yelmo: unknown language label 'X' — treating as dubbed"])
        self.assertTrue(issues and issues[0].startswith("UNKNOWN_LABEL"))


if __name__ == "__main__":
    unittest.main()
