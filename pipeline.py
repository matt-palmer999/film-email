"""
pipeline.py — Valencia cinema listings pipeline.

Calls all 9 scrapers → aggregates → TMDB enrichment → generates docs/ HTML.
Run directly:  python3.12 pipeline.py
"""

import json
import logging
import os
import re
import shutil
from datetime import datetime, timedelta, date
from zoneinfo import ZoneInfo

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

VALENCIA_TZ = ZoneInfo("Europe/Madrid")

# ── Config ────────────────────────────────────────────────────────────────────
TMDB_API_KEY  = os.environ.get("TMDB_API_KEY", "")
TMDB_BASE     = "https://api.themoviedb.org/3"
SUPABASE_URL  = os.environ.get("SUPABASE_URL", "")
SUPABASE_ANON = os.environ.get("SUPABASE_ANON", "")

# ── Cinema metadata ───────────────────────────────────────────────────────────
CINEMA_META = {
    "kinepolis":  {"name": "Kinépolis Valencia",  "website": "https://www.kinepolis.es/valencia",      "type": "multiplex"},
    "yelmo":      {"name": "Yelmo Campanar",       "website": "https://www.yelmocines.es",              "type": "multiplex"},
    "ocine_aqua": {"name": "Ocine Premium Aqua",   "website": "https://www.ocinepremiumaqua.es",        "type": "multiplex"},
    "park":       {"name": "Cines ABC Park",        "website": "https://park.cinesabc.com",              "type": "multiplex"},
    "elsaler":    {"name": "Cines ABC El Saler",    "website": "https://elsaler.cinesabc.com",           "type": "multiplex"},
    "granturia":  {"name": "Cines ABC Gran Turia",  "website": "https://granturia.cinesabc.com",         "type": "multiplex"},
    "lys":        {"name": "Cines Lys",             "website": "https://cineslys.com",                   "type": "multiplex"},
    "mn4":        {"name": "Cines MN4",             "website": "https://www.cinesmn4.com",               "type": "multiplex"},
    "tivoli":     {"name": "Cine Tívoli",           "website": "https://exhicine.es/cine-tivoli/",       "type": "multiplex"},
    "babel":      {"name": "Cines Babel",           "website": "https://www.cinesalbatrosbabel.com",     "type": "arthouse"},
    "dor":        {"name": "Cinestudio D'Or",       "website": "https://cinestudiodor.es",               "type": "arthouse"},
    "cinesa":     {"name": "Cinesa LUXE Bonaire",  "website": "https://www.cinesa.es/cines/bonaire/",   "type": "multiplex"},
}


# ── Date / slug helpers ───────────────────────────────────────────────────────

def week_range_es(anchor: datetime) -> str:
    MONTHS_ES = ["enero","febrero","marzo","abril","mayo","junio",
                 "julio","agosto","septiembre","octubre","noviembre","diciembre"]
    end = anchor + timedelta(days=6)
    if anchor.month == end.month:
        return f"{anchor.day} – {end.day} de {MONTHS_ES[anchor.month-1]} {anchor.year}"
    return f"{anchor.day} de {MONTHS_ES[anchor.month-1]} – {end.day} de {MONTHS_ES[end.month-1]} {anchor.year}"


def week_range_en(anchor: datetime) -> str:
    end = anchor + timedelta(days=6)
    if anchor.month == end.month:
        return f"{anchor.day} – {end.day} {anchor.strftime('%B')} {anchor.year}"
    return f"{anchor.day} {anchor.strftime('%B')} – {end.day} {end.strftime('%B')} {anchor.year}"


def slugify(title: str) -> str:
    import unicodedata
    s = unicodedata.normalize("NFKD", title.lower())
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s or "film"


def esc(s: str) -> str:
    return (s or "").replace("&", "&amp;").replace('"', "&quot;").replace("'", "&#39;").replace("<", "&lt;").replace(">", "&gt;")


# ── TMDB lookup ───────────────────────────────────────────────────────────────

def tmdb_lookup(title: str) -> dict:
    import requests as req
    import time as _time
    import re as _re

    if not TMDB_API_KEY:
        return {}

    # Strip regional language prefixes added by Spanish exhibitors (e.g. "CAT ", "VA ", "EUS ", "GAL ")
    search_title = _re.sub(r'^(CAT|VA|EUS|GAL|GL)\s+', '', title, flags=_re.IGNORECASE)
    # Strip re-release/special-screening suffixes before TMDB search
    _REISSUE_SUFFIXES = (
        r'\d+[°ºo]?\s*(Aniversario|Aniversari|Anniversary)'  # 40 Aniversario / 40º Anniversary
        r'|Reestreno|Re-estreno'                              # Reestreno
        r'|Versi[oó]n\s+(Extendida|Restaurada|Remasterizada|Original|del\s+Director)'  # Versión extendida etc.
        r'|Director\'?s?\s+Cut|Montaje\s+del\s+Director'     # Director's Cut
        r'|Edici[oó]n\s+(Especial|Coleccionista|Definitiva)'  # Edición especial
        r'|Pase\s+Especial'                                   # Pase especial
        r'|4K(\s+Restaurad[ao])?'                             # 4K / 4K Restaurada
        r'|\d{4}'                                             # bare year (1986)
    )
    search_title = _re.sub(
        r'\s*[\(\[]?\s*(' + _REISSUE_SUFFIXES + r')\s*[\)\]]?$',
        '', search_title, flags=_re.IGNORECASE
    ).strip()
    search_title = _re.split(r'\s*[-–]\s*[A-Z]|\s*\+', search_title)[0].strip()
    _time.sleep(0.25)

    try:
        headers = {
            "Authorization": f"Bearer {TMDB_API_KEY}",
            "accept": "application/json",
        }

        search_url = (
            f"{TMDB_BASE}/search/movie"
            f"?query={req.utils.quote(search_title)}"
            f"&language=es-ES&region=ES"
        )
        res = req.get(search_url, headers=headers, timeout=10)
        res.raise_for_status()
        results = res.json().get("results", [])

        if not results:
            search_url_en = (
                f"{TMDB_BASE}/search/movie"
                f"?query={req.utils.quote(search_title)}&language=en-US"
            )
            res = req.get(search_url_en, headers=headers, timeout=10)
            res.raise_for_status()
            results = res.json().get("results", [])

        if not results:
            log.info(f"  TMDB: no results for '{title}'")
            return {}

        movie    = results[0]
        movie_id = movie["id"]

        detail_res = req.get(f"{TMDB_BASE}/movie/{movie_id}?language=en-US", headers=headers, timeout=10)
        detail_res.raise_for_status()
        detail = detail_res.json()

        detail_es_res = req.get(f"{TMDB_BASE}/movie/{movie_id}?language=es-ES", headers=headers, timeout=10)
        detail_es_res.raise_for_status()
        detail_es = detail_es_res.json()

        cert_es = "?"
        try:
            rel_res = req.get(f"{TMDB_BASE}/movie/{movie_id}/release_dates", headers=headers, timeout=10)
            rel_res.raise_for_status()
            rel_results = rel_res.json().get("results", [])

            for entry in rel_results:
                if entry.get("iso_3166_1") == "ES":
                    for rd in entry.get("release_dates", []):
                        cert = rd.get("certification", "").strip()
                        if cert:
                            cert_es = cert
                            break
                    break

            if cert_es == "?":
                gb_map = {"U": "TP", "PG": "TP", "12": "12", "12A": "12", "15": "16", "18": "18", "R18": "18"}
                for entry in rel_results:
                    if entry.get("iso_3166_1") == "GB":
                        for rd in entry.get("release_dates", []):
                            cert = rd.get("certification", "").strip()
                            if cert and cert in gb_map:
                                cert_es = gb_map[cert]
                                break
                        break

            if cert_es == "?":
                us_map = {"G": "TP", "PG": "TP", "PG-13": "12", "R": "16", "NC-17": "18"}
                for entry in rel_results:
                    if entry.get("iso_3166_1") == "US":
                        for rd in entry.get("release_dates", []):
                            cert = rd.get("certification", "").strip()
                            if cert and cert in us_map:
                                cert_es = us_map[cert]
                                break
                        break
        except Exception:
            pass

        synopsis_es = detail_es.get("overview") or detail.get("overview", "")
        poster_path = detail.get("poster_path") or movie.get("poster_path")
        poster_url  = f"https://image.tmdb.org/t/p/w500{poster_path}" if poster_path else ""
        vote = detail.get("vote_average", 0)

        return {
            "title_en":       detail.get("title", ""),
            "title_original": detail.get("original_title", ""),
            "synopsis_en":    detail.get("overview", ""),
            "synopsis_es":    synopsis_es,
            "poster_url":     poster_url,
            "year":           (detail.get("release_date") or "")[:4],
            "release_date":   detail.get("release_date", ""),
            "tmdb_id":        movie_id,
            "rating_score":   round(vote, 1) if vote else None,
            "genres_en":      [g["name"] for g in detail.get("genres", [])],
            "runtime":        detail.get("runtime"),
            "origin_country": detail.get("origin_country", []),
            "cert_es":        cert_es,
        }

    except Exception as e:
        log.warning(f"  TMDB lookup failed for '{title}': {e}")
        return {}


# ── Scraper aggregation ───────────────────────────────────────────────────────

def aggregate_scrapers() -> dict:
    """
    Run all 9 scrapers and return films_by_title dict in old format:
    { title_es: {title, meta, synopsis, is_new, rating, poster, any_vose,
                 cinemas: [{id, name, website, type, vose, showtimes: {date:[times]}}]} }
    """
    from scrapers.kinepolis  import scrape_kinepolis
    from scrapers.yelmo      import scrape_yelmo
    from scrapers.babel      import scrape_babel
    from scrapers.abc        import scrape_abc
    from scrapers.dor        import scrape_dor
    from scrapers.tivoli     import scrape_tivoli
    from scrapers.ocine_aqua import scrape_ocine_aqua
    from scrapers.lys        import scrape_lys
    from scrapers.mn4        import scrape_mn4
    from scrapers.cinesa     import scrape_cinesa

    scrapers = [
        (scrape_kinepolis,  "Kinépolis"),
        (scrape_yelmo,      "Yelmo"),
        (scrape_babel,      "Babel"),
        (scrape_abc,        "ABC cinemas"),
        (scrape_dor,        "D'Or"),
        (scrape_tivoli,     "Tívoli"),
        (scrape_ocine_aqua, "Ocine Aqua"),
        (scrape_lys,        "Lys"),
        (scrape_mn4,        "MN4"),
        (scrape_cinesa,     "Cinesa Bonaire"),
    ]

    all_results: list[dict] = []
    for fn, label in scrapers:
        try:
            results = fn()
            log.info(f"  {label}: {len(results)} films")
            all_results.extend(results)
        except Exception as exc:
            log.error(f"  {label} scraper failed: {exc}", exc_info=True)

    films_by_title: dict = {}

    for film in all_results:
        if not film.get("is_film", True):
            continue

        title     = film["title_es"]
        cinema_id = film["cinema"]
        meta_info = CINEMA_META.get(cinema_id, {})

        # Flat showtimes → {date: [time_str]}
        # Handles two formats:
        #   {"date": "YYYY-MM-DD", "time": "HH:MM"}          (most scrapers)
        #   {"datetime_local": "YYYY-MM-DDTHH:MM:SS", ...}   (Kinepolis, Yelmo)
        showtimes_by_date: dict = {}
        for st in film.get("showtimes", []):
            d = st.get("date", "")
            t = st.get("time", "")
            if (not d or not t):
                dl = str(st.get("datetime_local", ""))
                if len(dl) >= 10 and not d:
                    d = dl[:10]
                if len(dl) >= 16 and not t:
                    t = dl[11:16]
            if d and t:
                bucket = showtimes_by_date.setdefault(d, [])
                if t not in bucket:
                    bucket.append(t)
        for d in showtimes_by_date:
            showtimes_by_date[d].sort()

        is_vose = bool(film.get("is_vose", False))

        if title not in films_by_title:
            duration = film.get("duration_mins", 0)
            films_by_title[title] = {
                "title":    title,
                "meta":     f"{duration} min" if duration else "",
                "synopsis": film.get("synopsis_es", ""),
                "is_new":   False,
                "rating":   "?",
                "poster":   film.get("poster_url", ""),
                "any_vose": False,
                "cinemas":  [],
            }
        else:
            f = films_by_title[title]
            if not f["poster"] and film.get("poster_url"):
                f["poster"] = film["poster_url"]
            if not f["synopsis"] and film.get("synopsis_es"):
                f["synopsis"] = film["synopsis_es"]
            if not f["meta"]:
                duration = film.get("duration_mins", 0)
                if duration:
                    f["meta"] = f"{duration} min"

        # Add or merge cinema entry
        existing = next((c for c in films_by_title[title]["cinemas"] if c["id"] == cinema_id), None)
        if existing:
            for d, times in showtimes_by_date.items():
                bucket = existing["showtimes"].setdefault(d, [])
                for t in times:
                    if t not in bucket:
                        bucket.append(t)
                existing["showtimes"][d].sort()
            if is_vose:
                existing["vose"] = True
        else:
            films_by_title[title]["cinemas"].append({
                "id":        cinema_id,
                "name":      meta_info.get("name", cinema_id),
                "website":   meta_info.get("website", ""),
                "type":      meta_info.get("type", "multiplex"),
                "vose":      is_vose,
                "showtimes": showtimes_by_date,
            })

        if is_vose:
            films_by_title[title]["any_vose"] = True

    return films_by_title


