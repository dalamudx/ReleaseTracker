export type TrackerChannelType = 'github' | 'gitlab' | 'gitea' | 'helm' | 'container'
export type TrackerSourceType = 'github' | 'gitlab' | 'gitea' | 'helm' | 'container'
export type TrackerChangelogPolicy = 'primary_channel' | 'primary_source'
export type GitHubFetchMode = 'graphql_first' | 'rest_first'

export interface ReleaseNotesSubject {
    tracker_name: string
    tracker_type?: TrackerChannelType | TrackerSourceType
    name: string
    tag_name: string
    version: string
    published_at: string
    url: string
    changelog_url?: string | null
    prerelease: boolean
    body?: string | null
    channel_name?: string | null
    channel_type?: ReleaseChannel['type']
    channel_keys?: string[]
}

export interface ReleaseHistoryPrimarySource {
    source_key: string
    source_type: TrackerSourceType
    source_release_history_id: number
}

export interface ReleaseHistoryItem extends ReleaseNotesSubject {
    tracker_release_history_id: number
    identity_key: string
    digest: string
    app_version?: string | null
    chart_version?: string | null
    commit_sha?: string | null
    primary_source: ReleaseHistoryPrimarySource | null
    created_at: string
}

export interface LatestCurrentReleaseSummary extends ReleaseNotesSubject {
    tracker_release_history_id: number
    identity_key: string
    digest: string
    primary_source: ReleaseHistoryPrimarySource | null
    primary_source_type?: TrackerChannelType | null
    projected_at: string | null
}

export interface TrackerCurrentSourceContribution extends ReleaseNotesSubject {
    source_release_history_id: number
    source_key: string
    source_type: TrackerSourceType
    contribution_kind: 'primary' | 'supporting'
    digest: string
    app_version?: string | null
    chart_version?: string | null
    observed_at: string
}

export interface TrackerCurrentMatrixColumn {
    channel_key: string
    channel_type: ReleaseChannel['type']
    enabled: boolean
    channel_rank: number
}

export interface TrackerCurrentMatrixCell {
    channel_key: string
    channel_type: ReleaseChannel['type']
    selected: boolean
}

export interface TrackerCurrentMatrixRow {
    tracker_release_history_id: number
    identity_key: string
    version: string
    digest: string
    published_at: string
    matched_channel_count: number
    channel_keys: string[]
    primary_source: ReleaseHistoryPrimarySource | null
    source_contributions: TrackerCurrentSourceContribution[]
    cells: Record<string, TrackerCurrentMatrixCell | null>
}

export interface TrackerCurrentViewTracker {
    name: string
    primary_changelog_source_key: string | null
    sources: TrackerCurrentMatrixColumn[]
}

export interface TrackerCurrentView {
    tracker: TrackerCurrentViewTracker
    status: {
        last_check: string | null
        last_version: string | null
        error: string | null
    }
    latest_release: LatestCurrentReleaseSummary | null
    matrix: {
        columns: TrackerCurrentMatrixColumn[]
        rows: TrackerCurrentMatrixRow[]
    }
    projected_at: string | null
}

export interface TrackerReleaseHistoryResponse extends PaginatedResponse<ReleaseHistoryItem> {
    tracker: string
}

export interface TrackerStatusSummary {
    last_check: string | null
    last_version: string | null
    error: string | null
    source_count: number
    enabled_source_count: number
    source_types: TrackerSourceType[]
}

export interface TrackerChannel {
    id?: number | null
    aggregate_tracker_id?: number | null
    channel_key: string
    channel_type: TrackerChannelType
    enabled: boolean
    credential_name?: string | null
    channel_config: {
        repo?: string
        project?: string
        instance?: string
        chart?: string
        image?: string
        registry?: string
        fetch_mode?: GitHubFetchMode
    }
    release_channels?: ReleaseChannelInput[]
    channel_rank: number
    created_at?: string
    updated_at?: string
    source_key?: string
    source_type?: TrackerSourceType
    source_config?: {
        repo?: string
        project?: string
        instance?: string
        chart?: string
        image?: string
        registry?: string
        fetch_mode?: GitHubFetchMode
    }
    source_rank?: number
}

export type TrackerSource = TrackerChannel

export interface AggregateTracker {
    id?: number | null
    name: string
    enabled: boolean
    description?: string | null
    changelog_policy?: TrackerChangelogPolicy
    primary_changelog_source_key: string | null
    sources: TrackerChannel[]
    interval: number
    version_sort_mode: 'published_at' | 'semver'
    fetch_limit: number
    fetch_timeout: number
    fallback_tags: boolean
    github_fetch_mode?: GitHubFetchMode
    channels?: ReleaseChannel[]
    status: TrackerStatusSummary
    created_at?: string
    updated_at?: string
}

