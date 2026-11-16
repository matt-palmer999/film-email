"""Send one test email to ADMIN_EMAILS through mailer.py (Microsoft 365 if set up).

    python send_test_email_m365.py
"""
import os
import sys
from datetime import datetime

from send_report import load_env

load_env()
import mailer  # noqa: E402

to = [a.strip() for a in os.environ.get("ADMIN_EMAILS", "").split(",") if a.strip()]
if not to:
    sys.exit("ADMIN_EMAILS not set")
now = datetime.now().strftime("%Y-%m-%d %H:%M")
try:
    mailer.send_email(
        to,
        f"whatson.movie test email via {mailer.route_name()} · {now}",
        f"This is a test from whatson.movie, sent through {mailer.route_name()} at {now}.\n\n"
        "If it reached your inbox (not junk), the new email route works.\n",
    )
except Exception as exc:
    sys.exit(f"Test email failed via {mailer.route_name()}: {exc}")
print(f"Test email sent via {mailer.route_name()} to {len(to)} admin recipient(s)")
