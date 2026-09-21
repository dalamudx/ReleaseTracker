-- migrate:up
CREATE TABLE ssh_compose_ownership (
    executor_id INTEGER PRIMARY KEY REFERENCES executors(id) ON DELETE CASCADE,
    runtime_key TEXT,
    project TEXT NOT NULL,
    working_dir TEXT NOT NULL,
    UNIQUE(runtime_key, project)
);
-- Existing SSH executors are intentionally unverified and cannot execute until reviewed.
INSERT INTO ssh_compose_ownership(executor_id, project, working_dir)
SELECT id, COALESCE(json_extract(target_ref, '$.project'), ''),
       COALESCE(json_extract(target_ref, '$.working_dir'), '')
FROM executors WHERE runtime_type = 'ssh';

-- migrate:down
DROP TABLE ssh_compose_ownership;
