-- Tracks every frame and whether it has been labeled
CREATE TABLE images (
  id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
  filename TEXT NOT NULL,
  storage_path TEXT NOT NULL,
  is_labeled BOOLEAN DEFAULT FALSE,
  created_at TIMESTAMP DEFAULT NOW()
);

-- Stores bounding box data for each labeled image
CREATE TABLE labels (
  id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
  image_id UUID REFERENCES images(id),
  labeled_by TEXT NOT NULL,
  boxes JSONB NOT NULL,
  created_at TIMESTAMP DEFAULT NOW()
);
