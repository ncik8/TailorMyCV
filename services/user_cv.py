"""
Persist user CV data to Supabase (user_cvs table).
Also handles uploading raw PDF/DOCX to Supabase Storage.
"""
import os
import json
from supabase import create_client, Client

SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")

_supabase_client: Client = None


def get_client() -> Client:
    global _supabase_client
    if _supabase_client is None:
        _supabase_client = create_client(SUPABASE_URL, SUPABASE_KEY)
    return _supabase_client


def save_cv(user_id: str, cv_data: dict) -> dict | None:
    """
    Upsert parsed CV data for a user. Replaces existing CV.
    Returns the saved row or None on failure.
    """
    client = get_client()
    try:
        result = client.table("user_cvs").upsert({
            "user_id": user_id,
            "cv_data": cv_data
        }, on_conflict="user_id").execute()
        if result.data:
            return result.data[0]
        return result.data if result.data else {"success": True}
    except Exception as e:
        # Log to stderr (Railway captures this via docker logs)
        import sys
        print(f"[CV] save_cv ERROR: {e}", file=sys.stderr)
        return None


def load_cv(user_id: str) -> dict | None:
    """Load parsed CV data for a user. Returns None if no CV saved yet."""
    client = get_client()
    try:
        result = client.table("user_cvs").select("cv_data").eq("user_id", user_id).execute()
        if result.data:
            return result.data[0]["cv_data"]
    except Exception as e:
        print(f"load_cv error: {e}")
    return None


def delete_cv(user_id: str) -> bool:
    """Delete a user's CV data."""
    client = get_client()
    try:
        client.table("user_cvs").delete().eq("user_id", user_id).execute()
        return True
    except Exception:
        return False


def upload_raw_file(user_id: str, file_data: bytes, filename: str, content_type: str) -> str | None:
    """
    Upload raw PDF/DOCX to Supabase Storage (bucket: 'cv-files').
    Returns the public URL or None on failure.
    """
    client = get_client()
    bucket = "cv-files"
    path = f"{user_id}/{filename}"

    try:
        client.storage.from_(bucket).upload(path, file_data, {"content-type": content_type})
        url = client.storage.from_(bucket).get_public_url(path)
        return url
    except Exception as e:
        # Try to create bucket if it doesn't exist
        try:
            client.storage.create_bucket(bucket, {"public": True})
            client.storage.from_(bucket).upload(path, file_data, {"content-type": content_type})
            url = client.storage.from_(bucket).get_public_url(path)
            return url
        except Exception as ex:
            print(f"upload_raw_file error: {ex}")
    return None


def get_raw_file_url(user_id: str, filename: str) -> str | None:
    """Get public URL for user's uploaded CV file."""
    client = get_client()
    bucket = "cv-files"
    path = f"{user_id}/{filename}"
    try:
        return client.storage.from_(bucket).get_public_url(path)
    except Exception:
        return None