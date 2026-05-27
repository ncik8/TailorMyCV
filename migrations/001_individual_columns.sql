-- RezMyCV: Individual columns for each CV section
-- Run this in Supabase SQL Editor

-- 1. Add individual columns (all nullable for migration safety)
ALTER TABLE user_cvs
  ADD COLUMN IF NOT EXISTS name          TEXT,
  ADD COLUMN IF NOT EXISTS email         TEXT,
  ADD COLUMN IF NOT EXISTS phone          TEXT,
  ADD COLUMN IF NOT EXISTS location       TEXT,
  ADD COLUMN IF NOT EXISTS linkedin       TEXT,
  ADD COLUMN IF NOT EXISTS title          TEXT,
  ADD COLUMN IF NOT EXISTS summary        TEXT,
  ADD COLUMN IF NOT EXISTS raw_text       TEXT,
  ADD COLUMN IF NOT EXISTS cv_filename     TEXT,
  ADD COLUMN IF NOT EXISTS experience      JSONB,
  ADD COLUMN IF NOT EXISTS education       JSONB,
  ADD COLUMN IF NOT EXISTS skills         JSONB,
  ADD COLUMN IF NOT EXISTS projects       JSONB,
  ADD COLUMN IF NOT EXISTS certifications JSONB,
  ADD COLUMN IF NOT EXISTS languages      JSONB;

-- 2. Set cv_data to empty JSON for migrated rows (satisfies NOT NULL constraint)
UPDATE user_cvs
SET cv_data = '{}'::jsonb
WHERE cv_data IS NULL;

-- 3. Migrate existing cv_data JSON → individual columns (one-time)
-- Only runs if cv_data is non-empty AND the individual column is still NULL
-- This preserves existing data during the transition
UPDATE user_cvs
SET
  name          = COALESCE(NULLIF(name, ''), (cv_data->>'name')::text),
  email         = COALESCE(NULLIF(email, ''), (cv_data->>'email')::text),
  phone         = COALESCE(NULLIF(phone, ''), (cv_data->>'phone')::text),
  location      = COALESCE(NULLIF(location, ''), (cv_data->>'location')::text),
  linkedin      = COALESCE(NULLIF(linkedin, ''), (cv_data->>'linkedin')::text),
  title         = COALESCE(NULLIF(title, ''), (cv_data->>'title')::text),
  summary       = COALESCE(NULLIF(summary, ''), (cv_data->>'summary')::text),
  raw_text      = COALESCE(NULLIF(raw_text, ''), (cv_data->>'raw_text')::text),
  cv_filename   = COALESCE(NULLIF(cv_filename, ''), (cv_data->>'cv_filename')::text),
  experience    = COALESCE(experience, cv_data->'experience'),
  education     = COALESCE(education, cv_data->'education'),
  skills        = COALESCE(skills, cv_data->'skills'),
  projects      = COALESCE(projects, cv_data->'projects'),
  certifications= COALESCE(certifications, cv_data->'certifications'),
  languages     = COALESCE(languages, cv_data->'languages')
WHERE cv_data IS NOT NULL AND cv_data != '{}'::jsonb;

-- 4. Drop NOT NULL constraint on cv_data (no longer needed — we use individual columns)
ALTER TABLE user_cvs ALTER COLUMN cv_data DROP NOT NULL;

-- 3. Add NOT NULL constraints now that migration is done (optional — safe to skip)
-- ALTER TABLE user_cvs ALTER COLUMN updated_at SET DEFAULT now();

-- 4. Create index on user_id if it doesn't exist (should already exist as PK)
-- Done — user_id is the primary key

SELECT 'Migration complete: user_cvs now has individual columns' AS status;