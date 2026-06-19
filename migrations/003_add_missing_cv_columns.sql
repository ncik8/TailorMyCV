-- Migration 003: Add columns save_cv() expects but migrations 001/002 missed.
--
-- Why: services/user_cv.py:save_cv() inserts gap_answers, additional_info,
-- and updated_at on every CV upload. None of these exist on user_cvs.
-- PostgREST returns PGRST204 ("Could not find the 'gap_answers' column
-- of 'user_cvs'"), save_cv() catches the exception and returns None,
-- and the /cv/parse route doesn't check the return value. Users see
-- "Upload successful!" but their CV is never persisted. Counter
-- (profiles.cv_count) still increments later when they click "Tailor",
-- creating the 86% ghost-user activation gap.
--
-- Run in: Supabase Dashboard → SQL Editor → New query → paste → Run.
-- Idempotent: safe to re-run.

BEGIN;

ALTER TABLE user_cvs
  ADD COLUMN IF NOT EXISTS additional_info TEXT,
  ADD COLUMN IF NOT EXISTS gap_answers      TEXT,
  ADD COLUMN IF NOT EXISTS updated_at       TIMESTAMPTZ DEFAULT NOW();

-- Sanity check: confirm the columns landed
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_schema = 'public' AND table_name = 'user_cvs' AND column_name = 'gap_answers'
  ) THEN
    RAISE EXCEPTION 'gap_answers column was not added — migration failed';
  END IF;
END $$;

COMMIT;

SELECT
  column_name,
  data_type,
  is_nullable,
  column_default
FROM information_schema.columns
WHERE table_schema = 'public'
  AND table_name = 'user_cvs'
  AND column_name IN ('additional_info', 'gap_answers', 'updated_at')
ORDER BY column_name;
