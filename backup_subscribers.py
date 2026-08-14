"""
backup_subscribers.py
Fetches all rows from the Supabase subscribers table, writes a CSV,
and emails it to the backup address. Runs weekly via GitHub Actions.
"""

import csv
import io
import json
import os
import smtplib
import urllib.request
from datetime import datetime, timezone
from email import encoders
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

# ── Config from environment ───────────────────────────────────────────────────
SUPABASE_URL         = os.environ["SUPABASE_URL"].rstrip("/")
SUPABASE_SERVICE_KEY = os.environ["SUPABASE_SERVICE_KEY"]
SMTP_HOST            = os.environ["SMTP_HOST"]
SMTP_PORT            = int(os.environ.get("SMTP_PORT", 587))
SMTP_USER            = os.environ["SMTP_USER"]
SMTP_PASSWORD        = os.environ["SMTP_PASSWORD"]
FROM_ADDRESS         = os.environ["FROM_ADDRESS"]
FROM_NAME            = os.environ.get("FROM_NAME", "Cartelera Valencia")
BACKUP_TO            = os.environ["BACKUP_TO"]


def fetch_all_subscribers() -> list[dict]:
    """Fetch every row from the subscribers table using the service key (bypasses RLS)."""
    url = f"{SUPABASE_URL}/rest/v1/subscribers?select=*&order=subscribed_at"
    req = urllib.request.Request(url, headers={
        "apikey":        SUPABASE_SERVICE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
    })
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode())


def to_csv(rows: list[dict]) -> bytes:
    """Serialise a list of dicts to UTF-8 CSV bytes."""
    if not rows:
        return b"(no subscribers)\n"
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=rows[0].keys())
    writer.writeheader()
    writer.writerows(rows)
    return buf.getvalue().encode("utf-8")


def send_backup(csv_bytes: bytes, row_count: int) -> None:
    """Email the CSV as an attachment."""
    now        = datetime.now(timezone.utc)
    date_str   = now.strftime("%Y-%m-%d")
    filename   = f"subscribers_backup_{date_str}.csv"
    subject    = f"📦 Subscriber backup {date_str} — {row_count} rows"

    body = (
        f"Weekly subscriber backup — {date_str}\n\n"
        f"Total rows: {row_count}\n"
        f"Active subscribers: {sum(1 for r in rows if r.get('active'))}\n"
        f"Email-enabled: {sum(1 for r in rows if r.get('email_enabled') and r.get('active'))}\n\n"
        f"CSV attached.\n"
    )

    msg = MIMEMultipart()
    msg["Subject"] = subject
    msg["From"]    = f"{FROM_NAME} <{FROM_ADDRESS}>"
    msg["To"]      = BACKUP_TO
    msg.attach(MIMEText(body, "plain", "utf-8"))

    attachment = MIMEBase("text", "csv")
    attachment.set_payload(csv_bytes)
    encoders.encode_base64(attachment)
    attachment.add_header("Content-Disposition", "attachment", filename=filename)
    msg.attach(attachment)

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
        server.ehlo()
        server.starttls()
        server.login(SMTP_USER, SMTP_PASSWORD)
        server.sendmail(FROM_ADDRESS, [BACKUP_TO], msg.as_string())

    print(f"Backup sent to {BACKUP_TO} ({row_count} rows, {len(csv_bytes):,} bytes)")


if __name__ == "__main__":
    print("Fetching subscribers from Supabase …")
    rows = fetch_all_subscribers()
    print(f"  {len(rows)} rows fetched")

    csv_bytes = to_csv(rows)
    send_backup(csv_bytes, len(rows))
