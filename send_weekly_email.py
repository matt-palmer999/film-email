"""
send_weekly_email.py
Loads today's film cache (built by the 8am pipeline run) and sends
the weekly subscriber email to everyone with email_enabled=true.

Run every Thursday at 7pm via Windows Task Scheduler.
FORCE_EMAIL=1 overrides the Thursday check (useful for testing).
"""

import json
import logging
import os
import sys
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger()

# ── Load .env ──────────────────────────────────────────────────────────────────
env_path = Path(__file__).parent / ".env"
if env_path.exists():
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

CACHE_PATH = Path(__file__).parent / "docs" / "data" / "films_cache.json"

def main() -> None:
    # Safety: only run on Thursdays unless overridden
    from datetime import datetime
    is_thursday = datetime.now().weekday() == 3
    force       = os.environ.get("FORCE_EMAIL", "").lower() in ("1", "true", "yes")

    if not (is_thursday or force):
        log.info(f"Not Thursday (weekday={datetime.now().weekday()}) — skipping. Set FORCE_EMAIL=1 to override.")
        sys.exit(0)

    if not CACHE_PATH.exists():
        log.error(f"Cache not found at {CACHE_PATH} — has the morning pipeline run yet?")
        sys.exit(1)

    log.info(f"Loading film cache from {CACHE_PATH} ...")
    with open(CACHE_PATH, encoding="utf-8") as f:
        films = json.load(f)
    log.info(f"Loaded {len(films)} films")

    # Import and call the email sender from pipeline.py
    sys.path.insert(0, str(Path(__file__).parent))
    from pipeline import send_weekly_emails
    send_weekly_emails(films)


if __name__ == "__main__":
    main()