# ── TMDB enrichment ───────────────────────────────────────────────────────────

def enrich_with_tmdb(films: dict) -> None:
    """Mutates films dict in-place: adds TMDB metadata fields."""
    today_local = datetime.now(VALENCIA_TZ).date()

    if TMDB_API_KEY:
        log.info("Enriching films with TMDB data …")
        for title, film in films.items():
            tmdb = tmdb_lookup(title)
            if tmdb:
                if tmdb.get("poster_url"):
                    film["poster"] = tmdb["poster_url"]
                film["title_en"]       = tmdb.get("title_en", title)
                film["title_original"] = tmdb.get("title_original", title)
                film["synopsis_en"]    = tmdb.get("synopsis_en", "")
                film["synopsis_es"]    = tmdb.get("synopsis_es") or film.get("synopsis", "")
                film["rating_score"]   = tmdb.get("rating_score")
                film["tmdb_id"]        = tmdb.get("tmdb_id")
                film["origin_country"] = tmdb.get("origin_country", [])

                if tmdb.get("cert_es") and tmdb["cert_es"] != "?":
                    cert = tmdb["cert_es"]
                    if cert in ("A", "APTA", "TP"):
                        cert = "TP"
                    film["rating"] = cert

                # is_new: released within last 42 days
                release_date_str = tmdb.get("release_date", "")
                if release_date_str:
                    try:
                        rd = date.fromisoformat(release_date_str)
                        film["is_new"] = (today_local - rd).days <= 42
                    except ValueError:
                        pass

                # Build meta strings
                genres_en  = tmdb.get("genres_en", [])
                runtime    = tmdb.get("runtime")
                countries  = tmdb.get("origin_country", [])
                year       = tmdb.get("year", "")
                if year:
                    film["year"] = year

                # Spanish meta (for display)
                parts_es = []
                if countries: parts_es.append(", ".join(countries))
                if year:      parts_es.append(year)
                if runtime:   parts_es.append(f"{runtime} min")
                if parts_es:
                    film["meta"] = " · ".join(parts_es)

                # English meta
                parts_en = []
                if countries: parts_en.append(", ".join(countries))
                if year:      parts_en.append(year)
                if genres_en: parts_en.append(", ".join(genres_en))
                if runtime:   parts_en.append(f"{runtime} min")
                film["meta_en"] = " · ".join(parts_en) if parts_en else film.get("meta", "")

                log.info(f"  ✓ {title[:45]} → {tmdb.get('title_en','?')} ⭐{tmdb.get('rating_score','?')}")
            else:
                film["title_en"]       = title
                film["title_original"] = title
                film["synopsis_en"]    = film.get("synopsis", "")
                film["synopsis_es"]    = film.get("synopsis", "")
                film["rating_score"]   = None
                film["meta_en"]        = film.get("meta", "")
    else:
        log.warning("TMDB_API_KEY not set — skipping TMDB enrichment")
        for film in films.values():
            film["title_en"]       = film["title"]
            film["title_original"] = film["title"]
            film["synopsis_en"]    = film.get("synopsis", "")
            film["synopsis_es"]    = film.get("synopsis", "")
            film["rating_score"]   = None
            film["meta_en"]        = film.get("meta", "")


def deduplicate_by_tmdb_id(films: dict) -> None:
    """Merge films that resolved to the same TMDB ID."""
    tmdb_id_map: dict = {}
    duplicates: list  = []

    for title, film in films.items():
        tid = film.get("tmdb_id")
        if not tid:
            continue
        if tid in tmdb_id_map:
            canonical = tmdb_id_map[tid]
            log.info(f"  Merging '{title}' → '{canonical}' (TMDB {tid})")
            existing_ids = {c["id"] for c in films[canonical]["cinemas"]}
            for c in film["cinemas"]:
                if c["id"] not in existing_ids:
                    films[canonical]["cinemas"].append(c)
                    existing_ids.add(c["id"])
            if film.get("any_vose"):
                films[canonical]["any_vose"] = True
            if film.get("is_new"):
                films[canonical]["is_new"] = True
            if film.get("poster") and not films[canonical].get("poster"):
                films[canonical]["poster"] = film["poster"]
            duplicates.append(title)
        else:
            tmdb_id_map[tid] = title

    for title in duplicates:
        del films[title]


# ── HTML helpers ──────────────────────────────────────────────────────────────

def cinemas_in_window(film: dict) -> list:
    today      = datetime.now(VALENCIA_TZ).date()
    week_ahead = (today + timedelta(days=6)).strftime("%Y-%m-%d")
    today_str  = today.strftime("%Y-%m-%d")
    return [
        c for c in film.get("cinemas", [])
        if any(today_str <= dk <= week_ahead for dk in c.get("showtimes", {}).keys())
    ]


def compute_card_data(film: dict) -> dict:
    year         = film.get("year", "")
    cinemas_set  = set(c["id"] for c in film["cinemas"])
    arthouse_only = cinemas_set.issubset({"babel", "dor"})
    is_old       = bool(year) and int(year) <= datetime.now(VALENCIA_TZ).year - 3
    section      = "2" if (arthouse_only or is_old) else "1"
    origin       = ",".join(film.get("origin_country", []))
    score_val    = str(film.get("rating_score") or "")
    rating_val   = film.get("rating", "?").replace("+", "")
    cinema_ids   = ",".join(c["id"] for c in cinemas_in_window(film))

    has_eve = False
    for _c in film.get("cinemas", []):
        for _dk, _times in _c.get("showtimes", {}).items():
            try:
                _d = date.fromisoformat(_dk)
                if _d.weekday() < 5:
                    for _t in _times:
                        _h = int(str(_t).split(":")[0])
                        _m = int(str(_t).split(":")[1]) if ":" in str(_t) else 0
                        if _h > 17 or (_h == 17 and _m >= 30):
                            has_eve = True
                            break
                else:
                    has_eve = True
                if has_eve:
                    break
            except Exception:
                pass
        if has_eve:
            break
    hasevening = "true" if has_eve else "false"

    today_qf   = datetime.now(VALENCIA_TZ).date()
    window_end = today_qf + timedelta(days=6)
    showdays: set = set()
    showtimes_by_cinema_day: dict = {}
    for _c in film.get("cinemas", []):
        _cid = _c.get("id", "")
        for _dk, _times in _c.get("showtimes", {}).items():
            try:
                _d = date.fromisoformat(_dk)
                if today_qf <= _d <= window_end:
                    showdays.add(_dk)
                    showtimes_by_cinema_day.setdefault(f"{_cid}_{_dk}", set()).update(
                        str(t) for t in _times
                    )
            except Exception:
                pass
    showdays_attr   = ",".join(sorted(showdays))
    showtimes_attrs = " ".join(
        f'data-t-{key}="{"|".join(sorted(times))}"'
        for key, times in showtimes_by_cinema_day.items()
    )

    return {
        "year":            year,
        "section":         section,
        "origin":          origin,
        "score_val":       score_val,
        "rating_val":      rating_val,
        "cinema_ids":      cinema_ids,
        "hasevening":      hasevening,
        "showdays_attr":   showdays_attr,
        "showtimes_attrs": showtimes_attrs,
    }


# ── CSS / JS constants (copied from scraper.py, allCinemas updated) ───────────

