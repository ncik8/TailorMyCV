"""
Persist user CV data to Supabase (user_cvs table with individual columns).
Also handles uploading raw PDF/DOCX to Supabase Storage.
"""
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


def save_cv(user_id: str, cv_data: dict) -> dict | None:
    """
    Save CV data using individual columns. Replaces existing CV entirely.
    cv_data keys: name, email, phone, location, linkedin, title, summary,
                  experience, education, skills, projects, certifications,
                  languages, raw_text, cv_filename
    Returns the saved row or None on failure.
    """
    client = get_client()

    # Build row with individual columns (avoids JSONB cv_data blob)
    row = {
        "user_id": user_id,
        "name": cv_data.get("name"),
        "email": cv_data.get("email"),
        "phone": cv_data.get("phone"),
        "location": cv_data.get("location"),
        "linkedin": cv_data.get("linkedin"),
        "title": cv_data.get("title"),
        "summary": cv_data.get("summary"),
        "experience": cv_data.get("experience", []),
        "education": cv_data.get("education", []),
        "skills": cv_data.get("skills", []),
        "projects": cv_data.get("projects", []),
        "certifications": cv_data.get("certifications", []),
        "languages": cv_data.get("languages", []),
        "raw_text": cv_data.get("raw_text"),
        "cv_filename": cv_data.get("cv_filename"),
        "updated_at": "now()",
    }

    try:
        result = client.table("user_cvs").upsert(row, on_conflict="user_id").execute()
        if result.data:
            return result.data[0]
        return result.data if result.data else {"success": True}
    except Exception as e:
        import sys
        print(f"[CV] save_cv ERROR: {e}", file=sys.stderr)
        return None


def load_cv(user_id: str) -> dict | None:
    """
    Load parsed CV data for a user. Reads from individual columns.
    Returns a cv_data-shaped dict (backward compatible with session['cv_data']).
    Returns None if no CV saved yet.
    """
    client = get_client()
    try:
        result = (
            client.table("user_cvs")
            .select(
                "name,email,phone,location,linkedin,title,summary,"
                "experience,education,skills,projects,certifications,languages,"
                "raw_text,cv_filename"
            )
            .eq("user_id", user_id)
            .maybe_single()
            .execute()
        )
        if result.data:
            # Reassemble into cv_data dict shape so session['cv_data'] stays compatible
            return {
                "name": result.data.get("name") or "",
                "email": result.data.get("email") or "",
                "phone": result.data.get("phone") or "",
                "location": result.data.get("location") or "",
                "linkedin": result.data.get("linkedin") or "",
                "title": result.data.get("title") or "",
                "summary": result.data.get("summary") or "",
                "experience": result.data.get("experience") or [],
                "education": result.data.get("education") or [],
                "skills": result.data.get("skills") or [],
                "projects": result.data.get("projects") or [],
                "certifications": result.data.get("certifications") or [],
                "languages": result.data.get("languages") or [],
                "raw_text": result.data.get("raw_text"),
                "cv_filename": result.data.get("cv_filename"),
            }
    except Exception as e:
        print(f"[CV] load_cv error: {e}")
    return None


def delete_cv(user_id: str) -> bool:
    """Delete a user's CV data (keeps account)."""
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