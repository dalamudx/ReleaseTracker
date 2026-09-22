-- migrate:up
CREATE TABLE deployment_admission_notification_outbox (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    admission_event_id INTEGER NOT NULL REFERENCES deployment_admission_events(id),
    notifier_id INTEGER NOT NULL,
    event TEXT NOT NULL,
    payload TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('pending','sending','delivered','failed','discarded')),
    attempts INTEGER NOT NULL DEFAULT 0,
    available_at REAL NOT NULL,
    created_at REAL NOT NULL,
    delivered_at REAL,
    UNIQUE(admission_event_id, notifier_id)
);
CREATE INDEX deployment_admission_notification_due ON deployment_admission_notification_outbox(status, available_at, id);

-- migrate:down
DROP TABLE deployment_admission_notification_outbox;