CSS = """
@import url('https://fonts.googleapis.com/css2?family=Playfair+Display:wght@700&family=DM+Sans:wght@300;400;500&display=swap');
*{margin:0;padding:0;box-sizing:border-box}
body{background:#0f0c14;font-family:'DM Sans',Helvetica,sans-serif;color:#f0eae0}
.wrapper{max-width:640px;margin:0 auto;background:#0f0c14}
.lang-bar{background:#0a0810;border-bottom:1px solid #1e1630;padding:10px 16px;display:flex;justify-content:space-between;align-items:center;gap:8px;flex-wrap:nowrap}
.lang-label{font-size:11px;color:#4a3f5e;letter-spacing:1px;text-transform:uppercase}
.lang-toggle{display:flex;border-radius:6px;overflow:hidden;border:1px solid #2e2545}
.lang-btn{padding:5px 14px;font-size:11px;font-weight:500;letter-spacing:1px;text-transform:uppercase;cursor:pointer;border:none;background:transparent;color:#6a5e7a;font-family:'DM Sans',Helvetica,sans-serif}
.lang-btn.active{background:#2e2040;color:#f0eae0}
.header{background:linear-gradient(135deg,#1a0a2e 0%,#0f0c14 60%);border-bottom:1px solid #3a2a55;padding:40px 40px 32px;text-align:center;position:relative;overflow:hidden}
.header::before{content:'';position:absolute;top:-60px;left:-60px;width:200px;height:200px;background:radial-gradient(circle,rgba(255,180,50,.15) 0%,transparent 70%);border-radius:50%}
.header::after{content:'';position:absolute;bottom:-40px;right:-40px;width:160px;height:160px;background:radial-gradient(circle,rgba(220,80,120,.12) 0%,transparent 70%);border-radius:50%}
.header-eyebrow{font-size:11px;font-weight:500;letter-spacing:3px;text-transform:uppercase;color:#ffb432;margin-bottom:12px}
.header-title{font-family:'Playfair Display',Georgia,serif;font-size:42px;font-weight:700;color:#f9f3e8;line-height:1.1;margin-bottom:10px;margin-top:0}
.header-subtitle{font-size:14px;color:#9b8faa;font-weight:300}
.header-date{display:inline-block;margin-top:18px;padding:6px 18px;background:rgba(255,180,50,.12);border:1px solid rgba(255,180,50,.3);border-radius:20px;font-size:12px;color:#ffb432;letter-spacing:1px}
.section-label{padding:28px 40px 12px;font-size:13px;letter-spacing:3px;text-transform:uppercase;color:#c5b8d8;font-weight:600}
.section-divider{height:1px;background:linear-gradient(90deg,transparent,#2e2040 30%,#2e2040 70%,transparent);margin:8px 24px 20px}
.cinema-group-header{margin:0 24px 14px;padding:14px 18px;background:#160f24;border:1px solid #ffb432;border-left:4px solid #ffb432;border-radius:10px;display:flex;align-items:center;gap:10px}#section2-header{border-color:#ffb432;border-left-color:#ffb432}
.cinema-group-name{font-family:'Playfair Display',Georgia,serif;font-size:17px;font-weight:700;color:#f0eae0}
.cinema-group-desc{font-size:12px;color:#7a6a8a}
.cinema-group-link{margin-left:auto;font-size:11px;color:#7a6a9a;text-decoration:none;white-space:nowrap}
.list-card{margin:0 24px 10px;padding:14px 16px;background:#1a1228;border:1px solid #2e2040;border-radius:12px;display:flex;gap:14px;align-items:flex-start;position:relative;cursor:pointer;transition:background .15s}.list-card:active{background:#221530}
.list-poster{width:54px;height:78px;flex-shrink:0;background:#2a1f3d;border-radius:6px;overflow:hidden;display:flex;align-items:center;justify-content:center;font-size:22px}
.list-poster img{width:100%;height:100%;object-fit:cover;display:block}
.list-body{flex:1}
.list-title{font-family:'Playfair Display',Georgia,serif;font-size:15px;font-weight:700;color:#f0eae0;line-height:1.2;margin-bottom:4px}
.list-meta{font-size:11px;color:#7a6d8a;margin-bottom:5px;line-height:1.5}
.list-synopsis{font-size:11.5px;color:#8c8090;line-height:1.5;margin-bottom:8px}
.badges{margin-bottom:8px;display:flex;flex-wrap:wrap;gap:5px;align-items:center}
.film-badge{display:inline-block;padding:2px 9px;border-radius:20px;font-size:10px;font-weight:500;letter-spacing:1px;text-transform:uppercase}
.badge-new{background:rgba(255,180,50,.15);color:#ffb432;border:1px solid rgba(255,180,50,.3)}
.badge-genre{background:rgba(100,140,220,.12);color:#7aa0e0;border:1px solid rgba(100,140,220,.25)}
.vose-badge{display:inline-block;padding:2px 7px;border-radius:4px;font-size:10px;font-weight:700;letter-spacing:1.5px;background:rgba(255,220,80,.15);color:#ffd84a;border:1px solid rgba(255,220,80,.35)}
.score-badge{display:inline-block;padding:2px 8px;border-radius:4px;font-size:10px;font-weight:600;letter-spacing:0.5px;background:rgba(255,255,255,.06);color:#c5b8d8;border:1px solid rgba(255,255,255,.12)}
.rating-badge{display:inline-block;padding:2px 8px;border-radius:4px;font-size:10px;font-weight:600;letter-spacing:0.5px;background:rgba(180,100,100,.12);color:#c98a8a;border:1px solid rgba(180,100,100,.3)}
.cinema-links-label{font-size:10px;letter-spacing:1px;text-transform:uppercase;color:#4a4060;font-weight:500;margin-bottom:5px}
.cinema-tags{display:flex;flex-wrap:wrap;gap:5px;margin-top:4px}
.cinema-tag{display:inline-block;padding:3px 9px;border-radius:4px;font-size:11px;color:#9a8fb0;background:rgba(255,255,255,.04);border:1px solid #2e2545;text-decoration:none;line-height:1.4}
.vose-mini{display:inline-block;margin-left:4px;font-size:9px;font-weight:700;letter-spacing:1px;color:#ffd84a;vertical-align:middle}
.rating{display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:3px;vertical-align:middle}
.rating-TP{background:#50c88c}.rating-12{background:#7aa0e0}.rating-16{background:#e08040}.rating-18{background:#e05050}.rating-7{background:#80cc80}
.featured-card{margin:0 24px 16px;border-radius:16px;overflow:hidden;background:#1a1228;border:1px solid #2e2040;display:flex;min-height:200px;position:relative;cursor:pointer;transition:background .15s}.featured-card:active{background:#221530}
.featured-poster{width:120px;flex-shrink:0;background:#2a1f3d;display:flex;align-items:flex-start;justify-content:center}
.featured-info{padding:18px 20px 16px;flex:1;display:flex;flex-direction:column;justify-content:space-between}
.film-title{font-family:'Playfair Display',Georgia,serif;font-size:21px;font-weight:700;color:#f0eae0;line-height:1.2;margin-bottom:7px;text-decoration:none;display:block}.film-title:hover{color:#ffb432}
.film-meta{font-size:12px;color:#7a6d8a;margin-bottom:8px;line-height:1.55}
.film-synopsis{font-size:13px;color:#9d909e;line-height:1.55;margin-bottom:11px}
.grid-row{display:grid;grid-template-columns:1fr 1fr;gap:14px;margin:0 24px 14px}
.grid-card{background:#1a1228;border:1px solid #2e2040;border-radius:14px;overflow:hidden;position:relative;cursor:pointer;transition:border-color .2s,background .15s}.grid-card:hover{border-color:rgba(255,180,50,.45)}.grid-card:active{background:#221530}
.grid-poster{width:100%;background:#2a1f3d;overflow:hidden;display:flex;align-items:center;justify-content:center;font-size:34px}
.grid-info{padding:12px 14px 14px}
.grid-title{font-family:'Playfair Display',Georgia,serif;font-size:15px;font-weight:700;color:#f0eae0;line-height:1.2;margin-bottom:4px;text-decoration:none;display:block}.grid-title:hover{color:#ffb432}
.grid-meta{font-size:11px;color:#7a6d8a;margin-bottom:6px;line-height:1.5}
.grid-synopsis{font-size:11.5px;color:#8c8090;line-height:1.5;margin-bottom:8px}.showtimes-hint{display:flex;align-items:center;gap:4px;margin-top:10px;padding-top:9px;border-top:1px solid #241a35;font-size:11px;font-weight:500;color:#a07840;letter-spacing:.3px}.grid-card:hover .showtimes-hint{color:#ffb432}
.footer{background:#0a0810;border-top:1px solid #1e1630;padding:28px 40px;text-align:center}
.footer p{font-size:12px;color:#4a3f5e;line-height:1.7}
.footer a{color:#7a6a9a;text-decoration:none}
.footer-logo{font-family:'Playfair Display',Georgia,serif;font-size:18px;color:#3a2e50;margin-bottom:10px}
.filter-bar{background:#0a0810;padding:10px 20px;display:flex;align-items:center;gap:8px;border-bottom:1px solid #1e1630;flex-wrap:wrap}
.filter-label{font-size:10px;letter-spacing:2px;text-transform:uppercase;color:#4a3f5e;font-weight:500}
.filter-btn{padding:5px 14px;border-radius:20px;font-size:11px;font-weight:600;letter-spacing:1px;text-transform:uppercase;cursor:pointer;border:1px solid #2e2545;background:transparent;color:#6a5e7a;font-family:'DM Sans',Helvetica,sans-serif;transition:all .2s}
.filter-btn:hover{color:#c5b8d8;border-color:#4a3a60}
.filter-btn.active{background:rgba(255,220,80,.15);color:#ffd84a;border-color:rgba(255,220,80,.4)}
.qf-btn{padding:7px 0;border-radius:20px;font-size:11px;font-weight:500;border:1px solid #2e2545;background:transparent;color:#6a5e7a;cursor:pointer;flex:1;text-align:center;font-family:'DM Sans',Helvetica,sans-serif;transition:all .2s;-webkit-tap-highlight-color:transparent}
@media(hover:hover){.qf-btn:hover{color:#c5b8d8;border-color:#4a3a60}}
.qf-active{background:rgba(255,180,50,.15);color:#ffb432;border-color:rgba(255,180,50,.4)}
.qf-hidden{display:none!important}
.filter-empty{display:none;margin:20px 24px;padding:20px;text-align:center;color:#5a4e6a;font-size:14px;border:1px dashed #2e2040;border-radius:10px}
@media(max-width:480px){.lang-bar{padding:8px 12px}.lang-btn{padding:4px 10px;font-size:10px}}
"""

