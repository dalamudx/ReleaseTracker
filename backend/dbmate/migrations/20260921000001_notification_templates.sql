-- migrate:up
CREATE TABLE notification_templates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL,
    body TEXT NOT NULL,
    translations TEXT NOT NULL DEFAULT '{}',
    revision INTEGER NOT NULL DEFAULT 1,
    updated_at TEXT NOT NULL
);
ALTER TABLE notifiers ADD COLUMN template_id INTEGER REFERENCES notification_templates(id);
CREATE TRIGGER notifier_template_insert BEFORE INSERT ON notifiers
WHEN NEW.template_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM notification_templates WHERE id=NEW.template_id)
BEGIN SELECT RAISE(ABORT, 'notification_template_not_found'); END;
CREATE TRIGGER notifier_template_update BEFORE UPDATE OF template_id ON notifiers
WHEN NEW.template_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM notification_templates WHERE id=NEW.template_id)
BEGIN SELECT RAISE(ABORT, 'notification_template_not_found'); END;
CREATE TRIGGER notification_template_in_use BEFORE DELETE ON notification_templates
WHEN EXISTS (SELECT 1 FROM notifiers WHERE template_id=OLD.id)
BEGIN SELECT RAISE(ABORT, 'notification_template_in_use'); END;

-- migrate:down
DROP TRIGGER notification_template_in_use;
DROP TRIGGER notifier_template_update;
DROP TRIGGER notifier_template_insert;
ALTER TABLE notifiers DROP COLUMN template_id;
DROP TABLE notification_templates;
