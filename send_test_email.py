"""
send_test_email.py
Builds and sends the weekly email to matt_palmer@outlook.com using
the cached film data from the last pipeline run. No scraping required.

Usage:
    python3.12 send_test_email.py

Edit TEST_PREFS below to simulate different subscriber preferences.
"""

import json, os, sys, smtplib
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

# ── Load .env ──────────────────────────────────────────────────────────────────
env_path = Path(__file__).parent / ".env"
if env_path.exists():
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

sys.path.insert(0, str(Path(__file__).parent))
from scraper import (
    build_full_email, apply_subscriber_filters,
    week_range_en, week_range_es,
    SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD,
    FROM_ADDRESS, FROM_NAME,
)

# ── Config ─────────────────────────────────────────────────────────────────────
TEST_RECIPIENT = "matt_palmer@outlook.com"
LISTINGS_URL   = "https://whatson.movie/listings/"
PREFS_URL      = "https://whatson.movie/preferences/"
UNSUB_URL      = ""

# Edit these to test different preference scenarios
TEST_PREFS = {
    "lang":          "en",
    "vose_only":     True,
    "vose_lang":     "en",
    "new_only":      True,
    "family_only":   True,
    "evening_only":  False,
    "classics":      True,
    "rating_filter": False,
    "min_rating":    7.0,
    "cinemas":       ["kinepolis", "yelmo", "babel"],
    "email_enabled": True,
}

# ── Load cached films ──────────────────────────────────────────────────────────
def main():
    cache = Path(__file__).parent / "docs" / "data" / "films_cache.json"
    if not cache.exists():
        print("ERROR: docs/data/films_cache.json not found.")
        print("Run pipeline.py first to generate the cache.")
        sys.exit(1)

    films  = json.loads(cache.read_text(encoding="utf-8"))
    anchor = datetime.now(ZoneInfo("Europe/Madrid"))

    # Apply subscriber filters
    filtered = apply_subscriber_filters(films, TEST_PREFS)
    print(f"Films total: {len(films)}  ->  after filter: {len(filtered)}")

    # Build email
    html, subject = build_full_email(
        filtered, anchor, LISTINGS_URL, PREFS_URL, UNSUB_URL, prefs=TEST_PREFS
    )

    lang  = TEST_PREFS.get("lang", "en")
    plain = (
        f"Cartelera Valencia – {week_range_es(anchor)}\n\nVer este email en un navegador compatible con HTML."
        if lang == "es" else
        f"Valencia Cinema Weekly – {week_range_en(anchor)}\n\nView this email in a browser that supports HTML."
    )

    # Send
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = f"{FROM_NAME} <{FROM_ADDRESS}>"
    msg["To"]      = TEST_RECIPIENT
    msg.attach(MIMEText(plain, "plain", "utf-8"))
    msg.attach(MIMEText(html,  "html",  "utf-8"))

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
        server.ehlo()
        server.starttls()
        server.login(SMTP_USER, SMTP_PASSWORD)
        server.sendmail(FROM_ADDRESS, [TEST_RECIPIENT], msg.as_string())

    subject_safe = subject.encode("ascii", "replace").decode("ascii")
    print(f"OK - Sent to {TEST_RECIPIENT}")
    print(f"  Subject : {subject_safe}")
    print(f"  Films   : {len(filtered)}")

if __name__ == "__main__":
    main()
