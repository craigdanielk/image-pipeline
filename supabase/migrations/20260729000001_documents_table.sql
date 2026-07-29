-- Documents — free-form document store used for verification markers,
-- knowledge base notes, and system-internal completion records.
--
-- The end-state assertion of atomic units checks this table for
-- slug='atomic-<unit>' rows; see db_row_count assertions in brief contracts.
--
-- This migration is additive and safe with existing data.
create table if not exists documents (
  id              uuid primary key default gen_random_uuid(),
  slug            text not null unique,
  doc_type        text not null default 'note',
  project         text,
  title           text,
  body            text,
  metadata        jsonb not null default '{}',
  tenant_id       text,
  created_at      timestamptz not null default now(),
  updated_at      timestamptz not null default now()
);

-- touch trigger
drop trigger if exists trg_documents_touch on documents;
create trigger trg_documents_touch
  before update on documents
  for each row execute function public.set_updated_at();
