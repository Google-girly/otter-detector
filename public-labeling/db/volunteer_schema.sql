-- Volunteer accounts for the public labeling site (new project rxdnubvmfomorzppjlxz).
-- Run once in the SQL Editor of a FRESH project, after Auth is enabled (it is by default).
--
-- Security model: the browser never talks to these tables directly. It signs in
-- with Supabase Auth and sends its access token to the FastAPI backend, which
-- verifies the token and reads/writes with the service-role key. So RLS is ON
-- with no policies: anon/authenticated roles see nothing, service role bypasses.

-- ── Frames to label ──────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS images (
  id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
  filename TEXT NOT NULL,
  storage_path TEXT NOT NULL,
  is_labeled BOOLEAN DEFAULT FALSE,
  created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS images_unlabeled_idx ON images (is_labeled) WHERE NOT is_labeled;

-- ── Volunteer profiles (1:1 with auth.users) ─────────────────────────────────
CREATE TABLE IF NOT EXISTS profiles (
  id UUID PRIMARY KEY REFERENCES auth.users(id) ON DELETE CASCADE,
  full_name TEXT NOT NULL,          -- what goes on a community-service hours report
  email TEXT NOT NULL,
  organization TEXT,                -- school / club the hours are for (optional)
  created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Auto-create a profile whenever someone signs up. full_name/organization come
-- from the signUp() call's options.data in the frontend.
CREATE OR REPLACE FUNCTION public.handle_new_user()
RETURNS TRIGGER LANGUAGE plpgsql SECURITY DEFINER SET search_path = '' AS $$
BEGIN
  INSERT INTO public.profiles (id, full_name, email, organization)
  VALUES (
    NEW.id,
    COALESCE(NULLIF(TRIM(NEW.raw_user_meta_data->>'full_name'), ''), split_part(NEW.email, '@', 1)),
    NEW.email,
    NULLIF(TRIM(NEW.raw_user_meta_data->>'organization'), '')
  );
  RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS on_auth_user_created ON auth.users;
CREATE TRIGGER on_auth_user_created
  AFTER INSERT ON auth.users
  FOR EACH ROW EXECUTE FUNCTION public.handle_new_user();

-- ── Labels ───────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS labels (
  id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
  image_id UUID REFERENCES images(id) ON DELETE CASCADE,
  user_id UUID REFERENCES profiles(id) ON DELETE SET NULL,  -- NULL for admin corrections
  labeled_by TEXT NOT NULL,         -- kept for export_dataset.py compatibility
  boxes JSONB NOT NULL,
  has_otter BOOLEAN NOT NULL,
  duration_ms INTEGER,              -- time from image shown to submit, as reported by the client
  created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS labels_user_idx ON labels (user_id, created_at);

-- ── Per-volunteer totals ─────────────────────────────────────────────────────
-- Active time per label is capped at 2 minutes so a tab left open overnight
-- doesn't count as 8 hours of service.
CREATE OR REPLACE VIEW volunteer_totals WITH (security_invoker = true) AS
SELECT
  p.id AS user_id,
  p.full_name,
  p.email,
  p.organization,
  p.created_at AS joined_at,
  COUNT(l.id)::INT AS labels,
  COUNT(l.id) FILTER (WHERE l.has_otter)::INT AS labels_with_otters,
  COALESCE(SUM(LEAST(GREATEST(l.duration_ms, 0), 120000)), 0)::BIGINT AS active_ms,
  MIN(l.created_at) AS first_label_at,
  MAX(l.created_at) AS last_label_at
FROM profiles p
LEFT JOIN labels l ON l.user_id = p.id
GROUP BY p.id;

-- ── Lock it down ─────────────────────────────────────────────────────────────
ALTER TABLE images   ENABLE ROW LEVEL SECURITY;
ALTER TABLE labels   ENABLE ROW LEVEL SECURITY;
ALTER TABLE profiles ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON volunteer_totals FROM anon, authenticated;

-- Tables made in the SQL editor are owned by postgres; make sure the backend's
-- service role can use them through PostgREST.
GRANT ALL ON images, labels, profiles TO service_role;
GRANT SELECT ON volunteer_totals TO service_role;
