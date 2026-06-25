import os
import secrets
from datetime import datetime, timezone, timedelta
from supabase import create_client, Client

SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")

_supabase_client: Client = None
_supabase_admin_client: Client = None


def get_client() -> Client:
    """Backend Supabase client. Uses service_role (RLS bypass) so server-side
    writes to user_cvs / job_descriptions / events / profiles aren't blocked
    by RLS policies that require auth.uid()=user_id (which is null for the
    server context).

    The anon key fallback below is for backwards-compat with deployments
    that only set SUPABASE_KEY — but anon-key writes WILL FAIL after the
    RLS lockdown migrations (008 + 009). Make sure SUPABASE_SERVICE_ROLE_KEY
    is set on Railway.
    """
    global _supabase_client
    if _supabase_client is None:
        key = (
            os.getenv("SUPABASE_SERVICE_ROLE_KEY")
            or os.getenv("SUPABASE_KEY")
            or ""
        )
        _supabase_client = create_client(SUPABASE_URL, key)
    return _supabase_client


def get_admin_client() -> Client:
    """Service-role Supabase client. Bypasses RLS.

    Use only for backend operations the user can't do themselves, e.g. updating
    someone else's password after a verified token, or writing to tables that
    explicitly deny the public role.
    """
    global _supabase_admin_client
    if _supabase_admin_client is None:
        if not SUPABASE_SERVICE_ROLE_KEY:
            raise RuntimeError(
                "SUPABASE_SERVICE_ROLE_KEY not set on the server. Add it to Railway env."
            )
        _supabase_admin_client = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)
    return _supabase_admin_client


def sign_up(email: str, password: str) -> dict:
    """Sign up a new user. Creates profile row on success."""
    client = get_client()
    # Check if user already exists (workaround: try to sign in first)
    try:
        auth_result = client.auth.sign_up({"email": email, "password": password})
        if auth_result.user:
            # Create profile row for new user.
            # Logged at info level so silent failures are visible in production.
            try:
                client.table("profiles").insert({
                    "user_id": auth_result.user.id,
                    "tier": "free",
                    "cv_count": 0
                }).execute()
                print(f"[AUTH] sign_up: profile created for user_id={auth_result.user.id}")
            except Exception as profile_err:
                # Common cause: profile already exists from trigger or earlier attempt.
                # Log loudly so it's visible if the row really should have been created.
                import sys
                print(f"[AUTH] sign_up: profile insert error for user_id={auth_result.user.id}: "
                      f"{type(profile_err).__name__}: {profile_err}", file=sys.stderr)
            return {"user": auth_result.user, "session": auth_result.session}
        return {"error": "Signup failed"}
    except Exception as e:
        return {"error": str(e)}


def sign_in(email: str, password: str) -> dict:
    """Sign in existing user."""
    client = get_client()
    try:
        auth_result = client.auth.sign_in_with_password({"email": email, "password": password})
        if auth_result.user:
            return {"user": auth_result.user, "session": auth_result.session}
        return {"error": "Invalid credentials"}
    except Exception as e:
        return {"error": str(e)}


def sign_out():
    """Sign out current user."""
    client = get_client()
    client.auth.sign_out()


def get_user(token: str = None):
    """Get current user from session token."""
    client = get_client()
    if token:
        client.auth.set_session(token, token)
    return client.auth.get_user()


def get_profile(user_id: str) -> dict:
    """Get user profile."""
    client = get_client()
    try:
        result = client.table("profiles").select("*").eq("user_id", user_id).execute()
        if result.data:
            return result.data[0]
        return None
    except Exception:
        return None


def get_or_create_profile(user_id: str) -> dict:
    """Get existing profile or create default one."""
    profile = get_profile(user_id)
    if profile:
        return profile
    client = get_client()
    try:
        result = client.table("profiles").insert({
            "user_id": user_id,
            "tier": "free",
            "cv_count": 0
        }).execute()
        if result.data:
            return result.data[0]
    except Exception:
        pass
    return {"user_id": user_id, "tier": "free", "cv_count": 0}


def increment_cv_count(user_id: str) -> dict:
    """Increment CV count for user. Returns updated profile."""
    client = get_client()
    profile = get_profile(user_id)
    if not profile:
        return None
    new_count = profile.get("cv_count", 0) + 1
    try:
        result = client.table("profiles").update({
            "cv_count": new_count,
            "updated_at": "now()"
        }).eq("user_id", user_id).execute()
        if result.data:
            return result.data[0]
    except Exception:
        pass
    return {**profile, "cv_count": new_count}


