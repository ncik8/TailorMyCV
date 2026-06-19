-- Migration 006: Backfill ghost profiles
--
-- Ghost user: signed up, uploaded a CV, but never got a profiles row.
-- Cause: auth.users → profiles insert path may have silently failed
-- (the try/except in sign_up swallows errors). Older accounts from before
-- profile creation was wired up are also affected.
--
-- This migration uses a server-side INSERT ... SELECT with NOT EXISTS to
-- create a profile row for every auth.users row that:
--   - has at least one CV in user_cvs, OR
--   - is older than 1 day (probably a real user)
--
-- tier defaults to 'free', cv_count = COUNT(user_cvs) for that user.
--
-- Idempotent: ON CONFLICT (user_id) DO NOTHING means re-running is safe.
-- Run via Supabase SQL Editor with service_role auth.

INSERT INTO profiles (user_id, tier, cv_count, created_at, updated_at)
SELECT
    u.id,
    'free' AS tier,
    COALESCE(cv.cnt, 0) AS cv_count,
    u.created_at,
    NOW() AS updated_at
FROM auth.users u
LEFT JOIN (
    SELECT user_id, COUNT(*) AS cnt
    FROM user_cvs
    GROUP BY user_id
) cv ON cv.user_id = u.id
WHERE
    -- Has uploaded at least one CV
    cv.cnt > 0
    -- OR is at least 1 day old (probably a real account that just never uploaded)
    OR u.created_at < NOW() - INTERVAL '1 day'
ON CONFLICT (user_id) DO NOTHING;

-- Verify: count of profiles before/after
-- SELECT
--     (SELECT COUNT(*) FROM profiles) AS profile_count,
--     (SELECT COUNT(*) FROM auth.users) AS auth_user_count,
--     (SELECT COUNT(DISTINCT user_id) FROM user_cvs) AS cv_user_count;