JS = """
function setLang(lang) {
  document.getElementById('btn-es').classList.toggle('active', lang === 'es');
  document.getElementById('btn-en').classList.toggle('active', lang === 'en');
  document.getElementById('html-root').setAttribute('lang', lang);
  document.querySelectorAll('[data-es][data-en]').forEach(el => {
    el.innerHTML = el.getAttribute('data-' + lang);
  });
  localStorage.setItem('cv_lang', lang);
  const url = new URL(window.location);
  url.searchParams.set('lang', lang);
  window.history.replaceState({}, '', url);
  updateHeaderDate();
  const titleEl = document.getElementById('header-title');
  if (titleEl) titleEl.innerHTML = lang === 'en' ? 'Cinema<br>Listings' : 'Cartelera<br>Valencia';
}

function getCookie(name) {
  const match = document.cookie.match(new RegExp('(^| )' + name + '=([^;]+)'));
  return match ? decodeURIComponent(match[2]) : null;
}

function applyPreferencesFromURL() {
  const params  = new URLSearchParams(window.location.search);
  const cinemas = params.get('cinemas') ? params.get('cinemas').split(',') : null;
  const alwaysClassics = params.get('classics') === 'true';
  if (cinemas) {
    document.querySelectorAll('.cinema-tag').forEach(tag => {
      const cid = tag.dataset.cinema;
      if (cid && !cinemas.includes(cid)) {
        const card = tag.closest('[data-section]');
        const isClassic = card && card.dataset.section === '2';
        if (!(alwaysClassics && isClassic)) {
          tag.style.display = 'none';
        }
      }
    });
  }
}

function setSubscriberUI(isSubscriber) {
  const navSubscribe = document.getElementById('nav-subscribe');
  if (navSubscribe) navSubscribe.style.display  = isSubscriber ? 'none' : '';
  const qf = document.getElementById('quick-filter');
  const qfOverlay = document.getElementById('qf-lock-overlay');
  if (qf) qf.style.display = 'block';
  if (qfOverlay) qfOverlay.style.display = isSubscriber ? 'none' : 'flex';
  const anonBanner = document.getElementById('anon-banner');
  if (anonBanner) anonBanner.style.display = isSubscriber ? 'none' : 'flex';
}

async function loadUserPreferences() {
  const savedLang = localStorage.getItem('cv_lang');
  if (savedLang) setLang(savedLang);

  const params = new URLSearchParams(window.location.search);
  const hasParams = params.has('vose') || params.has('cinemas') || params.has('new');

  if (hasParams) {
    setSubscriberUI(true);
    applyPreferencesFromURL();
    const currentParams = new URLSearchParams(window.location.search);
    document.querySelectorAll('a.film-title, a.grid-title, a.list-title').forEach(a => {
      const base = a.getAttribute('href').split('?')[0];
      const card = a.closest('[data-section]');
      const linkParams = new URLSearchParams(currentParams);
      if (card && card.dataset.section === '2') {
        linkParams.set('classic', 'true');
      }
      a.href = base + (linkParams.toString() ? '?' + linkParams.toString() : '');
    });
    return;
  }

  const email = getCookie('cv_email');
  if (!email || !window.SUPABASE_URL || !window.SUPABASE_ANON) {
    setSubscriberUI(false);
    return;
  }

  try {
    const res = await fetch(
      window.SUPABASE_URL + '/rest/v1/subscribers?email=eq.' + encodeURIComponent(email) + '&select=active,lang,cinemas,vose_only,vose_lang,new_only,family_only,evening_only,classics,rating_filter,min_rating,email_enabled',
      { headers: { 'apikey': window.SUPABASE_ANON, 'Authorization': 'Bearer ' + window.SUPABASE_ANON, 'x-subscriber-email': email } }
    );
    const rows = await res.json();

    if (!rows.length || rows[0].active === false) {
      document.cookie = 'cv_email=;expires=Thu, 01 Jan 1970 00:00:00 GMT;path=/;SameSite=Lax';
      setSubscriberUI(false);
      return;
    }

    const prefs = rows[0];
    setSubscriberUI(true);
    if (prefs.lang) setLang(prefs.lang);

    const newParams = new URLSearchParams();
    if (prefs.vose_only)     newParams.set('vose',      'true');
    if (prefs.vose_lang)     newParams.set('vose_lang',  prefs.vose_lang);
    if (prefs.new_only)      newParams.set('new',       'true');
    if (prefs.family_only)   newParams.set('family',    'true');
    if (prefs.evening_only)  newParams.set('evening',   'true');
    if (prefs.classics)      newParams.set('classics',  'true');
    if (prefs.rating_filter) newParams.set('min_rating', prefs.min_rating || 7);
    const allCinemas = ['kinepolis','yelmo','ocine_aqua','lys','park','elsaler','granturia','mn4','tivoli','babel','dor'];
    if (prefs.cinemas && prefs.cinemas.length < allCinemas.length) {
      newParams.set('cinemas', prefs.cinemas.join(','));
    }

    if (newParams.toString()) {
      window.history.replaceState({}, '', '?' + newParams.toString());
      applyVisibility();
      applyPreferencesFromURL();
    }

    const finalParams = new URLSearchParams(window.location.search);
    document.querySelectorAll('a.film-title, a.grid-title, a.list-title').forEach(a => {
      const base = a.getAttribute('href').split('?')[0];
      const card = a.closest('[data-section]');
      const linkParams = new URLSearchParams(finalParams);
      if (card && card.dataset.section === '2') {
        linkParams.set('classic', 'true');
      }
      a.href = base + (linkParams.toString() ? '?' + linkParams.toString() : '');
    });

  } catch(e) {
    console.warn('Could not load preferences:', e);
  }
}

function _anchorDate() {
  if (window.DATA_ANCHOR) {
    const p = window.DATA_ANCHOR.split('-');
    return new Date(parseInt(p[0]), parseInt(p[1])-1, parseInt(p[2]));
  }
  return new Date();
}

function updateHeaderDate() {
  const today = _anchorDate();
  const end   = new Date(today);
  end.setDate(today.getDate() + 6);
  const monthsEs = ["enero","febrero","marzo","abril","mayo","junio","julio","agosto","septiembre","octubre","noviembre","diciembre"];
  const monthsEn = ["January","February","March","April","May","June","July","August","September","October","November","December"];
  const lang = document.getElementById('html-root').lang || 'es';
  let label;
  if (lang === 'en') {
    if (today.getMonth() === end.getMonth()) {
      label = today.getDate() + ' – ' + end.getDate() + ' ' + monthsEn[today.getMonth()] + ' ' + today.getFullYear();
    } else {
      label = today.getDate() + ' ' + monthsEn[today.getMonth()] + ' – ' + end.getDate() + ' ' + monthsEn[end.getMonth()] + ' ' + today.getFullYear();
    }
  } else {
    if (today.getMonth() === end.getMonth()) {
      label = today.getDate() + ' – ' + end.getDate() + ' de ' + monthsEs[today.getMonth()] + ' ' + today.getFullYear();
    } else {
      label = today.getDate() + ' de ' + monthsEs[today.getMonth()] + ' – ' + end.getDate() + ' de ' + monthsEs[end.getMonth()] + ' ' + today.getFullYear();
    }
  }
  const el = document.getElementById('header-date');
  if (el) el.textContent = label;
}

updateHeaderDate();

function setFilter(filter) {
  const url = new URL(window.location);
  if (filter === 'all') url.searchParams.delete('filter');
  else url.searchParams.set('filter', filter);
  window.history.replaceState({}, '', url);
  applyVisibility();
}

function initSections() {
  const s2Container = document.getElementById('section2-cards');
  const s2Divider   = document.getElementById('section2-divider');
  const s2Label     = document.getElementById('section2-label');
  const s2Header    = document.getElementById('section2-header');
  if (!s2Container) return;
  const s2Cards = Array.from(document.querySelectorAll('.grid-card[data-section="2"]'));
  s2Cards.forEach(card => {
    const row = card.closest('.grid-row');
    card.remove();
    if (row && row.querySelectorAll('.grid-card').length === 0) row.remove();
  });
  for (let i = 0; i < s2Cards.length; i += 2) {
    const row = document.createElement('div');
    row.className = 'grid-row';
    row.appendChild(s2Cards[i]);
    if (s2Cards[i + 1]) row.appendChild(s2Cards[i + 1]);
    s2Container.appendChild(row);
  }
  if (s2Cards.length === 0) {
    if (s2Divider) s2Divider.style.display = 'none';
    if (s2Label)   s2Label.style.display   = 'none';
    if (s2Header)  s2Header.style.display  = 'none';
  }
}

function repairSection(container) {
  const allCards     = Array.from(container.querySelectorAll('.grid-card'));
  const visibleCards = allCards.filter(c => c.style.display !== 'none' && !c.classList.contains('qf-hidden'));
  container.querySelectorAll('.grid-row').forEach(row => row.remove());
  Array.from(container.children).forEach(child => {
    if (child.classList.contains('grid-card')) child.remove();
  });
  for (let i = 0; i < visibleCards.length; i += 2) {
    const row = document.createElement('div');
    row.className = 'grid-row';
    row.appendChild(visibleCards[i]);
    if (visibleCards[i + 1]) row.appendChild(visibleCards[i + 1]);
    container.appendChild(row);
  }
  const hiddenCards = allCards.filter(c => c.style.display === 'none' || c.classList.contains('qf-hidden'));
  if (hiddenCards.length > 0) {
    const holdingRow = document.createElement('div');
    holdingRow.className = 'grid-row';
    holdingRow.style.display = 'none';
    hiddenCards.forEach(c => holdingRow.appendChild(c));
    container.appendChild(holdingRow);
  }
}

function applyVisibility() {
  const params        = new URLSearchParams(window.location.search);
  const filter        = params.get('filter')     || 'all';
  const voseOnly      = params.get('vose')        === 'true';
  const voseLang      = params.get('vose_lang')   || 'all';
  const newOnly       = params.get('new')         === 'true';
  const familyOnly    = params.get('family')      === 'true';
  const eveningOnly   = params.get('evening')     === 'true';
  const alwaysClassics= params.get('classics')    === 'true';
  const minRating     = params.has('min_rating')  ? parseFloat(params.get('min_rating')) : null;
  const cinemas       = params.get('cinemas') ? params.get('cinemas').split(',') : null;

  const allBtn  = document.getElementById('filter-all');
  const voseBtn = document.getElementById('filter-vose');
  if (allBtn)  allBtn.classList.toggle('active',  !voseOnly && filter === 'all');
  if (voseBtn) voseBtn.classList.toggle('active', voseOnly  || filter === 'vose');

  let visible = 0;
  document.querySelectorAll('[data-vose]').forEach(card => {
    const isClassic = card.dataset.section === '2';

    if (alwaysClassics && isClassic) {
      let show = true;
      if (voseOnly || filter === 'vose') {
        if (card.dataset.vose !== 'true') show = false;
        if (show && voseLang === 'en') {
          const origins = (card.dataset.origin || '').split(',');
          const engOrigins = ['US','GB','AU','CA','IE','NZ'];
          if (origins.filter(o => o.trim()).length > 0 && !origins.some(o => engOrigins.includes(o.trim()))) show = false;
        }
      }
      card.style.display = show ? '' : 'none';
      if (show) visible++;
      return;
    }

    let show = true;
    if (voseOnly || filter === 'vose') {
      if (card.dataset.vose !== 'true') show = false;
      if (show && voseLang === 'en') {
        const origins = (card.dataset.origin || '').split(',');
        const engOrigins = ['US','GB','AU','CA','IE','NZ'];
        if (origins.filter(o => o.trim()).length > 0 && !origins.some(o => engOrigins.includes(o.trim()))) show = false;
      }
    }
    if (show && newOnly && card.dataset.isnew !== 'true') show = false;
    if (show && familyOnly) {
      const r = (card.dataset.rating || '').replace('+','');
      if (r === '16' || r === '18') show = false;
    }
    if (show && eveningOnly && card.dataset.hasevening !== 'true') show = false;
    if (show && minRating !== null) {
      const score = parseFloat(card.dataset.score || '0');
      if (!score || score < minRating) show = false;
    }
    if (show && cinemas && cinemas.length > 0) {
      const cardCinemas = (card.dataset.cinemas || '').split(',');
      if (!cardCinemas.some(c => cinemas.includes(c.trim()))) show = false;
    }

    card.style.display = show ? '' : 'none';
    if (show) visible++;
  });

  const s2El = document.getElementById('section2-cards');
  const s1El = document.getElementById('section1-cards');
  if (s1El) repairSection(s1El);
  if (s2El) repairSection(s2El);

  const empty = document.getElementById('filter-empty');
  if (empty) empty.style.display = visible === 0 ? 'block' : 'none';

  const s2Divider = document.getElementById('section2-divider');
  const s2Label   = document.getElementById('section2-label');
  const s2Header  = document.getElementById('section2-header');
  if (s2El) {
    const s2Visible = s2El.querySelectorAll('.grid-card:not([style*="display: none"])').length;
    const show = s2Visible > 0;
    if (s2Divider) s2Divider.style.display = show ? '' : 'none';
    if (s2Label)   s2Label.style.display   = show ? '' : 'none';
    if (s2Header)  s2Header.style.display  = show ? '' : 'none';
  }
}
"""


# ── HTML page builders ────────────────────────────────────────────────────────

