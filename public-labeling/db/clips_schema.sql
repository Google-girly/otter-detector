-- One row per motion clip uploaded by ec2-recorder/motion_recorder.py to the
-- `clips` Storage bucket. Storage holds the video; this table makes the set
-- queryable (how many minutes do we have, from which days, which are processed).
CREATE TABLE IF NOT EXISTS clips (
  id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
  filename TEXT NOT NULL,
  storage_path TEXT NOT NULL UNIQUE,      -- path in the `clips` bucket, e.g. 2026/11/11/otters-2026-11-11-1512.mp4
  duration_s REAL,
  size_bytes BIGINT,
  is_processed BOOLEAN DEFAULT FALSE,     -- flipped once frames have been cut / auto-labeled
  created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Only the service role (recorder + pipeline scripts) touches this table.
ALTER TABLE clips ENABLE ROW LEVEL SECURITY;
