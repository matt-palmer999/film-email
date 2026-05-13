"""
send_verifications.py
Finds unverified subscribers in Supabase and sends them a verification email.
Run every 15 minutes via Windows Task Scheduler.

Also handles resends: if verification_sent_at is older than 24 hours and the
subscriber is still unverified, a fresh token is not needed — just resend.
"""

import json
import os
import smtplib
import sys
import urllib.request
import urllib.parse
from datetime import datetime, timezone
from email.mime.text import MIMEText
from pathlib import Path

# ── Load .env ──────────────────────────────────────────────────────────────────
env_path = Path(__file__).parent / ".env"
if env_path.exists():
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

SUPABASE_URL         = os.environ.get("SUPABASE_URL", "")
SUPABASE_SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")
SMTP_HOST     = os.environ.get("SMTP_HOST", "")
SMTP_PORT     = int(os.environ.get("SMTP_PORT", 587))
SMTP_USER     = os.environ.get("SMTP_USER", "")
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "")
FROM_ADDRESS  = os.environ.get("FROM_ADDRESS", SMTP_USER)
FROM_NAME     = os.environ.get("FROM_NAME", "whatson.movie")
VERIFY_BASE   = "https://whatson.movie/verify/"

# ── Helpers ────────────────────────────────────────────────────────────────────
def sb_get(path: str) -> list:
    req = urllib.request.Request(
        f"{SUPABASE_URL}/rest/v1/{path}",
        headers={
            "apikey":        SUPABASE_SERVICE_KEY,
            "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
        }
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read())


def sb_patch(path: str, body: dict) -> None:
    data = json.dumps(body).encode()
    req  = urllib.request.Request(
        f"{SUPABASE_URL}/rest/v1/{path}",
        data=data,
        headers={
            "apikey":        SUPABASE_SERVICE_KEY,
            "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
            "Content-Type":  "application/json",
            "Prefer":        "return=minimal",
        },
        method="PATCH",
    )
    with urllib.request.urlopen(req, timeout=10):
        pass


def send_verification_email(email: str, token: str, lang: str) -> None:
    is_es      = lang == "es"
    verify_url = f"{VERIFY_BASE}?token={token}"

    subject = (
        "Confirma tu suscripción a whatson.movie"
        if is_es else
        "Confirm your whatson.movie subscription"
    )
    body = "\n".join([
        "¡Hola!" if is_es else "Hi!",
        "",
        ("Gracias por suscribirte a whatson.movie. Haz clic en el enlace de abajo para confirmar tu email y activar tu cuenta:"
         if is_es else
         "Thanks for signing up to whatson.movie. Click the link below to confirm your email and activate your account:"),
        "",
        verify_url,
        "",
        "Este enlace caduca en 24 horas." if is_es else "This link expires in 24 hours.",
        "",
        ("Una vez confirmado, podrás personalizar tu cartelera y empezar a recibir el email semanal cada jueves."
         if is_es else
         "Once confirmed, you can customise your listings and start receiving the weekly email every Thursday."),
        "",
        "Si no te has suscrito, puedes ignorar este mensaje." if is_es else "If you didn't sign up, you can safely ignore this email.",
        "",
        "— whatson.movie",
    ])

    msg            = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"]    = f"{FROM_NAME} <{FROM_ADDRESS}>"
    msg["To"]      = email

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
        server.ehlo()
        server.starttls()
        server.login(SMTP_USER, SMTP_PASSWORD)
        server.sendmail(FROM_ADDRESS, [email], msg.as_string())


# ── Main ───────────────────────────────────────────────────────────────────────
def main() -> None:
    if not all([SUPABASE_URL, SUPABASE_SERVICE_KEY, SMTP_HOST, SMTP_USER, SMTP_PASSWORD]):
        print("ERROR: Missing required env vars (SUPABASE_URL, SUPABASE_SERVICE_KEY, SMTP_*)")
        sys.exit(1)

    # Fetch subscribers who are unverified and have a token set,
    # where no email has been sent yet OR last send was > 24 hours ago
    pending = sb_get(
        "subscribers"
        "?verified=eq.false"
        "&verification_token=not.is.null"
        "&or=(verification_sent_at.is.null,verification_sent_at.lt."
        + datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ").replace("T", "T").replace(":", "%3A")
        + ")"  # Supabase handles lt. for timestamps
        "&select=email,lang,verification_token,verification_sent_at"
    )

    # Simpler approach: just fetch all unverified with tokens, filter in Python
    pending = sb_get(
        "subscribers"
        "?verified=eq.false"
        "&verification_token=not.is.null"
        "&select=email,lang,verification_token,verification_sent_at"
    )

    # Filter: not yet sent, OR sent more than 24 hours ago
    now = datetime.now(timezone.utc)
    to_send = []
    for sub in pending:
        sent_at = sub.get("verification_sent_at")
        if sent_at is None:
            to_send.append(sub)
        else:
            try:
                sent_dt = datetime.fromisoformat(sent_at.replace("Z", "+00:00"))
                hours_ago = (now - sent_dt).total_seconds() / 3600
                if hours_ago >= 24:
                    to_send.append(sub)
            except Exception:
                to_send.append(sub)

    if not to_send:
        print("No pending verifications.")
        return

    print(f"Sending verification emails to {len(to_send)} subscriber(s)…")

    for sub in to_send:
        email = sub["email"]
        token = sub["verification_token"]
        lang  = sub.get("lang") or "en"
        try:
            send_verification_email(email, token, lang)
            sb_patch(
                f"subscribers?email=eq.{urllib.parse.quote(email)}",
                {"verification_sent_at": datetime.now(timezone.utc).isoformat()}
            )
            print(f"  ✅ Sent to {email}")
        except Exception as exc:
            print(f"  ❌ Failed for {email}: {exc}")


if __name__ == "__main__":
    main()
