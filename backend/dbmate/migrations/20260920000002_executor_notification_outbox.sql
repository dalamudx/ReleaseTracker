-- migrate:up
-- No notifier credentials or raw probe responses belong in this table.
CREATE TABLE executor_notification_outbox (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL,
    notifier_id INTEGER NOT NULL,
    event TEXT NOT NULL,
    final_result TEXT NOT NULL,
    payload TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'sending', 'delivered', 'failed', 'discarded')),
    attempts INTEGER NOT NULL DEFAULT 0,
    available_at REAL NOT NULL,
    created_at REAL NOT NULL,
    delivered_at REAL,
    UNIQUE(run_id, notifier_id, final_result)
);
CREATE INDEX executor_notification_outbox_due ON executor_notification_outbox(status, available_at);

-- migrate:down
DROP TABLE executor_notification_outbox;
