"""Transactional email — one swappable `send_email()` over SMTP (aiosmtplib).

Dev points SMTP_HOST at the Mailpit container (UI on http://localhost:8025); prod points it
at a provider (Resend / Postmark / Brevo / SES). No provider SDK — SMTP keeps it portable.
"""
from __future__ import annotations

import html
import logging
from email.message import EmailMessage

import aiosmtplib

from app.config import settings

log = logging.getLogger("kappacut.email")

_SHELL = """\
<div style="font-family:ui-sans-serif,system-ui,sans-serif;max-width:520px;margin:0 auto;
            color:#1b1922;line-height:1.6">
  <div style="font-weight:700;font-size:18px;letter-spacing:-.02em;
              background:linear-gradient(96deg,#5f4ee0,#0c8b99);-webkit-background-clip:text;
              background-clip:text;color:transparent">Kappacut</div>
  <div style="margin-top:16px">{body}</div>
  {footer}
</div>"""
_IGNORE_ME = """<p style="margin-top:28px;font-size:12px;color:#8b8797">
    If you didn't request this, you can ignore this email.</p>"""


def _button(href: str, label: str) -> str:
    return (
        f'<a href="{href}" style="display:inline-block;margin:8px 0;padding:11px 20px;'
        f'background:#5f4ee0;color:#fff;text-decoration:none;border-radius:8px;'
        f'font-weight:600;font-size:14px">{label}</a>'
        f'<p style="font-size:12px;color:#8b8797;word-break:break-all">{href}</p>'
    )


async def send_email(to: str, subject: str, html_body: str, *, ignorable: bool = True) -> None:
    """`ignorable=False` for mail about something that happens whether they asked or not: "you
    can ignore this" would be wrong under a deletion warning or a copyright removal."""
    msg = EmailMessage()
    msg["From"] = settings.smtp_from
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content("This message needs an HTML-capable email client.")
    msg.add_alternative(_SHELL.format(body=html_body, footer=_IGNORE_ME if ignorable else ""), subtype="html")

    await aiosmtplib.send(
        msg,
        hostname=settings.smtp_host,
        port=settings.smtp_port,
        username=settings.smtp_user or None,
        password=settings.smtp_password or None,
        # SMTP_TLS=true: implicit TLS (port 465). Otherwise STARTTLS whenever the server offers
        # it (port 587) - forcing start_tls=False here meant a 587 provider got the password
        # in plain text or refused AUTH, and every verification email was lost.
        use_tls=settings.smtp_tls,
        start_tls=None,
    )


async def send_verify_email(to: str, token: str) -> None:
    link = f"{settings.public_base_url.rstrip('/')}/verify?token={token}"
    body = (
        "<p>Welcome. Confirm your email address to start making clips:</p>"
        + _button(link, "Verify email")
    )
    try:
        await send_email(to, "Verify your Kappacut account", body)
    except Exception as e:  # noqa: BLE001 — never fail registration on a mail hiccup
        log.warning("verify email to %s failed: %s", to, e)


async def send_reset_email(to: str, token: str) -> None:
    link = f"{settings.public_base_url.rstrip('/')}/reset?token={token}"
    body = (
        "<p>Someone asked to reset your Kappacut password. "
        "If it was you, choose a new one:</p>" + _button(link, "Reset password")
    )
    try:
        await send_email(to, "Reset your Kappacut password", body)
    except Exception as e:  # noqa: BLE001
        log.warning("reset email to %s failed: %s", to, e)


async def send_invite_email(to: str) -> None:
    link = f"{settings.public_base_url.rstrip('/')}/register"
    body = ("<p>You're invited to try Kappacut, which finds the best moments in a long video and "
            "cuts them into short clips. Sign up with this email address:</p>"
            + _button(link, "Create your account"))
    try:
        await send_email(to, "You're invited to Kappacut", body)
    except Exception as e:  # noqa: BLE001 — the invite stands; the admin can tell them another way
        log.warning("invite email to %s failed: %s", to, e)


async def send_takedown_notice(to: str, project_name: str, reference: str) -> None:
    """Sent when a project is removed for copyright: they have to be told, and told how to
    dispute it. The name and reference are escaped for the same reason as below."""
    contact = settings.copyright_email or settings.support_email
    body = (f"<p>We received a copyright notice about your project "
            f"<b>{html.escape(project_name)}</b>, and we've removed it and its clips.</p>"
            f"<p>Notice reference: {html.escape(reference)}</p>"
            f"<p>If you own this material or have permission to use it, you can send a counter-notice. "
            f"See our <a href=\"{settings.public_base_url.rstrip('/')}/copyright\">copyright page</a>"
            + (f" or write to {html.escape(contact)}" if contact else "") + ".</p>")
    try:
        await send_email(to, "Kappacut: a project was removed after a copyright notice", body, ignorable=False)
    except Exception as e:  # noqa: BLE001
        log.warning("takedown notice to %s failed: %s", to, e)


async def send_suspension_notice(to: str, reason: str) -> None:
    """The Terms promise to say why an account was suspended."""
    contact = settings.support_email
    body = (f"<p>Your Kappacut account has been suspended, so you can't sign in and any jobs you had "
            f"running have been stopped.</p>"
            f"<p>Reason: {html.escape(reason)}</p>"
            + (f"<p>If you think this is a mistake, write to {html.escape(contact)}.</p>" if contact else ""))
    try:
        await send_email(to, "Kappacut: your account has been suspended", body, ignorable=False)
    except Exception as e:  # noqa: BLE001
        log.warning("suspension notice to %s failed: %s", to, e)


async def send_reinstated_notice(to: str) -> None:
    body = "<p>Your Kappacut account is active again, and you can sign in as usual.</p>"
    try:
        await send_email(to, "Kappacut: your account is active again", body, ignorable=False)
    except Exception as e:  # noqa: BLE001
        log.warning("reinstated notice to %s failed: %s", to, e)


async def send_retention_warning(to: str, project_name: str, days_left: int) -> None:
    # the name is whatever the user typed: escaped, it can't put links or markup in our mail
    body = (
        f"<p>Your project <b>{html.escape(project_name)}</b> has been inactive for a while. "
        f"It and its clips will be deleted in about <b>{days_left} day(s)</b> to free up space.</p>"
        f"<p>Open it in Kappacut to keep it.</p>"
        + _button(settings.public_base_url.rstrip('/') + "/app", "Open Kappacut")
    )
    try:
        subject_name = " ".join(project_name.split())   # no line breaks in a mail header
        await send_email(to, f"Kappacut: “{subject_name}” expires soon", body, ignorable=False)
    except Exception as e:  # noqa: BLE001
        log.warning("retention warning to %s failed: %s", to, e)
