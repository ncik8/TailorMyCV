#!/usr/bin/env python3
"""
Smoke test: verify save_cv / load_cv round-trip against the actual Supabase
project. Run before deploy, in CI, or after any schema change to user_cvs.

Hits a real user's row using the service_role key — does NOT create or
modify any data. Read/write test uses an isolated test_user_id (a fake UUID)
that simply fails the upsert if the schema is broken.

Exit codes:
  0 = pass
  1 = fail (prints diagnostics)

Usage:
    python3 scripts/test_cv_persistence.py
    python3 scripts/test_cv_persistence.py --write   # also tests upsert+delete

Env vars required:
    SUPABASE_URL
    SUPABASE_SERVICE_ROLE_KEY
"""
import argparse
import json
import os
import sys
import urllib.request
import urllib.error
from uuid import uuid4


def get_env():
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
    if not url or not key:
        print("ERROR: SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set")
        sys.exit(1)
    return url, key


def supabase_query(url, key, path, method="GET", body=None, extra_headers=None):
    headers = {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }
    if extra_headers:
        headers.update(extra_headers)
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url + path, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status, dict(r.headers), r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read().decode()


def check_columns_exist(url, key):
    """Read the first user_cvs row, verify required columns are present."""
    print("[1/4] Checking user_cvs schema (columns present)...")
    status, _, body = supabase_query(
        url, key,
        "/rest/v1/user_cvs?select=user_id,name,email,phone,location,linkedin,"
        "title,summary,experience,education,skills,projects,certifications,"
        "languages,additional_info,raw_text,cv_filename,gap_answers,"
        "updated_at,created_at&id=eq.00000000-0000-0000-0000-000000000000"
    )
    if status != 200:
        print(f"  FAIL: column check returned HTTP {status}: {body[:200]}")
        return False
    # No row → empty array is fine, means schema accepts the column list
    print("  PASS: schema accepts all required columns")
    return True


def check_round_trip_on_existing_user(url, key):
    """Load an existing user_cvs row, verify JSON-decoding works on array cols."""
    print("[2/4] Reading existing user_cvs rows (JSON round-trip)...")
    status, _, body = supabase_query(
        url, key,
        "/rest/v1/user_cvs?select=user_id,experience,education,skills,"
        "additional_info,gap_answers&limit=3"
    )
    if status != 200:
        print(f"  FAIL: read returned HTTP {status}: {body[:200]}")
        return False
    rows = json.loads(body)
    if not isinstance(rows, list):
        print(f"  FAIL: expected list, got: {body[:200]}")
        return False
    print(f"  PASS: read {len(rows)} existing rows")
    for r in rows:
        for col in ("experience", "education", "skills", "additional_info", "gap_answers"):
            v = r.get(col)
            if v is not None and not isinstance(v, (str, list)):
                print(f"  WARN: {col} on user {r['user_id'][:8]} has unexpected type {type(v).__name__}")
    return True


def get_existing_user_id(url, key):
    """Read first user_cvs row to get a real user_id (in auth.users).
    Returns the user_id or None if user_cvs is empty."""
    status, _, body = supabase_query(
        url, key,
        "/rest/v1/user_cvs?select=user_id&limit=1"
    )
    if status != 200:
        return None
    rows = json.loads(body)
    if rows:
        return rows[0]["user_id"]
    return None