def can_generate_cv(user_id: str) -> tuple:
    """
    Check if user can generate a CV.
    Returns (allowed: bool, reason: str, profile: dict)
    """
    profile = get_or_create_profile(user_id)
    tier = profile.get("tier", "free")
    cv_count = profile.get("cv_count", 0)

    if tier in ("pro", "pro_plus"):
        return True, "", profile
    if cv_count >= 10:
        return False, "upgrade", profile
    return True, "", profile


def update_tier(user_id: str, tier: str) -> dict:
    """Update user's subscription tier."""
    client = get_client()
    try:
        result = client.table("profiles").update({
            "tier": tier,
            "updated_at": "now()"
        }).eq("user_id", user_id).execute()
        if result.data:
            return result.data[0]
    except Exception:
        pass
    return None


# ---------------------------------------------------------------------------
# Password reset tokens (custom Resend flow, replaces Supabase's default reset)
# ---------------------------------------------------------------------------
#
# Flow:
#   1. User hits /auth/forgot-password, enters email.
#   2. We look up the user, mint a token, save to password_reset_tokens.
#   3. We send the token in an email via Resend.
#   4. User clicks the link -> /auth/reset-password/<token>.
#   5. They enter a new password. We mark the token used AND update the
#      password via Supabase admin API (since the user isn't logged in).

RESET_TOKEN_TTL_HOURS = 1


def create_password_reset_token(email: str) -> dict:
    """Create a reset token for the given email, if the user exists.

    Returns {"user_id": str, "token": str} on success, or None if no user.
    Caller decides what to do with None (usually: still show the same neutral
    "check your email" message to avoid leaking which addresses are registered).
    """
    user_client = get_client()
    try:
        # Look up user by email via the admin client (anon can't list users).
        admin = get_admin_client()
        # admin.list_users() paginates; for a single user by email, fetch all and
        # match. Acceptable for a low-traffic reset path; the table isn't huge.
        # If this becomes a hotspot, switch to a server-side function or RPC.
        users_res = admin.auth.admin.list_users()
        target = None
        for u in users_res:
            if (u.email or "").lower() == email.lower():
                target = u
                break
        if target is None:
            return None

        # Mint a 43-char URL-safe token. Storing as text, not uuid, so it's
        # already the link component.
        token = secrets.token_urlsafe(32)
        expires_at = datetime.now(timezone.utc) + timedelta(hours=RESET_TOKEN_TTL_HOURS)

        # Use admin client to insert (the public schema denies anon/authenticated
        # on this table; only service_role can write).
        admin.table("password_reset_tokens").insert({
            "user_id": target.id,
            "token": token,
            "expires_at": expires_at.isoformat(),
        }).execute()

        return {"user_id": target.id, "token": token}
    except Exception as e:
        import sys
        print(f"[AUTH] create_password_reset_token error: {type(e).__name__}: {e}", file=sys.stderr)
        return None


def verify_password_reset_token(token: str) -> dict | None:
    """Check that a token exists, isn't used, and isn't expired.

    Returns the row (with user_id) if valid, else None.
    Does NOT consume the token -- caller calls mark_reset_token_used() after the
    password has actually been updated.
    """
    if not token:
        return None
    try:
        admin = get_admin_client()
        res = admin.table("password_reset_tokens").select(
            "id, user_id, expires_at, used_at, created_at"
        ).eq("token", token).limit(1).execute()
        rows = res.data or []
        if not rows:
            return None
        row = rows[0]
        if row.get("used_at"):
            return None  # already consumed
        expires = datetime.fromisoformat(row["expires_at"].replace("Z", "+00:00"))
        if expires < datetime.now(timezone.utc):
            return None
        return row
    except Exception as e:
        import sys
        print(f"[AUTH] verify_password_reset_token error: {type(e).__name__}: {e}", file=sys.stderr)
        return None


def consume_password_reset_token(token: str, new_password: str) -> bool:
    """Mark token used and update the user's password.

    Returns True if both operations succeeded. False on any failure (token
    is NOT marked used if the password update fails -- so a failed attempt can
    be retried with the same link).
    """
    row = verify_password_reset_token(token)
    if not row:
        return False
    if len(new_password) < 6:
        return False
    try:
        admin = get_admin_client()
        # 1) Update the password in Supabase auth.
        admin.auth.admin.update_user_by_id(
            row["user_id"],
            {"password": new_password},
        )
        # 2) Mark the token consumed.
        admin.table("password_reset_tokens").update({
            "used_at": datetime.now(timezone.utc).isoformat(),
        }).eq("id", row["id"]).execute()
        return True
    except Exception as e:
        import sys
        print(f"[AUTH] consume_password_reset_token error: {type(e).__name__}: {e}", file=sys.stderr)
        return False