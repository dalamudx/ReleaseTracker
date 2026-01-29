export interface Release {
    id: number
    tracker_name: string
    name: string
    tag_name: string
    version: string
    published_at: string
    url: string
    prerelease: boolean
    body?: string | null
    channel_name?: string | null
    commit_sha?: string | null
    republish_count?: number
    is_historical?: number  // 0 = current, 1 = historical
    created_at: string
}

export interface TrackerStatus {
    name: string
    type: string
    enabled: boolean
    last_check: string | null
    last_version: string | null
    error: string | null
    channel_count?: number
}

export interface Channel {
    name: 'stable' | 'prerelease' | 'beta' | 'canary'
    type?: 'release' | 'prerelease' | null
    include_pattern?: string
    exclude_pattern?: string
    enabled: boolean
}

export interface TrackerConfig {
    name: string
    type: 'github' | 'gitlab' | 'helm'
    enabled: boolean
    repo?: string
    project?: string
    instance?: string
    chart?: string
    credential_name?: string
    channels?: Channel[]
    interval?: number
    description?: string
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

export interface ApiCredential {
    id: number
    name: string
    type: string
    token: string // 通常已脱敏
    description?: string | null
    created_at: string
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

export interface Notifier {
    id: number
    name: string
    type: string
    url: string
    events: string[]
    enabled: boolean
    description?: string
    created_at: string
}

export interface SettingItem {
    key: string
    value: any
    description?: string
    updated_at?: string
}

export interface EnvInfo {
    key: string
    value: string
    description?: string
}

export interface PaginatedResponse<T> {
    items: T[]
    total: number
    skip?: number
    limit?: number
}
