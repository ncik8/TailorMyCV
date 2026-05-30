"""
Persist user CV data to Supabase (user_cvs table with TEXT columns).
All arrays are JSON-encoded to TEXT strings before insert.
"""
import json
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


def _json_col(val):
    """Coerce a Python value to a JSON string for TEXT storage."""
    if val is None:
        return None
    if isinstance(val, list) or isinstance(val, dict):
        return json.dumps(val, ensure_ascii=False)
    return val


def save_cv(user_id: str, cv_data: dict) -> dict | None:
    """
    Save CV data using TEXT columns. Arrays are JSON-encoded.
    cv_data keys: name, email, phone, location, linkedin, title, summary,
                  experience, education, skills, projects, certifications,
                  languages, raw_text, cv_filename
    Returns the saved row or None on failure.
    """
    if not user_id:
        print("[CV] save_cv: ERROR — user_id is empty!")
        return None

    client = get_client()

    row = {
        "user_id": user_id,
        "name": _json_col(cv_data.get("name")),
        "email": _json_col(cv_data.get("email")),
        "phone": _json_col(cv_data.get("phone")),
        "location": _json_col(cv_data.get("location")),
        "linkedin": _json_col(cv_data.get("linkedin")),
        "title": _json_col(cv_data.get("title")),
        "summary": _json_col(cv_data.get("summary")),
        "experience": _json_col(cv_data.get("experience", [])),
        "education": _json_col(cv_data.get("education", [])),
        "skills": _json_col(cv_data.get("skills", [])),
        "projects": _json_col(cv_data.get("projects", [])),
        "certifications": _json_col(cv_data.get("certifications", [])),
        "languages": _json_col(cv_data.get("languages", [])),
        "additional_info": _json_col(cv_data.get("additional_info", [])),
        "raw_text": _json_col(cv_data.get("raw_text")),
        "cv_filename": _json_col(cv_data.get("cv_filename")),
        "gap_answers": _json_col(cv_data.get("gap_answers", [])),
        "updated_at": "now()",
    }

    print(f"[CV] save_cv: user_id={user_id}, name={row['name']}, "
          f"experience={row['experience'][:50] if row['experience'] else 'None'}...")

    try:
        result = client.table("user_cvs").upsert(row, on_conflict="user_id").execute()
        print(f"[CV] save_cv SUCCESS: {result.data[0].get('id') if result.data else 'no data'}")
        return result.data[0] if result.data else {"success": True}
    except Exception as e:
        import sys
        print(f"[CV] save_cv ERROR: {type(e).__name__}: {e}", file=sys.stderr)
        return None


def load_cv(user_id: str) -> dict | None:
    """
    Load CV data from TEXT columns. JSON-decodes array fields.
    Returns None if no CV saved yet.
    """
    if not user_id:
        print("[CV] load_cv: user_id is empty")
        return None

    client = get_client()
    try:
        result = (
            client.table("user_cvs")
            .select(
                "name,email,phone,location,linkedin,title,summary,"
                "experience,education,skills,projects,certifications,languages,"
                "additional_info,raw_text,cv_filename,gap_answers"
            )
            .eq("user_id", user_id)
            .maybe_single()
            .execute()
        )
        if not (result and result.data):
            print(f"[CV] load_cv: no row for user_id={user_id}")
            return None

        d = result.data
        cv = {
            "name": d.get("name") or "",
            "email": d.get("email") or "",
            "phone": d.get("phone") or "",
            "location": d.get("location") or "",
            "linkedin": d.get("linkedin") or "",
            "title": d.get("title") or "",
            "summary": d.get("summary") or "",
            "raw_text": d.get("raw_text") or "",
            "cv_filename": d.get("cv_filename") or "",
        }

        # JSON-decode the array fields
        def _decode(val):
            if val is None:
                return []
            if isinstance(val, list):
                return val
            if isinstance(val, str):
                try:
                    parsed = json.loads(val)
                    return parsed if isinstance(parsed, list) else []
                except (json.JSONDecodeError, TypeError):
                    return []
            return []

        cv["experience"] = _decode(d.get("experience"))
        cv["education"] = _decode(d.get("education"))
        cv["skills"] = _decode(d.get("skills"))
        cv["projects"] = _decode(d.get("projects"))
        cv["certifications"] = _decode(d.get("certifications"))
        cv["languages"] = _decode(d.get("languages"))
        cv["additional_info"] = _decode(d.get("additional_info"))
        cv["gap_answers"] = _decode(d.get("gap_answers"))

        # Normalise education: AI parser returns institution/field_of_study/start_date/end_date
        # but the app uses school/field/year — map AI fields to app fields
        for edu in cv["education"]:
            if "institution" in edu and "school" not in edu:
                edu["school"] = edu.pop("institution")
            if "field_of_study" in edu and "field" not in edu:
                edu["field"] = edu.pop("field_of_study")
            # Use start_date as year if year is missing
            if not edu.get("year") and edu.get("start_date"):
                edu["year"] = edu["start_date"]

        print(f"[CV] load_cv: user_id={user_id}, name={cv['name']}, "
              f"exp_count={len(cv['experience'])}, edu_count={len(cv['education'])}, "
              f"lang_count={len(cv['languages'])}")
        return cv

    except Exception as e:
        import sys
        print(f"[CV] load_cv ERROR: {type(e).__name__}: {e}", file=sys.stderr)
        return None


def delete_cv(user_id: str) -> bool:
    """Delete a user's CV data (keeps account)."""
    if not user_id:
        print("[CV] delete_cv: ERROR — user_id is empty!")
        return False

    client = get_client()
    try:
        print(f"[CV] delete_cv: deleting user_id={user_id}")
        client.table("user_cvs").delete().eq("user_id", user_id).execute()
        print(f"[CV] delete_cv: SUCCESS")
        return True
    except Exception as e:
        import sys
        print(f"[CV] delete_cv ERROR: {type(e).__name__}: {e}", file=sys.stderr)
        return False


def upload_raw_file(user_id: str, file_data: bytes, filename: str, content_type: str) -> str | None:
    """
    Upload raw PDF/DOCX to Supabase Storage (bucket: 'cv-files').
    Returns the public URL or None on failure.
    """
    print(f"[upload_raw_file] START — user_id={user_id}, filename={repr(filename)}, "
          f"content_type={content_type}, file_data_len={len(file_data) if file_data else 'None'}")
    client = get_client()
    bucket = "cv-files"
    path = f"{user_id}/{filename}"

    try:
        print(f"[upload_raw_file] uploading to bucket={bucket}, path={path}")
        client.storage.from_(bucket).upload(path, file_data, {"content-type": content_type})
        url = client.storage.from_(bucket).get_public_url(path)
        print(f"[upload_raw_file] SUCCESS: {url}")
        return url
    except Exception as e:
        print(f"[upload_raw_file] upload error: {type(e).__name__}: {e}")
        # Try to create bucket if it doesn't exist — supabase-py v2: create_bucket(name_str, options_dict)
        try:
            client.storage.create_bucket(bucket, options={"public": True})
            client.storage.from_(bucket).upload(path, file_data, {"content-type": content_type})
            url = client.storage.from_(bucket).get_public_url(path)
            print(f"[upload_raw_file] SUCCESS after bucket create: {url}")
            return url
        except Exception as ex:
            print(f"[upload_raw_file] bucket-create retry error: {type(ex).__name__}: {ex}")
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