export interface TrackerStatus extends AggregateTracker {
    type?: TrackerChannelType
    last_check?: string | null
    last_version?: string | null
    error?: string | null
    channel_count?: number
}

export interface ReleaseChannelInput {
    release_channel_key?: string
    channel_key?: string
    key?: string
    name: 'stable' | 'prerelease' | 'beta' | 'canary'
    type?: 'release' | 'prerelease' | null
    include_pattern?: string | null
    exclude_pattern?: string | null
    enabled?: boolean
}

export interface ReleaseChannel extends ReleaseChannelInput {
    release_channel_key: string
    type: 'release' | 'prerelease' | null
    enabled: boolean
}

export interface TrackerConfig {
    name: string
    type: TrackerChannelType | TrackerSourceType
    enabled: boolean
    repo?: string
    project?: string
    instance?: string
    chart?: string
    image?: string        // docker: image name, such as "library/nginx"
    registry?: string     // docker: registry URL; leave empty to default to Docker Hub
    credential_name?: string
    channels?: ReleaseChannelInput[]
    interval?: number
    version_sort_mode?: 'published_at' | 'semver'
    fetch_limit?: number
    fetch_timeout?: number
    fallback_tags?: boolean
    github_fetch_mode?: GitHubFetchMode
    description?: string
}

export interface CanonicalReleaseObservationSummary {
    channel_release_observation_id?: number
    source_release_observation_id: number
    contribution_kind: 'primary' | 'supporting'
    channel_key?: string
    channel_type?: TrackerChannelType
    channel_release_key?: string
    source_key: string
    source_type: TrackerSourceType
    source_release_key: string
    version: string
    app_version?: string | null
    chart_version?: string | null
    tag_name: string
    published_at: string
    url: string
}

export interface CanonicalReleaseItem {
    id: number
    canonical_key: string
    version: string
    name: string
    tag_name: string
    published_at: string
    url: string
    changelog_url?: string | null
    prerelease: boolean
    body?: string | null
    app_version?: string | null
    chart_version?: string | null
    primary_tracker_channel?: {
        channel_key: string
        channel_type: TrackerChannelType
        channel_release_observation_id: number
    } | null
    primary_source?: {
        source_key: string
        source_type: TrackerSourceType
        source_release_observation_id: number
    } | null
    observations: CanonicalReleaseObservationSummary[]
    created_at: string
    updated_at: string
}

export interface CanonicalReleaseResponse {
    tracker: string
    items: CanonicalReleaseItem[]
}

export interface SourceReleaseObservationItem {
    id: number
    tracker_channel_id?: number
    channel_key?: string
    channel_type?: TrackerChannelType
    channel_rank?: number
    channel_release_key?: string
    tracker_source_id: number
    source_key: string
    source_type: TrackerSourceType
    source_rank: number
    source_release_key: string
    name: string
    tag_name: string
    version: string
    app_version?: string | null
    chart_version?: string | null
    published_at: string
    url: string
    changelog_url?: string | null
    prerelease: boolean
    body?: string | null
    commit_sha?: string | null
    raw_payload?: Record<string, unknown>
    observed_at: string
    created_at: string
    updated_at: string
}

export interface SourceReleaseObservationResponse {
    tracker: string
    source_key?: string | null
    items: SourceReleaseObservationItem[]
}

export interface ReleaseStats {
    total_releases: number
    total_trackers: number
    latest_update: string | null
    daily_stats: Array<{ date: string; channels: Record<string, number> }>
    recent_releases: number
    channel_stats: Record<string, number>
    release_type_stats: Record<string, number>
}

export type CredentialType =
    | 'github'
    | 'gitlab'
    | 'gitea'
    | 'helm'
    | 'docker'
    | 'docker_runtime'
    | 'podman_runtime'
    | 'kubernetes_runtime'
    | 'portainer_runtime'

export interface ApiCredential {
    id: number
    name: string
    type: CredentialType
    token: string
    secrets?: Record<string, unknown>
    secret_keys?: string[]
    description?: string | null
    created_at: string
}

export interface CredentialReferenceItem {
    id?: number
    name: string
    type: string
    tracker_id?: number
    tracker_name?: string
}

export interface CredentialReferencesResponse {
    credential_id: number
    references: Record<string, CredentialReferenceItem[]>
    counts: Record<string, number>
    deletable: boolean
}

