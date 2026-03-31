"""
Valencia Cinema Weekly Newsletter
Scrapes current listings from Mabuse.es and builds + sends a bilingual HTML email.
Runs every Thursday evening via GitHub Actions.
"""

import os
import re
import smtplib
import logging
from datetime import datetime, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

# ─── Configuration ────────────────────────────────────────────────────────────
# All sensitive values come from environment variables (GitHub Secrets).

SMTP_HOST     = os.environ["SMTP_HOST"]       # e.g. mail.yourdomain.com
SMTP_PORT     = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER     = os.environ["SMTP_USER"]       # e.g. cartelera@yourdomain.com
SMTP_PASSWORD = os.environ["SMTP_PASSWORD"]
FROM_ADDRESS  = os.environ.get("FROM_ADDRESS", SMTP_USER)
FROM_NAME     = os.environ.get("FROM_NAME", "Cartelera Valencia")

# Comma-separated list of recipient emails stored as a single secret
RECIPIENTS = [r.strip() for r in os.environ["RECIPIENTS"].split(",") if r.strip()]

# TMDB API key for English titles and synopses
TMDB_API_KEY  = os.environ.get("TMDB_API_KEY", "")

# Supabase credentials — injected into generated HTML pages
SUPABASE_URL  = os.environ.get("SUPABASE_URL", "")
SUPABASE_ANON = os.environ.get("SUPABASE_ANON", "")
TMDB_BASE    = "https://api.themoviedb.org/3"

# ─── Cinema definitions ───────────────────────────────────────────────────────

CINEMAS = {
    "babel":      {"name": "Cines Babel",       "url": "https://mabuse.es/cine/cines-babel/",        "website": "https://www.cinesalbatrosbabel.com", "type": "arthouse"},
    "yelmo":      {"name": "Yelmo Campanar",     "url": "https://mabuse.es/cine/cine-yelmo-mercado-de-campanar/", "website": "https://www.yelmocines.es", "type": "multiplex"},
    "kinepolis":  {"name": "Kinépolis",          "url": "https://mabuse.es/cine/kinepolis-valencia/", "website": "https://www.kinepolis.es/valencia", "type": "multiplex"},
    "ocine":      {"name": "Ocine Aqua",         "url": "https://mabuse.es/cine/ocine-aqua/",         "website": "https://www.ocine.es",              "type": "multiplex"},
    "lys":        {"name": "Cines Lys",          "url": "https://mabuse.es/cine/cines-lys/",          "website": "https://cineslys.com",              "type": "multiplex"},
    "abc_saler":  {"name": "ABC El Saler",       "url": "https://mabuse.es/cine/abc-saler/",          "website": "https://cinesabc.com",              "type": "multiplex"},
    "abc_park":   {"name": "ABC Park",           "url": "https://mabuse.es/cine/abc-park/",           "website": "https://cinesabc.com",              "type": "multiplex"},
    "mn4":        {"name": "Cines MN4",          "url": "https://mabuse.es/cine/cines-mn4/",          "website": "https://www.cinesmn4.com",          "type": "multiplex"},
    "dor":        {"name": "Cinestudio D'Or",    "url": "https://mabuse.es/cine/cinestudio-dor/",     "website": "https://cinestudiodor.es",          "type": "arthouse"},
    "gran_turia": {"name": "ABC Gran Turia",     "url": "https://mabuse.es/cine/abc-gran-turia/",     "website": "https://cinesabc.com",              "type": "multiplex"},
}

# Playwright browser instance — shared across all cinema fetches
_playwright = None
_browser    = None
_page       = None

def get_page():
    """Return a shared Playwright page, launching the browser on first call."""
    global _playwright, _browser, _page
    if _page is None:
        log.info("Launching headless Chromium ...")
        _playwright = sync_playwright().start()
        _browser = _playwright.chromium.launch(headless=True)
        context = _browser.new_context(
            locale="es-ES",
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/123.0.0.0 Safari/537.36"
            ),
        )
        _page = context.new_page()
    return _page

def close_browser():
    global _playwright, _browser, _page
    if _browser:
        _browser.close()
    if _playwright:
        _playwright.stop()
    _page = _browser = _playwright = None

# ─── Scraping ─────────────────────────────────────────────────────────────────

def fetch_cinema(cinema_id: str) -> list[dict]:
    """Scrape today's listings for a single cinema. Returns list of film dicts."""
    cinema = CINEMAS[cinema_id]
    log.info(f"Fetching {cinema['name']} ...")

    import time, random
    # Polite random delay between requests (1-3 seconds)
    time.sleep(random.uniform(1, 3))

    try:
        page = get_page()
        page.goto(cinema["url"], wait_until="networkidle", timeout=30000)
        # Give JS a moment to render
        page.wait_for_timeout(3000)
        html = page.content()
        log.info(f"  Page loaded — {len(html)} bytes")

        # mabuse.es puts ALL dates in the page HTML as divs with class "listafechas fecha_YYYYMMDD"
        # No need to interact with the dropdown — just parse them all from the HTML
        all_day_html = {}  # not used anymore — we parse soup directly per date div



    except Exception as e:
        log.warning(f"  Failed to fetch {cinema['name']}: {e}")
        return []

    soup = BeautifulSoup(html, "html.parser")
    films = []

    # Each film block: poster <a><img>, rating <img>, optional ESTRENO, <h3> title,
    # <p> meta (country/year/genre/runtime), <p> director/cast, <p> synopsis.
    # The list appears twice (mobile + desktop) so deduplicate by title.
    seen_titles = set()

    for h3 in soup.find_all("h3"):
        title = h3.get_text(strip=True)
        if not title or title in seen_titles:
            continue
        seen_titles.add(title)

        # Walk up to find the block that also contains a poster image
        container = None
        for parent in h3.parents:
            if parent.find("img", src=re.compile(r"uploads")):
                container = parent
                break
        if not container:
            container = h3.parent

        raw_html = str(container)

        # ── Poster: first img with a real uploads URL (skip SVG placeholders)
        poster_url = ""
        for img in container.find_all("img"):
            src = img.get("src", "")
            if "uploads" in src and not src.startswith("data:"):
                poster_url = src
                break

        # ── Rating: first calificacion img with a real URL
        rating = "?"
        for img in container.find_all("img"):
            src = img.get("src", "")
            if "calificacion" in src and not src.startswith("data:"):
                if "ai.png"  in src: rating = "TP"
                elif "18.png" in src: rating = "18"
                elif "16.png" in src: rating = "16"
                elif "12.png" in src: rating = "12"
                elif "7.png"  in src: rating = "7"
                break

        # ── Meta and synopsis: <p> tags immediately after the h3
        paragraphs = []
        found_h3 = False
        for tag in container.find_all(["h3", "p"]):
            if tag is h3:
                found_h3 = True
                continue
            if found_h3 and tag.name == "p":
                text = tag.get_text(" ", strip=True)
                if text and len(text) > 5:
                    paragraphs.append(text)
            elif found_h3 and tag.name == "h3":
                break  # reached the next film

        meta_text     = paragraphs[0] if len(paragraphs) > 0 else ""
        synopsis_text = paragraphs[1] if len(paragraphs) > 1 else ""

        # ── VOSE and new release flags
        vose   = bool(re.search(r"VOSE|INGL[ÉE]S SUBTITULADO|English.*es\b|nosubt.*English", raw_html, re.IGNORECASE))
        is_new = bool(re.search(r"ESTRENO", raw_html, re.IGNORECASE))

        # ── Showtimes: all dates are in page HTML as div.listafechas.fecha_YYYYMMDD
        # Each date div contains the film list for that day — find our film and extract times
        showtimes = {}
        try:
            # Find all date divs — class is like "listafechas fecha_20260331"
            date_divs = soup.find_all("div", class_=re.compile(r"fecha_(\d{8})"))
            log.debug(f"  Found {len(date_divs)} date divs")

            for date_div in date_divs:
                # Extract date from class name: fecha_20260331 -> 2026-03-31
                classes    = date_div.get("class", [])
                date_class = next((c for c in classes if re.match(r"fecha_\d{8}", c)), None)
                if not date_class:
                    continue
                raw_date = date_class.replace("fecha_", "")  # "20260331"
                date_key = f"{raw_date[:4]}-{raw_date[4:6]}-{raw_date[6:8]}"  # "2026-03-31"

                # Find our film's title within this date div
                for h3 in date_div.find_all("h3"):
                    if h3.get_text(strip=True) != title:
                        continue
                    # Found our film — find the sesiones ul nearby
                    for parent in h3.parents:
                        ul = parent.find("ul", class_=re.compile(r"ficha_sesiones|sesiones", re.I))
                        if not ul:
                            for u in parent.find_all("ul"):
                                if u.find("a", attrs={"data-fecha": True}):
                                    ul = u
                                    break
                        if ul:
                            for a in ul.find_all("a", attrs={"data-fecha": True}):
                                hora = a.get("data-hora", "")
                                t    = hora[:5] if hora else ""
                                if not t:
                                    raw = a.get_text(strip=True)
                                    m   = re.search(r"([01]?[0-9]|2[0-3]):[0-5][0-9]", raw)
                                    t   = m.group(0) if m else ""
                                if t:
                                    showtimes.setdefault(date_key, [])
                                    if t not in showtimes[date_key]:
                                        showtimes[date_key].append(t)
                            break
                        if date_key in showtimes:
                            break

            # Sort times within each day
            for dk in showtimes:
                showtimes[dk] = sorted(showtimes[dk])

            if showtimes:
                log.info(f"  Showtimes {title}: { {k: len(v) for k, v in showtimes.items()} } days")

        except Exception as _e:
            log.debug(f"  Showtime extraction failed for {title}: {_e}")
            showtimes = {}

        films.append({
            "title":     title,
            "meta":      meta_text,
            "synopsis":  synopsis_text,
            "vose":      vose,
            "is_new":    is_new,
            "rating":    rating,
            "poster":    poster_url,
            "cinema_id": cinema_id,
            "showtimes": showtimes,
        })
    log.info(f"  Found {len(films)} films at {cinema['name']}")
    return films


