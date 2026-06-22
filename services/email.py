"""Email send helper for RezMyCV.

Wraps the Resend SDK in a thin, fault-tolerant layer so callers never have
to think about missing keys or transient API errors. Every function returns
True/False rather than raising, so a broken email path can't take down the
request flow that called it.

Env vars required (set on Railway):
  - RESEND_API_KEY              (re_...)
  - FROM_EMAIL                  (e.g. "RezMyCV <hello@rezmycv.com>")
"""
import os
import logging
from typing import Optional

log = logging.getLogger(__name__)

RESEND_API_KEY = os.getenv("RESEND_API_KEY", "")
FROM_EMAIL = os.getenv("FROM_EMAIL", "RezMyCV <hello@rezmycv.com>")


def _get_resend():
    """Lazy-import resend so a missing package doesn't break unrelated routes."""
    try:
        import resend
    except ImportError:
        log.warning("resend package not installed; install via 'pip install resend'")
        return None
    if not RESEND_API_KEY:
        log.warning("RESEND_API_KEY not set; cannot send email")
        return None
    resend.api_key = RESEND_API_KEY
    return resend


def is_configured() -> bool:
    """True if Resend is wired up. Used by routes to short-circuit cleanly."""
    return bool(RESEND_API_KEY) and _get_resend() is not None


def send_password_reset_email(to_email: str, reset_url: str, user_display_name: Optional[str] = None) -> bool:
    """Send the password reset email. Returns True on success, False on any failure.

    Same response regardless of whether the email exists in our system, so we
    don't leak which addresses are registered. (Callers should already be
    showing a neutral success message to the user.)
    """
    resend = _get_resend()
    if resend is None:
        log.warning("send_password_reset_email: Resend not configured, skipping %s", to_email)
        return False

    name = user_display_name or "there"
    subject = "Reset your RezMyCV password"
    html = f"""<div style="font-family: -apple-system, BlinkMacSystem, 'Segoe UI', sans-serif; max-width: 480px; margin: 0 auto; padding: 24px; color: #0a0a0a; background: #f8f8f5;">
  <div style="font-family: 'JetBrains Mono', ui-monospace, monospace; font-size: 14px; font-weight: 600; margin-bottom: 24px; color: #0a0a0a;">rezmycv<span style="color: #8a8a8a;">.com</span></div>
  <h1 style="font-size: 20px; font-weight: 600; margin: 0 0 16px;">Reset your password</h1>
  <p style="font-size: 15px; line-height: 1.6; color: #4a4a52; margin: 0 0 24px;">
    Hi {name}, someone (hopefully you) asked to reset the password for your RezMyCV account.
    Click the button below to choose a new one.
  </p>
  <p style="margin: 0 0 24px;">
    <a href="{reset_url}" style="display: inline-block; background: #0a0a0a; color: #f8f8f5; padding: 12px 20px; border-radius: 6px; text-decoration: none; font-weight: 600; font-size: 14px;">Reset password</a>
  </p>
  <p style="font-size: 13px; line-height: 1.6; color: #8a8a8a; margin: 0 0 8px;">
    The link expires in 1 hour and can only be used once.
  </p>
  <p style="font-size: 13px; line-height: 1.6; color: #8a8a8a; margin: 0 0 24px;">
    If the button doesn't work, paste this URL into your browser:
  </p>
  <p style="font-family: 'JetBrains Mono', ui-monospace, monospace; font-size: 12px; word-break: break-all; background: #fff; padding: 10px 12px; border-radius: 4px; color: #4a4a52; margin: 0 0 24px; border: 1px solid #e0e0db;">
    {reset_url}
  </p>
  <p style="font-size: 13px; line-height: 1.6; color: #8a8a8a; margin: 0 0 24px;">
    If you didn't request this, you can ignore the email — your password stays the same.
  </p>
  <hr style="border: none; border-top: 1px solid #e0e0db; margin: 24px 0;">
  <p style="font-size: 11px; color: #8a8a8a; margin: 0;">
    RezMyCV — Tailor your CV to the job. Beat the ATS.
  </p>
</div>"""

    try:
        resend.Emails.send({
            "from": FROM_EMAIL,
            "to": to_email,
            "subject": subject,
            "html": html,
        })
        log.info("password reset email sent to %s", to_email)
        return True
    except Exception as e:
        # Log the actual error for ops, but never leak to the user.
        log.exception("send_password_reset_email failed for %s: %s", to_email, e)
        return False
