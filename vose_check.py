"""Daily VOSE sanity check.

Showing a dubbed session as VOSE is the worst mistake whatson.movie can make,
and it never shows up as a failed scrape: the run looks perfectly healthy.
This module looks for the patterns behind every VOSE bug found so far and
reports them in the admin summary email:

  * VOSE_JUMP      a cinema's VOSE session count jumps well above the last run
  * ALL_VOSE       a multiplex shows every session of a film as VOSE while
                   another cinema in the same city has it dubbed (the
                   Cinesa Bonaire bug: a film-level flag copied to every session)
  * SPANISH_VOSE   VOSE sessions on a film whose original language is Spanish
                   (often a mislabelled dub, or Spanish audio with English subs)
  * UNKNOWN_LABEL  a scraper met a language label it doesn't know and treated
                   the session as dubbed (needs a human to classify it)

Flags that have been checked against the cinema's own site and found correct
go in vose_reviewed.json ("<cinema name>|<film title>": "why it's fine"), so
they stop repeating and real problems aren't buried.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import date, datetime
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Europe/Madrid")
COUNTS_PATH = "docs/data/vose_counts.json"

# Thresholds
JUMP_MIN_ABS = 10      # a jump must add at least this many VOSE sessions...
JUMP_MIN_REL = 0.5     # ...and at least +50% on the previous run
ALL_VOSE_MIN = 6       # ignore one-off screenings: need this many all-VOSE sessions
# Only Spanish: a Catalan or Basque film with Spanish subtitles is genuinely VOSE.
SPANISH_LANGS = {"es"}
REVIEWED_PATH = "vose_reviewed.json"

_LABEL_MARKER = "treating as dubbed"


class _LabelWatch(logging.Handler):
    """Collects scraper warnings about unknown language labels during a run."""

    def __init__(self) -> None:
        super().__init__(level=logging.WARNING)
        self.messages: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = record.getMessage()
        except Exception:
            return
        if _LABEL_MARKER in msg and msg not in self.messages:
            self.messages.append(msg)


_watch: _LabelWatch | None = None


def install_label_watch() -> None:
    """Call once at the start of a pipeline run, before the scrapers."""
    global _watch
    if _watch is None:
        _watch = _LabelWatch()
        logging.getLogger().addHandler(_watch)


def _session_counts(cinema: dict, today: str) -> tuple[int, int]:
    """(VOSE, dubbed) sessions from today onwards, premium formats included."""
    vose = sum(len(t) for d, t in (cinema.get("vose_showtimes") or {}).items() if d >= today)
    dub = sum(len(t) for d, t in (cinema.get("showtimes") or {}).items() if d >= today)
    for dates in (cinema.get("format_showtimes") or {}).values():
        for d, times in dates.items():
            if d < today:
                continue
            for info in times.values():
                if isinstance(info, dict) and info.get("is_vose"):
                    vose += 1
                else:
                    dub += 1
    return vose, dub


def load_reviewed(path: str = REVIEWED_PATH) -> dict:
    try:
        with open(path, encoding="utf-8") as fh:
            return {k: v for k, v in json.load(fh).items() if not k.startswith("_")}
    except Exception:
        return {}


def vose_audit(films: dict, cinema_meta: dict, prev_counts: dict | None,
               today: date | None = None, label_warnings: list[str] | None = None,
               reviewed: dict | None = None) -> tuple[dict, list[str]]:
    """Return (VOSE sessions per cinema name, list of issue lines)."""
    today_s = (today or datetime.now(TZ).date()).isoformat()
    reviewed = reviewed or {}

    def _ok(cinema_name: str, film: dict) -> bool:
        names = {film.get("title_en"), film.get("title"), film.get("title_es")} - {None, ""}
        return any(f"{cinema_name}|{n}" in reviewed for n in names)
    counts: dict[str, int] = {}
    issues: list[str] = []

    for film in films.values():
        for c in film.get("cinemas", []):
            v, _ = _session_counts(c, today_s)
            counts[c.get("name", c.get("id", "?"))] = counts.get(c.get("name", c.get("id", "?")), 0) + v

    # 1. Big jumps against the previous run
    for name, n in sorted(counts.items()):
        prev = (prev_counts or {}).get(name)
        if prev is not None and n - prev >= JUMP_MIN_ABS and n >= prev * (1 + JUMP_MIN_REL):
            issues.append(f"VOSE_JUMP     {name}: {n} VOSE sessions (was {prev}) — check the scraper's VOSE labels")

    # 2. A multiplex with every session of a film VOSE, while it's dubbed elsewhere in the city
    for film in films.values():
        title = film.get("title_en") or film.get("title", "?")
        rows = []
        for c in film.get("cinemas", []):
            v, d = _session_counts(c, today_s)
            meta = cinema_meta.get(c.get("id", ""), {})
            rows.append((c, meta.get("city", ""), c.get("type") or meta.get("type", ""), v, d))
        for c, city, ctype, v, d in rows:
            if ctype != "multiplex" or v < ALL_VOSE_MIN or d or _ok(c["name"], film):
                continue
            dubbed_elsewhere = [o["name"] for o, ocity, _t, _ov, od in rows
                                if o is not c and ocity == city and od > 0]
            if dubbed_elsewhere:
                issues.append(f"ALL_VOSE      {c['name']}: all {v} sessions of '{title}' are VOSE, "
                              f"but it is dubbed at {', '.join(dubbed_elsewhere[:2])}")

    # 3. VOSE on Spanish-language films
    for film in films.values():
        if (film.get("original_language") or "") not in SPANISH_LANGS:
            continue
        title = film.get("title_en") or film.get("title", "?")
        for c in film.get("cinemas", []):
            v, _ = _session_counts(c, today_s)
            if v and not _ok(c["name"], film):
                issues.append(f"SPANISH_VOSE  {c['name']}: {v} VOSE session(s) of '{title}' "
                              f"(original language '{film['original_language']}') — confirm it isn't a dub")

    # 4. Labels the scrapers didn't recognise
    for msg in (label_warnings if label_warnings is not None else (_watch.messages if _watch else [])):
        issues.append(f"UNKNOWN_LABEL {msg}")

    return counts, issues


def run_vose_check(films: dict, cinema_meta: dict, path: str = COUNTS_PATH) -> list[str]:
    """Audit, save today's VOSE counts for tomorrow's comparison, return issue lines."""
    log = logging.getLogger(__name__)
    prev = None
    try:
        with open(path, encoding="utf-8") as fh:
            prev = json.load(fh)
    except Exception:
        pass
    counts, issues = vose_audit(films, cinema_meta, prev, reviewed=load_reviewed())
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(counts, fh, indent=2, ensure_ascii=False, sort_keys=True)
    except Exception as exc:
        log.warning("Could not save VOSE counts: %s", exc)
    for line in issues:
        log.warning("VOSE check: %s", line)
    if not issues:
        log.info("VOSE check: no problems found")
    return issues
