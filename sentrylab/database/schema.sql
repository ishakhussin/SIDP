PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS incidents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    camera_id TEXT NOT NULL,
    detector TEXT NOT NULL,
    current_level TEXT NOT NULL CHECK (
        current_level IN ('WARNING', 'UNSAFE', 'CLOSED')
    ),
    opened_at REAL NOT NULL,
    unsafe_at REAL,
    closed_at REAL,
    close_reason TEXT,
    clip_path TEXT,
    overlay_enabled INTEGER NOT NULL DEFAULT 1,
    updated_at REAL NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS one_active_incident_per_camera_detector
ON incidents(camera_id, detector)
WHERE closed_at IS NULL;

CREATE INDEX IF NOT EXISTS incidents_time_index
ON incidents(opened_at DESC);

CREATE INDEX IF NOT EXISTS incidents_camera_detector_index
ON incidents(camera_id, detector, opened_at DESC);

CREATE TABLE IF NOT EXISTS incident_subjects (
    incident_id INTEGER NOT NULL,
    subject_id TEXT NOT NULL,
    subject_kind TEXT NOT NULL CHECK (subject_kind IN ('PERSON', 'PAIR')),
    current_level TEXT NOT NULL CHECK (
        current_level IN ('SAFE', 'WARNING', 'UNSAFE', 'UNKNOWN')
    ),
    first_seen_at REAL NOT NULL,
    last_seen_at REAL NOT NULL,
    details_json TEXT NOT NULL DEFAULT '{}',
    PRIMARY KEY (incident_id, subject_kind, subject_id),
    FOREIGN KEY (incident_id) REFERENCES incidents(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS incident_transitions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    incident_id INTEGER NOT NULL,
    camera_id TEXT NOT NULL,
    detector TEXT NOT NULL,
    subject_id TEXT NOT NULL,
    subject_kind TEXT NOT NULL CHECK (subject_kind IN ('PERSON', 'PAIR')),
    from_level TEXT NOT NULL,
    to_level TEXT NOT NULL,
    occurred_at REAL NOT NULL,
    details_json TEXT NOT NULL DEFAULT '{}',
    FOREIGN KEY (incident_id) REFERENCES incidents(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS transitions_incident_time_index
ON incident_transitions(incident_id, occurred_at);

CREATE TABLE IF NOT EXISTS monitoring_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    camera_id TEXT NOT NULL,
    level TEXT NOT NULL DEFAULT 'SAFE' CHECK (level = 'SAFE'),
    message TEXT NOT NULL,
    people_count INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS monitoring_logs_camera_time_index
ON monitoring_logs(camera_id, created_at DESC);

CREATE TABLE IF NOT EXISTS detector_settings (
    camera_id TEXT NOT NULL,
    detector TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 0,
    overlay_enabled INTEGER NOT NULL DEFAULT 1,
    updated_at REAL NOT NULL,
    PRIMARY KEY (camera_id, detector)
);

CREATE TABLE IF NOT EXISTS restricted_zones (
    camera_id TEXT NOT NULL,
    preset_name TEXT NOT NULL DEFAULT 'HOME',
    points_json TEXT NOT NULL,
    updated_at REAL NOT NULL,
    PRIMARY KEY (camera_id, preset_name)
);