export interface User {
    id: number
    username: string
    email: string
    avatar_url?: string
}

export interface TokenPair {
    access_token: string
    refresh_token: string
    token_type: string
    expires_in: number
}

export interface LoginResponse {
    user: User
    token: TokenPair
}

export type NotifierLanguage = 'en' | 'zh'

export interface Notifier {
    id: number
    name: string
    type: string
    url: string
    events: string[]
    enabled: boolean
    language: NotifierLanguage
    description?: string
    created_at: string
}

export interface SettingItem {
    key: string
    value: unknown
    description?: string
    updated_at?: string
}

export interface SecurityKeyInventory {
    credentials_token: number
    credentials_secrets: number
    oauth_provider_client_secret: number
    runtime_connection_secrets: number
}

export interface SecurityKeysStatus {
    jwt_secret: {
        configured: boolean
        fingerprint: string
        active_sessions: number
    }
    encryption_key: {
        configured: boolean
        fingerprint: string
        inventory: SecurityKeyInventory
        undecryptable_count: number
    }
}

export interface RotateSecurityKeyRequest {
    value?: string | null
    generate: boolean
}

export interface RotateJwtSecretResponse {
    fingerprint: string
    invalidated_sessions: number
    requires_reauth: boolean
}

export interface RotateEncryptionKeyResponse {
    fingerprint: string
    inventory: SecurityKeyInventory
    rotated: SecurityKeyInventory
    plaintext_reencrypted: number
    undecryptable_count: number
}

export interface PaginatedResponse<T> {
    items: T[]
    total: number
    skip?: number
    limit?: number
}

export type RuntimeType = 'docker' | 'podman' | 'kubernetes' | 'portainer'
export type RuntimeConnectionType = RuntimeType | 'portainer'
export type ExecutorUpdateMode = 'manual' | 'maintenance_window' | 'immediate'
export type ImageSelectionMode = 'replace_tag_on_current_image' | 'use_tracker_image_and_tag'
export type ImageReferenceMode = 'digest' | 'tag'
export type SupportedExecutorTargetMode = 'container' | 'portainer_stack' | 'docker_compose' | 'kubernetes_workload' | 'helm_release'
export type ExecutorTargetMode = SupportedExecutorTargetMode

export interface ExecutorTargetRefBase {
    mode?: ExecutorTargetMode
    container_id?: string
    container_name?: string
    namespace?: string
    kind?: string
    name?: string
    container?: string
    [key: string]: unknown
}

export interface ContainerExecutorTargetRef extends ExecutorTargetRefBase {
    mode?: 'container'
}

export interface KubernetesExecutorTargetRef extends ExecutorTargetRefBase {
    mode?: 'kubernetes_workload'
    namespace?: string
    kind?: string
    name?: string
    container?: string
    services?: Array<{
        service: string
        image?: string | null
    }>
    service_count?: number
}

export interface HelmReleaseExecutorTargetRef extends ExecutorTargetRefBase {
    mode: 'helm_release'
    namespace: string
    release_name: string
    chart_name?: string | null
    chart_version?: string | null
    app_version?: string | null
    workloads?: Array<{
        kind?: string
        name?: string
    }>
    service_count?: number
}

export interface PortainerStackExecutorTargetRef extends ExecutorTargetRefBase {
    mode: 'portainer_stack'
    endpoint_id: number
    stack_id: number
    stack_name: string
    stack_type: string
    entrypoint?: string
    project_path?: string
    services?: Array<{
        service: string
        image?: string | null
    }>
    service_count?: number
}

export interface DockerComposeExecutorTargetRef extends ExecutorTargetRefBase {
    mode: 'docker_compose'
    project: string
    working_dir?: string | null
    config_files?: string[]
    services?: Array<{
        service: string
        image?: string | null
        replica_count?: number
    }>
    service_count?: number
}

export type ExecutorTargetRef =
    | ContainerExecutorTargetRef
    | KubernetesExecutorTargetRef
    | HelmReleaseExecutorTargetRef
    | PortainerStackExecutorTargetRef
    | DockerComposeExecutorTargetRef

export interface ExecutorServiceBinding {
    service: string
    tracker_source_id: number
    channel_name: string
}

export interface MaintenanceWindowConfig {
    timezone: string
    days_of_week: number[]
    start_time: string
    end_time: string
}

