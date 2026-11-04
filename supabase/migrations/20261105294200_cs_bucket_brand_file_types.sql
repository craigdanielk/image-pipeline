-- Brand package files in the private Creative Studio bucket (2026-11-05, additive). The brand-package importer
-- (service/brand/importer.py _EXT) mirrors fonts, the approved brand-guide PDF and audio as well as images, but the
-- bucket admitted only images, video and JSON, so every Marketing OS brand import failed at storage (400) and no
-- media could be prepared. Admit exactly the types the importer writes; size limit unchanged. Idempotent.

update storage.buckets
   set allowed_mime_types = (
     select array_agg(distinct t order by t)
       from unnest(coalesce(allowed_mime_types, array[]::text[])
                   || array['font/ttf', 'font/otf', 'font/woff2', 'application/pdf', 'audio/mp4']) as t)
 where id = 'creative-studio';
