"""Email notifications via SMTP (Gmail by default). Fails soft: never crashes the app."""
from __future__ import annotations

import smtplib
from email.message import EmailMessage

import config


def email_configured() -> bool:
    return bool(config.get_secret("SMTP_USER") and config.get_secret("SMTP_PASSWORD"))


def send_email(to: str, subject: str, body: str) -> tuple[bool, str]:
    if not to:
        return False, "no recipient set"
    if not email_configured():
        return False, "SMTP_USER / SMTP_PASSWORD not configured"
    user = config.get_secret("SMTP_USER")
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = user, to, subject
    msg.set_content(body)
    try:
        host = config.get_secret("SMTP_HOST", "smtp.gmail.com")
        port = int(config.get_secret("SMTP_PORT", "465"))
        with smtplib.SMTP_SSL(host, port, timeout=15) as s:
            s.login(user, config.get_secret("SMTP_PASSWORD"))
            s.send_message(msg)
        return True, "sent"
    except Exception as e:  # network/auth errors
        return False, f"{type(e).__name__}: {e}"
