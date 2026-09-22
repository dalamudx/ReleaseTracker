-- migrate:up
CREATE TABLE podman_target_lineages (
 executor_id INTEGER PRIMARY KEY,
 target_id TEXT NOT NULL UNIQUE,
 mode TEXT NOT NULL CHECK(mode IN ('container','docker_compose')),
 target_fingerprint TEXT NOT NULL,
 generation INTEGER NOT NULL CHECK(generation>=1),
 members TEXT NOT NULL,
 created_at REAL NOT NULL,
 updated_at REAL NOT NULL
);
CREATE TABLE podman_target_lineage_transitions (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 executor_id INTEGER NOT NULL,
 task_id INTEGER,
 executor_run_id INTEGER,
 from_generation INTEGER NOT NULL,
 to_generation INTEGER NOT NULL,
 old_members TEXT NOT NULL,
 new_members TEXT NOT NULL,
 created_at REAL NOT NULL,
 CHECK((task_id IS NOT NULL) != (executor_run_id IS NOT NULL)),
 UNIQUE(executor_id,to_generation)
);
CREATE INDEX podman_lineage_transition_executor ON podman_target_lineage_transitions(executor_id,id);

-- migrate:down
CREATE TEMP TABLE podman_lineage_down_guard (active INTEGER CHECK(active=0));
INSERT INTO podman_lineage_down_guard SELECT COUNT(*) FROM tasks
WHERE kind IN ('deploy','recover') AND state IN ('running','needs_attention');
DROP TABLE podman_lineage_down_guard;
DROP TABLE podman_target_lineage_transitions;
DROP TABLE podman_target_lineages;