def build_film_detail_page(film: dict, anchor: datetime) -> str:
    title_es   = film["title"]
    title_en   = film.get("title_en", title_es)
    title_orig = film.get("title_original", title_es)
    syn_es     = (film.get("synopsis_es") or film.get("synopsis", ""))[:400]
    syn_en     = (film.get("synopsis_en") or film.get("synopsis", ""))[:400]
    poster     = film.get("poster", "")
    meta       = film.get("meta", "")
    meta_en    = film.get("meta_en", meta)
    score      = film.get("rating_score")
    rating     = film.get("rating", "?")
    vose       = film.get("any_vose", False)
    is_new     = film.get("is_new", False)

    today = datetime.now(VALENCIA_TZ).date()
    DAYS_EN = ["Monday","Tuesday","Wednesday","Thursday","Friday","Saturday","Sunday"]
    DAYS_ES = ["Lunes","Martes","Miércoles","Jueves","Viernes","Sábado","Domingo"]

    days = []
    for i in range(7):
        d = today + timedelta(days=i)
        days.append({
            "key":      d.strftime("%Y-%m-%d"),
            "label_en": ("Today" if i==0 else "Tomorrow" if i==1 else DAYS_EN[d.weekday()]) + f" {d.day}",
            "label_es": ("Hoy" if i==0 else "Mañana" if i==1 else DAYS_ES[d.weekday()]) + f" {d.day}",
        })

    tab_btns  = ""
    tab_panels = ""
    for i, day in enumerate(days):
        active = "active" if i == 0 else ""
        dk = day["key"]; les = day["label_es"]; len_ = day["label_en"]
        has_shows = any(c.get("showtimes", {}).get(dk) for c in film["cinemas"])
        show_class = "has-shows" if has_shows else ""
        tab_btns += f'<button class="day-tab {active} {show_class}" data-day="{dk}" data-es="{les}" data-en="{len_}" onclick="showDay(\'{dk}\')">{les}</button>'

        cinema_rows = ""
        for c in film["cinemas"]:
            times = c.get("showtimes", {}).get(day["key"], [])
            if not times:
                continue
            vose_label = '<span class="vose-mini">VOSE</span>' if c["vose"] else ""
            time_btns  = "".join(
                f'<button onclick="showComingSoon()" class="time-btn" data-time="{t}">{t}</button>'
                for t in times
            )
            cinema_rows += f'<div class="showtime-row" data-cinema-id="{c["id"]}"><div class="showtime-cinema"><span translate="no">{c["name"]}</span>{vose_label}</div><div class="showtime-times">{time_btns}</div></div>'

        if not cinema_rows:
            cinema_rows = f'<div class="no-times" data-es="Sin sesiones este día" data-en="No screenings this day">Sin sesiones este día</div>'

        panel_active = "active" if i == 0 else ""
        tab_panels += f'<div class="day-panel {panel_active}" id="day-{day["key"]}">{cinema_rows}</div>'

    new_badge   = '<span class="film-badge badge-new" data-es="ESTRENO" data-en="NEW RELEASE">ESTRENO</span>' if is_new else ""
    vose_badge  = '<span class="vose-badge">VOSE</span>' if vose else ""
    score_badge = f'<span class="score-badge">⭐ {score}</span>' if score else ""
    rating_label = 'TP' if rating == 'TP' else (f'+{rating}' if rating not in ('?', '') else '')
    rating_badge = f'<span class="rating-badge">{rating_label}</span>' if rating_label else ""
    poster_html  = f'<img src="{poster}" alt="{esc(title_es)}" style="width:100%;height:auto;object-fit:contain;display:block;">' if poster else '<div style="font-size:64px;text-align:center;padding:40px;">🎬</div>'
    orig_label   = f'<div class="orig-title" translate="no">{title_orig}</div>' if title_orig and title_orig != title_es and title_orig != title_en else ""

    return f"""<!DOCTYPE html>
<html lang="es" id="html-root">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta http-equiv="X-Content-Type-Options" content="nosniff">
<meta http-equiv="X-Frame-Options" content="SAMEORIGIN">
<link rel="icon" type="image/png" href="/favicon.png">
<link rel="shortcut icon" href="/favicon.ico">
<link rel="manifest" href="/manifest.json">
<meta name="theme-color" content="#0a0810">
<meta name="mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="apple-mobile-web-app-title" content="whatson.movie">
<link rel="apple-touch-icon" href="/icons/icon-192.png">
<title data-es="{esc(title_es)} — Cartelera Valencia" data-en="{esc(title_en)} — Cartelera Valencia">{esc(title_es)} — Cartelera Valencia</title>
<style>
@import url('https://fonts.googleapis.com/css2?family=Playfair+Display:wght@700&family=DM+Sans:wght@300;400;500&display=swap');
*{{margin:0;padding:0;box-sizing:border-box}}
body{{background:#0f0c14;font-family:'DM Sans',Helvetica,sans-serif;color:#f0eae0;min-height:100vh}}
.wrapper{{max-width:640px;margin:0 auto;background:#0f0c14}}
.lang-bar{{background:#0a0810;border-bottom:1px solid #1e1630;padding:10px 16px;display:flex;justify-content:space-between;align-items:center;gap:8px}}
.lang-bar a{{font-family:'Playfair Display',Georgia,serif;font-size:15px;font-weight:700;color:#f0eae0;text-decoration:none;white-space:nowrap}}
.lang-bar a span{{color:#ffb432}}
.lang-toggle{{display:flex;border-radius:6px;overflow:hidden;border:1px solid #2e2545}}
.lang-btn{{padding:5px 14px;font-size:11px;font-weight:500;letter-spacing:1px;text-transform:uppercase;cursor:pointer;border:none;background:transparent;color:#6a5e7a;font-family:'DM Sans',sans-serif;transition:all .2s}}
.lang-btn.active{{background:#160f24;color:#f0eae0}}
.back-bar{{padding:12px 20px;background:#0a0810;border-bottom:1px solid #1e1630}}
.back-link{{font-size:12px;color:#7a6a9a;text-decoration:none;letter-spacing:0.5px}}
.back-link:hover{{color:#c5b8d8}}
.film-hero{{display:flex;gap:16px;padding:20px;background:#160f24;border-bottom:1px solid #2e2040}}
.hero-poster{{width:90px;height:130px;flex-shrink:0;border-radius:8px;overflow:hidden;background:#2a1f3d}}
.hero-info{{flex:1;display:flex;flex-direction:column;justify-content:center}}
.badges{{display:flex;flex-wrap:wrap;gap:5px;margin-bottom:8px}}
.film-badge{{display:inline-block;padding:2px 7px;border-radius:4px;font-size:10px;font-weight:700;letter-spacing:1.5px}}
.badge-new{{background:rgba(255,180,50,.15);color:#ffb432;border:1px solid rgba(255,180,50,.35)}}
.vose-badge{{display:inline-block;padding:2px 7px;border-radius:4px;font-size:10px;font-weight:700;letter-spacing:1.5px;background:rgba(255,220,80,.15);color:#ffd84a;border:1px solid rgba(255,220,80,.35)}}
.score-badge{{display:inline-block;padding:2px 8px;border-radius:4px;font-size:10px;font-weight:600;background:rgba(255,255,255,.06);color:#c5b8d8;border:1px solid rgba(255,255,255,.12)}}
.rating-badge{{display:inline-block;padding:2px 8px;border-radius:4px;font-size:10px;font-weight:600;letter-spacing:0.5px;background:rgba(180,100,100,.12);color:#c98a8a;border:1px solid rgba(180,100,100,.3)}}
.hero-title{{font-family:'Playfair Display',Georgia,serif;font-size:22px;font-weight:700;color:#f0eae0;line-height:1.2;margin-bottom:4px}}
.orig-title{{font-size:11px;color:#5a4e6a;margin-bottom:6px}}
.hero-meta{{font-size:11px;color:#7a6d8a;line-height:1.55;margin-bottom:8px}}
.hero-synopsis{{font-size:12px;color:#9d909e;line-height:1.6}}
.section-title{{font-size:10px;letter-spacing:3px;text-transform:uppercase;color:#4a3f5e;padding:20px 20px 10px}}
.day-tabs{{display:flex;flex-wrap:wrap;gap:8px;padding:0 20px 14px}}
.day-tab{{padding:7px 14px;border-radius:20px;font-size:11px;font-weight:500;letter-spacing:0.5px;cursor:pointer;border:1px solid #2e2545;background:transparent;color:#6a5e7a;font-family:'DM Sans',sans-serif;white-space:nowrap;transition:all .2s}}
.day-tab.active{{background:rgba(255,180,50,.15);color:#ffb432;border-color:rgba(255,180,50,.4)}}
.day-tab.has-shows{{color:#50c88c}}
.day-panel{{display:none;padding:0 20px 20px}}
.day-panel.active{{display:block}}
.showtime-row{{padding:14px 0;border-bottom:1px solid #1e1630}}
.showtime-row:last-child{{border-bottom:none}}
.showtime-cinema{{font-size:13px;font-weight:500;color:#c5b8d8;margin-bottom:8px;display:flex;align-items:center;gap:6px}}
.vose-mini{{font-size:9px;font-weight:700;letter-spacing:1px;padding:1px 5px;background:rgba(255,220,80,.12);color:#ffd84a;border:1px solid rgba(255,220,80,.35);border-radius:3px}}
.showtime-times{{display:flex;flex-wrap:wrap;gap:8px}}
.time-btn{{padding:6px 14px;background:#1a1228;border:1px solid #2e2040;border-radius:6px;font-size:13px;color:#f0eae0;text-decoration:none;transition:all .2s;font-weight:500}}
.time-btn:hover{{background:#2a1f3d;border-color:#ffb432;color:#ffb432}}
.time-btn--match{{background:#0d2418;border-color:#1d6b3a;color:#50c88c}}
.time-btn--match:hover{{background:#112e1e;border-color:#50c88c;color:#50c88c}}
.showtime-legend{{display:flex;align-items:center;gap:8px;padding:10px 20px 16px;font-size:11px;color:#6a5e7a;border-top:1px solid #1e1630}}
.showtime-legend-dot{{width:10px;height:10px;border-radius:3px;background:#0d2418;border:1px solid #1d6b3a;flex-shrink:0}}
.no-times{{font-size:13px;color:#4a3f5e;padding:20px 0;text-align:center}}
.footer{{background:#0a0810;border-top:1px solid #1e1630;padding:20px;text-align:center;font-size:11px;color:#3a2e50}}
@media(max-width:480px){{.lang-bar{{padding:8px 12px}}.lang-btn{{padding:4px 10px;font-size:10px}}}}
</style>
</head>
<body>
<div class="wrapper">
  <div class="lang-bar">
    <a href="../../">whatson<span>.movie</span></a>
    <div style="display:flex;align-items:center;gap:8px;">
      <div class="lang-toggle">
        <button class="lang-btn active" id="btn-es" onclick="setLang('es')">ES</button>
        <button class="lang-btn" id="btn-en" onclick="setLang('en')">EN</button>
      </div>
    </div>
  </div>

  <div class="back-bar">
    <a href="../" class="back-link" onclick="history.length>1?history.back():window.location='../';return false;">← <span data-es="Volver a la cartelera" data-en="Back to listings">Volver a la cartelera</span></a>
  </div>

  <div class="film-hero">
    <div class="hero-poster">{poster_html}</div>
    <div class="hero-info">
      <div class="badges">{new_badge}{vose_badge}{score_badge}{rating_badge}</div>
      <div class="hero-title" data-es="{esc(title_es)}" data-en="{esc(title_en)}">{title_es}</div>
      {orig_label}
      <div class="hero-meta"><span data-es="{meta}" data-en="{meta_en}">{meta}</span></div>
      <div class="hero-synopsis" data-es="{esc(syn_es)}" data-en="{esc(syn_en)}">{syn_es}</div>
    </div>
  </div>

  <div id="showtimes-section" style="display:none;">
    <div class="section-title" data-es="🕖 HORARIOS — próximos 7 días" data-en="🕖 SHOWTIMES — next 7 days">🕖 HORARIOS — próximos 7 días</div>
    <div class="day-tabs">{tab_btns}</div>
    <div id="day-panels">{tab_panels}</div>
    <div class="showtime-legend" id="showtime-legend" style="display:none;">
      <div class="showtime-legend-dot"></div>
      <span data-es="Coincide con tu filtro de tardes y fines de semana" data-en="Meets your evenings &amp; weekends filter">Coincide con tu filtro de tardes y fines de semana</span>
    </div>
  </div>

  <div id="gate-section" style="display:none;margin:20px;padding:24px 20px;background:rgba(255,180,50,0.06);border:1px solid rgba(255,180,50,0.2);border-radius:10px;text-align:center;">
    <div style="font-size:24px;margin-bottom:12px;">🎬</div>
    <div style="font-family:'Playfair Display',Georgia,serif;font-size:17px;font-weight:700;color:#f0eae0;margin-bottom:8px;" data-es="Los horarios son para suscriptores" data-en="Showtimes are for subscribers">Los horarios son para suscriptores</div>
    <div style="font-size:13px;color:#9b8faa;line-height:1.6;margin-bottom:20px;" data-es="Suscríbete para ver los horarios en todos los cines, filtrados exactamente como quieres, y recibe un email a tu medida cada jueves." data-en="Subscribe to see showtimes across all cinemas, filtered exactly how you like it, plus get a tailored email every Thursday.">Suscríbete para ver los horarios en todos los cines, filtrados exactamente como quieres, y recibe un email a tu medida cada jueves.</div>
    <a href="../../" style="display:inline-block;padding:11px 28px;background:#ffb432;color:#0a0810;font-size:12px;font-weight:600;letter-spacing:1px;text-transform:uppercase;border-radius:7px;text-decoration:none;" data-es="Suscribirse →" data-en="Subscribe →">Suscribirse →</a>
  </div>

  <div class="footer">
    <span data-es="Horarios sujetos a cambios — verifica siempre en la web del cine." data-en="Showtimes subject to change — always verify on the cinema's website.">Horarios sujetos a cambios — verifica siempre en la web del cine.</span>
  </div>
</div>

<div id="coming-soon-overlay" onclick="hideComingSoon()" style="display:none;position:fixed;inset:0;background:rgba(0,0,0,0.5);z-index:1000;align-items:center;justify-content:center;">
  <div style="background:#ffffff;border-radius:4px;padding:28px 32px;text-align:center;max-width:280px;margin:0 20px;box-shadow:0 8px 32px rgba(0,0,0,0.2);">
    <div style="font-size:28px;margin-bottom:12px;">🎬</div>
    <div style="font-size:16px;font-weight:700;color:#111111;margin-bottom:8px;" data-es="Próximamente" data-en="Coming soon">Próximamente</div>
    <div style="font-size:13px;color:#555555;line-height:1.5;" data-es="La compra de entradas estará disponible muy pronto." data-en="Ticket purchasing will be available very soon.">La compra de entradas estará disponible muy pronto.</div>
    <button onclick="hideComingSoon()" style="margin-top:20px;padding:8px 24px;background:#c0392b;color:#ffffff;border:none;border-radius:3px;font-size:13px;font-weight:600;cursor:pointer;font-family:'DM Sans',sans-serif;">OK</button>
  </div>
</div>
<script>
function setLang(lang) {{
  document.getElementById('html-root').lang = lang;
  document.getElementById('btn-es').classList.toggle('active', lang === 'es');
  document.getElementById('btn-en').classList.toggle('active', lang === 'en');
  document.querySelectorAll('[data-es][data-en]').forEach(el => {{
    el.innerHTML = el.getAttribute('data-' + lang);
  }});
  document.title = (lang === 'en' ? '{esc(title_en)}' : '{esc(title_es)}') + ' — Cartelera Valencia';
  localStorage.setItem('cv_lang', lang);
}}
function isWeekend(dateKey) {{
  const d = new Date(dateKey);
  return d.getDay() === 0 || d.getDay() === 6;
}}
function applyEveningHighlights(eveningFilter) {{
  if (!eveningFilter) return;
  document.querySelectorAll('.time-btn[data-time]').forEach(btn => {{
    const panel = btn.closest('.day-panel');
    const dayKey = panel ? panel.id.replace('day-', '') : '';
    const weekend = isWeekend(dayKey);
    const t = btn.getAttribute('data-time');
    const parts = t.split(':');
    const mins = parseInt(parts[0]) * 60 + parseInt(parts[1] || 0);
    if (weekend || mins >= 17 * 60 + 30) {{
      btn.classList.add('time-btn--match');
    }} else {{
      btn.classList.remove('time-btn--match');
    }}
  }});
}}
function showDay(key) {{
  document.querySelectorAll('.day-tab').forEach(t => t.classList.remove('active'));
  document.querySelectorAll('.day-panel').forEach(p => p.classList.remove('active'));
  const tab = document.querySelector(`.day-tab[data-day="${{key}}"]`);
  const panel = document.getElementById('day-' + key);
  if (tab) tab.classList.add('active');
  if (panel) panel.classList.add('active');
  const params = new URLSearchParams(window.location.search);
  applyEveningHighlights(params.get('evening') === 'true');
}}
window.addEventListener('DOMContentLoaded', () => {{
  const urlParams = new URLSearchParams(window.location.search);
  const lang = urlParams.get('lang') || localStorage.getItem('cv_lang') || 'es';
  if (lang !== 'es') setLang(lang);
  const tabParam = urlParams.get('tab');
  if (tabParam) {{
    const today    = new Date();
    const tomorrow = new Date(today); tomorrow.setDate(today.getDate()+1);
    const plus1    = new Date(today); plus1.setDate(today.getDate()+2);
    function toKey(d) {{ return d.getFullYear()+'-'+String(d.getMonth()+1).padStart(2,'0')+'-'+String(d.getDate()).padStart(2,'0'); }}
    const targetKey = tabParam === 'today' ? toKey(today) : tabParam === 'tomorrow' ? toKey(tomorrow) : tabParam === 'plus1' ? toKey(plus1) : null;
    if (targetKey) {{
      const tab = document.querySelector(`.day-tab[data-day="${{targetKey}}"]`);
      if (tab) tab.click();
    }}
  }}
  const isSubscriber = document.cookie.match(/(^| )cv_email=([^;]+)/);
  document.getElementById('showtimes-section').style.display = isSubscriber ? 'block' : 'none';
  document.getElementById('gate-section').style.display     = isSubscriber ? 'none'  : 'block';
  const params  = new URLSearchParams(window.location.search);
  const cinemas = params.get('cinemas');
  const isClassicFilm = params.get('classic') === 'true';
  const alwaysClassics = params.get('classics') === 'true';
  if (cinemas && !(alwaysClassics && isClassicFilm)) {{
    const allowed = cinemas.split(',');
    document.querySelectorAll('.showtime-row[data-cinema-id]').forEach(row => {{
      const cid = row.getAttribute('data-cinema-id');
      if (!allowed.includes(cid)) {{
        row.style.display = 'none';
      }}
    }});
    document.querySelectorAll('.day-panel').forEach(panel => {{
      const dayKey = panel.id.replace('day-', '');
      const visibleRows = Array.from(panel.querySelectorAll('.showtime-row[data-cinema-id]'))
                               .filter(r => r.style.display !== 'none');
      const noTimesEl = panel.querySelector('.no-times');
      if (visibleRows.length === 0) {{
        if (!noTimesEl) {{
          const msg = document.createElement('div');
          msg.className = 'no-times';
          msg.setAttribute('data-es', 'Sin sesiones este día');
          msg.setAttribute('data-en', 'No screenings this day');
          msg.textContent = 'Sin sesiones este día';
          panel.appendChild(msg);
        }} else {{
          noTimesEl.style.display = '';
        }}
        const tab = document.querySelector(`.day-tab[data-day="${{dayKey}}"]`);
        if (tab) tab.classList.remove('has-shows');
      }} else {{
        if (noTimesEl) noTimesEl.style.display = 'none';
      }}
    }});
  }}
  const eveningFilter = params.get('evening') === 'true';
  if (eveningFilter) {{
    applyEveningHighlights(true);
    const legend = document.getElementById('showtime-legend');
    if (legend) legend.style.display = 'flex';
  }}
}});
function showComingSoon() {{
  const overlay = document.getElementById('coming-soon-overlay');
  overlay.style.display = 'flex';
  const lang = document.getElementById('html-root').lang || 'es';
  overlay.querySelectorAll('[data-es][data-en]').forEach(el => {{
    el.innerHTML = el.getAttribute('data-' + lang);
  }});
}}
function hideComingSoon() {{
  document.getElementById('coming-soon-overlay').style.display = 'none';
}}
</script>
</body>
</html>"""


