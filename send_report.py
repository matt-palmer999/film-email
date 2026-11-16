"""Email a report file to the admins.

    python send_report.py reports/once_over_2026-11-23.md "whatson.movie weekly once-over"

Uses the same SMTP settings and ADMIN_EMAILS list as the pipeline summary.
Reads .env itself, so it works without loading it into the shell first.
"""
from __future__ import annotations

import os
import smtplib
import sys
from email.mime.text import MIMEText

ENV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")


def load_env(path: str = ENV_PATH) -> None:
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                os.environ.setdefault(key.strip(), value.strip())
    except FileNotFoundError:
        pass


def send_report(path: str, subject: str) -> bool:
    load_env()
    host = os.environ.get("SMTP_HOST", "")
    port = int(os.environ.get("SMTP_PORT", 587))
    user = os.environ.get("SMTP_USER", "")
    password = os.environ.get("SMTP_PASSWORD", "")
    from_addr = os.environ.get("FROM_ADDRESS", user)
    from_name = os.environ.get("FROM_NAME", "whatson.movie")
    to = [a.strip() for a in os.environ.get("ADMIN_EMAILS", "").split(",") if a.strip()]

    if not all([host, user, password]):
        print("SMTP not configured in .env, so the report was not emailed")
        return False
    if not to:
        print("ADMIN_EMAILS not set, so the report was not emailed")
        return False

    with open(path, encoding="utf-8") as fh:
        body = fh.read()

    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = f"{from_name} <{from_addr}>"
    msg["To"] = ", ".join(to)

    try:
        with smtplib.SMTP(host, port) as server:
            server.ehlo()
            server.starttls()
            server.login(user, password)
            server.sendmail(from_addr, to, msg.as_string())
    except Exception as exc:
        print(f"Could not send the report: {exc}")
        return False
    print(f"Report emailed to {len(to)} admin recipient(s)")
    return True


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    ok = send_report(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else "whatson.movie report")
    sys.exit(0 if ok else 1)
