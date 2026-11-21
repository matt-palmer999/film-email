"""VOSE this week only, "VOSE from Sat 28", and the "times not published yet" note.

A film whose only VOSE sessions fall after this week's window must not show
the VOSE badge or pass the VOSE filter (that was Cliff Booth at Kinépolis
Valencia on 2026-11-21). Instead the card says when VOSE starts.

Run:  python -m unittest discover -s tests -v
"""
import logging
import os
import re
import sys
import unittest
from datetime import datetime, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
for _k in ("FROM_EMAIL", "SUPABASE_URL", "SUPABASE_KEY"):
    os.environ.setdefault(_k, "test")
logging.disable(logging.CRITICAL)

import pipeline  # noqa: E402

TODAY = datetime.now(pipeline.VALENCIA_TZ).date()
ANCHOR = datetime.now(pipeline.VALENCIA_TZ)


def _d(n):
    return (TODAY + timedelta(days=n)).isoformat()


def _cinema(cid, dubbed_days, vose_days):
    fs = {"imax": {}}
    for n in dubbed_days:
        fs["imax"].setdefault(_d(n), {})["18:00"] = {"url": "u", "is_vose": False}
    for n in vose_days:
        fs["imax"].setdefault(_d(n), {})["15:30"] = {"url": "u", "is_vose": True}
    return {"id": cid, "name": cid, "website": "", "type": "multiplex", "vose": bool(vose_days),
            "showtimes": {}, "vose_showtimes": {}, "format_showtimes": fs}


def _film(cinemas, title="Cliff"):
    return {title: {
        "title": title, "title_es": title, "title_en": title, "slug": title.lower(),
        "meta": "US · 2026", "meta_en": "US · 2026", "synopsis": "", "is_new": True, "rating": "16",
        "poster": "", "any_vose": True, "year": "2026", "origin_country": ["US"],
        "original_language": "en", "cinemas": cinemas}}


def _card(page, slug):
    cards = [c for c in page.split('<div class="grid-card"')[1:] if f'{slug}/" class="grid-title"' in c]
    assert len(cards) == 1, len(cards)
    return cards[0].split('<div class="showtimes-hint"')[0]


class VoseThisWeekOnly(unittest.TestCase):
    def test_vose_only_after_the_window_is_not_vose_now(self):
        films = _film([_cinema("kinepolis", [0, 1, 7, 8], [7, 8])])
        page = pipeline.build_html(films, ANCHOR, city="Valencia")
        card = _card(page, "cliff")
        self.assertIn('data-vose="false"', card)
        self.assertIn('data-vose-cinemas=""', card)
        self.assertNotIn('<span class="vose-badge">VOSE</span>', card)
        day = pipeline.short_day(_d(7), "en")
        self.assertIn(f'data-vose-later="kinepolis:{_d(7)}"', card)
        self.assertIn(f'data-en="VOSE from {day}"', card)
        self.assertIn(f'>VOSE desde el {pipeline.short_day(_d(7), "es")}</span>', card)

    def test_vose_this_week_keeps_the_badge_and_no_later_label(self):
        films = _film([_cinema("kinepolis", [0, 1], [1, 8])])
        card = _card(pipeline.build_html(films, ANCHOR, city="Valencia"), "cliff")
        self.assertIn('data-vose="true"', card)
        self.assertIn('<span class="vose-badge">VOSE</span>', card)
        self.assertNotIn("vose-later", card)

    def test_later_label_only_counts_this_citys_cinemas(self):
        # VOSE later in Barcelona says nothing about Valencia
        films = _film([_cinema("kinepolis", [0, 1], []), _cinema("cinesa_diagonal_mar", [0], [8])])
        card = _card(pipeline.build_html(films, ANCHOR, city="Valencia"), "cliff")
        self.assertNotIn("vose-later", card)

    def test_too_far_ahead_is_not_mentioned(self):
        far = 6 + pipeline.VOSE_LATER_DAYS + 1
        films = _film([_cinema("kinepolis", [0], [far])])
        card = _card(pipeline.build_html(films, ANCHOR, city="Valencia"), "cliff")
        self.assertNotIn("vose-later", card)

    def test_english_page_writes_the_english_label(self):
        films = _film([_cinema("kinepolis", [0, 1], [8])])
        card = _card(pipeline.build_html(films, ANCHOR, city="Valencia", lang="en"), "/listings/cliff")
        self.assertIn(f'>VOSE from {pipeline.short_day(_d(8), "en")}</span>', card)

    def test_page_script_follows_the_cinema_filter(self):
        page = pipeline.build_html(_film([_cinema("kinepolis", [0], [8])]), ANCHOR, city="Valencia")
        self.assertIn("function updateVoseLater(el, cinemas)", page)
        self.assertIn("if (voseLater) updateVoseLater(voseLater, cinemas);", page)

    def test_film_page_badge_is_this_week_only(self):
        film = next(iter(_film([_cinema("kinepolis", [0, 1], [8])]).values()))
        page = pipeline.build_film_detail_page(film, ANCHOR)
        self.assertNotIn('<span class="vose-badge">VOSE</span>', page)
        self.assertNotIn("VOSE disponible", page)


class ShortDay(unittest.TestCase):
    def test_month_only_when_not_this_month(self):
        self.assertEqual(pipeline.short_day(TODAY.isoformat(), "en"),
                         f"{pipeline._DAY_ABBR_EN[TODAY.weekday()]} {TODAY.day}")
        nxt = (TODAY.replace(day=1) + timedelta(days=40)).replace(day=5)
        self.assertEqual(pipeline.short_day(nxt.isoformat(), "es"),
                         f"{pipeline._DAY_ABBR_ES[nxt.weekday()]} 5 {pipeline._MON_ABBR_ES[nxt.month - 1]}")


class UnpublishedNote(unittest.TestCase):
    def _films(self, per_day):
        cins = []
        for i in range(40):
            fs = {"2d": {}}
            for n, k in enumerate(per_day):
                if i < k:
                    fs["2d"][_d(n)] = {"18:00": {"url": "u", "is_vose": False}}
            cins.append({"id": "kinepolis", "name": "k", "website": "", "type": "multiplex",
                         "showtimes": {}, "vose_showtimes": {}, "format_showtimes": fs})
        return {f"F{i}": dict(next(iter(_film([c], f"F{i}").values()))) for i, c in enumerate(cins)}

    def test_thin_last_days_are_flagged(self):
        films = self._films([40, 40, 40, 40, 40, 5, 4])
        self.assertEqual(pipeline.unpublished_from(films, "Valencia"), _d(5))
        page = pipeline.build_html(films, ANCHOR, city="Valencia", lang="en")
        self.assertIn(f"published all their times from {pipeline.short_day(_d(5), 'en')} yet", page)

    def test_full_week_has_no_note(self):
        films = self._films([40, 40, 30, 30, 35, 30, 28])
        self.assertEqual(pipeline.unpublished_from(films, "Valencia"), "")
        self.assertNotIn('class="header-unpublished"', pipeline.build_html(films, ANCHOR, city="Valencia"))

    def test_a_thin_day_in_the_middle_is_not_flagged(self):
        films = self._films([40, 40, 40, 5, 40, 40, 4])
        self.assertEqual(pipeline.unpublished_from(films, "Valencia"), _d(6))


class DailyCommit(unittest.TestCase):
    def test_daily_commit_includes_the_english_pages(self):
        with open(os.path.join(ROOT, "run_pipeline.ps1"), encoding="utf-8") as fh:
            add = next(l for l in fh if "add docs/listings/" in l)
        self.assertIn("docs/en/", add)


if __name__ == "__main__":
    unittest.main()
