"""
backup_subscribers.py
Fetches all rows from the Supabase subscribers table, writes a CSV,
and emails it to the backup address. Runs weekly via GitHub Actions.
"""

import base64
import csv
import io
import json
import os
import urllib.request
from datetime import datetime, timezone

import requests  # pip install requests — better TLS behaviour than urllib

# ── Config from environment ───────────────────────────────────────────────────
SUPABASE_URL         = os.environ["SUPABASE_URL"].rstrip("/")
SUPABASE_SERVICE_KEY = os.environ["SUPABASE_SERVICE_KEY"]
RESEND_API_KEY       = os.environ["RESEND_API_KEY"]
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
    """Email the CSV as an attachment via Resend API."""
    now      = datetime.now(timezone.utc)
    date_str = now.strftime("%Y-%m-%d")
    filename = f"subscribers_backup_{date_str}.csv"
    subject  = f"📦 Subscriber backup {date_str} — {row_count} rows"

    body_html = (
        f"<p><strong>Weekly subscriber backup — {date_str}</strong></p>"
        f"<p>Total rows: {row_count}<br>"
        f"Active subscribers: {sum(1 for r in rows if r.get('active'))}<br>"
        f"Email-enabled: {sum(1 for r in rows if r.get('email_enabled') and r.get('active'))}</p>"
        f"<p>CSV attached.</p>"
    )

    payload = {
        "from":        f"{FROM_NAME} <{FROM_ADDRESS}>",
        "to":          [BACKUP_TO],
        "subject":     subject,
        "html":        body_html,
        "attachments": [{
            "filename": filename,
            "content":  base64.b64encode(csv_bytes).decode("ascii"),
        }],
    }

    resp = requests.post(
        "https://api.resend.com/emails",
        json=payload,
        headers={"Authorization": f"Bearer {RESEND_API_KEY}"},
        timeout=30,
    )

    if not resp.ok:
        print(f"Resend API error {resp.status_code}: {resp.text}")
        print(f"  FROM_ADDRESS={FROM_ADDRESS}")
        print(f"  RESEND_API_KEY starts with: {RESEND_API_KEY[:8]}...")
        resp.raise_for_status()

    result = resp.json()
    print(f"Backup sent to {BACKUP_TO} via Resend (id={result.get('id')}, {row_count} rows, {len(csv_bytes):,} bytes)")


if __name__ == "__main__":
    print("Fetching subscribers from Supabase …")
    rows = fetch_all_subscribers()
    print(f"  {len(rows)} rows fetched")

    csv_bytes = to_csv(rows)
    send_backup(csv_bytes, len(rows))
