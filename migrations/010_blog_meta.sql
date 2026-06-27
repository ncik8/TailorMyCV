-- Add meta_description and og_image to blog_posts for SEO
ALTER TABLE blog_posts
  ADD COLUMN IF NOT EXISTS meta_description TEXT,
  ADD COLUMN IF NOT EXISTS og_image TEXT,
  ADD COLUMN IF NOT EXISTS focus_keyword TEXT;

-- Index on focus_keyword for analytics
CREATE INDEX IF NOT EXISTS blog_posts_focus_keyword_idx ON blog_posts(focus_keyword) WHERE focus_keyword IS NOT NULL;