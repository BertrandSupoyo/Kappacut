"""What the public pages need to know about this deployment: who runs it, who may sign up, and
which outside companies handle users' data.

The Privacy Policy reads this instead of hard-coding it, so turning on a feature or changing
provider can't leave the page describing a different service from the one running.
"""
from __future__ import annotations

from fastapi import APIRouter

from app.config import TERMS_VERSION, settings

router = APIRouter(prefix="/api", tags=["public"])


def _provider(address: str, known: dict[str, str], fallback: str) -> str:
    return next((name for needle, name in known.items() if needle in address.lower()), fallback)


def processors(cfg: dict) -> list[dict]:
    """Every outside company that handles user data here, and exactly what it gets. `cfg` is
    the engine config: Claude only sees anything if the deployment chose it for picking or for
    its vision pass, and merely holding an API key doesn't send it data."""
    out = [
        {"name": settings.hosting_provider, "gets": "Everything on our server: your account, projects and transcripts."},
        {"name": _provider(settings.s3_endpoint_url, {"r2.cloudflarestorage": "Cloudflare R2"}, "Our storage provider"),
         "gets": "Your uploaded videos, rendered clips, thumbnails and sound effects."},
        {"name": _provider(settings.smtp_host, {"resend": "Resend"}, "Our email provider"),
         "gets": "Your email address and the messages we send you."},
        {"name": "Groq (United States)",
         "gets": "The audio from your video, to transcribe it, and the transcript, to pick clips. "
                 "Groq says it doesn't keep this by default, apart from temporary logs of up to "
                 "30 days when it's troubleshooting or investigating abuse."},
    ]
    if cfg.get("backend") == "claude" or cfg.get("vision"):
        gets = "The transcript, to pick clips." if cfg.get("backend") == "claude" else ""
        if cfg.get("vision"):
            gets = f"{gets} One still frame from each suggested clip.".strip()
        out.append({"name": "Anthropic (United States)", "gets": gets})
    thumbnails = [n for n, key in (("Together AI", settings.together_api_key),
                                   ("Replicate", settings.replicate_api_key)) if key]
    if thumbnails:
        out.append({"name": " and ".join(thumbnails) + " (United States)",
                    "gets": "The title of a clip, only when you ask for an AI thumbnail."})
    return out


@router.get("/config")
async def public_config() -> dict:
    import pipeline                     # local import: the engine is heavy, and tests here don't need it

    return {
        "invite_only": settings.invite_only,
        "support_email": settings.support_email,
        "copyright_email": settings.copyright_email or settings.support_email,
        "legal": {
            "name": settings.legal_name,
            "address": settings.legal_address,
            "jurisdiction": settings.legal_jurisdiction,
            "eu_representative": settings.legal_eu_representative,
            "reviewed": settings.legal_reviewed,
            "version": TERMS_VERSION,
        },
        "processors": processors(pipeline.get_cfg()),
        "retention": {
            "warn_days": settings.retention_warn_days,
            "delete_days": settings.retention_delete_days,
            "unverified_days": settings.retention_unverified_days,
            "backup_days": settings.backup_keep,
        },
    }
