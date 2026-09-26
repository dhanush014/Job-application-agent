-- Run once in the Supabase SQL editor (free tier is plenty).
-- Also create a PRIVATE storage bucket named "applications" for resume PDFs.

create table if not exists jobs (
  key text primary key,
  ats text not null,
  company text not null,
  company_name text,
  job_id text not null,
  title text,
  location text,
  url text,
  apply_url text,
  description text,
  posted_at text,
  remote boolean,
  first_seen timestamptz default now(),
  last_seen timestamptz default now()
);

create table if not exists applications (
  id text primary key,
  job_key text not null references jobs(key),
  ats text not null,
  company text not null,
  company_name text,
  job_id text not null,
  title text,
  location text,
  url text,
  apply_url text,
  jd text,                      -- full job description
  status text not null,
  score int,
  fit_summary text,
  matched jsonb default '[]',
  gaps jsonb default '[]',
  jd_keywords jsonb default '[]',
  review_reasons jsonb default '[]',
  resume_json jsonb,            -- exact resume content that was sent
  resume_pdf text,              -- local path; PDF mirrored to storage at <id>/resume.pdf
  cover_letter text,
  cover_letter_pdf text,
  answers jsonb default '[]',   -- every question + the answer that was submitted
  screenshot text,
  error text,
  attempt int default 1,
  request text,                 -- "prepare" / "reapply" asked for from the hosted dashboard
  created_at timestamptz default now(),
  updated_at timestamptz default now(),
  applied_at timestamptz        -- date applied
);

create index if not exists applications_status on applications(status);
create index if not exists applications_job on applications(job_key);

-- Keep the tables private: the agent uses the service role key.
alter table jobs enable row level security;
alter table applications enable row level security;

-- Upgrading an existing database from an earlier version:
alter table applications add column if not exists request text;
