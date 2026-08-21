-- ============================================================================
-- Exam Management System — Multi-Tenant Schema
-- Government Law College, Morena → multi-center rebuild
-- ============================================================================
--
-- AUTH MODEL: simple password-in-table login (exam_centers.admin_password_hash /
-- cs_password_hash), checked by the Streamlit app. The app connects with the
-- Supabase SERVICE ROLE key, which bypasses RLS — so RLS here is a backstop,
-- not the primary access control. Every table has RLS enabled with NO
-- permissive policies for anon/authenticated roles, so if the anon key ever
-- leaks or gets used by mistake, it can read/write nothing. The service role
-- key (used only by your backend) is unaffected by RLS and keeps working
-- exactly as it does today.
--
-- Run this whole file in the Supabase SQL editor.
-- ============================================================================

-- ----------------------------------------------------------------------------
-- exam_centers (the tenant table)
-- ----------------------------------------------------------------------------
create table exam_centers (
    id                    uuid primary key default gen_random_uuid(),
    university_name       text not null default 'Jiwaji University',
    center_name           text not null,
    center_code           text not null unique,
    address               text,
    admin_username        text not null,
    admin_password_hash   text not null,
    cs_username           text not null,
    cs_password_hash      text not null,
    is_active             boolean not null default true,
    created_at            timestamptz not null default now()
);

-- ----------------------------------------------------------------------------
-- timetable
-- ----------------------------------------------------------------------------
create table timetable (
    id            bigserial primary key,
    center_id     uuid not null references exam_centers(id) on delete cascade,
    date          date,
    shift         text,
    paper_code    text not null,
    paper_name    text,
    paper_short   text,
    class         text,
    mode          text default 'REGULAR',
    type          text default 'REGULAR',
    time_slot     text,
    created_at    timestamptz not null default now(),
    unique (center_id, date, shift, paper_code)
);
create index idx_timetable_center on timetable(center_id);
create index idx_timetable_lookup on timetable(center_id, date, shift);

-- ----------------------------------------------------------------------------
-- sitting_plan (raw PDF-ingested room listing, wide data kept as JSONB)
-- ----------------------------------------------------------------------------
create table sitting_plan (
    id            bigserial primary key,
    center_id     uuid not null references exam_centers(id) on delete cascade,
    room_number   text,
    paper_code    text,
    roll_numbers  jsonb not null default '[]',
    dedupe_key    text not null,
    raw_row       jsonb,
    created_at    timestamptz not null default now(),
    unique (center_id, paper_code, dedupe_key)
);
create index idx_sitting_plan_center on sitting_plan(center_id);
create index idx_sitting_plan_room on sitting_plan(center_id, room_number);

-- ----------------------------------------------------------------------------
-- assigned_seats (operational, per-student truth)
-- ----------------------------------------------------------------------------
create table assigned_seats (
    id            bigserial primary key,
    center_id     uuid not null references exam_centers(id) on delete cascade,
    roll_number   text not null,
    paper_code    text not null,
    paper_name    text,
    class         text,
    mode          text,
    type          text,
    room_number   text,
    seat_number   text,
    date          date not null,
    shift         text not null,
    created_at    timestamptz not null default now(),
    unique (center_id, roll_number, date, shift, paper_code)
);
create index idx_assigned_seats_center on assigned_seats(center_id);
create index idx_assigned_seats_lookup on assigned_seats(center_id, roll_number, date);
create index idx_assigned_seats_session on assigned_seats(center_id, date, shift);

-- ----------------------------------------------------------------------------
-- attestation_data (student enrollment / attestation, wide data kept as JSONB)
-- ----------------------------------------------------------------------------
create table attestation_data (
    id            bigserial primary key,
    center_id     uuid not null references exam_centers(id) on delete cascade,
    roll_number   text not null,
    papers        jsonb,
    raw_row       jsonb,
    created_at    timestamptz not null default now(),
    unique (center_id, roll_number)
);
create index idx_attestation_center on attestation_data(center_id);

-- ----------------------------------------------------------------------------
-- cs_reports (Centre Superintendent session reports, incl. UFM cases)
-- ----------------------------------------------------------------------------
create table cs_reports (
    id                    bigserial primary key,
    center_id             uuid not null references exam_centers(id) on delete cascade,
    report_key            text not null,
    date                  date not null,
    shift                 text not null,
    room_num              text,
    paper_code            text,
    paper_name            text,
    class                 text,
    absent_roll_numbers   jsonb not null default '[]',
    ufm_roll_numbers      jsonb not null default '[]',
    created_at            timestamptz not null default now(),
    unique (center_id, report_key)
);
create index idx_cs_reports_center on cs_reports(center_id);
create index idx_cs_reports_session on cs_reports(center_id, date, shift);

-- ----------------------------------------------------------------------------
-- shift_assignments (staff-to-shift, by role)
-- ----------------------------------------------------------------------------
create table shift_assignments (
    id                                  bigserial primary key,
    center_id                           uuid not null references exam_centers(id) on delete cascade,
    date                                date not null,
    shift                               text not null,
    senior_center_superintendent        jsonb not null default '[]',
    center_superintendent               jsonb not null default '[]',
    assistant_center_superintendent     jsonb not null default '[]',
    permanent_invigilator               jsonb not null default '[]',
    assistant_permanent_invigilator     jsonb not null default '[]',
    class_3_worker                      jsonb not null default '[]',
    class_4_worker                      jsonb not null default '[]',
    created_at                          timestamptz not null default now(),
    unique (center_id, date, shift)
);
create index idx_shift_assignments_center on shift_assignments(center_id);

