CREATE TABLE company_plan_entry (
    company_id TEXT NOT NULL REFERENCES company(id),
    market TEXT NOT NULL CHECK (market IN ('technology', 'trading')),
    priority_rank INTEGER NOT NULL CHECK (priority_rank > 0),
    priority_tier TEXT CHECK (priority_tier IN ('A', 'B', 'C')),
    target_roles TEXT NOT NULL DEFAULT '',
    ml_opportunity REAL CHECK (ml_opportunity IS NULL OR (ml_opportunity >= 0 AND ml_opportunity <= 5)),
    income_potential REAL CHECK (income_potential IS NULL OR (income_potential >= 0 AND income_potential <= 5)),
    fit REAL CHECK (fit IS NULL OR (fit >= 0 AND fit <= 5)),
    recommendation TEXT,
    active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
    source_artifact_id TEXT NOT NULL REFERENCES source_artifact(id),
    source_sha256 TEXT NOT NULL,
    approved_proposal_id TEXT NOT NULL REFERENCES proposal(id),
    approved_at TEXT NOT NULL,
    row_version INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (company_id, market)
);

CREATE UNIQUE INDEX company_plan_active_rank_idx
ON company_plan_entry(market, priority_rank)
WHERE active = 1;

CREATE INDEX company_plan_active_market_idx
ON company_plan_entry(active, market, priority_rank);

CREATE TABLE company_alias (
    company_id TEXT NOT NULL REFERENCES company(id),
    alias TEXT NOT NULL,
    normalized_alias TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL,
    PRIMARY KEY (company_id, normalized_alias)
);

CREATE TABLE company_source (
    id TEXT PRIMARY KEY,
    company_id TEXT NOT NULL REFERENCES company(id),
    career_url TEXT NOT NULL,
    adapter_type TEXT,
    source_key TEXT,
    authority TEXT NOT NULL DEFAULT 'official' CHECK (authority = 'official'),
    priority_tier TEXT CHECK (priority_tier IN ('A', 'B', 'C')),
    enabled INTEGER NOT NULL DEFAULT 0 CHECK (enabled IN (0, 1)),
    health_status TEXT NOT NULL DEFAULT 'not_checked',
    last_checked_at TEXT,
    row_version INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (company_id, career_url)
);

CREATE TABLE source_run (
    id TEXT PRIMARY KEY,
    company_source_id TEXT NOT NULL REFERENCES company_source(id),
    idempotency_key TEXT NOT NULL,
    status TEXT NOT NULL,
    started_at TEXT NOT NULL,
    completed_at TEXT,
    cursor_json TEXT,
    record_count INTEGER CHECK (record_count IS NULL OR record_count >= 0),
    error_code TEXT,
    error_message TEXT,
    UNIQUE (company_source_id, idempotency_key)
);

CREATE TABLE source_health_event (
    id TEXT PRIMARY KEY,
    company_source_id TEXT NOT NULL REFERENCES company_source(id),
    source_run_id TEXT REFERENCES source_run(id),
    health_status TEXT NOT NULL,
    detail_json TEXT NOT NULL DEFAULT '{}',
    occurred_at TEXT NOT NULL
);

CREATE INDEX source_health_timeline_idx
ON source_health_event(company_source_id, occurred_at);

CREATE TABLE raw_observation (
    id TEXT PRIMARY KEY,
    source_run_id TEXT NOT NULL REFERENCES source_run(id),
    source_record_key TEXT NOT NULL,
    external_job_id TEXT,
    content_sha256 TEXT NOT NULL,
    artifact_id TEXT REFERENCES artifact(id),
    observed_at TEXT NOT NULL,
    UNIQUE (source_run_id, source_record_key)
);

CREATE TABLE job_source_ref (
    id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL REFERENCES job(id),
    company_source_id TEXT NOT NULL REFERENCES company_source(id),
    external_job_id TEXT,
    canonical_url TEXT,
    application_url TEXT,
    current_open INTEGER CHECK (current_open IS NULL OR current_open IN (0, 1)),
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    UNIQUE (company_source_id, external_job_id)
);

CREATE TABLE job_snapshot (
    id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL REFERENCES job(id),
    job_source_ref_id TEXT NOT NULL REFERENCES job_source_ref(id),
    source_run_id TEXT NOT NULL REFERENCES source_run(id),
    content_sha256 TEXT NOT NULL,
    normalized_json TEXT NOT NULL,
    source_published_at TEXT,
    source_updated_at TEXT,
    observed_at TEXT NOT NULL,
    UNIQUE (job_id, content_sha256)
);

CREATE TABLE job_freshness (
    id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL REFERENCES job(id),
    job_source_ref_id TEXT NOT NULL REFERENCES job_source_ref(id),
    classification TEXT NOT NULL,
    confidence TEXT NOT NULL,
    decision_rule_version TEXT NOT NULL,
    evidence_json TEXT NOT NULL,
    detection_window_start TEXT,
    detection_window_end TEXT NOT NULL,
    classified_at TEXT NOT NULL
);

CREATE INDEX job_freshness_latest_idx
ON job_freshness(job_id, classified_at);

CREATE TABLE job_identity_candidate (
    id TEXT PRIMARY KEY,
    left_job_id TEXT NOT NULL REFERENCES job(id),
    right_job_id TEXT NOT NULL REFERENCES job(id),
    confidence REAL NOT NULL CHECK (confidence >= 0 AND confidence <= 1),
    evidence_json TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT NOT NULL,
    CHECK (left_job_id <> right_job_id),
    UNIQUE (left_job_id, right_job_id)
);
