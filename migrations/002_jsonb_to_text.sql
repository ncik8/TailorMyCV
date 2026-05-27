-- Migration 002: JSONB → TEXT for all array/scalar columns
-- Drop old individual columns and recreate as TEXT
-- Then populate from existing cv_data

BEGIN;

-- 1. Drop existing columns (if they exist from failed migration)
ALTER TABLE user_cvs DROP COLUMN IF EXISTS name;
ALTER TABLE user_cvs DROP COLUMN IF EXISTS email;
ALTER TABLE user_cvs DROP COLUMN IF EXISTS phone;
ALTER TABLE user_cvs DROP COLUMN IF EXISTS location;
ALTER TABLE user_cvs DROP COLUMN IF EXISTS linkedin;
ALTER TABLE user_cvs DROP COLUMN IF EXISTS title;
ALTER TABLE user_cvs DROP COLUMN IF EXISTS summary;
ALTER TABLE user_cvs DROP COLUMN IF EXISTS raw_text;
ALTER TABLE user_cvs DROP COLUMN IF EXISTS cv_filename;
ALTER TABLE user_cvs DROP COLUMN IF EXISTS experience;
ALTER TABLE user_cvs DROP COLUMN IF EXISTS education;
ALTER TABLE user_cvs DROP COLUMN IF EXISTS skills;
ALTER TABLE user_cvs DROP COLUMN IF EXISTS projects;
ALTER TABLE user_cvs DROP COLUMN IF EXISTS certifications;
ALTER TABLE user_cvs DROP COLUMN IF EXISTS languages;

-- 2. Add as TEXT columns
ALTER TABLE user_cvs ADD COLUMN name          TEXT;
ALTER TABLE user_cvs ADD COLUMN email         TEXT;
ALTER TABLE user_cvs ADD COLUMN phone         TEXT;
ALTER TABLE user_cvs ADD COLUMN location      TEXT;
ALTER TABLE user_cvs ADD COLUMN linkedin      TEXT;
ALTER TABLE user_cvs ADD COLUMN title         TEXT;
ALTER TABLE user_cvs ADD COLUMN summary       TEXT;
ALTER TABLE user_cvs ADD COLUMN raw_text      TEXT;
ALTER TABLE user_cvs ADD COLUMN cv_filename   TEXT;
ALTER TABLE user_cvs ADD COLUMN experience    TEXT;  -- JSON string
ALTER TABLE user_cvs ADD COLUMN education     TEXT;  -- JSON string
ALTER TABLE user_cvs ADD COLUMN skills        TEXT;  -- JSON string
ALTER TABLE user_cvs ADD COLUMN projects      TEXT;  -- JSON string
ALTER TABLE user_cvs ADD COLUMN certifications TEXT; -- JSON string
ALTER TABLE user_cvs ADD COLUMN languages     TEXT;  -- JSON string

-- 3. Populate from cv_data (if cv_data is a valid JSON object)
UPDATE user_cvs SET
  name          = (cv_data->>'name')::text,
  email         = (cv_data->>'email')::text,
  phone         = (cv_data->>'phone')::text,
  location      = (cv_data->>'location')::text,
  linkedin      = (cv_data->>'linkedin')::text,
  title         = (cv_data->>'title')::text,
  summary       = (cv_data->>'summary')::text,
  raw_text      = (cv_data->>'raw_text')::text,
  cv_filename   = (cv_data->>'cv_filename')::text,
  experience    = COALESCE(NULLIF(cv_data->>'experience', ''), '[]'),
  education     = COALESCE(NULLIF(cv_data->>'education', ''), '[]'),
  skills        = COALESCE(NULLIF(cv_data->>'skills', ''), '[]'),
  projects      = COALESCE(NULLIF(cv_data->>'projects', ''), '[]'),
  certifications= COALESCE(NULLIF(cv_data->>'certifications', ''), '[]'),
  languages     = COALESCE(NULLIF(cv_data->>'languages', ''), '[]')
WHERE cv_data IS NOT NULL;

COMMIT;

SELECT 'Migration 002 complete: all columns now TEXT' AS status;
