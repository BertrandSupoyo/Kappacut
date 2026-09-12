"""Transactional email — one swappable `send_email()` over SMTP (aiosmtplib).

Dev points SMTP_HOST at the Mailpit container (UI on http://localhost:8025); prod points it
at a provider (Resend / Postmark / Brevo / SES). No provider SDK — SMTP keeps it portable.
"""
from __future__ import annotations

import logging
from email.message import EmailMessage

import aiosmtplib

from app.config import settings

log = logging.getLogger("clipfinder.email")

_SHELL = """\
<div style="font-family:ui-sans-serif,system-ui,sans-serif;max-width:520px;margin:0 auto;
            color:#1b1922;line-height:1.6">
  <div style="font-weight:700;font-size:18px;letter-spacing:-.02em;
              background:linear-gradient(96deg,#5f4ee0,#0c8b99);-webkit-background-clip:text;
              background-clip:text;color:transparent">clipfinder</div>
  <div style="margin-top:16px">{body}</div>
  <p style="margin-top:28px;font-size:12px;color:#8b8797">
    If you didn't request this, you can ignore this email.</p>
</div>"""


def _button(href: str, label: str) -> str:
    return (
        f'<a href="{href}" style="display:inline-block;margin:8px 0;padding:11px 20px;'
        f'background:#5f4ee0;color:#fff;text-decoration:none;border-radius:8px;'
        f'font-weight:600;font-size:14px">{label}</a>'
        f'<p style="font-size:12px;color:#8b8797;word-break:break-all">{href}</p>'
    )


async def send_email(to: str, subject: str, html_body: str) -> None:
    msg = EmailMessage()
    msg["From"] = settings.smtp_from
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content("This message needs an HTML-capable email client.")
    msg.add_alternative(_SHELL.format(body=html_body), subtype="html")

    await aiosmtplib.send(
        msg,
        hostname=settings.smtp_host,
        port=settings.smtp_port,
        username=settings.smtp_user or None,
        password=settings.smtp_password or None,
        use_tls=settings.smtp_tls,
        start_tls=False if not settings.smtp_tls else None,
    )


async def send_verify_email(to: str, token: str) -> None:
    link = f"{settings.public_base_url.rstrip('/')}/verify?token={token}"
    body = (
        "<p>Welcome. Confirm your email address to start making clips:</p>"
        + _button(link, "Verify email")
    )
    try:
        await send_email(to, "Verify your clipfinder account", body)
    except Exception as e:  # noqa: BLE001 — never fail registration on a mail hiccup
        log.warning("verify email to %s failed: %s", to, e)


async def send_reset_email(to: str, token: str) -> None:
    link = f"{settings.public_base_url.rstrip('/')}/reset?token={token}"
    body = (
        "<p>Someone asked to reset your clipfinder password. "
        "If it was you, choose a new one:</p>" + _button(link, "Reset password")
    )
    try:
        await send_email(to, "Reset your clipfinder password", body)
    except Exception as e:  # noqa: BLE001
        log.warning("reset email to %s failed: %s", to, e)


async def send_retention_warning(to: str, project_name: str, days_left: int) -> None:
    body = (
        f"<p>Your project <b>{project_name}</b> has been inactive for a while. "
        f"It and its clips will be deleted in about <b>{days_left} day(s)</b> to free up space.</p>"
        f"<p>Open it in clipfinder to keep it.</p>"
        + _button(settings.public_base_url.rstrip('/') + "/app", "Open clipfinder")
    )
    try:
        await send_email(to, f"clipfinder: “{project_name}” expires soon", body)
    except Exception as e:  # noqa: BLE001
        log.warning("retention warning to %s failed: %s", to, e)
