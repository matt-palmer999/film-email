"""One place to send email from, for every script.

Sends through Microsoft 365 (Graph API, app sign-in). There is no other route:
the old AuthSMTP fallback was removed on 2026-11-20.

Settings (in .env, and as GitHub secrets for the Actions workflows):
    MS_TENANT_ID, MS_CLIENT_ID, MS_CLIENT_SECRET   Entra app "whatson.movie mailer"
                                                   (Graph application permission Mail.Send)
    MS_SENDER      optional; the mailbox to send from (defaults to FROM_ADDRESS)
    FROM_ADDRESS   sender address; FROM_NAME display name
"""
from __future__ import annotations

import logging
import os
import time

import requests

log = logging.getLogger(__name__)

_token: dict = {"value": None, "expires": 0.0}


def _env(name: str, default: str = "") -> str:
    return (os.environ.get(name) or default).strip()


def using_graph() -> bool:
    return all(_env(k) for k in ("MS_TENANT_ID", "MS_CLIENT_ID", "MS_CLIENT_SECRET"))


def sender_address() -> str:
    return _env("MS_SENDER") or _env("FROM_ADDRESS")


def is_configured() -> bool:
    return using_graph() and bool(sender_address())


def route_name() -> str:
    return "Microsoft 365"


def _graph_token() -> str:
    if _token["value"] and time.time() < _token["expires"] - 60:
        return _token["value"]
    r = requests.post(
        f"https://login.microsoftonline.com/{_env('MS_TENANT_ID')}/oauth2/v2.0/token",
        data={
            "client_id": _env("MS_CLIENT_ID"),
            "client_secret": _env("MS_CLIENT_SECRET"),
            "scope": "https://graph.microsoft.com/.default",
            "grant_type": "client_credentials",
        },
        timeout=30,
    )
    if r.status_code != 200:
        try:
            err = r.json()
            detail = f"{err.get('error')}: {(err.get('error_description') or '').splitlines()[0]}"
        except Exception:
            detail = r.text[:200]
        raise RuntimeError(f"Microsoft sign-in failed ({r.status_code}) — {detail}")
    data = r.json()
    _token["value"] = data["access_token"]
    _token["expires"] = time.time() + int(data.get("expires_in", 3600))
    return _token["value"]


def _send_graph(to: list[str], subject: str, text: str, html: str | None,
                reply_to: str | None, headers: dict | None) -> None:
    message: dict = {
        "subject": subject,
        "body": {"contentType": "HTML" if html else "Text", "content": html or text},
        "toRecipients": [{"emailAddress": {"address": a}} for a in to],
        "from": {"emailAddress": {"address": sender_address(), "name": _env("FROM_NAME", "whatson.movie")}},
    }
    if reply_to:
        message["replyTo"] = [{"emailAddress": {"address": reply_to}}]
    if headers:
        # Graph only allows custom headers starting with X-; List-Unsubscribe etc. are dropped
        xh = [{"name": k, "value": v} for k, v in headers.items() if k.lower().startswith("x-")]
        if xh:
            message["internetMessageHeaders"] = xh
    url = f"https://graph.microsoft.com/v1.0/users/{sender_address()}/sendMail"
    for attempt in range(4):
        r = requests.post(url, json={"message": message, "saveToSentItems": False},
                          headers={"Authorization": f"Bearer {_graph_token()}"}, timeout=30)
        if r.status_code == 202:
            return
        if r.status_code in (429, 503, 504) and attempt < 3:
            wait = int(r.headers.get("Retry-After", "0") or 0) or 5 * (attempt + 1)
            log.warning(f"Microsoft 365 is throttling sends — waiting {wait}s")
            time.sleep(wait)
            continue
        try:
            err = r.json().get("error", {})
            detail = f"{err.get('code')}: {err.get('message')}"
        except Exception:
            detail = r.text[:200]
        raise RuntimeError(f"Microsoft 365 send failed ({r.status_code}) — {detail}")


def send_email(to: list[str] | str, subject: str, text: str = "", html: str | None = None,
               reply_to: str | None = None, headers: dict | None = None) -> None:
    """Send one message. Raises on failure (callers decide whether that's fatal)."""
    if isinstance(to, str):
        to = [to]
    to = [a for a in to if a]
    if not to:
        raise ValueError("no recipients")
    if not is_configured():
        raise RuntimeError("email not configured (needs MS_TENANT_ID, MS_CLIENT_ID, "
                           "MS_CLIENT_SECRET and FROM_ADDRESS or MS_SENDER)")
    _send_graph(to, subject, text, html, reply_to, headers)
