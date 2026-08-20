CREATE TABLE app_metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE workflow_run (
    id TEXT PRIMARY KEY,
    workflow_type TEXT NOT NULL,
    state TEXT NOT NULL CHECK (state IN (
        'pending', 'running', 'waiting_for_approval', 'completed',
        'completed_with_warnings', 'failed_retryable', 'failed_terminal', 'cancelled'
    )),
    idempotency_key TEXT NOT NULL UNIQUE,
    input_sha256 TEXT NOT NULL,
    config_sha256 TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE workflow_checkpoint (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES workflow_run(id) ON DELETE CASCADE,
    phase TEXT NOT NULL,
    state TEXT NOT NULL,
    input_sha256 TEXT,
    output_json TEXT,
    attempt_count INTEGER NOT NULL DEFAULT 1 CHECK (attempt_count > 0),
    created_at TEXT NOT NULL,
    UNIQUE (run_id, phase)
);

CREATE TABLE proposal (
    id TEXT PRIMARY KEY,
    proposal_type TEXT NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('pending', 'approved', 'rejected', 'expired', 'conflict')),
    payload_json TEXT NOT NULL,
    payload_sha256 TEXT NOT NULL,
    expected_versions_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    expires_at TEXT,
    decided_at TEXT
);

CREATE TABLE approval_event (
    id TEXT PRIMARY KEY,
    proposal_id TEXT NOT NULL REFERENCES proposal(id),
    decision TEXT NOT NULL CHECK (decision IN ('approved', 'rejected')),
    actor TEXT NOT NULL,
    reason TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE audit_event (
    id TEXT PRIMARY KEY,
    event_type TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    actor TEXT NOT NULL,
    before_sha256 TEXT,
    after_sha256 TEXT,
    run_id TEXT REFERENCES workflow_run(id),
    proposal_id TEXT REFERENCES proposal(id),
    created_at TEXT NOT NULL
);

CREATE INDEX audit_event_entity_idx ON audit_event(entity_type, entity_id, created_at);

CREATE TABLE artifact (
    id TEXT PRIMARY KEY,
    artifact_type TEXT NOT NULL,
    relative_path TEXT NOT NULL UNIQUE,
    sha256 TEXT NOT NULL,
    size_bytes INTEGER NOT NULL CHECK (size_bytes >= 0),
    tier TEXT,
    confidentiality TEXT NOT NULL DEFAULT 'private',
    immutable INTEGER NOT NULL DEFAULT 0 CHECK (immutable IN (0, 1)),
    created_at TEXT NOT NULL
);

INSERT INTO app_metadata(key, value, updated_at)
VALUES ('application_version', '0.1.0', strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));
