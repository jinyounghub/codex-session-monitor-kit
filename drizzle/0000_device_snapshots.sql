CREATE TABLE device_snapshots (
    device_id TEXT PRIMARY KEY NOT NULL,
    label TEXT NOT NULL,
    observed_at INTEGER NOT NULL,
    received_at INTEGER NOT NULL,
    payload TEXT NOT NULL
);
