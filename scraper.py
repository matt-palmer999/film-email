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

import requests
from bs4 import BeautifulSoup

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

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/123.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Cache-Control": "max-age=0",
}

# Persistent session so cookies carry over between requests (more browser-like)
_session = requests.Session()
_session.headers.update(HEADERS)

# ─── Scraping ─────────────────────────────────────────────────────────────────

def fetch_cinema(cinema_id: str) -> list[dict]:
    """Scrape today's listings for a single cinema. Returns list of film dicts."""
    import time, random
    cinema = CINEMAS[cinema_id]
    log.info(f"Fetching {cinema['name']} ...")

    # Polite random delay between requests (2-5 seconds) — looks more human
    time.sleep(random.uniform(2, 5))

    try:
        resp = _session.get(cinema["url"], timeout=20)
        resp.raise_for_status()
        log.info(f"  HTTP {resp.status_code} — {len(resp.text)} bytes received")
    except requests.RequestException as e:
        log.warning(f"  Failed to fetch {cinema['name']}: {e}")
        return []

    # Log a page snippet to help debug if films are still 0
    snippet = resp.text[:400].replace("\n", " ")
    log.info(f"  Page snippet: {snippet}")

    soup = BeautifulSoup(resp.text, "html.parser")
    films = []

    # Each film block is an <h3> inside a section following a poster image
    for h3 in soup.select("h3 a"):
        title = h3.get_text(strip=True)
        if not title:
            continue

        # Walk up to the film container
        container = h3.find_parent(class_=lambda c: c and "pelicula" in c) or h3.find_parent("article") or h3.parent.parent

        # Genre / metadata paragraph
        meta_text = ""
        meta_p = container.find("p") if container else None
        if meta_p:
            meta_text = meta_p.get_text(" ", strip=True)

        # Detect VOSE — look for the label in surrounding text
        raw_html = str(container) if container else ""
        vose = bool(re.search(r"VOSE|INGL[ÉE]S SUBTITULADO|English.*es\b|nosubt.*English", raw_html, re.IGNORECASE))

        # Detect if it's a new release
        is_new = bool(container and container.find(string=re.compile(r"ESTRENO", re.I))) if container else False

        # Rating
        rating = "?"
        rating_img = container.find("img", src=re.compile(r"calificacion")) if container else None
        if rating_img:
            src = rating_img["src"]
            if "ai.png"  in src: rating = "TP"
            elif "7.png"  in src: rating = "7"
            elif "12.png" in src: rating = "12"
            elif "16.png" in src: rating = "16"
            elif "18.png" in src: rating = "18"

        # Poster image
        poster_img = container.find("img", src=re.compile(r"uploads")) if container else None
        poster_url = poster_img["src"] if poster_img else ""

        films.append({
            "title":    title,
            "meta":     meta_text,
            "vose":     vose,
            "is_new":   is_new,
            "rating":   rating,
            "poster":   poster_url,
            "cinema_id": cinema_id,
        })

    log.info(f"  Found {len(films)} films at {cinema['name']}")
    return films


def warm_up_session() -> None:
    """Visit mabuse.es homepage first to get cookies, just like a real browser would."""
    import time
    log.info("Warming up session on mabuse.es ...")
    try:
        _session.get("https://mabuse.es/", timeout=15)
        time.sleep(2)
        log.info("  Session warmed up.")
    except Exception as e:
        log.warning(f"  Warm-up failed (non-fatal): {e}")


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
                    "title":   title,
                    "meta":    film["meta"],
                    "is_new":  film["is_new"],
                    "rating":  film["rating"],
                    "poster":  film["poster"],
                    "cinemas": [],   # list of {id, name, website, vose, type}
                    "any_vose": False,
                }
            cinema_info = CINEMAS[cinema_id]
            by_film[title]["cinemas"].append({
                "id":      cinema_id,
                "name":    cinema_info["name"],
                "website": cinema_info["website"],
                "type":    cinema_info["type"],
                "vose":    film["vose"],
            })
            if film["vose"]:
                by_film[title]["any_vose"] = True
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


# ─── HTML builder ─────────────────────────────────────────────────────────────