export interface RuntimeConnection {
    id: number
    name: string
    type: RuntimeConnectionType
    enabled: boolean
    config: Record<string, unknown>
    credential_id?: number | null
    credential_name?: string | null
    credential_type?: CredentialType | null
    uses_credentials?: boolean
    has_inline_secrets?: boolean
    secrets: Record<string, unknown>
    endpoint?: string | null
    description?: string | null
}

export interface RuntimeTargetDiscoveryItem {
    runtime_type: RuntimeType
    name: string
    target_ref: ExecutorTargetRef
    image?: string | null
}

export interface ExecutorStatus {
    id?: number | null
    executor_id: number
    last_run_at?: string | null
    last_result?: 'success' | 'failed' | 'skipped' | null
    last_error?: string | null
    last_version?: string | null
    updated_at?: string
}

export type ExecutorRunHistoryStatus = 'queued' | 'running' | 'success' | 'failed' | 'skipped'

export interface ExecutorRunServiceDiagnostic {
    service: string
    status: ExecutorRunHistoryStatus
    from_version?: string | null
    to_version?: string | null
    message?: string | null
}

export interface ExecutorRunDiagnostics {
    kind: 'docker_compose' | 'podman_compose' | 'portainer_stack' | 'kubernetes_workload' | string
    summary: {
        updated_count: number
        skipped_count: number
        failed_count: number
        group_message: string | null
    }
    services: ExecutorRunServiceDiagnostic[]
}

export interface ExecutorRunHistory {
    id?: number | null
    executor_id: number
    started_at: string
    finished_at?: string | null
    status: ExecutorRunHistoryStatus
    from_version?: string | null
    to_version?: string | null
    message?: string | null
    diagnostics: ExecutorRunDiagnostics | null
    created_at?: string
}

export interface ExecutorConfig {
    id?: number | null
    name: string
    runtime_type: RuntimeType
    runtime_connection_id: number
    tracker_name: string
    tracker_source_id?: number | null
    channel_name?: string | null
    enabled: boolean
    update_mode: ExecutorUpdateMode
    image_selection_mode?: ImageSelectionMode | null
    image_reference_mode?: ImageReferenceMode | null
    target_ref: ExecutorTargetRef
    service_bindings?: ExecutorServiceBinding[]
    maintenance_window?: MaintenanceWindowConfig | null
    description?: string | null
    invalid_config_error?: string | null
}

export interface ExecutorListItem extends ExecutorConfig {
    runtime_connection_name?: string | null
    status?: ExecutorStatus | null
}

export interface ExecutorDetail {
    id: number
    name: string
    runtime_type: RuntimeType
    tracker_name: string
    enabled: boolean
    update_mode: ExecutorUpdateMode
    image_selection_mode?: ImageSelectionMode | null
    image_reference_mode?: ImageReferenceMode | null
    runtime_connection_id: number
    runtime_connection_name?: string | null
    status?: ExecutorStatus | null
    latest_run?: ExecutorRunHistory | null
}

export interface ExecutorRunResponse {
    status: 'queued'
    run_id: number
}

// Request Types
export type CreateTrackerRequest = Omit<AggregateTracker, 'id' | 'status' | 'created_at' | 'updated_at' | 'channels' | 'sources' | 'primary_changelog_source_key'> & {
    primary_changelog_source_key: string
    channels: ReleaseChannelInput[]
    sources: Array<{
        source_key: string
        source_type: string
        enabled: boolean
        credential_name?: string
        source_config: Record<string, unknown>
        release_channels: ReleaseChannelInput[]
        source_rank: number
    }>
}
export type UpdateTrackerRequest = CreateTrackerRequest

export interface CreateCredentialRequest {
    name: string
    type: CredentialType
    token?: string
    secrets?: Record<string, unknown>
    description?: string | null
}
export type UpdateCredentialRequest = Partial<Omit<CreateCredentialRequest, 'name'>>

export interface AuthLoginRequest {
    username?: string
    email?: string
    password?: string
}

export interface AuthRegisterRequest {
    username: string
    email: string
    password: string
    code?: string
}

export interface ChangePasswordRequest {
    old_password?: string
    new_password: string
}

export interface UpdateSettingRequest {
    key: string
    value: unknown
}

export type CreateExecutorRequest = ExecutorConfig
export type UpdateExecutorRequest = Partial<ExecutorConfig>

export interface CreateRuntimeConnectionRequest {
    name: string
    type: RuntimeConnectionType
    enabled?: boolean
    config: Record<string, unknown>
    credential_id?: number | null
    secrets: Record<string, unknown>
    description?: string | null
}

export type UpdateRuntimeConnectionRequest = Partial<CreateRuntimeConnectionRequest>