def warm_up_session() -> None:
    """Visit mabuse.es homepage first to warm up cookies and JS state."""
    import time
    log.info("Warming up session on mabuse.es ...")
    try:
        page = get_page()
        page.goto("https://mabuse.es/", wait_until="networkidle", timeout=30000)
        time.sleep(2)
        log.info("  Session warmed up.")
    except Exception as e:
        log.warning(f"  Warm-up failed (non-fatal): {e}")



def tmdb_lookup(title: str) -> dict:
    """
    Search TMDB for a film by its Spanish title.
    Returns dict with: title_en, title_original, synopsis_en, poster_url, year
    Returns empty dict if not found or API key missing.
    """
    import requests as req
    import time

    if not TMDB_API_KEY:
        return {}

    time.sleep(0.25)  # polite rate limiting

    try:
        headers = {
            "Authorization": f"Bearer {TMDB_API_KEY}",
            "accept": "application/json"
        }

        # Search in Spanish first to match the scraped title
        search_url = (
            f"{TMDB_BASE}/search/movie"
            f"?query={req.utils.quote(title)}"
            f"&language=es-ES"
            f"&region=ES"
        )
        res = req.get(search_url, headers=headers, timeout=10)
        res.raise_for_status()
        results = res.json().get("results", [])

        if not results:
            # Try English search as fallback
            search_url_en = (
                f"{TMDB_BASE}/search/movie"
                f"?query={req.utils.quote(title)}"
                f"&language=en-US"
            )
            res = req.get(search_url_en, headers=headers, timeout=10)
            res.raise_for_status()
            results = res.json().get("results", [])

        if not results:
            log.info(f"  TMDB: no results for '{title}'")
            return {}

        movie = results[0]
        movie_id = movie["id"]

        # Fetch full details in English
        detail_url = f"{TMDB_BASE}/movie/{movie_id}?language=en-US"
        detail_res = req.get(detail_url, headers=headers, timeout=10)
        detail_res.raise_for_status()
        detail = detail_res.json()

        # Fetch Spanish details for synopsis
        detail_es_res = req.get(f"{TMDB_BASE}/movie/{movie_id}?language=es-ES", headers=headers, timeout=10)
        detail_es_res.raise_for_status()
        detail_es = detail_es_res.json()

        # Use Spanish overview if available, fall back to English
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
            "tmdb_id":        movie_id,
            "rating_score":   round(vote, 1) if vote else None,
        }

    except Exception as e:
        log.warning(f"  TMDB lookup failed for '{title}': {e}")
        return {}

def fetch_all() -> dict:
    """
    Returns a dict keyed by film title, each value containing film info
    plus a list of cinemas showing it and whether any offer VOSE.
    """
    by_film: dict[str, dict] = {}

    for cinema_id in CINEMAS:
        for film in fetch_cinema(cinema_id):
            title = film["title"]
            if title not in by_film:
                by_film[title] = {
                    "title":    title,
                    "meta":     film["meta"],
                    "synopsis": film.get("synopsis", ""),
                    "is_new":   film["is_new"],
                    "rating":   film["rating"],
                    "poster":   film["poster"],
                    "cinemas":  [],
                    "any_vose": False,
                }
            cinema_info = CINEMAS[cinema_id]
            by_film[title]["cinemas"].append({
                "id":        cinema_id,
                "name":      cinema_info["name"],
                "website":   cinema_info["website"],
                "type":      cinema_info["type"],
                "vose":      film["vose"],
                "showtimes": film.get("showtimes", {}),
            })
            if film["vose"]:
                by_film[title]["any_vose"] = True
            if film.get("synopsis") and not by_film[title].get("synopsis"):
                by_film[title]["synopsis"] = film["synopsis"]
            # Prefer is_new=True and a poster if we have one
            if film["is_new"]:
                by_film[title]["is_new"] = True
            if film["poster"] and not by_film[title]["poster"]:
                by_film[title]["poster"] = film["poster"]

    return by_film


# ─── Date helpers ─────────────────────────────────────────────────────────────

def week_range_es(anchor: datetime) -> str:
    """Returns e.g. '17 – 23 de Abril 2026'"""
    MONTHS_ES = ["enero","febrero","marzo","abril","mayo","junio",
                 "julio","agosto","septiembre","octubre","noviembre","diciembre"]
    end = anchor + timedelta(days=6)
    if anchor.month == end.month:
        return f"{anchor.day} – {end.day} de {MONTHS_ES[anchor.month-1]} {anchor.year}"
    return f"{anchor.day} de {MONTHS_ES[anchor.month-1]} – {end.day} de {MONTHS_ES[end.month-1]} {anchor.year}"

def week_range_en(anchor: datetime) -> str:
    """Returns e.g. '17 – 23 April 2026'"""
    end = anchor + timedelta(days=6)
    if anchor.month == end.month:
        return f"{anchor.day} – {end.day} {anchor.strftime('%B')} {anchor.year}"
    return f"{anchor.day} {anchor.strftime('%B')} – {end.day} {end.strftime('%B')} {anchor.year}"


# ─── Film detail page builder ────────────────────────────────────────────────

def slugify(title: str) -> str:
    """Convert a film title to a URL-safe slug."""
    import unicodedata
    s = unicodedata.normalize("NFKD", title.lower())
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s or "film"


