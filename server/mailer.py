"""Outgoing email over SMTP (Gmail by default), with a console fallback for local use.

Check your settings with ``python -m server.mailer you@example.com``.
"""

from __future__ import annotations

import logging
import smtplib
import ssl
import sys
from email.message import EmailMessage
from email.utils import parseaddr

from config.settings import get_settings

logger = logging.getLogger(__name__)


class MailError(RuntimeError):
    """Raised when an email could not be handed to the SMTP server."""


def delivery_mode() -> str:
    """``"smtp"`` when SMTP is configured, otherwise ``"console"``."""
    return "smtp" if get_settings().email_enabled else "console"


def send_email(to: str, subject: str, text: str, html: str | None = None) -> None:
    """Send one email, or print it to the server console when SMTP isn't configured."""
    settings = get_settings()
    if not settings.email_enabled:
        logger.warning(
            "\n"
            + "=" * 64
            + "\nEMAIL (development mode - SMTP is not configured)\nTo: %s\nSubject: %s\n\n%s\n"
            + "=" * 64,
            to,
            subject,
            text,
        )
        return

    sender = settings.smtp_from or settings.smtp_username or ""
    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(text)
    if html:
        msg.add_alternative(html, subtype="html")

    password = settings.smtp_password.get_secret_value().replace(" ", "") if settings.smtp_password else ""
    context = ssl.create_default_context()
    try:
        if settings.smtp_port == 465:
            with smtplib.SMTP_SSL(settings.smtp_host or "", settings.smtp_port, context=context, timeout=20) as smtp:
                smtp.login(settings.smtp_username or "", password)
                smtp.send_message(msg)
        else:
            with smtplib.SMTP(settings.smtp_host or "", settings.smtp_port, timeout=20) as smtp:
                smtp.starttls(context=context)
                smtp.login(settings.smtp_username or "", password)
                smtp.send_message(msg)
    except (smtplib.SMTPException, OSError) as exc:
        logger.exception("Sending email to %s failed", to)
        raise MailError(str(exc)) from exc


def _layout(heading: str, intro: str, code: str, minutes: int, footer: str) -> str:
    return f"""<!doctype html><html><body style="margin:0;background:#f6f7fb;font-family:Arial,Helvetica,sans-serif;color:#111827">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr><td align="center" style="padding:32px 16px">
<table role="presentation" width="100%" style="max-width:480px;background:#ffffff;border-radius:14px;border:1px solid #e5e7eb" cellpadding="0" cellspacing="0">
<tr><td style="padding:28px 32px 0;font-size:20px;font-weight:800;letter-spacing:2px;color:#0f766e">VaaniSetu AI</td></tr>
<tr><td style="padding:16px 32px 0"><h1 style="margin:0;font-size:22px;line-height:1.3">{heading}</h1>
<p style="margin:12px 0 0;font-size:15px;line-height:1.6;color:#374151">{intro}</p></td></tr>
<tr><td align="center" style="padding:24px 32px">
<div style="display:inline-block;padding:14px 22px;border-radius:12px;background:#e6f4f1;color:#115e59;font-size:32px;font-weight:800;letter-spacing:8px">{code}</div>
<p style="margin:12px 0 0;font-size:13px;color:#6b7280">This code expires in {minutes} minutes.</p></td></tr>
<tr><td style="padding:0 32px 28px;font-size:13px;line-height:1.6;color:#6b7280">{footer}</td></tr>
</table></td></tr></table></body></html>"""


def send_code_email(to: str, name: str, code: str, purpose: str) -> None:
    """Send a verification or password-reset code."""
    minutes = get_settings().email_code_minutes
    first = (name.split() or ["there"])[0]
    if purpose == "verify":
        subject = f"{code} is your VaaniSetu AI verification code"
        intro = f"Hi {first}, enter this code in VaaniSetu AI to confirm your email address."
        footer = "If you didn't create a VaaniSetu AI account, you can ignore this email."
        heading = "Confirm your email"
    else:
        subject = f"{code} is your VaaniSetu AI password reset code"
        intro = f"Hi {first}, enter this code in VaaniSetu AI to choose a new password."
        footer = "If you didn't ask to reset your password, you can ignore this email. Your password stays the same."
        heading = "Reset your password"
    text = f"{heading}\n\n{intro}\n\nYour code: {code}\n\nIt expires in {minutes} minutes.\n\n{footer}\n"
    send_email(to, subject, text, _layout(heading, intro, code, minutes, footer))


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    if len(sys.argv) != 2 or "@" not in parseaddr(sys.argv[1])[1]:
        raise SystemExit("Usage: python -m server.mailer you@example.com")
    if not get_settings().email_enabled:
        raise SystemExit("Email isn't set up yet. Fill in the SMTP_ values in .env first.")
    to = sys.argv[1]
    text = "Your VaaniSetu AI email settings work. Verification codes and password resets will be delivered."
    html = f'<p style="font-family:Arial,Helvetica,sans-serif;font-size:15px">{text}</p>'
    try:
        send_email(to, "VaaniSetu AI email is set up", text, html)
    except MailError as exc:
        raise SystemExit(f"Sending failed: {exc}") from exc
    print(f"Email sent to {to}. Check the inbox (and the spam folder).")


if __name__ == "__main__":
    main()