-- ----------------------------------------------------------------------------
-- room_invigilator_assignments
-- ----------------------------------------------------------------------------
create table room_invigilator_assignments (
    id             bigserial primary key,
    center_id      uuid not null references exam_centers(id) on delete cascade,
    date           date not null,
    shift          text not null,
    room_num       text not null,
    invigilators   jsonb not null default '[]',
    created_at     timestamptz not null default now(),
    unique (center_id, date, shift, room_num)
);
create index idx_room_invigilators_center on room_invigilator_assignments(center_id);

-- ----------------------------------------------------------------------------
-- prep_closing_assignments
-- ----------------------------------------------------------------------------
create table prep_closing_assignments (
    id                  bigserial primary key,
    center_id           uuid not null references exam_centers(id) on delete cascade,
    name                text not null,
    role                text,
    prep_days           jsonb not null default '[]',
    closing_days        jsonb not null default '[]',
    selected_classes    jsonb not null default '[]',
    created_at          timestamptz not null default now()
);
create index idx_prep_closing_center on prep_closing_assignments(center_id);

-- ----------------------------------------------------------------------------
-- exam_team_members
-- ----------------------------------------------------------------------------
create table exam_team_members (
    id            bigserial primary key,
    center_id     uuid not null references exam_centers(id) on delete cascade,
    name          text not null,
    created_at    timestamptz not null default now(),
    unique (center_id, name)
);
create index idx_exam_team_members_center on exam_team_members(center_id);

-- ----------------------------------------------------------------------------
-- global_settings (per-center config, e.g. holiday dates)
-- ----------------------------------------------------------------------------
create table global_settings (
    id               bigserial primary key,
    center_id        uuid not null references exam_centers(id) on delete cascade,
    setting_key      text not null,
    setting_value    jsonb,
    created_at       timestamptz not null default now(),
    unique (center_id, setting_key)
);
create index idx_global_settings_center on global_settings(center_id);

-- ----------------------------------------------------------------------------
-- remuneration_rates (persists what used to be re-typed manual_rates each run)
-- ----------------------------------------------------------------------------
create table remuneration_rates (
    id            bigserial primary key,
    center_id     uuid not null references exam_centers(id) on delete cascade,
    role_key      text not null,
    rate          numeric,
    unit          text,
    updated_at    timestamptz not null default now(),
    unique (center_id, role_key)
);
create index idx_remuneration_rates_center on remuneration_rates(center_id);

-- ----------------------------------------------------------------------------
-- remuneration_bills (history of generated bills)
-- ----------------------------------------------------------------------------
create table remuneration_bills (
    id                    bigserial primary key,
    center_id             uuid not null references exam_centers(id) on delete cascade,
    bill_name             text,
    selected_classes      jsonb,
    individual_bills      jsonb,
    role_summary          jsonb,
    class_workers         jsonb,
    generated_at          timestamptz not null default now()
);
create index idx_remuneration_bills_center on remuneration_bills(center_id);

-- ----------------------------------------------------------------------------
-- room_capacities — uploaded per-center room capacity sheet, used by the
-- automatic seat-proposal engine (auto_seat_planner.py)
-- ----------------------------------------------------------------------------
create table room_capacities (
    id                          bigserial primary key,
    center_id                   uuid not null references exam_centers(id) on delete cascade,
    room_no                     text not null,
    each_table_capacity         integer not null default 1,
    capacity_n                  integer not null,
    seat_type                   text not null,
    accommodate_2_same          boolean not null default false,
    accommodate_2_diff          boolean not null default false,
    capacity_easy               integer not null,
    capacity_normal             integer not null,
    capacity_tight              integer not null,
    created_at                  timestamptz not null default now(),
    unique (center_id, room_no)
);
create index idx_room_capacities_center on room_capacities(center_id);

-- ============================================================================
-- Row-Level Security — backstop only.
-- RLS is enabled everywhere with NO policies for anon/authenticated roles,
-- so those roles are denied by default (Postgres RLS default-denies when no
-- policy matches). The service_role key your Streamlit app uses bypasses RLS
-- automatically and is completely unaffected — no app changes needed.
-- ============================================================================

alter table exam_centers enable row level security;
alter table timetable enable row level security;
alter table sitting_plan enable row level security;
alter table assigned_seats enable row level security;
alter table attestation_data enable row level security;
alter table cs_reports enable row level security;
alter table shift_assignments enable row level security;
alter table room_invigilator_assignments enable row level security;
alter table prep_closing_assignments enable row level security;
alter table exam_team_members enable row level security;
alter table global_settings enable row level security;
alter table remuneration_rates enable row level security;
alter table remuneration_bills enable row level security;
alter table room_capacities enable row level security;

-- No CREATE POLICY statements — intentional. If you later want the
-- Streamlit app itself to use the anon key instead of service_role
-- (e.g. to further limit blast radius), you'd add per-center policies
-- keyed on a JWT claim or a signed request header at that point.