def build_film_detail_page(film: dict, anchor: datetime) -> str:
    """Build a standalone HTML page for a single film with showtimes by day."""
    from datetime import date as _date, timedelta as _td

    title_es   = film["title"]
    title_en   = film.get("title_en", title_es)
    title_orig = film.get("title_original", title_es)
    syn_es     = (film.get("synopsis_es") or film.get("synopsis", ""))[:400]
    syn_en     = (film.get("synopsis_en") or film.get("synopsis", ""))[:400]
    poster     = film.get("poster", "")
    meta       = film.get("meta", "")
    score      = film.get("rating_score")
    vose       = film.get("any_vose", False)
    is_new     = film.get("is_new", False)

    # Build day tabs for today + 6 days
    today = _date.today()
    DAYS_EN = ["Monday","Tuesday","Wednesday","Thursday","Friday","Saturday","Sunday"]
    DAYS_ES = ["Lunes","Martes","Miércoles","Jueves","Viernes","Sábado","Domingo"]

    days = []
    for i in range(7):
        d = today + _td(days=i)
        days.append({
            "key":    d.strftime("%Y-%m-%d"),
            "label_en": ("Today" if i==0 else "Tomorrow" if i==1 else DAYS_EN[d.weekday()]) + f" {d.day}",
            "label_es": ("Hoy" if i==0 else "Mañana" if i==1 else DAYS_ES[d.weekday()]) + f" {d.day}",
        })

    # Build showtime grid HTML
    def showtime_tabs():
        tab_btns  = ""
        tab_panels = ""
        for i, day in enumerate(days):
            active = "active" if i == 0 else ""
            dk = day["key"]; les = day["label_es"]; len_ = day["label_en"]
            tab_btns += f'<button class="day-tab {active}" data-day="{dk}" data-es="{les}" data-en="{len_}" onclick="showDay(\'{dk}\')">{les}</button>'

            # Cinema rows — each gets data-cinema-id for JS filtering
            cinema_rows = ""
            for c in film["cinemas"]:
                times = c.get("showtimes", {}).get(day["key"], [])
                if not times:
                    continue
                vose_label = '<span class="vose-mini">VOSE</span>' if c["vose"] else ""
                time_btns  = "".join(
                    f'<a href="{c["website"]}" target="_blank" class="time-btn">{t}</a>'
                    for t in times
                )
                cinema_rows += f'<div class="showtime-row" data-cinema-id="{c["id"]}"><div class="showtime-cinema"><span translate="no">{c["name"]}</span>{vose_label}</div><div class="showtime-times">{time_btns}</div></div>'

            if not cinema_rows:
                cinema_rows = f'<div class="no-times" data-es="Sin sesiones este día" data-en="No screenings this day">Sin sesiones este día</div>'

            panel_active = "active" if i == 0 else ""
            tab_panels += f'<div class="day-panel {panel_active}" id="day-{day["key"]}">{cinema_rows}</div>'

        return tab_btns, tab_panels

    tab_btns, tab_panels = showtime_tabs()

    new_badge  = '<span class="film-badge badge-new" data-es="ESTRENO" data-en="NEW RELEASE">ESTRENO</span>' if is_new else ""
    vose_badge = '<span class="vose-badge">VOSE</span>' if vose else ""
    score_badge = f'<span class="score-badge">⭐ {score}</span>' if score else ""
    poster_html = f'<img src="{poster}" alt="{esc(title_es)}" style="width:100%;height:100%;object-fit:cover;display:block;">' if poster else '<div style="font-size:64px;text-align:center;padding:40px;">🎬</div>'
    orig_label = f'<div class="orig-title" translate="no">{title_orig}</div>' if title_orig and title_orig != title_es and title_orig != title_en else ""

    return f"""<!DOCTYPE html>
<html lang="es" id="html-root">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
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
.hero-title{{font-family:'Playfair Display',Georgia,serif;font-size:22px;font-weight:700;color:#f0eae0;line-height:1.2;margin-bottom:4px}}
.orig-title{{font-size:11px;color:#5a4e6a;margin-bottom:6px}}
.hero-meta{{font-size:11px;color:#7a6d8a;line-height:1.55;margin-bottom:8px}}
.hero-synopsis{{font-size:12px;color:#9d909e;line-height:1.6}}
.section-title{{font-size:10px;letter-spacing:3px;text-transform:uppercase;color:#4a3f5e;padding:20px 20px 10px}}
.day-tabs{{display:flex;gap:0;padding:0 20px 14px;overflow-x:auto;scrollbar-width:none}}
.day-tabs::-webkit-scrollbar{{display:none}}
.day-tab{{padding:7px 14px;border-radius:20px;font-size:11px;font-weight:500;letter-spacing:0.5px;cursor:pointer;border:1px solid #2e2545;background:transparent;color:#6a5e7a;font-family:'DM Sans',sans-serif;white-space:nowrap;transition:all .2s;margin-right:6px;flex-shrink:0}}
.day-tab.active{{background:rgba(255,180,50,.15);color:#ffb432;border-color:rgba(255,180,50,.4)}}
.day-panel{{display:none;padding:0 20px 20px}}
.day-panel.active{{display:block}}
.showtime-row{{padding:14px 0;border-bottom:1px solid #1e1630}}
.showtime-row:last-child{{border-bottom:none}}
.showtime-cinema{{font-size:13px;font-weight:500;color:#c5b8d8;margin-bottom:8px;display:flex;align-items:center;gap:6px}}
.vose-mini{{font-size:9px;font-weight:700;letter-spacing:1px;padding:1px 5px;background:rgba(255,220,80,.12);color:#ffd84a;border:1px solid rgba(255,220,80,.35);border-radius:3px}}
.showtime-times{{display:flex;flex-wrap:wrap;gap:8px}}
.time-btn{{padding:6px 14px;background:#1a1228;border:1px solid #2e2040;border-radius:6px;font-size:13px;color:#f0eae0;text-decoration:none;transition:all .2s;font-weight:500}}
.time-btn:hover{{background:#2a1f3d;border-color:#ffb432;color:#ffb432}}
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
      <div class="badges">{new_badge}{vose_badge}{score_badge}</div>
      <div class="hero-title" data-es="{esc(title_es)}" data-en="{esc(title_en)}">{title_es}</div>
      {orig_label}
      <div class="hero-meta">{meta}</div>
      <div class="hero-synopsis" data-es="{esc(syn_es)}" data-en="{esc(syn_en)}">{syn_es}</div>
    </div>
  </div>

  <div class="section-title" data-es="🕖 HORARIOS" data-en="🕖 SHOWTIMES">🕖 HORARIOS</div>
  <div class="day-tabs">{tab_btns}</div>

  <div id="day-panels">{tab_panels}</div>

  <div class="footer">
    <span data-es="Horarios sujetos a cambios — verifica siempre en la web del cine." data-en="Showtimes subject to change — always verify on the cinema's website.">Horarios sujetos a cambios — verifica siempre en la web del cine.</span>
  </div>
</div>
<script>
function setLang(lang) {{
  document.getElementById('btn-es').classList.toggle('active', lang === 'es');
  document.getElementById('btn-en').classList.toggle('active', lang === 'en');
  document.getElementById('html-root').setAttribute('lang', lang);
  document.querySelectorAll('[data-es][data-en]').forEach(el => {{
    el.textContent = el.getAttribute('data-' + lang);
  }});
  document.title = (lang === 'en' ? '{esc(title_en)}' : '{esc(title_es)}') + ' — Cartelera Valencia';
  localStorage.setItem('cv_lang', lang);
}}
function showDay(key) {{
  document.querySelectorAll('.day-tab').forEach(t => t.classList.remove('active'));
  document.querySelectorAll('.day-panel').forEach(p => p.classList.remove('active'));
  const tab = document.querySelector(`.day-tab[data-day="${{key}}"]`);
  const panel = document.getElementById('day-' + key);
  if (tab) tab.classList.add('active');
  if (panel) panel.classList.add('active');
}}
window.addEventListener('DOMContentLoaded', () => {{
  const lang = localStorage.getItem('cv_lang') || 'es';
  if (lang !== 'es') setLang(lang);

  // Apply cinema filter from URL params (set by preferences)
  const params  = new URLSearchParams(window.location.search);
  const cinemas = params.get('cinemas');
  if (cinemas) {{
    const allowed = cinemas.split(',');
    // Hide showtime rows not in preferences
    document.querySelectorAll('.showtime-row[data-cinema-id]').forEach(row => {{
      const cid = row.getAttribute('data-cinema-id');
      if (!allowed.includes(cid)) {{
        row.style.display = 'none';
      }}
    }});
  }}
}});
</script>
</body>
</html>"""


