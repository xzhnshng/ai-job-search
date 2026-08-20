CREATE TABLE company (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    normalized_name TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL
);

CREATE TABLE job (
    id TEXT PRIMARY KEY,
    company_id TEXT NOT NULL REFERENCES company(id),
    title TEXT NOT NULL,
    canonical_url TEXT,
    location TEXT,
    description_sha256 TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(company_id, title, canonical_url)
);

CREATE TABLE application (
    id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL REFERENCES job(id),
    role_track TEXT,
    applied_date TEXT,
    current_stage TEXT NOT NULL DEFAULT 'planning',
    current_result TEXT,
    next_action TEXT,
    next_action_date TEXT,
    notes TEXT,
    row_version INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE application_event (
    id TEXT PRIMARY KEY,
    application_id TEXT NOT NULL REFERENCES application(id),
    event_type TEXT NOT NULL,
    occurred_at TEXT NOT NULL,
    payload_json TEXT NOT NULL DEFAULT '{}',
    idempotency_key TEXT NOT NULL,
    supersedes_event_id TEXT REFERENCES application_event(id),
    created_at TEXT NOT NULL,
    UNIQUE(application_id, idempotency_key)
);

CREATE INDEX application_event_timeline_idx
ON application_event(application_id, occurred_at, created_at);

CREATE TABLE interview_event (
    id TEXT PRIMARY KEY,
    application_id TEXT NOT NULL REFERENCES application(id),
    interview_type TEXT NOT NULL,
    scheduled_at TEXT NOT NULL,
    completed_at TEXT,
    result TEXT,
    notes TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE compensation_record (
    id TEXT PRIMARY KEY,
    application_id TEXT NOT NULL REFERENCES application(id),
    kind TEXT NOT NULL,
    amount_min REAL,
    amount_max REAL,
    currency TEXT NOT NULL,
    period TEXT NOT NULL,
    source TEXT,
    recorded_at TEXT NOT NULL,
    CHECK (amount_min IS NULL OR amount_min >= 0),
    CHECK (amount_max IS NULL OR amount_max >= 0)
);

CREATE TABLE offer (
    id TEXT PRIMARY KEY,
    application_id TEXT NOT NULL REFERENCES application(id),
    compensation_record_id TEXT REFERENCES compensation_record(id),
    received_date TEXT NOT NULL,
    decision_deadline TEXT,
    decision TEXT,
    notes TEXT,
    created_at TEXT NOT NULL
);
