"""
ABC Cinemas scraper.

Three Valencia multiplexes on the same platform:
  - Cines ABC Park      (park.cinesabc.com)
  - Cines ABC El Saler  (elsaler.cinesabc.com)
  - Cines ABC Gran Turia (granturia.cinesabc.com)

Strategy:
1. GET {base}/index?pag=cartelera  — film list (titles, posters, fichas, etiq)
2. For each film/ficha POST {base}/ws.pro with a 7-day window — sessions + metadata
3. VOSE detected via Formato field on each session

No browser needed — plain requests + BeautifulSoup throughout.
Run directly for a quick test summary.
"""

import base64
import json
import logging
import re
import time
from datetime import date, timedelta
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

log = logging.getLogger(__name__)

VALENCIA_TZ = ZoneInfo("Europe/Madrid")

CINEMAS = [
    {
        "key":     "park",
        "name":    "Cines ABC Park",
        "base":    "https://park.cinesabc.com",
        "booking": "abcpark",
    },
    {
        "key":     "elsaler",
        "name":    "Cines ABC El Saler",
        "base":    "https://elsaler.cinesabc.com",
        "booking": "abcelsaler",
    },
    {
        "key":     "granturia",
        "name":    "Cines ABC Gran Turia",
        "base":    "https://granturia.cinesabc.com",
        "booking": "abcgranturia",
    },
]

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)
_BASE_HEADERS = {
    "User-Agent":      _UA,
    "Accept-Language": "es-ES,es;q=0.9",
}

# DD/MM/YYYY
_FECHA_RE = re.compile(r"^(\d{2})/(\d{2})/(\d{4})$")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _add_to_json(body: str, key: str) -> dict:
    """Robustly extract the JSON argument from addToJSON('key', {...}) in body."""
    marker = f"addToJSON('{key}',"
    idx = body.find(marker)
    if idx == -1:
        return {}
    try:
        start = body.index("{", idx)
    except ValueError:
        return {}
    depth = 0
    for i, ch in enumerate(body[start:], start):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(body[start : i + 1])
                except json.JSONDecodeError:
                    return {}
    return {}


def _decode_synopsis(b64: str) -> str:
    if not b64:
        return ""
    try:
        # ABC synopses are Latin-1 bytes, then Base64-encoded
        padded = b64 + "=="
        return base64.b64decode(padded).decode("latin-1").strip()
    except Exception:
        return ""


def _parse_fecha(s: str) -> str:
    """DD/MM/YYYY -> YYYY-MM-DD, or '' on failure."""
    m = _FECHA_RE.match(s.strip())
    if not m:
        return ""
    return f"{m.group(3)}-{m.group(2)}-{m.group(1)}"


def _parse_time(hora: str) -> str:
    """' 18:10'  or  '(VOSE) 20:30' -> '18:10'"""
    m = re.search(r"(\d{1,2}:\d{2})", hora)
    return m.group(1) if m else ""


def _fmt_is_vose(fmt: str) -> bool:
    return "VOSE" in fmt.upper()


def _fmt_is_3d(fmt: str) -> bool:
    return "3D" in fmt.upper()


def _fmt_label(fmt: str) -> str:
    """Clean up the Formato value: '(VOSE)' -> 'VOSE', '' -> '2D'."""
    s = fmt.strip().strip("()")
    return s if s else "2D"


# ---------------------------------------------------------------------------
# Per-cinema scraper
# ---------------------------------------------------------------------------

