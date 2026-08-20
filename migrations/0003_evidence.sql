CREATE TABLE source_artifact (
    id TEXT PRIMARY KEY,
    source_type TEXT NOT NULL,
    relative_path TEXT NOT NULL UNIQUE,
    sha256 TEXT NOT NULL,
    size_bytes INTEGER NOT NULL CHECK (size_bytes >= 0),
    media_type TEXT,
    extraction_status TEXT NOT NULL DEFAULT 'pending',
    extraction_version TEXT,
    confidentiality TEXT NOT NULL DEFAULT 'private',
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL
);

CREATE TABLE source_span (
    id TEXT PRIMARY KEY,
    source_artifact_id TEXT NOT NULL REFERENCES source_artifact(id),
    locator_type TEXT NOT NULL,
    locator_json TEXT NOT NULL,
    text_sha256 TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE experience (
    id TEXT PRIMARY KEY,
    organization TEXT,
    title TEXT,
    start_date TEXT,
    end_date TEXT,
    summary TEXT,
    verification_state TEXT NOT NULL DEFAULT 'proposed',
    row_version INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE project (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    project_type TEXT,
    start_date TEXT,
    end_date TEXT,
    summary TEXT,
    confidentiality TEXT NOT NULL DEFAULT 'private',
    verification_state TEXT NOT NULL DEFAULT 'proposed',
    row_version INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE project_component (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES project(id),
    name TEXT NOT NULL,
    component_type TEXT,
    summary TEXT,
    ownership_level TEXT,
    verification_state TEXT NOT NULL DEFAULT 'proposed',
    row_version INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE claim (
    id TEXT PRIMARY KEY,
    project_component_id TEXT REFERENCES project_component(id),
    experience_id TEXT REFERENCES experience(id),
    claim_text TEXT NOT NULL,
    claim_type TEXT,
    verification_state TEXT NOT NULL DEFAULT 'proposed',
    wording_strength TEXT NOT NULL DEFAULT 'exact',
    confidentiality TEXT NOT NULL DEFAULT 'private',
    superseded_by_id TEXT REFERENCES claim(id),
    row_version INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    CHECK (project_component_id IS NOT NULL OR experience_id IS NOT NULL)
);

CREATE TABLE claim_evidence (
    claim_id TEXT NOT NULL REFERENCES claim(id),
    source_span_id TEXT NOT NULL REFERENCES source_span(id),
    support_type TEXT NOT NULL,
    confidence TEXT NOT NULL,
    PRIMARY KEY (claim_id, source_span_id)
);

CREATE TABLE metric (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    value_text TEXT NOT NULL,
    unit TEXT,
    context TEXT,
    verification_state TEXT NOT NULL DEFAULT 'proposed',
    created_at TEXT NOT NULL
);

CREATE TABLE claim_metric (
    claim_id TEXT NOT NULL REFERENCES claim(id),
    metric_id TEXT NOT NULL REFERENCES metric(id),
    PRIMARY KEY (claim_id, metric_id)
);

CREATE TABLE skill (
    id TEXT PRIMARY KEY,
    canonical_name TEXT NOT NULL UNIQUE,
    category TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE skill_alias (
    skill_id TEXT NOT NULL REFERENCES skill(id),
    alias TEXT NOT NULL UNIQUE,
    PRIMARY KEY (skill_id, alias)
);

CREATE TABLE claim_skill (
    claim_id TEXT NOT NULL REFERENCES claim(id),
    skill_id TEXT NOT NULL REFERENCES skill(id),
    relation TEXT NOT NULL,
    PRIMARY KEY (claim_id, skill_id)
);
