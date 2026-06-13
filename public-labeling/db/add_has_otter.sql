-- Migration: add has_otter flag to existing labels table
-- Run this once in the Supabase SQL Editor if the table already exists

ALTER TABLE labels ADD COLUMN has_otter BOOLEAN;

-- Backfill existing rows: infer from whether boxes array is empty
UPDATE labels SET has_otter = (jsonb_array_length(boxes) > 0);

-- Now enforce NOT NULL
ALTER TABLE labels ALTER COLUMN has_otter SET NOT NULL;