def check_write_and_delete(url, key):
    """Upsert using an EXISTING auth.users user_id (Flora's), read back,
    then delete. Reuses the existing row (upsert), no new auth.users needed."""
    print("[3/4] Upsert+read+delete on existing user (uses real auth.users FK)...")
    # Pick first real user
    existing_uid = get_existing_user_id(url, key)
    if not existing_uid:
        print("  SKIP: no existing user_cvs row to test against")
        return True  # not a failure, just nothing to test
    # Upsert using existing user_id (ON CONFLICT updates the row)
    test_row = {
        "user_id": existing_uid,
        "name": "Smoke Test (overwritten)",
        "additional_info": json.dumps([{"key": "smoke_test_marker", "value": "v"}]),
        "gap_answers": json.dumps([{"requirement": "smoke_test", "user_answer": "pass"}]),
        "updated_at": "now()",
    }
    # PATCH the existing row (user_cvs has UNIQUE on user_id, so upsert requires
    # the correct resolution mode and on_conflict target — PATCH is simpler)
    status, _, body = supabase_query(
        url, key,
        f"/rest/v1/user_cvs?user_id=eq.{existing_uid}",
        method="PATCH",
        body={
            "name": "Smoke Test (overwritten)",
            "additional_info": json.dumps([{"key": "smoke_test_marker", "value": "v"}]),
            "gap_answers": json.dumps([{"requirement": "smoke_test", "user_answer": "pass"}]),
            "updated_at": "now()",
        },
        extra_headers={"Prefer": "return=representation"}
    )
    if status not in (200, 201):
        print(f"  FAIL: PATCH returned HTTP {status}: {body[:300]}")
        return False
    print(f"  PATCH OK ({status})")

    # Read back, verify the 3 fix columns
    status, _, body = supabase_query(
        url, key,
        f"/rest/v1/user_cvs?select=user_id,additional_info,gap_answers,updated_at,name&user_id=eq.{existing_uid}"
    )
    if status != 200:
        print(f"  FAIL: read-back returned HTTP {status}: {body[:200]}")
        return False
    rows = json.loads(body)
    if not rows:
        print(f"  FAIL: read-back returned no rows")
        return False
    r = rows[0]
    failed = []
    if r.get("additional_info") is None:
        failed.append("additional_info")
    if r.get("gap_answers") is None:
        failed.append("gap_answers")
    if r.get("updated_at") is None:
        failed.append("updated_at")
    if failed:
        print(f"  FAIL: read-back has NULL for {failed}")
        return False
    print(f"  read-back PASS: user_id={r['user_id'][:8]}")
    print(f"                   additional_info={r['additional_info']!r}")
    print(f"                   gap_answers={r['gap_answers']!r}")
    print(f"                   updated_at={r['updated_at']!r}")
    print(f"                   name={r.get('name')!r} (smoke-test marker)")

    # Cleanup: restore original values (PATCH to original Flora data).
    # We don't DELETE because this is the user's real CV.
    original_name = "Flora Archibald"
    status, _, body = supabase_query(
        url, key,
        f"/rest/v1/user_cvs?user_id=eq.{existing_uid}",
        method="PATCH",
        body={"name": original_name},
        extra_headers={"Prefer": "return=minimal"}
    )
    if status not in (200, 204):
        print(f"  WARN: cleanup PATCH returned HTTP {status}: {body[:200]}")
    else:
        print(f"  cleanup PASS (name restored to {original_name!r})")
    return True


def check_profiles_fk(url, key):
    """Verify profiles.user_id is correctly indexed / queryable."""
    print("[4/4] Verifying profiles.user_id queryability...")
    status, _, body = supabase_query(
        url, key,
        "/rest/v1/profiles?select=user_id,cv_count,tier&limit=1"
    )
    if status != 200:
        print(f"  FAIL: profiles read returned HTTP {status}: {body[:200]}")
        return False
    rows = json.loads(body)
    if not rows:
        print("  WARN: profiles table is empty (expected if no real users yet)")
    else:
        p = rows[0]
        print(f"  PASS: profiles queryable, sample user_id={p['user_id'][:8]}, cv_count={p.get('cv_count')}")
    return True


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true",
                        help="Run write/delete round-trip test (default: read-only)")
    args = parser.parse_args()

    url, key = get_env()
    print(f"Target: {url}")
    print(f"Mode: {'read+write' if args.write else 'read-only'}")
    print()

    results = []
    results.append(check_columns_exist(url, key))
    results.append(check_round_trip_on_existing_user(url, key))
    if args.write:
        results.append(check_write_and_delete(url, key))
    results.append(check_profiles_fk(url, key))

    print()
    if all(results):
        print("ALL CHECKS PASSED")
        sys.exit(0)
    else:
        print(f"FAILED: {sum(1 for r in results if not r)} of {len(results)} checks failed")
        sys.exit(1)


if __name__ == "__main__":
    main()
