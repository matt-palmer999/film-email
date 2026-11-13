"""
Multicines Rosaleda scraper (Málaga, CC Rosaleda, Av. Simón Bolívar — 12 screens).

Run by Unión Cine Ciudad (UCC, Mendivil S.A.). cineciudad.com is server-rendered
PHP: the homepage has a cinema <select> whose option links to the cinema page
(cine.php?id=<long opaque token>), and that page lists every film as a card:

  <img class="card-img-top" ... src=poster>   (+ imagenes/vose.png badge if VOSE)
  <p class="card-text ..."><i class="fas fa-info-circle"></i> TITLE [V.O.S]</p>
  <div class="fw-bold" ...>Sábado, 14 de Noviembre del 2026</div>
  <a class="btn ... btn-hora ..." href="https://www.compraentradas.com/Sesion/56/...">16:20</a>

The id token is looked up from the homepage each run rather than hard-coded,
in case it isn't stable. The HTML mixes UTF-8 and cp1252 text, so it's decoded
as latin-1 (lossless) and each string is repaired with _fix_text().
"""

import html
import logging
import re

import requests

log = logging.getLogger(__name__)

CINEMA_KEY  = "rosaleda"
CINEMA_NAME = "Multicines Rosaleda"
BASE        = "https://cineciudad.com/"

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "es-ES,es;q=0.9",
}

_MONTHS = {m: i for i, m in enumerate(
    ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
     "agosto", "septiembre", "octubre", "noviembre", "diciembre"], 1)}

_CINE_LINK_RE = re.compile(r'<option value="(cine\.php\?id=[0-9a-f]+)">\s*Multicines Rosaleda', re.I)
_TITLE_RE     = re.compile(r'fa-info-circle"></i>\s*([^<]+)</p>')
_POSTER_RE    = re.compile(r'^[^>]*?src="([^"]+)"')
_DAY_RE       = re.compile(r'<div class="fw-bold"[^>]*>([^<]+)</div>(.*?)</div>', re.S)
_DATE_RE      = re.compile(r'(\d{1,2}) de ([A-Za-z]+) del? (\d{4})')
_SESSION_RE   = re.compile(r'<a[^>]*btn-hora[^>]*href="([^"]+)"[^>]*>\s*(\d{1,2}):(\d{2})\s*<')
_VOSE_TAG_RE  = re.compile(r'\s*\(?\bV\.?\s?O\.?\s?S\.?(?:E\.?)?\)?\s*$', re.I)
_3D_TAG_RE    = re.compile(r'\s+3D\s*$', re.I)


def _fix_text(s: str) -> str:
    """Text decoded as latin-1: re-decode as UTF-8 where it was UTF-8, else as cp1252."""
    raw = s.encode("latin-1")
    for enc in ("utf-8", "cp1252"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            pass
    return s


def _cinema_url(sess: requests.Session) -> str:
    r = sess.get(BASE, headers=_HEADERS, timeout=25)
    r.raise_for_status()
    m = _CINE_LINK_RE.search(r.content.decode("latin-1"))
    if not m:
        raise RuntimeError("Rosaleda: cinema link not found on cineciudad.com homepage")
    return BASE + html.unescape(m.group(1))


def _parse(page: str) -> list[dict]:
    films = []
    for card in page.split('<img class="card-img-top')[1:]:
        t = _TITLE_RE.search(card)
        if not t:
            continue
        raw_title = _fix_text(html.unescape(t.group(1))).strip()
        is_vose = "imagenes/vose.png" in card or bool(_VOSE_TAG_RE.search(raw_title))
        title = _VOSE_TAG_RE.sub("", raw_title).strip()
        is_3d = bool(_3D_TAG_RE.search(title))
        title = _3D_TAG_RE.sub("", title).strip()

        showtimes, seen = [], set()
        for day_label, body in _DAY_RE.findall(card):
            d = _DATE_RE.search(_fix_text(html.unescape(day_label)))
            if not d or d.group(2).lower() not in _MONTHS:
                continue
            date = f"{d.group(3)}-{_MONTHS[d.group(2).lower()]:02d}-{int(d.group(1)):02d}"
            for url, hh, mm in _SESSION_RE.findall(body):
                time = f"{int(hh):02d}:{mm}"
                if (date, time) in seen:
                    continue
                seen.add((date, time))
                showtimes.append({
                    "date":           date,
                    "time":           time,
                    "datetime_local": f"{date}T{time}:00",
                    "format":         "VOSE" if is_vose else ("3D" if is_3d else "Digital"),
                    "is_vose":        is_vose,
                    "audio_lang":     "EN" if is_vose else "ES",
                    "is_3d": is_3d, "is_imax": False, "is_4dx": False, "is_screenx": False,
                    "booking_url":    html.unescape(url),
                })
        if not showtimes:
            continue
        showtimes.sort(key=lambda s: s["datetime_local"])
        poster = _POSTER_RE.search(card)
        films.append({
            "cinema":        CINEMA_KEY,
            "cinema_name":   CINEMA_NAME,
            "title_es":      title,
            "is_vose":       is_vose,
            "audio_lang":    "EN" if is_vose else "ES",
            "imdb_id":       "",
            "is_film":       True,
            "duration_mins": 0,
            "poster_url":    poster.group(1) if poster else "",
            "synopsis_es":   "",
            "showtimes":     showtimes,
        })
    return films


def scrape_rosaleda() -> list[dict]:
    """Fetch Multicines Rosaleda listings and return a list of film dicts."""
    sess = requests.Session()
    url = _cinema_url(sess)
    r = sess.get(url, headers=_HEADERS, timeout=25)
    r.raise_for_status()
    films = _parse(r.content.decode("latin-1"))
    if not films:
        raise RuntimeError("Rosaleda: cinema page fetched but no sessions parsed (layout changed?)")
    log.info("Multicines Rosaleda: %d films, %d sessions",
             len(films), sum(len(f["showtimes"]) for f in films))
    return films


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    films = scrape_rosaleda()
    print(f"\n{CINEMA_NAME}: {len(films)} films, {sum(len(f['showtimes']) for f in films)} sessions, "
          f"{sum(len(f['showtimes']) for f in films if f['is_vose'])} VOSE")
    for f in films:
        d = [s["date"] for s in f["showtimes"]]
        flags = ("VOSE" if f["is_vose"] else "    ") + (" 3D" if f["showtimes"][0]["is_3d"] else "   ")
        print(f"  [{flags}] {f['title_es'][:48]:<48} {d[0]} -> {d[-1]} ({len(d)})")
