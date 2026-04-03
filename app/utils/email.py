"""
Email utility for Sentinel.

Uses dev-mode logging by default. Set EMAIL_SEND_ENABLED=true and configure
a real email provider (e.g. Resend, SendGrid) for production.

Environment variables:
    FRONTEND_URL  — e.g. https://app.sentinel.ai (no trailing slash)
"""
import logging
import os

logger = logging.getLogger(__name__)

_FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost:3000")


def send_invite_email(
    *,
    recipient_email: str,
    invited_by_name: str,
    token: str,
    role: str,
) -> None:
    """
    Send a team invitation email to ``recipient_email``.

    In dev/CI this logs the invite link instead of sending.
    Replace the logger call with your email SDK for production.
    """
    invite_url = f"{_FRONTEND_URL}/auth/accept-invite?token={token}"

    if os.getenv("EMAIL_SEND_ENABLED", "false").lower() != "true":
        logger.info(
            "INVITE EMAIL (dev mode — not sent)\n"
            "  To:      %s\n"
            "  Role:    %s\n"
            "  From:    %s\n"
            "  Link:    %s",
            recipient_email,
            role,
            invited_by_name,
            invite_url,
        )
        return

    # Production: integrate real email SDK here
    raise NotImplementedError(
        "Set EMAIL_SEND_ENABLED=true and configure a real email provider."
    )
