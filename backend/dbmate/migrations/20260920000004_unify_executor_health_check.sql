-- migrate:up
-- Pre-readiness profiles used strategy=none to mean disabled. Make that
-- intent explicit while mapping every enabled legacy strategy into the
-- durable post-update readiness observer.
UPDATE executors
SET health_check = json_set(
    health_check,
    '$.readiness_enabled',
    json(
        CASE
            WHEN COALESCE(json_extract(health_check, '$.use_default_strategy'), 0) = 1
              OR COALESCE(json_extract(health_check, '$.strategy'), 'none') <> 'none'
            THEN 'true'
            ELSE 'false'
        END
    ),
    '$.notify_result', json('false'),
    '$.use_system_readiness_defaults', json('true'),
    '$.readiness_timeout_seconds', 600,
    '$.readiness_interval_seconds', 5,
    '$.readiness_attempt_timeout_seconds', 10,
    '$.readiness_stable_seconds', 10
)
WHERE json_valid(health_check)
  AND json_type(health_check, '$.readiness_enabled') IS NULL;

-- Previous builds allowed the old strategy and the new readiness switch to
-- disagree. The single switch is authoritative for probing: disabled means
-- no retained probe strategy. Notification intent is preserved independently.
UPDATE executors
SET health_check = json_set(
    health_check,
    '$.strategy', 'none',
    '$.use_default_strategy', json('false'),
    '$.failure_policy', 'mark_failed',
    '$.services', json('null'),
    '$.http', json('null'),
    '$.tcp', json('null')
)
WHERE json_valid(health_check)
  AND json_extract(health_check, '$.readiness_enabled') = 0;

-- migrate:down
-- Irreversible data migration: removing readiness_enabled would make a
-- disabled legacy profile ambiguous again. Schema rollback requires no action.