def build_html(films_by_title: dict, anchor: datetime) -> str:
    date_es = week_range_es(anchor)
    date_en = week_range_en(anchor)

    multiplex_films = []
    arthouse_films: dict = {}

    for title, film in sorted(
        films_by_title.items(),
        key=lambda x: (-x[1]["is_new"], -(x[1].get("rating_score") or 0), x[0])
    ):
        cinema_types = {c["type"] for c in film["cinemas"]}
        cinema_ids   = {c["id"]   for c in film["cinemas"]}

        if "multiplex" in cinema_types or cinema_ids.issubset({"babel", "dor"}):
            multiplex_films.append(film)

        for cid in ["babel", "dor"]:
            if cid in cinema_ids:
                arthouse_films.setdefault(cid, []).append(film)

    def grid_card_html(film):
        poster   = film.get("poster", "")
        synopsis = film.get("synopsis", "")
        meta     = film.get("meta", "")
        vose     = film.get("any_vose", False)
        is_new   = film.get("is_new", False)
        cinemas  = film["cinemas"]
        rating   = film.get("rating", "?")

        poster_html = (
            f'<img src="{poster}" alt="{film["title"]}" style="width:100%;height:auto;object-fit:contain;display:block;">'
            if poster else '<div style="font-size:34px;">🎬</div>'
        )
        new_badge   = '<span class="film-badge badge-new" data-es="ESTRENO" data-en="NEW">ESTRENO</span>' if is_new else ""
        vose_badge  = '<span class="vose-badge">VOSE</span>' if vose else ""
        score       = film.get("rating_score")
        score_badge = f'<span class="score-badge">⭐ {score}</span>' if score else ""
        rating_label = 'TP' if rating == 'TP' else (f'+{rating}' if rating not in ('?', '') else '')
        rating_badge = f'<span class="rating-badge">{rating_label}</span>' if rating_label else ""
        cinema_tags = "".join(
            '<a href="' + c["website"] + '" class="cinema-tag" data-cinema="' + c["id"] + '">' + c["name"] + ('<span class="vose-mini">VOSE</span>' if c["vose"] else "") + '</a>'
            for c in cinemas
        )
        where_es, where_en = "Dónde verla", "Where to see it"
        cd = compute_card_data(film)
        title_es = film["title"]
        title_en = film.get("title_en", film["title"])
        slug     = film.get("slug")
        section  = cd["section"]
        if slug:
            classic_param = '?classic=true' if section == '2' else ''
            title_html = f'<a href="./{slug}/{classic_param}" class="grid-title" data-es="{esc(title_es)}" data-en="{esc(title_en)}">{title_es}</a>'
        else:
            title_html = f'<div class="grid-title" data-es="{esc(title_es)}" data-en="{esc(title_en)}">{title_es}</div>'
        syn_es = (film.get("synopsis_es") or synopsis)[:140]
        syn_en = (film.get("synopsis_en") or synopsis)[:140]

        return f"""
    <div class="grid-card" data-vose="{'true' if vose else 'false'}" data-isnew="{'true' if is_new else 'false'}" data-cinemas="{cd['cinema_ids']}" data-year="{cd['year']}" data-section="{cd['section']}" data-rating="{cd['rating_val']}" data-score="{cd['score_val']}" data-origin="{cd['origin']}" data-hasevening="{cd['hasevening']}" data-showdays="{cd['showdays_attr']}" {cd['showtimes_attrs']}>
      <div class="grid-poster">{poster_html}</div>
      <div class="grid-info">
        <div class="badges">{new_badge}{vose_badge}{score_badge}{rating_badge}</div>
        {title_html}
        <div class="grid-meta"><span data-es="{meta[:80]}" data-en="{film.get('meta_en', meta)[:80]}">{meta[:80]}</span></div>
        <div class="grid-synopsis" data-es="{esc(syn_es)}" data-en="{esc(syn_en)}">{syn_es}</div>
        <div class="cinema-links">
          <div class="cinema-links-label" data-es="{where_es}" data-en="{where_en}">{where_es}</div>
          <div class="cinema-tags">{cinema_tags}</div>
        </div>
        <div class="showtimes-hint"><svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg><span data-es="Horarios y detalles →" data-en="Showtimes &amp; details →">Horarios y detalles →</span></div>
      </div>
    </div>"""

    multiplex_cards = ""
    for i in range(0, len(multiplex_films), 2):
        pair  = multiplex_films[i:i+2]
        inner = "".join(grid_card_html(f) for f in pair)
        multiplex_cards += f'\n  <div class="grid-row">{inner}\n  </div>'

    babel_cards = ""
    if "babel" in arthouse_films:
        babel_only = [f for f in arthouse_films["babel"] if not any(c["type"] == "multiplex" for c in f["cinemas"])]
        for i in range(0, len(babel_only), 2):
            pair  = babel_only[i:i+2]
            inner = "".join(grid_card_html(f) for f in pair)
            babel_cards += f'\n  <div class="grid-row">{inner}\n  </div>'

    dor_cards = ""
    if "dor" in arthouse_films:
        for i in range(0, len(arthouse_films["dor"]), 2):
            pair  = arthouse_films["dor"][i:i+2]
            inner = "".join(grid_card_html(f) for f in pair)
            dor_cards += f'\n  <div class="grid-row">{inner}\n  </div>'

    return f"""<!DOCTYPE html>
<html lang="es" id="html-root">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta http-equiv="X-Content-Type-Options" content="nosniff">
<meta http-equiv="X-Frame-Options" content="SAMEORIGIN">
<meta http-equiv="Cache-Control" content="no-cache, no-store, must-revalidate">
<meta http-equiv="Pragma" content="no-cache">
<meta http-equiv="Expires" content="0">
<link rel="icon" type="image/png" href="/favicon.png">
<link rel="shortcut icon" href="/favicon.ico">
<link rel="manifest" href="/manifest.json">
<meta name="theme-color" content="#0a0810">
<meta name="mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="apple-mobile-web-app-title" content="whatson.movie">
<link rel="apple-touch-icon" href="/icons/icon-192.png">
<title>Cartelera Valencia – {date_en}</title>
<style>{CSS}</style>
</head>
<body>
<div class="wrapper">

  <div class="lang-bar">
    <a href="../" style="font-family:'Playfair Display',Georgia,serif;font-size:15px;font-weight:700;color:#f0eae0;text-decoration:none;white-space:nowrap;">whatson<span style="color:#ffb432;">.movie</span></a>
    <div style="display:flex;align-items:center;gap:8px;margin-left:auto;">
      <div class="lang-toggle">
        <button class="lang-btn active" id="btn-es" onclick="setLang('es')">ES</button>
        <button class="lang-btn" id="btn-en" onclick="setLang('en')">EN</button>
      </div>
      <a href="../" id="nav-subscribe" style="font-size:11px;font-weight:600;padding:5px 12px;background:#ffb432;color:#0a0810;border-radius:5px;text-decoration:none;white-space:nowrap;" data-es="Suscribirse" data-en="Subscribe">Suscribirse</a>
    </div>
  </div>

  <div id="anon-banner" style="background:linear-gradient(135deg,rgba(255,180,50,0.12),rgba(180,80,120,0.08));border-bottom:1px solid rgba(255,180,50,0.25);padding:18px 24px;display:flex;align-items:center;justify-content:space-between;gap:16px;flex-wrap:wrap;">
    <div style="display:flex;flex-direction:column;gap:4px;">
      <span style="font-size:15px;font-weight:500;color:#f0eae0;" data-es="🎬 Más de 30 películas. 11 cines. Cada semana." data-en="🎬 30+ films. 11 cinemas. Every week.">🎬 Más de 30 películas. 11 cines. Cada semana.</span>
      <span style="font-size:12px;color:#9b8faa;" data-es="Suscríbete gratis para filtrar por VOSE, elegir tus cines favoritos y recibir un email curado cada semana." data-en="Subscribe free to filter by VOSE, choose your favourite cinemas and receive a curated weekly email.">Suscríbete gratis para filtrar por VOSE, elegir tus cines favoritos y recibir un email curado cada semana.</span>
    </div>
    <a href="../" style="flex-shrink:0;font-size:13px;font-weight:700;padding:10px 22px;background:#ffb432;color:#0a0810;border-radius:8px;text-decoration:none;white-space:nowrap;letter-spacing:0.5px;" data-es="Suscribirse gratis →" data-en="Subscribe free →">Suscribirse gratis →</a>
  </div>

  <main>
  <div class="header">
    <h1 class="header-title" id="header-title">Cartelera<br>Valencia</h1>
    <div class="header-subtitle" data-es="La guía completa del cine en Valencia esta semana" data-en="Your complete guide to cinema in Valencia this week">La guía completa del cine en Valencia esta semana</div>
    <div class="header-date" id="header-date"></div>
  </div>

  <div id="quick-filter" style="display:block;position:relative;">
    <div id="qf-lock-overlay" style="display:none;position:absolute;inset:0;background:rgba(10,8,16,0.7);z-index:10;display:flex;align-items:center;justify-content:center;gap:10px;cursor:pointer;" onclick="window.location.href='../'">
      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="#ffb432" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="11" width="18" height="11" rx="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/></svg>
      <span style="font-size:12px;color:#ffb432;" data-es="Solo suscriptores" data-en="Subscribers only">Solo suscriptores</span>
      <a href="../" style="font-size:11px;color:#9b8faa;text-decoration:underline;text-underline-offset:3px;" data-es="Suscribirse →" data-en="Subscribe →">Suscribirse →</a>
    </div>
    <div style="display:flex;align-items:flex-end;padding:0;">
      <div style="font-family:'Playfair Display',Georgia,serif;font-size:17px;font-weight:700;color:#f0eae0;line-height:1;background:#0f0c14;border:2px solid #5a4a7a;border-bottom:2px solid #0f0c14;border-radius:8px 8px 0 0;padding:8px 20px 10px;position:relative;z-index:2;margin-bottom:-2px;">quick<em style="color:#ffb432;font-style:italic;">filters</em></div>
      <a href="../preferences/" style="font-family:'Playfair Display',Georgia,serif;font-size:17px;font-weight:700;color:#c5b8d8;line-height:1;text-decoration:none;padding:8px 16px 10px;border-bottom:2px solid #5a4a7a;flex:1;white-space:nowrap;" data-es="filtros <em style='color:#ffb432;font-style:italic;'>avanzados</em> →" data-en="advanced <em style='color:#ffb432;font-style:italic;'>filters</em> →">advanced <em style="color:#ffb432;font-style:italic;">filters</em> →</a>
    </div>
    <div style="background:#0f0c14;border:2px solid #5a4a7a;border-top:none;border-bottom:2px solid #5a4a7a;padding:14px 20px;">
      <div id="qf-days" style="display:flex;gap:8px;margin-bottom:10px;">
        <button class="qf-btn qf-active" id="qf-all" data-es="Próximos 7 días" data-en="Next 7 days" onclick="setQFDay('all')">Próximos 7 días</button>
        <button class="qf-btn" id="qf-today" data-es="Hoy" data-en="Today" onclick="setQFDay('today')">Hoy</button>
        <button class="qf-btn" id="qf-tomorrow" data-es="Mañana" data-en="Tomorrow" onclick="setQFDay('tomorrow')">Mañana</button>
        <button class="qf-btn" id="qf-plus1" onclick="setQFDay('plus1')"></button>
      </div>
      <div id="qf-times" style="display:flex;gap:8px;">
        <button class="qf-btn qf-active" id="qf-anytime" data-es="Cualquier hora" data-en="Any time" onclick="setQFTime('anytime')">Cualquier hora</button>
        <button class="qf-btn" id="qf-morning" data-es="Mañana" data-en="Morning" onclick="setQFTime('morning')">Mañana</button>
        <button class="qf-btn" id="qf-afternoon" data-es="Tarde" data-en="Afternoon" onclick="setQFTime('afternoon')">Tarde</button>
        <button class="qf-btn" id="qf-evening" data-es="Noche" data-en="Evening" onclick="setQFTime('evening')">Noche</button>
      </div>
    </div>
  </div>

  <div class="section-label" data-es="🎬 Cines Multiplex — Grandes Estrenos" data-en="🎬 Multiplex Cinemas — Major Releases">🎬 Cines Multiplex — Grandes Estrenos</div>
  <div class="cinema-group-header">
    <div>
      <div class="cinema-group-name">Kinépolis · Yelmo · Ocine Aqua · ABC · MN4 · Lys · Tívoli</div>
      <div class="cinema-group-desc" data-es="Los grandes multiplex de Valencia y área metropolitana" data-en="Valencia's main multiplexes across the city and metropolitan area">Los grandes multiplex de Valencia y área metropolitana</div>
    </div>
  </div>
  <div id="section1-cards">
  {multiplex_cards}
  </div>

  <div class="section-divider" id="section2-divider"></div>
  <div class="section-label" id="section2-label" data-es="🎭 Arthouse &amp; Clásicos" data-en="🎭 Arthouse &amp; Classics">🎭 Arthouse &amp; Clásicos</div>
  <div class="cinema-group-header" id="section2-header">
    <div>
      <div class="cinema-group-name">Cines Babel · Cinestudio D'Or</div>
      <div class="cinema-group-desc" data-es="Cine de autor, sesiones VOSE especializadas y reposiciones clásicas" data-en="Arthouse cinema, specialist VOSE screenings and classic re-releases">Cine de autor, sesiones VOSE especializadas y reposiciones clásicas</div>
    </div>
  </div>
  <div id="section2-cards"></div>
  <div class="section-divider"></div>
  </main>

  <div class="footer">
    <div class="footer-logo">Cartelera Valencia</div>
    <p>
      <span data-es="Fuente de metadatos:" data-en="Metadata source:">Fuente de metadatos:</span>
      <a href="https://www.themoviedb.org">TMDB</a><br>
      <span data-es="Horarios y disponibilidad VOSE pueden variar — verifica siempre en la web de cada cine." data-en="Showtimes and VOSE availability may vary — always check the cinema's website before you go.">Horarios y disponibilidad VOSE pueden variar — verifica siempre en la web de cada cine.</span><br>
      <em style="color:#3a2050;" data-es="🎭 Babel y Cinestudio D'Or son los referentes del cine de autor y VOSE en Valencia" data-en="🎭 Babel and Cinestudio D'Or are Valencia's homes for arthouse and VOSE cinema">🎭 Babel y Cinestudio D'Or son los referentes del cine de autor y VOSE en Valencia</em><br><br>
      <span style="color:#3a2e50;">© {anchor.year} · Cartelera Valencia Weekly</span> · <a href="../privacy/" data-es="Privacidad" data-en="Privacy">Privacidad</a>
    </p>
  </div>

</div>
<script>
window.SUPABASE_URL  = "__SUPABASE_URL__";
window.SUPABASE_ANON = "__SUPABASE_ANON__";
window.DATA_ANCHOR   = "{anchor.strftime('%Y-%m-%d')}";
{JS}
window.addEventListener('DOMContentLoaded', () => {{
  initSections();
  applyVisibility();
  loadUserPreferences();
  attachCardClicks();

  setTimeout(() => {{
    const currentParams = new URLSearchParams(window.location.search);
    if (window._qfDay && window._qfDay !== 'all') {{
      currentParams.set('tab', window._qfDay);
    }}
    document.querySelectorAll('a.film-title, a.grid-title, a.list-title').forEach(a => {{
      const base = a.getAttribute('href').split('?')[0];
      const card = a.closest('[data-section]');
      const linkParams = new URLSearchParams(currentParams);
      if (card && card.dataset.section === '2') {{
        linkParams.set('classic', 'true');
      }}
      a.href = base + (linkParams.toString() ? '?' + linkParams.toString() : '');
    }});
    attachCardClicks();
  }}, 1500);
}});

(function() {{
  const days = ['Sunday','Monday','Tuesday','Wednesday','Thursday','Friday','Saturday'];
  const today    = _anchorDate();
  const tomorrow = new Date(today); tomorrow.setDate(today.getDate()+1);
  const plus1    = new Date(today); plus1.setDate(today.getDate()+2);

  function toDateKey(d) {{
    return d.getFullYear()+'-'+String(d.getMonth()+1).padStart(2,'0')+'-'+String(d.getDate()).padStart(2,'0');
  }}

  const todayKey    = toDateKey(today);
  const tomorrowKey = toDateKey(tomorrow);
  const plus1Key    = toDateKey(plus1);

  const plus1Btn = document.getElementById('qf-plus1');
  if (plus1Btn) {{
    plus1Btn.textContent = days[plus1.getDay()] + ' ' + plus1.getDate();
  }}

  let qfDay  = null;
  window._qfDay = null;
  let qfTime = 'anytime';

  window.setQFDay = function(day) {{
    qfDay = (day === 'all') ? null : day;
    window._qfDay = qfDay;
    const currentParams = new URLSearchParams(window.location.search);
    if (qfDay) currentParams.set('tab', qfDay);
    else currentParams.delete('tab');
    document.querySelectorAll('a.film-title, a.grid-title, a.list-title').forEach(a => {{
      const base = a.getAttribute('href').split('?')[0];
      const lp = new URLSearchParams(currentParams);
      const card = a.closest('[data-section]');
      if (card && card.dataset.section === '2') lp.set('classic', 'true');
      a.href = base + (lp.toString() ? '?' + lp.toString() : '');
    }});
    ['all','today','tomorrow','plus1'].forEach(d => {{
      const active = (d === 'all' && !qfDay) || (d === qfDay);
      document.getElementById('qf-'+d)?.classList.toggle('qf-active', active);
    }});
    applyQF();
  }};

  window.setQFTime = function(time) {{
    qfTime = time;
    ['morning','afternoon','evening','anytime'].forEach(t => {{
      document.getElementById('qf-'+t)?.classList.toggle('qf-active', qfTime === t);
    }});
    applyQF();
  }};

  function applyQF() {{
    const dayKey = qfDay === 'today' ? todayKey : qfDay === 'tomorrow' ? tomorrowKey : qfDay === 'plus1' ? plus1Key : null;

    document.querySelectorAll('[data-showdays]').forEach(card => {{
      if (!dayKey && qfTime === 'anytime') {{
        card.classList.remove('qf-hidden');
        return;
      }}
      if (dayKey) {{
        const showdays = (card.dataset.showdays || '').split(',');
        if (!showdays.includes(dayKey)) {{
          card.classList.add('qf-hidden');
          return;
        }}
        if (qfTime !== 'anytime') {{
          const selectedCinemas = new URLSearchParams(window.location.search).get('cinemas');
          const cinemaList = selectedCinemas ? selectedCinemas.split(',') : null;
          const allAttrs = Array.from(card.attributes).filter(a => a.name.startsWith('data-t-') && a.name.endsWith('_'+dayKey));
          const relevantAttrs = cinemaList ? allAttrs.filter(a => cinemaList.some(c => a.name.includes('data-t-'+c+'_'))) : allAttrs;
          const times = relevantAttrs.flatMap(a => a.value.split('|').filter(Boolean));
          const matches = times.some(t => {{
            const h = parseInt(t.split(':')[0]);
            if (qfTime === 'morning')   return h < 12;
            if (qfTime === 'afternoon') return h >= 12 && h < 18;
            if (qfTime === 'evening')   return h >= 18;
            return true;
          }});
          if (matches) card.classList.remove('qf-hidden');
          else card.classList.add('qf-hidden');
          return;
        }}
        card.classList.remove('qf-hidden');
        return;
      }}
      const selectedCinemas = new URLSearchParams(window.location.search).get('cinemas');
      const cinemaList = selectedCinemas ? selectedCinemas.split(',') : null;
      const showdays = (card.dataset.showdays || '').split(',').filter(Boolean);
      const matches = showdays.some(dk => {{
        const allAttrs = Array.from(card.attributes).filter(a => a.name.startsWith('data-t-') && a.name.endsWith('_'+dk));
        const relevantAttrs = cinemaList ? allAttrs.filter(a => cinemaList.some(c => a.name.includes('data-t-'+c+'_'))) : allAttrs;
        const times = relevantAttrs.flatMap(a => a.value.split('|').filter(Boolean));
        return times.some(t => {{
          const h = parseInt(t.split(':')[0]);
          if (qfTime === 'morning')   return h < 12;
          if (qfTime === 'afternoon') return h >= 12 && h < 18;
          if (qfTime === 'evening')   return h >= 18;
          return true;
        }});
      }});
      if (matches) card.classList.remove('qf-hidden');
      else card.classList.add('qf-hidden');
    }});
    applyVisibility();
  }}
}})();

function attachCardClicks() {{
  document.querySelectorAll('.grid-card, .list-card, .featured-card').forEach(card => {{
    if (card._clickAttached) return;
    card._clickAttached = true;
    card.addEventListener('click', function(e) {{
      if (e.target.closest('a')) return;
      const titleLink = card.querySelector('a.grid-title, a.list-title, a.film-title');
      if (titleLink) window.location.href = titleLink.href;
    }});
  }});
}}
</script>
<script>
if ('serviceWorker' in navigator) {{
  window.addEventListener('load', () => {{
    navigator.serviceWorker.register('/sw.js')
      .catch(err => console.log('SW registration failed:', err));
  }});
}}
</script>
</body>
</html>"""