CSS = """
@import url('https://fonts.googleapis.com/css2?family=Playfair+Display:wght@700&family=DM+Sans:wght@300;400;500&display=swap');
*{margin:0;padding:0;box-sizing:border-box}
body{background:#0f0c14;font-family:'DM Sans',Helvetica,sans-serif;color:#f0eae0}
.wrapper{max-width:640px;margin:0 auto;background:#0f0c14}
.lang-bar{background:#0a0810;border-bottom:1px solid #1e1630;padding:10px 24px;display:flex;justify-content:flex-end;align-items:center;gap:8px}
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
.cinema-links-label{font-size:10px;letter-spacing:1px;text-transform:uppercase;color:#4a4060;font-weight:500;margin-bottom:5px}
.cinema-tags{display:flex;flex-wrap:wrap;gap:5px;margin-top:4px}
.cinema-tag{display:inline-block;padding:3px 9px;border-radius:4px;font-size:11px;color:#9a8fb0;background:rgba(255,255,255,.04);border:1px solid #2e2545;text-decoration:none;line-height:1.4}
.vose-mini{display:inline-block;margin-left:4px;font-size:9px;font-weight:700;letter-spacing:1px;color:#ffd84a;vertical-align:middle}
.rating{display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:3px;vertical-align:middle}
.rating-TP{background:#50c88c}.rating-12{background:#7aa0e0}.rating-16{background:#e08040}.rating-18{background:#e05050}.rating-7{background:#80cc80}
.footer{background:#0a0810;border-top:1px solid #1e1630;padding:28px 40px;text-align:center}
.footer p{font-size:12px;color:#4a3f5e;line-height:1.7}
.footer a{color:#7a6a9a;text-decoration:none}
.footer-logo{font-family:'Playfair Display',Georgia,serif;font-size:18px;color:#3a2e50;margin-bottom:10px}
"""

JS = """
function setLang(lang) {
  document.getElementById('btn-es').classList.toggle('active', lang === 'es');
  document.getElementById('btn-en').classList.toggle('active', lang === 'en');
  document.getElementById('html-root').setAttribute('lang', lang);
  document.querySelectorAll('[data-es][data-en]').forEach(el => {
    el.textContent = el.getAttribute('data-' + lang);
  });
}
"""


def t(el_type: str, es: str, en: str, cls: str = "") -> str:
    """Render a bilingual element."""
    c = f' class="{cls}"' if cls else ""
    return f'<{el_type}{c} data-es="{es}" data-en="{en}">{es}</{el_type}>'


def film_card_html(film: dict) -> str:
    """Build a list-card for one film."""
    title  = film["title"]
    rating = film["rating"]
    vose   = film["any_vose"]
    poster = film["poster"]
    cinemas = film["cinemas"]
    is_new = film["is_new"]

    poster_html = (
        f'<img src="{poster}" alt="{title}" onerror="this.style.display=\'none\'">'
        if poster else "🎬"
    )

    new_badge = (
        f'<span class="film-badge badge-new" data-es="ESTRENO" data-en="NEW RELEASE">ESTRENO</span>'
        if is_new else ""
    )
    vose_badge = '<span class="vose-badge">VOSE</span>' if vose else ""
    rating_dot = f'<span class="rating rating-{rating}"></span>+{rating} &nbsp;·&nbsp; ' if rating != "TP" else '<span class="rating rating-TP"></span>'

    cinema_tags = ""
    for c in cinemas:
        vm = '<span class="vose-mini">VOSE</span>' if c["vose"] else ""
        cinema_tags += f'<a href="{c["website"]}" class="cinema-tag">{c["name"]}{vm}</a>\n'

    where_es = "Dónde verla"
    where_en = "Where to see it"

    return f"""
  <div class="list-card">
    <div class="list-poster">{poster_html}</div>
    <div class="list-body">
      <div class="badges">{new_badge}{vose_badge}</div>
      <div class="list-title">{title}</div>
      <div class="list-meta">{rating_dot}{film['meta'][:120]}</div>
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

    multiplex_cards = "\n".join(film_card_html(f) for f in multiplex_films)

    babel_cards = ""
    if "babel" in arthouse_films:
        babel_only = [f for f in arthouse_films["babel"] if not any(c["type"]=="multiplex" for c in f["cinemas"])]
        babel_shared = [f for f in arthouse_films["babel"] if any(c["type"]=="multiplex" for c in f["cinemas"])]
        babel_cards = "\n".join(film_card_html(f) for f in babel_only)
        if babel_shared:
            shared_tags = "".join(
                f'<span class="cinema-tag" style="cursor:default;">{f["title"]}'
                + (' <span class="vose-mini">VOSE</span>' if f["any_vose"] else "")
                + "</span>"
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
    {t('span','Idioma','Language','lang-label')}
    <div class="lang-toggle">
      <button class="lang-btn active" id="btn-es" onclick="setLang('es')">ES</button>
      <button class="lang-btn" id="btn-en" onclick="setLang('en')">EN</button>
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
<script>{JS}</script>
</body>
</html>"""


# ─── Email sender ─────────────────────────────────────────────────────────────

def send_email(html: str, anchor: datetime) -> None:
    date_en = week_range_en(anchor)
    subject = f"🎬 Valencia Cinema – Week of {date_en}"

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

    html = build_html(films, anchor)

    # Optionally save to disk (useful for debugging)
    out_path = os.environ.get("OUTPUT_FILE", "")
    if out_path:
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(html)
        log.info(f"HTML saved to {out_path}")

    send_email(html, anchor)


if __name__ == "__main__":
    main()
