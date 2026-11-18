# Instance configuration

## Purpose

Give administrators a single persistent source for instance policy that is honored by the web application, background workers and maintenance commands across upgrades.

## ADDED Requirements

### Requirement: Public base URL
The application SHALL use the existing `system.base_url` for its configured public base URL, including deployment subpaths. It SHALL ignore `FRONTEND_URL` during installation, upgrade and normal operation.

#### Scenario: OIDC return after upgrade
- **WHEN** a saved public URL contains a subpath and an obsolete frontend URL points elsewhere
- **THEN** the OIDC return address uses the saved origin and subpath and the stored setting retains its value.

#### Scenario: Public URL left empty
- **WHEN** the saved setting is empty and the obsolete variable is present
- **THEN** the field stays empty and each consumer follows its existing unconfigured behavior.

### Requirement: Administrators control operational policy
Administrators SHALL be able to read and edit shared deployment and recovery concurrency, worker polling, image pull grace, runtime health inspections, online restore, pre-migration backup, CORS origins and trusted inbound proxies in global settings. Settings SHALL retain their existing validation, defaults and platform constraints. Metrics credential management SHALL support setting, rotating and revoking the credential with masked status.

#### Scenario: Reduce concurrency during deployments
- **WHEN** an administrator lowers the shared limit below the active worker count
- **THEN** active work runs to completion and new mutations wait for capacity under the new limit and existing resource conflict rules.

#### Scenario: Save inspection and backup policies
- **WHEN** an administrator changes inspection frequency, backup retention count or the pre-migration backup preference
- **THEN** subsequent inspections, backup pruning and migration commands consume the saved policies after restart as well as during their next applicable operation.

#### Scenario: Forwarded request through a trusted ingress
- **WHEN** a trusted ingress forwards an HTTPS request
- **THEN** login throttling uses the resolved client address and session handling recognizes the forwarded scheme according to the saved inbound trust policy.

#### Scenario: Protect metrics credential
- **WHEN** a metrics token is saved or rotated
- **THEN** ordinary settings responses disclose only configured status and rotated credentials lose access.

### Requirement: Fixed data location and upgrade continuity
Server, migration, backup, restore and maintenance entry points SHALL use the same fixed data directory. The upgrade procedure SHALL preserve the existing database, associated system secrets and backups and prevent silently starting with an empty replacement database when transitioning a known custom path.

#### Scenario: Move a custom data mount
- **WHEN** an operator follows the upgrade procedure for an existing custom database location
- **THEN** the fixed location contains the original database and matching secrets, migration targets that database, and previous backups remain available.

### Requirement: Retire policy environment overrides
An explicit upgrade operation SHALL import supported legacy policy values once, validate them before persistence, preserve existing saved values, and protect secrets. Subsequent operation SHALL use persisted configuration. Timezone and existing key management SHALL retain their current semantics.

#### Scenario: Repeated upgrade after an administrator edits a setting
- **WHEN** an operator retries the upgrade with stale environment values
- **THEN** the previously saved settings, including explicitly empty and disabled values, are preserved.
