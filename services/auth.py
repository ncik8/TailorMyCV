import os
from supabase import create_client, Client

SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")

_supabase_client: Client = None


def get_client() -> Client:
    global _supabase_client
    if _supabase_client is None:
        _supabase_client = create_client(SUPABASE_URL, SUPABASE_KEY)
    return _supabase_client


def sign_up(email: str, password: str) -> dict:
    """Sign up a new user. Creates profile row on success."""
    client = get_client()
    # Check if user already exists (workaround: try to sign in first)
    try:
        auth_result = client.auth.sign_up({"email": email, "password": password})
        if auth_result.user:
            # Create profile row for new user
            try:
                client.table("profiles").insert({
                    "user_id": auth_result.user.id,
                    "tier": "free",
                    "cv_count": 0
                })
            except Exception:
                pass  # Profile may already exist from trigger
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