# ─── HTML builder ─────────────────────────────────────────────────────────────

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
.header-title{font-family:'Playfair Display',Georgia,serif;font-size:42px;font-weight:700;color:#f9f3e8;line-height:1.1;margin-bottom:10px}
.header-subtitle{font-size:14px;color:#9b8faa;font-weight:300}
.header-date{display:inline-block;margin-top:18px;padding:6px 18px;background:rgba(255,180,50,.12);border:1px solid rgba(255,180,50,.3);border-radius:20px;font-size:12px;color:#ffb432;letter-spacing:1px}
.section-label{padding:28px 40px 12px;font-size:10px;letter-spacing:3px;text-transform:uppercase;color:#5a4e6a;font-weight:500}
.section-divider{height:1px;background:linear-gradient(90deg,transparent,#2e2040 30%,#2e2040 70%,transparent);margin:8px 24px 20px}
.cinema-group-header{margin:0 24px 14px;padding:12px 16px;background:#160f24;border:1px solid #2a1f3d;border-radius:10px;display:flex;align-items:center;gap:10px}
.cinema-group-name{font-family:'Playfair Display',Georgia,serif;font-size:15px;font-weight:700;color:#c5b8d8}
.cinema-group-desc{font-size:11px;color:#6a5e7a}
.cinema-group-link{margin-left:auto;font-size:11px;color:#7a6a9a;text-decoration:none;white-space:nowrap}
.list-card{margin:0 24px 10px;padding:14px 16px;background:#1a1228;border:1px solid #2e2040;border-radius:12px;display:flex;gap:14px;align-items:flex-start}
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
.cinema-links-label{font-size:10px;letter-spacing:1px;text-transform:uppercase;color:#4a4060;font-weight:500;margin-bottom:5px}
.cinema-tags{display:flex;flex-wrap:wrap;gap:5px;margin-top:4px}
.cinema-tag{display:inline-block;padding:3px 9px;border-radius:4px;font-size:11px;color:#9a8fb0;background:rgba(255,255,255,.04);border:1px solid #2e2545;text-decoration:none;line-height:1.4}
.vose-mini{display:inline-block;margin-left:4px;font-size:9px;font-weight:700;letter-spacing:1px;color:#ffd84a;vertical-align:middle}
.rating{display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:3px;vertical-align:middle}
.rating-TP{background:#50c88c}.rating-12{background:#7aa0e0}.rating-16{background:#e08040}.rating-18{background:#e05050}.rating-7{background:#80cc80}
.featured-card{margin:0 24px 16px;border-radius:16px;overflow:hidden;background:#1a1228;border:1px solid #2e2040;display:flex;min-height:200px}
.featured-poster{width:120px;flex-shrink:0;background:#2a1f3d;overflow:hidden;display:flex;align-items:center;justify-content:center}
.featured-info{padding:18px 20px 16px;flex:1;display:flex;flex-direction:column;justify-content:space-between}
.film-title{font-family:'Playfair Display',Georgia,serif;font-size:21px;font-weight:700;color:#f0eae0;line-height:1.2;margin-bottom:7px;text-decoration:none;display:block}.film-title:hover{color:#ffb432}
.film-meta{font-size:12px;color:#7a6d8a;margin-bottom:8px;line-height:1.55}
.film-synopsis{font-size:13px;color:#9d909e;line-height:1.55;margin-bottom:11px}
.grid-row{display:flex;gap:14px;margin:0 24px 14px}
.grid-card{flex:1;background:#1a1228;border:1px solid #2e2040;border-radius:14px;overflow:hidden}
.grid-poster{width:100%;height:85px;background:#2a1f3d;overflow:hidden;display:flex;align-items:center;justify-content:center;font-size:34px}
.grid-info{padding:12px 14px 14px}
.grid-title{font-family:'Playfair Display',Georgia,serif;font-size:15px;font-weight:700;color:#f0eae0;line-height:1.2;margin-bottom:4px;text-decoration:none;display:block}.grid-title:hover{color:#ffb432}
.grid-meta{font-size:11px;color:#7a6d8a;margin-bottom:6px;line-height:1.5}
.grid-synopsis{font-size:11.5px;color:#8c8090;line-height:1.5;margin-bottom:8px}
.footer{background:#0a0810;border-top:1px solid #1e1630;padding:28px 40px;text-align:center}
.footer p{font-size:12px;color:#4a3f5e;line-height:1.7}
.footer a{color:#7a6a9a;text-decoration:none}
.footer-logo{font-family:'Playfair Display',Georgia,serif;font-size:18px;color:#3a2e50;margin-bottom:10px}
@media(max-width:480px){.lang-bar{padding:8px 12px}.lang-btn{padding:4px 10px;font-size:10px}}
"""

JS = """
function setLang(lang) {
  document.getElementById('btn-es').classList.toggle('active', lang === 'es');
  document.getElementById('btn-en').classList.toggle('active', lang === 'en');
  document.getElementById('html-root').setAttribute('lang', lang);
  document.querySelectorAll('[data-es][data-en]').forEach(el => {
    el.textContent = el.getAttribute('data-' + lang);
  });
  localStorage.setItem('cv_lang', lang);
}

function getCookie(name) {
  const match = document.cookie.match(new RegExp('(^| )' + name + '=([^;]+)'));
  return match ? decodeURIComponent(match[2]) : null;
}

function applyPreferencesFromURL() {
  const params  = new URLSearchParams(window.location.search);
  const cinemas = params.get('cinemas') ? params.get('cinemas').split(',') : null;

  // Hide cinema tags for excluded cinemas
  if (cinemas) {
    document.querySelectorAll('.cinema-tag').forEach(tag => {
      const cid = tag.dataset.cinema;
      if (cid && !cinemas.includes(cid)) {
        tag.style.display = 'none';
      }
    });
  }
}

async function loadUserPreferences() {
  // Check URL params first (pre-filtered link)
  const params = new URLSearchParams(window.location.search);
  const hasParams = params.has('vose') || params.has('cinemas') || params.has('new');

  // Apply language from localStorage or cookie
  const savedLang = localStorage.getItem('cv_lang');
  if (savedLang) setLang(savedLang);

  // If we have URL params, apply cinema tag visibility
  if (hasParams) {
    applyPreferencesFromURL();
    return;
  }

  // Otherwise try to load from Supabase via cookie
  const email = getCookie('cv_email');
  if (!email || !window.SUPABASE_URL || !window.SUPABASE_ANON) return;

  try {
    const res = await fetch(
      window.SUPABASE_URL + '/rest/v1/subscribers?email=eq.' + encodeURIComponent(email) + '&select=lang,cinemas,vose_only,new_only',
      { headers: { 'apikey': window.SUPABASE_ANON, 'Authorization': 'Bearer ' + window.SUPABASE_ANON } }
    );
    const rows = await res.json();
    if (!rows.length) return;

    const prefs = rows[0];

    // Apply language
    if (prefs.lang) setLang(prefs.lang);

    // Build URL params from preferences and reload if needed
    const newParams = new URLSearchParams();
    if (prefs.vose_only) newParams.set('vose', 'true');
    if (prefs.new_only)  newParams.set('new',  'true');
    const allCinemas = ['kinepolis','yelmo','ocine','lys','abc_saler','abc_park','gran_turia','mn4','babel','dor'];
    if (prefs.cinemas && prefs.cinemas.length < allCinemas.length) {
      newParams.set('cinemas', prefs.cinemas.join(','));
    }

    if (newParams.toString()) {
      window.history.replaceState({}, '', '?' + newParams.toString());
      applyVisibility();
      applyPreferencesFromURL();
    }

  } catch(e) {
    console.warn('Could not load preferences:', e);
  }
}

function setFilter(filter) {
  const url = new URL(window.location);
  if (filter === 'all') url.searchParams.delete('filter');
  else url.searchParams.set('filter', filter);
  window.history.replaceState({}, '', url);
  applyVisibility();
}

function applyVisibility() {
  const params   = new URLSearchParams(window.location.search);
  const filter   = params.get('filter')  || 'all';
  const voseOnly = params.get('vose')    === 'true';
  const newOnly  = params.get('new')     === 'true';
  const cinemas  = params.get('cinemas') ? params.get('cinemas').split(',') : null;

  // Sync filter buttons
  const allBtn  = document.getElementById('filter-all');
  const voseBtn = document.getElementById('filter-vose');
  if (allBtn)  allBtn.classList.toggle('active',  !voseOnly && filter === 'all');
  if (voseBtn) voseBtn.classList.toggle('active', voseOnly  || filter === 'vose');

  let visible = 0;
  document.querySelectorAll('[data-vose]').forEach(card => {
    let show = true;

    // VOSE filter
    if (voseOnly || filter === 'vose') {
      if (card.dataset.vose !== 'true') show = false;
    }

    // New releases filter
    if (newOnly && card.dataset.isnew !== 'true') show = false;

    // Cinema filter
    if (cinemas && cinemas.length > 0) {
      const cardCinemas = (card.dataset.cinemas || '').split(',');
      if (!cardCinemas.some(c => cinemas.includes(c.trim()))) show = false;
    }

    card.style.display = show ? '' : 'none';
    if (show) visible++;
  });

  // Collapse empty grid rows
  document.querySelectorAll('.grid-row').forEach(row => {
    const anyVisible = Array.from(row.children).some(c => c.style.display !== 'none');
    row.style.display = anyVisible ? '' : 'none';
  });

  // Empty state message
  const empty = document.getElementById('filter-empty');
  if (empty) empty.style.display = visible === 0 ? 'block' : 'none';
}

"""


def esc(s: str) -> str:
    """Escape a string for safe use inside an HTML attribute value."""
    return (s or "").replace("&", "&amp;").replace('"', "&quot;").replace("'", "&#39;").replace("<", "&lt;").replace(">", "&gt;")


def t(el_type: str, es: str, en: str, cls: str = "") -> str:
    """Render a bilingual element."""
    c = f' class="{cls}"' if cls else ""
    return f'<{el_type}{c} data-es="{es}" data-en="{en}">{es}</{el_type}>'


def film_card_html(film: dict) -> str:
    """Build a list-card for one film."""
    title    = film["title"]
    rating   = film["rating"]
    vose     = film["any_vose"]
    poster   = film["poster"]
    cinemas  = film["cinemas"]
    is_new   = film["is_new"]
    synopsis = film.get("synopsis", "")
    meta     = film.get("meta", "")

    poster_html = (
        f'<img src="{poster}" alt="{title}" onerror="this.style.display=\'none\'">'
        if poster else "🎬"
    )

    new_badge = (
        f'<span class="film-badge badge-new" data-es="ESTRENO" data-en="NEW RELEASE">ESTRENO</span>'
        if is_new else ""
    )
    vose_badge  = '<span class="vose-badge">VOSE</span>' if vose else ""
    score       = film.get("rating_score")
    score_badge = f'<span class="score-badge">⭐ {score}</span>' if score else ""
    rating_dot = f'<span class="rating rating-{rating}"></span>+{rating} &nbsp;·&nbsp; ' if rating != "TP" else '<span class="rating rating-TP"></span>'

    cinema_tags = ""
    for c in cinemas:
        vm = '<span class="vose-mini">VOSE</span>' if c["vose"] else ""
        cinema_tags += f'<a href="{c["website"]}" class="cinema-tag" data-cinema="{c["id"]}">{c["name"]}{vm}</a>\n'

    where_es = "Dónde verla"
    where_en = "Where to see it"

    cinema_ids = ",".join(c["id"] for c in cinemas)
    title_es = title
    title_en = film.get("title_en", title)
    syn_es   = (film.get("synopsis_es") or synopsis)[:200]
    syn_en   = (film.get("synopsis_en") or synopsis)[:200]

    return f"""
  <div class="list-card" data-vose="{"true" if vose else "false"}" data-isnew="{"true" if is_new else "false"}" data-cinemas="{cinema_ids}">
    <div class="list-poster">{poster_html}</div>
    <div class="list-body">
      <div class="badges">{new_badge}{vose_badge}{score_badge}</div>
      <a href="./{film.get('slug', slugify(film.get('title_en', title_es)))}/"
         class="list-title" data-es="{esc(title_es)}" data-en="{esc(title_en)}">{title_es}</a>
      <div class="list-meta">{rating_dot}{meta[:120]}</div>
      {f'<div class="list-synopsis" data-es="{esc(syn_es)}" data-en="{esc(syn_en)}">{syn_es}</div>' if synopsis else ""}
      <div class="cinema-links">
        <div class="cinema-links-label" data-es="{where_es}" data-en="{where_en}">{where_es}</div>
        <div class="cinema-tags">{cinema_tags}</div>
      </div>
    </div>
  </div>"""


def build_html(films_by_title: dict, anchor: datetime) -> str:
    date_es = week_range_es(anchor)
    date_en = week_range_en(anchor)

    # Split into multiplex vs arthouse
    multiplex_films = []
    arthouse_films  = {}  # keyed by cinema_id

    for title, film in sorted(films_by_title.items(), key=lambda x: (-x[1]["is_new"], x[0])):
        cinema_types = {c["type"] for c in film["cinemas"]}
        cinema_ids   = {c["id"]   for c in film["cinemas"]}

        if "multiplex" in cinema_types:
            multiplex_films.append(film)

        for cid in ["babel", "dor"]:
            if cid in cinema_ids:
                arthouse_films.setdefault(cid, []).append(film)

    # ── Multiplex: featured card for top new release, grid pairs for the rest
    def featured_card_html(film):
        poster    = film["poster"]
        synopsis  = film.get("synopsis", "")
        meta      = film.get("meta", "")
        vose      = film["any_vose"]
        is_new    = film["is_new"]
        cinemas   = film["cinemas"]
        rating    = film["rating"]

        poster_html = (
            f'<img src="{poster}" alt="{film["title"]}" style="width:100%;height:100%;object-fit:cover;display:block;">' 
            if poster else '<div style="font-size:42px;text-align:center;">🎬</div>'
        )
        new_badge   = '<span class="film-badge badge-new" data-es="ESTRENO" data-en="NEW RELEASE">ESTRENO</span>' if is_new else ""
        vose_badge  = '<span class="vose-badge">VOSE</span>' if vose else ""
        score       = film.get("rating_score")
        score_badge = f'<span class="score-badge">⭐ {score}</span>' if score else ""  ""
        rating_dot = f'<span class="rating rating-{rating}"></span>+{rating}&nbsp;·&nbsp;' if rating not in ("?","TP") else ""
        cinema_tags = "".join(
            f'<a href="{c["website"]}" class="cinema-tag" data-cinema="{c["id"]}">{c["name"]}{'<span class="vose-mini">VOSE</span>' if c["vose"] else ""}</a>'
            for c in cinemas
        )
        where_es, where_en = "Dónde verla", "Where to see it"
        cinema_ids = ",".join(c["id"] for c in cinemas)
        title_es  = film["title"]
        title_en  = film.get("title_en", film["title"])
        title_orig= film.get("title_original", film["title"])
        syn_es    = (film.get("synopsis_es") or synopsis)[:220]
        syn_en    = (film.get("synopsis_en") or synopsis)[:220]

        # Show original title if different from Spanish
        orig_label = ""
        if title_orig and title_orig != title_es and title_orig != title_en:
            orig_label = f'<div style="font-size:11px;color:var(--faint);margin-top:2px;" translate="no">{title_orig}</div>'

        return f"""
  <div class="featured-card" data-vose="{"true" if vose else "false"}" data-isnew="{"true" if is_new else "false"}" data-cinemas="{cinema_ids}">
    <div class="featured-poster">{poster_html}</div>
    <div class="featured-info">
      <div>
        <div class="badges">{new_badge}{vose_badge}{score_badge}</div>
        <a href="./{film.get('slug', slugify(film.get('title_en', title_es)))}/"
           class="film-title" data-es="{esc(title_es)}" data-en="{esc(title_en)}">{title_es}</a>
        {orig_label}
        <div class="film-meta">{rating_dot}{meta[:100]}</div>
        <div class="film-synopsis" data-es="{esc(syn_es)}" data-en="{esc(syn_en)}">{syn_es}</div>
      </div>
      <div class="cinema-links">
        <div class="cinema-links-label" data-es="{where_es}" data-en="{where_en}">{where_es}</div>
        <div class="cinema-tags">{cinema_tags}</div>
      </div>
    </div>
  </div>"""

    def grid_card_html(film):
        poster   = film["poster"]
        synopsis = film.get("synopsis", "")
        meta     = film.get("meta", "")
        vose     = film["any_vose"]
        is_new   = film["is_new"]
        cinemas  = film["cinemas"]
        rating   = film["rating"]

        poster_html = (
            f'<img src="{poster}" alt="{film["title"]}" style="width:100%;height:100%;object-fit:cover;display:block;">' 
            if poster else '<div style="font-size:34px;">🎬</div>'
        )
        new_badge   = '<span class="film-badge badge-new" data-es="ESTRENO" data-en="NEW">ESTRENO</span>' if is_new else ""
        vose_badge  = '<span class="vose-badge">VOSE</span>' if vose else ""
        score       = film.get("rating_score")
        score_badge = f'<span class="score-badge">⭐ {score}</span>' if score else ""  ""
        rating_dot = f'<span class="rating rating-{rating}"></span>+{rating}&nbsp;·&nbsp;' if rating not in ("?","TP") else ""
        cinema_tags = "".join(
            f'<a href="{c["website"]}" class="cinema-tag" data-cinema="{c["id"]}">{c["name"]}{'<span class="vose-mini">VOSE</span>' if c["vose"] else ""}</a>'
            for c in cinemas
        )
        where_es, where_en = "Dónde verla", "Where to see it"
        cinema_ids = ",".join(c["id"] for c in cinemas)
        title_es = film["title"]
        title_en = film.get("title_en", film["title"])
        syn_es   = (film.get("synopsis_es") or synopsis)[:140]
        syn_en   = (film.get("synopsis_en") or synopsis)[:140]

        return f"""
    <div class="grid-card" data-vose="{"true" if vose else "false"}" data-isnew="{"true" if is_new else "false"}" data-cinemas="{cinema_ids}">
      <div class="grid-poster">{poster_html}</div>
      <div class="grid-info">
        <div class="badges">{new_badge}{vose_badge}{score_badge}</div>
        <a href="./{film.get('slug', slugify(film.get('title_en', title_es)))}/"
           class="grid-title" data-es="{esc(title_es)}" data-en="{esc(title_en)}">{title_es}</a>
        <div class="grid-meta">{rating_dot}{meta[:80]}</div>
        <div class="grid-synopsis" data-es="{esc(syn_es)}" data-en="{esc(syn_en)}">{syn_es}</div>
        <div class="cinema-links">
          <div class="cinema-links-label" data-es="{where_es}" data-en="{where_en}">{where_es}</div>
          <div class="cinema-tags">{cinema_tags}</div>
        </div>
      </div>
    </div>"""

    # Build multiplex section: first film gets featured card, rest go in grid pairs
    multiplex_cards = ""
    if multiplex_films:
        multiplex_cards += featured_card_html(multiplex_films[0])
        rest = multiplex_films[1:]
        for i in range(0, len(rest), 2):
            pair = rest[i:i+2]
            inner = "".join(grid_card_html(f) for f in pair)
            multiplex_cards += f'\n  <div class="grid-row">{inner}\n  </div>'

    # Babel: compact list cards for babel-only films, tag strip for shared ones
    babel_cards = ""
    if "babel" in arthouse_films:
        babel_only   = [f for f in arthouse_films["babel"] if not any(c["type"]=="multiplex" for c in f["cinemas"])]
        babel_shared = [f for f in arthouse_films["babel"] if any(c["type"]=="multiplex" for c in f["cinemas"])]
        babel_cards  = "\n".join(film_card_html(f) for f in babel_only)
        if babel_shared:
            shared_tags = "".join(
                f'<span class="cinema-tag" style="cursor:default;">{f["title"]}{'<span class="vose-mini">VOSE</span>' if f["any_vose"] else ""}</span>'
                for f in babel_shared
            )
            babel_cards += f"""
  <div style="margin:0 24px 16px;padding:12px 16px;background:#130d20;border:1px solid #261d3a;border-radius:10px;">
    <div class="cinema-links-label" data-es="También en Babel esta semana" data-en="Also at Babel this week" style="margin-bottom:8px;">También en Babel esta semana</div>
    <div class="cinema-tags">{shared_tags}</div>
  </div>"""

    dor_cards = ""
    if "dor" in arthouse_films:
        dor_cards = "\n".join(film_card_html(f) for f in arthouse_films["dor"])

    return f"""<!DOCTYPE html>
<html lang="es" id="html-root">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
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
      <a href="../preferences/" style="font-size:11px;color:#7a6a9a;text-decoration:none;white-space:nowrap;" data-es="⚙️ Preferencias" data-en="⚙️ Preferences">⚙️ Preferencias</a>
    </div>
  </div>

  <div class="header">
    <div class="header-eyebrow" data-es="🎬 Newsletter Semanal" data-en="🎬 Weekly Newsletter">🎬 Newsletter Semanal</div>
    <div class="header-title">Cartelera<br>Valencia</div>
    <div class="header-subtitle" data-es="La guía completa del cine en Valencia esta semana" data-en="Your complete guide to cinema in Valencia this week">La guía completa del cine en Valencia esta semana</div>
    <div class="header-date" data-es="{date_es}" data-en="{date_en}">{date_es}</div>
  </div>

  <div class="section-label" data-es="🎬 Cines Multiplex — Grandes Estrenos" data-en="🎬 Multiplex Cinemas — Major Releases">🎬 Cines Multiplex — Grandes Estrenos</div>
  <div class="cinema-group-header">
    <div>
      <div class="cinema-group-name">Kinépolis · Yelmo · Ocine Aqua · ABC · MN4 · Lys</div>
      <div class="cinema-group-desc" data-es="Los grandes multiplex de Valencia y área metropolitana" data-en="Valencia's main multiplexes across the city and metropolitan area">Los grandes multiplex de Valencia y área metropolitana</div>
    </div>
  </div>
  {multiplex_cards}

  <div class="section-divider"></div>

  <div class="section-label" data-es="🎭 Cines Babel — Cine Independiente &amp; VOSE" data-en="🎭 Cines Babel — Independent &amp; VOSE Cinema">🎭 Cines Babel — Cine Independiente &amp; VOSE</div>
  <div class="cinema-group-header">
    <div>
      <div class="cinema-group-name">Cines Babel</div>
      <div class="cinema-group-desc" data-es="C/ Vicent Sancho Tello, 10 · 5 salas · Especialistas en cine independiente y VOSE" data-en="C/ Vicent Sancho Tello, 10 · 5 screens · Independent &amp; VOSE specialists">C/ Vicent Sancho Tello, 10 · 5 salas · Especialistas en cine independiente y VOSE</div>
    </div>
    <a href="https://www.cinesalbatrosbabel.com" class="cinema-group-link">cinesalbatrosbabel.com →</a>
  </div>
  {babel_cards}

  <div class="section-divider"></div>

  <div class="section-label" data-es="🎞️ Cinestudio D'Or — Sesión Doble, Cine de Autor" data-en="🎞️ Cinestudio D'Or — Double Bills &amp; Art Cinema">🎞️ Cinestudio D'Or — Sesión Doble, Cine de Autor</div>
  <div class="cinema-group-header">
    <div>
      <div class="cinema-group-name">Cinestudio D'Or</div>
      <div class="cinema-group-desc" data-es="C/ Almirante Cadarso, 31 · El cine más antiguo de Valencia · Sesión doble continua" data-en="C/ Almirante Cadarso, 31 · Valencia's oldest cinema · Continuous double-bill screenings">C/ Almirante Cadarso, 31 · El cine más antiguo de Valencia · Sesión doble continua</div>
    </div>
    <a href="https://cinestudiodor.es" class="cinema-group-link">cinestudiodor.es →</a>
  </div>
  {dor_cards}

  <div class="section-divider"></div>

  <div class="footer">
    <div class="footer-logo">Cartelera Valencia</div>
    <p>
      <span data-es="Fuentes:" data-en="Sources:">Fuentes:</span>
      <a href="https://mabuse.es">Mabuse</a> · <a href="https://www.ecartelera.com">eCartelera</a><br>
      <span data-es="Horarios y disponibilidad VOSE pueden variar — verifica siempre en la web de cada cine." data-en="Showtimes and VOSE availability may vary — always check the cinema's website before you go.">Horarios y disponibilidad VOSE pueden variar — verifica siempre en la web de cada cine.</span><br>
      <em style="color:#3a2050;" data-es="🎭 Babel y Cinestudio D'Or son los referentes del cine de autor y VOSE en Valencia" data-en="🎭 Babel and Cinestudio D'Or are Valencia's homes for arthouse and VOSE cinema">🎭 Babel y Cinestudio D'Or son los referentes del cine de autor y VOSE en Valencia</em><br><br>
      <span style="color:#3a2e50;">© {anchor.year} · Cartelera Valencia Weekly</span>
    </p>
  </div>

</div>
<script>
window.SUPABASE_URL  = "{SUPABASE_URL}";
window.SUPABASE_ANON = "{SUPABASE_ANON}";
{JS}
window.addEventListener('DOMContentLoaded', () => {{
  applyVisibility();
  loadUserPreferences();

  // Make film title links pass through URL params (preferences) to detail pages
  document.querySelectorAll('a.film-title, a.grid-title, a.list-title').forEach(a => {{
    const params = window.location.search;
    if (params) {{
      a.href = a.getAttribute('href') + params;
    }}
  }});
}});
</script>
</body>
</html>"""


# ─── Email sender ─────────────────────────────────────────────────────────────


def build_teaser_email(films_by_title: dict, anchor: datetime, page_url: str, prefs_url: str = "") -> str:
    """Build a clean, simple teaser email that links to the full hosted page."""
    date_en = week_range_en(anchor)

    total_films   = len(films_by_title)
    total_cinemas = len(CINEMAS)
    vose_films    = [f for f in films_by_title.values() if f["any_vose"]]
    new_films     = [f for f in films_by_title.values() if f["is_new"]]

    # Pick up to 3 highlights: newest first, then VOSE, then anything
    highlights = []
    seen = set()
    for f in sorted(films_by_title.values(), key=lambda x: (not x["is_new"], not x["any_vose"], x["title"])):
        if f["title"] not in seen:
            highlights.append(f)
            seen.add(f["title"])
        if len(highlights) == 3:
            break

    def highlight_card(film):
        poster = film["poster"]
        poster_html = (
            f'<img src="{poster}" alt="{film["title"]}" width="60" height="87" "'
            f'style="width:60px;height:87px;object-fit:cover;border-radius:6px;display:block;">' 
            if poster else
            '<div style="width:60px;height:87px;border-radius:6px;background:#2a1f3d;display:flex;align-items:center;justify-content:center;font-size:24px;">🎬</div>'
        )
        badges = ""
        if film["is_new"]:
            badges += '<span style="display:inline-block;padding:2px 8px;border-radius:12px;font-size:10px;font-weight:600;letter-spacing:1px;text-transform:uppercase;background:#2a1a00;color:#ffb432;border:1px solid #ffb43260;margin-right:4px;">NEW</span>'
        if film["any_vose"]:
            badges += '<span style="display:inline-block;padding:2px 7px;border-radius:4px;font-size:10px;font-weight:700;letter-spacing:1px;background:#1a1800;color:#ffd84a;border:1px solid #ffd84a50;">VOSE</span>'

        cinemas_str = " · ".join(c["name"] for c in film["cinemas"][:4])
        if len(film["cinemas"]) > 4:
            cinemas_str += f' +{len(film["cinemas"])-4} more'

        synopsis   = film.get("synopsis", "")
        meta_clean = film["meta"][:80].strip(". ")

        return f"""
        <tr>
          <td style="padding:12px 0;border-bottom:1px solid #1e1630;">
            <table width="100%" cellpadding="0" cellspacing="0" border="0">
              <tr>
                <td width="70" valign="top" style="padding-right:14px;">{poster_html}</td>
                <td valign="top">
                  <div style="margin-bottom:5px;">{badges}</div>
                  <div style="font-family:Georgia,serif;font-size:16px;font-weight:700;color:#f0eae0;margin-bottom:4px;">{film["title"]}</div>
                  <div style="font-size:11px;color:#7a6d8a;margin-bottom:5px;">{meta_clean}</div>
                  <div style="font-size:12px;color:#8c8090;margin-bottom:6px;line-height:1.4;">{synopsis[:160] if synopsis else ""}</div>
                  <div style="font-size:11px;color:#5a4e6a;">{cinemas_str}</div>
                </td>
              </tr>
            </table>
          </td>
        </tr>"""

    highlights_html = "".join(highlight_card(f) for f in highlights)

    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>Valencia Cinema – {date_en}</title>
</head>
<body style="margin:0;padding:0;background:#0f0c14;font-family:Helvetica,Arial,sans-serif;">
<table width="100%" cellpadding="0" cellspacing="0" border="0" style="background:#0f0c14;">
  <tr>
    <td align="center" style="padding:20px 10px;">
      <table width="600" cellpadding="0" cellspacing="0" border="0" style="max-width:600px;width:100%;">

        <!-- HEADER -->
        <tr>
          <td style="background:linear-gradient(135deg,#1a0a2e,#0f0c14);border-bottom:1px solid #3a2a55;padding:36px 40px 28px;text-align:center;border-radius:12px 12px 0 0;">
            <div style="font-size:11px;font-weight:500;letter-spacing:3px;text-transform:uppercase;color:#ffb432;margin-bottom:10px;">🎬 Weekly Newsletter</div>
            <div style="font-family:Georgia,serif;font-size:38px;font-weight:700;color:#f9f3e8;line-height:1.1;margin-bottom:8px;">Cartelera<br>Valencia</div>
            <div style="font-size:13px;color:#9b8faa;">Your weekly guide to cinema in Valencia</div>
            <div style="display:inline-block;margin-top:16px;padding:5px 16px;background:rgba(255,180,50,0.12);border:1px solid rgba(255,180,50,0.3);border-radius:20px;font-size:12px;color:#ffb432;letter-spacing:1px;">{date_en}</div>
          </td>
        </tr>

        <!-- STATS BAR -->
        <tr>
          <td style="background:#160f24;border-bottom:1px solid #2a1f3d;padding:14px 40px;">
            <table width="100%" cellpadding="0" cellspacing="0" border="0">
              <tr>
                <td style="text-align:center;">
                  <div style="font-size:22px;font-weight:700;color:#f0eae0;">{total_films}</div>
                  <div style="font-size:10px;letter-spacing:1px;text-transform:uppercase;color:#5a4e6a;">Films showing</div>
                </td>
                <td style="text-align:center;border-left:1px solid #2a1f3d;border-right:1px solid #2a1f3d;">
                  <div style="font-size:22px;font-weight:700;color:#f0eae0;">{len(vose_films)}</div>
                  <div style="font-size:10px;letter-spacing:1px;text-transform:uppercase;color:#5a4e6a;">With VOSE</div>
                </td>
                <td style="text-align:center;">
                  <div style="font-size:22px;font-weight:700;color:#f0eae0;">{len(new_films)}</div>
                  <div style="font-size:10px;letter-spacing:1px;text-transform:uppercase;color:#5a4e6a;">New releases</div>
                </td>
              </tr>
            </table>
          </td>
        </tr>

        <!-- HIGHLIGHTS -->
        <tr>
          <td style="background:#0f0c14;padding:20px 40px 10px;">
            <div style="font-size:10px;letter-spacing:3px;text-transform:uppercase;color:#5a4e6a;margin-bottom:4px;">This week's highlights</div>
            <table width="100%" cellpadding="0" cellspacing="0" border="0">
              {highlights_html}
            </table>
          </td>
        </tr>

        <!-- CTA BUTTON -->
        <tr>
          <td style="background:#0f0c14;padding:24px 40px 32px;text-align:center;">
            <div style="font-size:13px;color:#7a6d8a;margin-bottom:18px;">See the full programme — all cinemas, all films, VOSE sessions highlighted</div>
            <a href="{page_url}" target="_blank" style="display:inline-block;padding:14px 36px;background:#ffb432;color:#0f0c14;font-family:Helvetica,Arial,sans-serif;font-weight:700;font-size:14px;text-decoration:none;border-radius:8px;">View Full Listings →</a>
            <br><br>
            <div style="font-size:11px;color:#5a4e6a;">Or copy this link: <a href="{page_url}" target="_blank" style="color:#7a6a9a;word-break:break-all;">{page_url}</a></div>
          </td>
        </tr>

        <!-- CINEMA LIST -->
        <tr>
          <td style="background:#0a0810;border-top:1px solid #1e1630;padding:18px 40px;text-align:center;">
            <div style="font-size:11px;color:#4a3f5e;line-height:1.8;">
              Kinépolis · Yelmo Campanar · Ocine Aqua · ABC El Saler · ABC Park · ABC Gran Turia<br>
              Cines MN4 · Cines Lys · <strong style="color:#6a5e7a;">Cines Babel</strong> · <strong style="color:#6a5e7a;">Cinestudio D'Or</strong>
            </div>
          </td>
        </tr>

        <!-- FOOTER -->
        <tr>
          <td style="background:#0a0810;padding:14px 40px 24px;text-align:center;border-radius:0 0 12px 12px;">
            <div style="font-size:11px;color:#3a2e50;line-height:1.6;">
              Showtimes may vary — always check the cinema's website before you go.<br>
              <a href="{prefs_url}" style="color:#5a4e6a;text-decoration:none;">⚙️ Manage preferences</a>
              &nbsp;·&nbsp;
              <a href="{prefs_url}" style="color:#5a4e6a;text-decoration:none;">Unsubscribe</a><br>
              © {anchor.year} Cartelera Valencia Weekly
            </div>
          </td>
        </tr>

      </table>
    </td>
  </tr>
</table>
</body>
</html>"""


def send_email(html: str, anchor: datetime) -> None:
    date_en = week_range_en(anchor)
    subject = f"🎬 Valencia Cinema – {date_en}"

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = f"{FROM_NAME} <{FROM_ADDRESS}>"
    msg["To"]      = ", ".join(RECIPIENTS)

    # Plain-text fallback
    plain = f"Valencia Cinema Weekly – {date_en}\n\nView this email in a browser that supports HTML.\n\nSource: mabuse.es"
    msg.attach(MIMEText(plain, "plain", "utf-8"))
    msg.attach(MIMEText(html,  "html",  "utf-8"))

    log.info(f"Connecting to {SMTP_HOST}:{SMTP_PORT} ...")
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
        server.ehlo()
        server.starttls()
        server.login(SMTP_USER, SMTP_PASSWORD)
        server.sendmail(FROM_ADDRESS, RECIPIENTS, msg.as_string())
    log.info(f"Email sent to {len(RECIPIENTS)} recipient(s).")


# ─── Entry point ──────────────────────────────────────────────────────────────

def main():
    # The newsletter covers Friday → Thursday; anchor on this coming Friday
    today  = datetime.now()
    days_until_friday = (4 - today.weekday()) % 7  # 4 = Friday
    if days_until_friday == 0:
        days_until_friday = 7  # if today IS Friday, show next week
    anchor = today + timedelta(days=days_until_friday)
    anchor = anchor.replace(hour=0, minute=0, second=0, microsecond=0)

    log.info(f"Building newsletter for week starting {anchor.date()} ...")
    warm_up_session()
    films = fetch_all()
    log.info(f"Total unique films found: {len(films)}")

    # Enrich each film with TMDB data
    log.info(f"TMDB_API_KEY present: {bool(TMDB_API_KEY)}, length: {len(TMDB_API_KEY)}, value_start: {TMDB_API_KEY[:4] if TMDB_API_KEY else 'empty'}")
    if TMDB_API_KEY:
        log.info("Enriching films with TMDB data ...")
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
                log.info(f"  ✓ {title} → {tmdb.get('title_en','?')} / {tmdb.get('title_original','?')} ⭐{tmdb.get('rating_score','?')}")
            else:
                film["title_en"]       = title
                film["title_original"] = title
                film["synopsis_en"]    = film.get("synopsis", "")
                film["synopsis_es"]    = film.get("synopsis", "")
                film["rating_score"]   = None
    else:
        for film in films.values():
            film["title_en"]       = film["title"]
            film["title_original"] = film["title"]
            film["synopsis_en"]    = film.get("synopsis", "")
            film["synopsis_es"]    = film.get("synopsis", "")
            film["rating_score"]   = None

    # Build the full bilingual listings page
    full_html = build_html(films, anchor)

    # Save listings to docs/listings/index.html for GitHub Pages
    os.makedirs("docs/listings", exist_ok=True)
    with open("docs/listings/index.html", "w", encoding="utf-8") as f:
        f.write(full_html)
    log.info("Full listings page saved to docs/listings/index.html")

    # Generate individual film detail pages
    log.info("Generating film detail pages ...")
    for title, film in films.items():
        slug = slugify(film.get("title_en", title) or title)
        film_dir = f"docs/listings/{slug}"
        os.makedirs(film_dir, exist_ok=True)
        detail_html = build_film_detail_page(film, anchor)
        with open(f"{film_dir}/index.html", "w", encoding="utf-8") as f:
            f.write(detail_html)
        # Store slug in film for linking from cards
        film["slug"] = slug
    log.info(f"Generated {len(films)} film detail pages")

    # Inject Supabase credentials into landing page and preferences page
    if SUPABASE_URL and SUPABASE_ANON:
        for page_path in ["docs/index.html", "docs/preferences/index.html"]:
            if os.path.exists(page_path):
                with open(page_path, "r", encoding="utf-8") as f:
                    page = f.read()
                page = page.replace("YOUR_SUPABASE_URL", SUPABASE_URL)
                page = page.replace("YOUR_SUPABASE_ANON_KEY", SUPABASE_ANON)
                with open(page_path, "w", encoding="utf-8") as f:
                    f.write(page)
                log.info(f"Supabase credentials injected into {page_path}")
    else:
        log.warning("SUPABASE_URL or SUPABASE_ANON not set — skipping credential injection")

    # Build and send the teaser email
    page_url  = os.environ.get("LISTINGS_URL", "https://matt-palmer999.github.io/film-email/listings")
    prefs_url = page_url.replace("/listings", "/preferences")

    # Only send email on Thursdays (scraper now runs daily)
    is_thursday = anchor.weekday() == 3
    force_email = os.environ.get("FORCE_EMAIL", "").lower() in ("1", "true", "yes")

    if is_thursday or force_email:
        teaser = build_teaser_email(films, anchor, page_url, prefs_url)
    else:
        log.info(f"Not Thursday (weekday={anchor.weekday()}) — skipping email send")
        teaser = None
    if teaser:
        send_email(teaser, anchor)
    close_browser()


if __name__ == "__main__":
    main()
