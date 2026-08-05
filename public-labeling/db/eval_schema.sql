-- ─────────────────────────────────────────────────────────────────────────────
-- Model-evaluation tables.
--
-- These are SEPARATE from images/labels (the crowd training-label tables) on
-- purpose: here we are judging a model's output, not creating ground truth, so
-- mixing the two would pollute the training set. Run this once in the Supabase
-- SQL Editor, then expose both tables under Integrations -> Data API -> Settings.
-- ─────────────────────────────────────────────────────────────────────────────

-- One row per frame the model was asked to judge.
CREATE TABLE eval_predictions (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  filename      TEXT NOT NULL,
  storage_path  TEXT NOT NULL,              -- e.g. frames/eval/<clip>_00003.jpg (public bucket)
  model_version TEXT NOT NULL,              -- 'otter_v2'
  source_clip   TEXT,                       -- S3 key the frame came from
  bucket        TEXT NOT NULL,              -- 'auto' | 'review' | 'empty' (confidence band)
  max_conf      REAL NOT NULL DEFAULT 0,    -- highest box confidence on the frame
  boxes         JSONB NOT NULL DEFAULT '[]',-- model's predicted boxes, pixel coords {x,y,width,height,conf}
  created_at    TIMESTAMP DEFAULT NOW()
);

-- One row per human verdict on a prediction. A prediction with no feedback row
-- is "not yet reviewed"; that's what the review page serves next.
CREATE TABLE eval_feedback (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  prediction_id UUID REFERENCES eval_predictions(id),
  verdict       TEXT NOT NULL,              -- correct | missed | false_alarm | box_off | correctly_empty
  reviewed_by   TEXT,
  notes         TEXT,
  created_at    TIMESTAMP DEFAULT NOW()
);

-- Speeds up "give me the next unreviewed prediction".
CREATE INDEX idx_eval_feedback_prediction ON eval_feedback (prediction_id);

-- Optional: block a nonsense verdict from ever landing in the table.
ALTER TABLE eval_feedback
  ADD CONSTRAINT eval_feedback_verdict_check
  CHECK (verdict IN ('correct', 'missed', 'false_alarm', 'box_off', 'correctly_empty'));

-- ─────────────────────────────────────────────────────────────────────────────
-- Grant the API roles access. Tables created in the SQL Editor are owned by
-- `postgres`; without these grants PostgREST returns "permission denied for
-- table ..." (42501). The pipeline writes as service_role; the review page
-- reads/writes as the publishable (anon) key.
-- ─────────────────────────────────────────────────────────────────────────────
GRANT ALL              ON public.eval_predictions TO service_role;
GRANT ALL              ON public.eval_feedback    TO service_role;
GRANT SELECT, INSERT   ON public.eval_predictions TO anon, authenticated;
GRANT SELECT, INSERT   ON public.eval_feedback    TO anon, authenticated;

-- Human-drawn correction boxes (original-image pixel coords {x,y,width,height}).
-- Populated when a reviewer boxes a missed otter or fixes a sloppy box; these
-- become high-value training examples. Empty when the verdict needed no drawing.
ALTER TABLE eval_feedback ADD COLUMN IF NOT EXISTS corrected_boxes JSONB NOT NULL DEFAULT '[]';

-- ─────────────────────────────────────────────────────────────────────────────
-- Per-frame detection counts, so multi-otter frames get exact precision/recall.
--   n_boxes   = how many boxes the model drew on the frame
--   n_correct = boxes that are on a real otter        (True Positives)
--   n_false   = boxes on a non-otter                  (False Positives) = n_boxes - n_correct
--   n_missed  = otters the model missed               (False Negatives) = # boxes the human drew
--   box_off   = the correct boxes are sloppily placed (localization flag)
-- `verdict` becomes a human-readable summary derived from these ('mixed' when a
-- single frame has more than one outcome).
-- ─────────────────────────────────────────────────────────────────────────────
ALTER TABLE eval_feedback ADD COLUMN IF NOT EXISTS n_boxes   INT     NOT NULL DEFAULT 0;
ALTER TABLE eval_feedback ADD COLUMN IF NOT EXISTS n_correct INT     NOT NULL DEFAULT 0;
ALTER TABLE eval_feedback ADD COLUMN IF NOT EXISTS n_false   INT     NOT NULL DEFAULT 0;
ALTER TABLE eval_feedback ADD COLUMN IF NOT EXISTS n_missed  INT     NOT NULL DEFAULT 0;
ALTER TABLE eval_feedback ADD COLUMN IF NOT EXISTS box_off   BOOLEAN NOT NULL DEFAULT FALSE;

ALTER TABLE eval_feedback DROP CONSTRAINT IF EXISTS eval_feedback_verdict_check;
ALTER TABLE eval_feedback ADD CONSTRAINT eval_feedback_verdict_check
  CHECK (verdict IN ('correct', 'missed', 'false_alarm', 'box_off', 'correctly_empty', 'mixed'));