# ── Main pipeline ─────────────────────────────────────────────────────────────

def run() -> None:
    anchor = datetime.now(VALENCIA_TZ).replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=None)
    log.info(f"Pipeline starting for {anchor.date()} …")

    # 1. Scrape all cinemas
    log.info("Running scrapers …")
    films = aggregate_scrapers()
    log.info(f"Unique films before enrichment: {len(films)}")

    if len(films) < 10:
        log.error(f"Only {len(films)} films found — possible scraper failure. Aborting to preserve cached listings.")
        return

    # 2. TMDB enrichment
    enrich_with_tmdb(films)

    # 3. Deduplicate by TMDB ID
    deduplicate_by_tmdb_id(films)
    log.info(f"Films after deduplication: {len(films)}")

    # 4. Remove films with no showtimes in the next 7 days
    today_local = datetime.now(VALENCIA_TZ).date()
    today_str   = today_local.strftime("%Y-%m-%d")
    week_ahead  = (today_local + timedelta(days=6)).strftime("%Y-%m-%d")
    stale = [
        title for title, film in films.items()
        if not any(
            any(today_str <= dk <= week_ahead for dk in c.get("showtimes", {}).keys())
            for c in film.get("cinemas", [])
        )
    ]
    for title in stale:
        log.info(f"  Removing stale: '{title}'")
        del films[title]
    log.info(f"Films after stale removal: {len(films)}")

    # 5. Assign slugs
    for title, film in films.items():
        film["slug"] = slugify(film.get("title_en", title) or title)

    # 6. Build listings page
    full_html = build_html(films, anchor)
    os.makedirs("docs/listings", exist_ok=True)
    with open("docs/listings/index.html", "w", encoding="utf-8") as fh:
        fh.write(full_html)
    log.info("Wrote docs/listings/index.html")

    # 7. Write stats.json + films cache
    os.makedirs("docs/data", exist_ok=True)
    stats = {"film_count": len(films), "updated": anchor.strftime("%Y-%m-%d")}
    with open("docs/data/stats.json", "w", encoding="utf-8") as fh:
        json.dump(stats, fh)
    log.info(f"Wrote docs/data/stats.json: {stats}")
    with open("docs/data/films_cache.json", "w", encoding="utf-8") as fh:
        json.dump(films, fh, ensure_ascii=False, default=str)
    log.info(f"Wrote docs/data/films_cache.json ({len(films)} films)")

    # 8. Clean up stale film detail dirs
    current_slugs = {film["slug"] for film in films.values() if film.get("slug")}
    for entry in os.scandir("docs/listings"):
        if entry.is_dir() and entry.name not in current_slugs:
            shutil.rmtree(entry.path)
            log.info(f"  Deleted stale detail dir: {entry.name}")

    # 9. Generate film detail pages
    generated = 0
    for title, film in films.items():
        slug = film.get("slug")
        if slug:
            film_dir = f"docs/listings/{slug}"
            os.makedirs(film_dir, exist_ok=True)
            detail_html = build_film_detail_page(film, anchor)
            with open(f"{film_dir}/index.html", "w", encoding="utf-8") as fh:
                fh.write(detail_html)
            generated += 1
    log.info(f"Generated {generated} film detail pages")

    # 10. Inject Supabase credentials into all pages that need them
    if SUPABASE_URL and SUPABASE_ANON:
        pages_to_inject = [
            "docs/index.html",
            "docs/preferences/index.html",
            "docs/listings/index.html",
        ]
        for page_path in pages_to_inject:
            if os.path.exists(page_path):
                with open(page_path, "r", encoding="utf-8") as fh:
                    page = fh.read()
                # listings page uses __SUPABASE_URL__ placeholders (pipeline-generated)
                page = page.replace("__SUPABASE_URL__",      SUPABASE_URL)
                page = page.replace("__SUPABASE_ANON__",     SUPABASE_ANON)
                # landing + preferences pages use YOUR_SUPABASE_* placeholders (static HTML)
                page = page.replace("YOUR_SUPABASE_URL",      SUPABASE_URL)
                page = page.replace("YOUR_SUPABASE_ANON_KEY", SUPABASE_ANON)
                with open(page_path, "w", encoding="utf-8") as fh:
                    fh.write(page)
                log.info(f"Supabase credentials injected into {page_path}")
    else:
        log.warning("SUPABASE_URL or SUPABASE_ANON not set — skipping credential injection")

    log.info(f"Pipeline complete. {len(films)} films, {generated} detail pages.")


if __name__ == "__main__":
    run()
