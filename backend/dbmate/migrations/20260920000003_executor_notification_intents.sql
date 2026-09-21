-- migrate:up
-- Independent of run retention: clearing history must not erase unsent results.
CREATE TABLE executor_notification_intents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL,
    final_result TEXT NOT NULL,
    payload TEXT NOT NULL,
    notify_health_result INTEGER NOT NULL CHECK (notify_health_result IN (0,1)),
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','expanded')),
    attempts INTEGER NOT NULL DEFAULT 0,
    available_at REAL NOT NULL,
    created_at REAL NOT NULL,
    expanded_at REAL,
    UNIQUE(run_id,final_result)
);
CREATE INDEX executor_notification_intents_due ON executor_notification_intents(status,available_at);

-- migrate:down
DROP TABLE executor_notification_intents;
