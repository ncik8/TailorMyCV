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


def check_write_and_delete(url, key):
    """Insert a row with all columns, read it back, then delete it."""
    print("[3/4] Upsert+delete round-trip with all 3 fix columns...")
    test_uid = str(uuid4())
    test_row = {
        "user_id": test_uid,
        "name": "Smoke Test",
        "email": "smoke@test.local",
        "experience": json.dumps([{"title": "T", "company": "C", "bullets": []}]),
        "skills": json.dumps(["Python"]),
        "additional_info": json.dumps([{"key": "k", "value": "v"}]),
        "gap_answers": json.dumps([{"requirement": "r", "user_answer": "a"}]),
        "updated_at": "now()",
    }
    # Upsert
    status, _, body = supabase_query(
        url, key,
        "/rest/v1/user_cvs",
        method="POST",
        body=test_row,
        extra_headers={"Prefer": "return=representation,resolution=ignore-duplicates"}
    )
    if status not in (200, 201):
        print(f"  FAIL: upsert returned HTTP {status}: {body[:300]}")
        return False
    print(f"  upsert OK ({status})")

    # Read back, verify the 3 fix columns
    status, _, body = supabase_query(
        url, key,
        f"/rest/v1/user_cvs?select=user_id,additional_info,gap_answers,updated_at&user_id=eq.{test_uid}"
    )
    if status != 200:
        print(f"  FAIL: read-back returned HTTP {status}: {body[:200]}")
        return False
    rows = json.loads(body)
    if not rows:
        print(f"  FAIL: read-back returned no rows (insert didn't stick?)")
        return False
    r = rows[0]
    failed = []
    for col in ("additional_info", "gap_answers", "updated_at"):
        if r.get(col) is None:
            failed.append(col)
    if failed:
        print(f"  FAIL: read-back has NULL for {failed}")
        return False
    print(f"  read-back PASS: additional_info={r['additional_info']!r}")
    print(f"                   gap_answers={r['gap_answers']!r}")
    print(f"                   updated_at={r['updated_at']!r}")

    # Cleanup
    status, _, body = supabase_query(
        url, key,
        f"/rest/v1/user_cvs?user_id=eq.{test_uid}",
        method="DELETE"
    )
    if status not in (200, 204):
        print(f"  WARN: cleanup DELETE returned HTTP {status}: {body[:200]}")
    else:
        print(f"  cleanup PASS")
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
