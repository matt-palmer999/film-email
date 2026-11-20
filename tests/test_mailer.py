"""mailer.py: Microsoft 365 is the only route. Offline."""
import os
import sys
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import mailer  # noqa: E402

MS = {"MS_TENANT_ID": "t", "MS_CLIENT_ID": "c", "MS_CLIENT_SECRET": "s",
      "FROM_ADDRESS": "hello@whatson.movie", "FROM_NAME": "whatson.movie"}


class _Resp:
    def __init__(self, status, data=None, headers=None):
        self.status_code, self._data, self.headers, self.text = status, data or {}, headers or {}, ""

    def json(self):
        return self._data


class Routing(unittest.TestCase):
    def setUp(self):
        mailer._token.update(value=None, expires=0)

    def test_graph_used_when_all_three_settings_present(self):
        with mock.patch.dict(os.environ, MS, clear=True):
            self.assertTrue(mailer.using_graph())
            self.assertEqual(mailer.sender_address(), "hello@whatson.movie")
        with mock.patch.dict(os.environ, {**MS, "MS_CLIENT_SECRET": ""}, clear=True):
            self.assertFalse(mailer.using_graph())

    def test_ms_sender_overrides_from_address(self):
        with mock.patch.dict(os.environ, {**MS, "MS_SENDER": "news@whatson.movie"}, clear=True):
            self.assertEqual(mailer.sender_address(), "news@whatson.movie")

    def test_graph_send_builds_html_message(self):
        calls = []

        def fake_post(url, **kw):
            calls.append((url, kw))
            if "oauth2" in url:
                return _Resp(200, {"access_token": "tok", "expires_in": 3600})
            return _Resp(202)

        with mock.patch.dict(os.environ, MS, clear=True), mock.patch.object(mailer.requests, "post", fake_post):
            mailer.send_email("a@example.com", "Hi", "plain", html="<b>hi</b>")
        url, kw = calls[-1]
        self.assertTrue(url.endswith("/users/hello@whatson.movie/sendMail"))
        msg = kw["json"]["message"]
        self.assertEqual(msg["body"]["contentType"], "HTML")
        self.assertEqual(msg["toRecipients"][0]["emailAddress"]["address"], "a@example.com")
        self.assertEqual(kw["headers"]["Authorization"], "Bearer tok")

    def test_graph_error_is_raised_with_reason(self):
        def fake_post(url, **kw):
            if "oauth2" in url:
                return _Resp(200, {"access_token": "tok"})
            return _Resp(403, {"error": {"code": "ErrorAccessDenied", "message": "no"}})

        with mock.patch.dict(os.environ, MS, clear=True), mock.patch.object(mailer.requests, "post", fake_post):
            with self.assertRaisesRegex(RuntimeError, "ErrorAccessDenied"):
                mailer.send_email("a@example.com", "Hi", "plain")

    def test_no_fallback_without_microsoft_365(self):
        # SMTP settings alone no longer send anything (AuthSMTP removed 2026-11-20)
        env = {"SMTP_HOST": "smtp.example.com", "SMTP_USER": "u", "SMTP_PASSWORD": "p",
               "FROM_ADDRESS": "hello@whatson.movie"}
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertFalse(mailer.is_configured())
            with self.assertRaisesRegex(RuntimeError, "not configured"):
                mailer.send_email(["a@example.com"], "Hi", "plain")

    def test_no_smtplib_left_in_the_senders(self):
        # Every email must go through mailer.py, so a route change happens in one place
        for f in ("mailer.py", "pipeline.py", "scraper.py", "send_report.py", "send_test_email.py"):
            with open(os.path.join(ROOT, f), encoding="utf-8") as fh:
                self.assertNotIn("smtplib", fh.read(), f)


if __name__ == "__main__":
    unittest.main()