def _scrape_one(cinema: dict, session: requests.Session) -> list[dict]:
    base     = cinema["base"]
    booking  = cinema["booking"]
    name     = cinema["name"]
    key      = cinema["key"]

    ws_headers = {
        **_BASE_HEADERS,
        "Content-Type":      "application/json",
        "X-Requested-With":  "XMLHttpRequest",
        "Origin":            base,
        "Referer":           f"{base}/index?pag=cartelera",
    }

    # ---- Step 1: cartelera HTML ------------------------------------------------
    log.info("Fetching %s cartelera …", name)
    r = session.get(f"{base}/index?pag=cartelera", headers=_BASE_HEADERS, timeout=20)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "lxml")

    # etiq lives in the ws-data attribute of every session container
    ws_cont = soup.select_one("div.cont-sesiones-asinc[ws-data]")
    if not ws_cont:
        log.error("%s: could not find etiq — skipping", name)
        return []
    etiq = ws_cont["ws-data"]
    log.debug("etiq: %s", etiq)

    # Build film entries: ficha -> {title, poster_url}
    film_entries: dict[str, dict] = {}
    for div in soup.select("div.cartelera"):
        cont = div.select_one("div.cont-sesiones-asinc[ws-ficha]")
        if not cont:
            continue
        ficha = cont["ws-ficha"]
        title_el = div.select_one("div.ver-ficha")
        title    = title_el.get_text(strip=True) if title_el else ""
        img_el   = div.select_one(f"img[id-ficha='{ficha}']")
        poster   = img_el["src"] if img_el else ""
        film_entries[ficha] = {"title": title, "poster": poster}

    log.info("%s: %d films in cartelera", name, len(film_entries))

    # ---- Step 2: query ws.pro per film ----------------------------------------
    today     = date.today()
    primerdia = today.strftime("%Y-%m-%d")
    ultimodia = (today + timedelta(days=7)).strftime("%Y-%m-%d")

    results: list[dict] = []

    for ficha, fmeta in film_entries.items():
        payload = {
            "uuid":      "scraper",
            "uuid-fid":  None,
            "proc":      "bloque",
            "sesion":    "0",
            "primerdia": primerdia,
            "ultimodia": ultimodia,
            "etiq":      etiq,
            "ficha":     ficha,
        }
        try:
            resp = session.post(
                f"{base}/ws.pro", json=payload, headers=ws_headers, timeout=15
            )
            resp.raise_for_status()
            body = resp.json().get("body", "")
        except Exception as exc:
            log.warning("%s ficha=%s ws.pro error: %s", name, ficha, exc)
            continue

        # Film metadata (synopsis) — may not be present for films without sessions
        synopsis = ""
        fdata = _add_to_json(body, "f")
        if fdata:
            finfo    = fdata.get(ficha, {})
            synopsis = _decode_synopsis(finfo.get("sinopsis", ""))

        # Sessions
        showtimes: list[dict] = []
        sdata = _add_to_json(body, "s")
        for ses_key, ses in sdata.items():
            fecha_str = ses.get("Fecha", "")
            hora_str  = ses.get("Hora", "")
            formato   = ses.get("Formato", "")
            ses_id    = str(ses.get("Id", ""))

            date_str = _parse_fecha(fecha_str)
            time_str = _parse_time(hora_str)
            if not date_str or not time_str:
                continue

            is_v   = _fmt_is_vose(formato)
            is_3d  = _fmt_is_3d(formato)
            b_url  = (
                f"https://{booking}.reservaentradas.com/{booking}/"
                f"?pag=sesion&id={ses_id}"
                if ses_id else ""
            )

            showtimes.append({
                "datetime_local": f"{date_str}T{time_str}:00",
                "date":           date_str,
                "time":           time_str,
                "format":         _fmt_label(formato),
                "is_vose":        is_v,
                "audio_lang":     "EN" if is_v else "ES",
                "is_3d":          is_3d,
                "is_imax":        False,
                "is_4dx":         False,
                "sala":           ses.get("Sala", ""),
                "session_id":     ses_id,
                "booking_url":    b_url,
            })

        if not showtimes:
            continue  # film has no upcoming sessions this week

        any_vose = any(s["is_vose"] for s in showtimes)
        results.append({
            "cinema":         key,
            "cinema_name":    name,
            "title_es":       fmeta["title"],
            "original_title": "",
            "is_vose":        any_vose,
            "audio_lang":     "EN" if any_vose else "ES",
            "imdb_id":        "",
            "is_film":        True,
            "duration_mins":  0,
            "poster_url":     fmeta["poster"],
            "synopsis_es":    synopsis,
            "showtimes":      showtimes,
        })

        time.sleep(0.1)   # gentle rate limit

    log.info(
        "%s: %d films, %d sessions",
        name, len(results), sum(len(f["showtimes"]) for f in results),
    )
    return results


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def scrape_abc() -> list[dict]:
    """Scrape all three ABC cinemas and return a combined film list."""
    all_films: list[dict] = []
    with requests.Session() as sess:
        for cinema in CINEMAS:
            try:
                films = _scrape_one(cinema, sess)
                all_films.extend(films)
            except Exception as exc:
                log.error("Failed to scrape %s: %s", cinema["name"], exc, exc_info=True)
    return all_films


# ---------------------------------------------------------------------------
# CLI test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )

    films = scrape_abc()
    total = sum(len(f["showtimes"]) for f in films)
    vose  = [f for f in films if f["is_vose"]]

    print(f"\n{'='*65}")
    print(f"ABC Cinemas (Park + El Saler + Gran Turia)")
    print(f"  {len(films)} films  /  {total} sessions  /  {len(vose)} VOSE")
    print(f"{'='*65}")

    for cinema in CINEMAS:
        cfilms = [f for f in films if f["cinema"] == cinema["key"]]
        csess  = sum(len(f["showtimes"]) for f in cfilms)
        cvose  = sum(1 for f in cfilms if f["is_vose"])
        print(f"\n  {cinema['name']}: {len(cfilms)} films / {csess} sessions / {cvose} VOSE")
        for f in sorted(cfilms, key=lambda x: x["title_es"]):
            vose_tag = "VOSE" if f["is_vose"] else "    "
            dates    = sorted({s["date"] for s in f["showtimes"] if s["date"]})
            n        = len(f["showtimes"])
            d_range  = f"{dates[0]} -> {dates[-1]}" if dates else "?"
            print(f"    [{vose_tag}] {f['title_es'][:45]:<45} {n:3d} sessions  {d_range}")
