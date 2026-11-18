# Tasks

## 1. Public base URL

- [x] 1.1 Use public base URL terminology in the settings page, translations, OIDC callback and both deployment guides. Check tracked production code and configuration examples for stale frontend URL naming.
- [x] 1.2 Exercise saved, empty and subpath public URLs with a conflicting `FRONTEND_URL` present. Verify the settings API, OIDC redirects, generated links and fresh-install defaults.

## 2. Settings and upgrade continuity

- [x] 2.1 Define validated administrator settings, defaults and masked credential operations. Test unauthorized requests, invalid ranges, origin lists, proxy addresses, and secret handling in list and individual setting responses.
- [x] 2.2 Implement and document an explicit idempotent import of effective legacy policy settings. Verify persisted values take priority, invalid imports are atomic, and retries preserve edits. Check credentials and upgrade logs for disclosure.
- [x] 2.3 Align CLI, server, dbmate and container data paths and remove the DB path override. Document backup, custom-volume transition and rollback. Test legacy custom filenames, an existing restore journal, fresh initialization and an interrupted upgrade using isolated fixtures.

## 3. Queue, readiness and backup policy

- [x] 3.1 Add shared concurrency and polling controls. Test live increases, decreases, shared deploy/recover limits and unchanged resource conflict checks. Update queue documentation with the actual settings.
- [x] 3.2 Add pull grace and ongoing health inspection controls. Test that new observations use the saved grace, in-flight deadlines survive edits and the watch starts and stops with its configured interval. Update health guidance.
- [x] 3.3 Expose and consume online restore and pre-migration backup choices. Exercise backup rotation, POSIX capability gating, recovery of an in-progress restore and the migration CLI against old and current schemas. Update upgrade and backup instructions.

## 4. Inbound network and metrics settings

- [x] 4.1 Apply trusted proxies before IP-based rate limiting and secure-session scheme checks. Update ingress guidance and verify trusted and untrusted peers, a proxy chain, HTTPS sessions and OIDC in isolation.
- [x] 4.2 Apply CORS changes consistently in CORS middleware and session-origin checks. Verify allowed and refused origins after a save and after restart.
- [x] 4.3 Implement token configuration, rotation and revocation for metrics. Verify scrape authorization, redaction and secret lifecycle including backup and restore. Update monitoring instructions.

## 5. Integration verification

- [x] 5.1 Exercise all global configuration controls through the administrator UI, reload and restart an isolated instance and verify the effective worker, CLI and proxy behavior.
- [x] 5.2 Audit container entry points, server-library defaults, tests and bilingual examples against retired variables. Verify only the explicit upgrade operation reads legacy policy variables and basic timezone and key management pass existing tests.
