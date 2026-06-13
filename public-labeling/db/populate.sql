-- Populate the images table from frames already uploaded to Supabase Storage.
-- Run this in the Supabase SQL Editor if upload_to_db.py returns 0 rows.
INSERT INTO images (filename, storage_path)
SELECT
  split_part(name, '/', 2) AS filename,
  name AS storage_path
FROM storage.objects
WHERE bucket_id = 'frames';

-- Verify the import
SELECT COUNT(*) FROM images;
