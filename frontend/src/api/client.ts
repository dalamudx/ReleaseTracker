import axios, { type AxiosError, type InternalAxiosRequestConfig } from 'axios'
import type {
    AggregateTracker,
    ReleaseStats,
    ApiCredential,
    CredentialReferencesResponse,
    Notifier,
    SettingItem,
    PaginatedResponse,
    User,
    CreateTrackerRequest,
    UpdateTrackerRequest,
    CreateCredentialRequest,
    UpdateCredentialRequest,
    AuthLoginRequest,
    AuthRegisterRequest,
    ChangePasswordRequest,
    UpdateSettingRequest,
    RuntimeConnection,
    RuntimeTargetDiscoveryItem,
    ExecutorListItem,
    ExecutorConfig,
    ExecutorDetail,
    ExecutorRunHistory,
    ExecutorRunResponse,
    CreateExecutorRequest,
    UpdateExecutorRequest,
    CreateRuntimeConnectionRequest,
    UpdateRuntimeConnectionRequest,
    TokenPair,
    ReleaseHistoryItem,
    LatestCurrentReleaseSummary,
    TrackerCurrentView,
    TrackerReleaseHistoryResponse,
    SecurityKeysStatus,
    RotateSecurityKeyRequest,
    RotateJwtSecretResponse,
    RotateEncryptionKeyResponse,
} from "./types"

const API_BASE = '' // Vite proxy handles /api

export const apiClient = axios.create({
    baseURL: API_BASE,
    headers: {
        'Content-Type': 'application/json',
    },
})

const AUTH_REDIRECT_HEADER = 'x-auth-skip-redirect'
const AUTH_REFRESH_ENDPOINT = '/api/auth/refresh'
const AUTH_REDIRECT_EXCLUDED_PATHS = new Set([
    '/api/auth/login',
    '/api/auth/register',
])
const AUTH_REFRESH_EXCLUDED_PATHS = new Set([
    '/api/auth/login',
    '/api/auth/logout',
    '/api/auth/refresh',
])

type RetryableRequestConfig = InternalAxiosRequestConfig & {
    __isRetry?: boolean
}

let refreshPromise: Promise<void> | null = null

function shouldSkipAuthRedirect(error: AxiosError): boolean {
    const headerValue = error.config?.headers?.[AUTH_REDIRECT_HEADER]
    return headerValue === 'true' || headerValue === true
}

function resolveRequestPath(error: AxiosError): string | null {
    const requestUrl = error.config?.url
    if (!requestUrl) {
        return null
    }

    try {
        return new URL(requestUrl, window.location.origin).pathname
    } catch {
        return requestUrl.split('?')[0]
    }
}

function shouldRedirectOnUnauthorized(error: AxiosError): boolean {
    if (error.response?.status !== 401) {
        return false
    }
    if (shouldSkipAuthRedirect(error)) {
        return false
    }

    const requestPath = resolveRequestPath(error)
    if (!requestPath) {
        return false
    }

    if (AUTH_REDIRECT_EXCLUDED_PATHS.has(requestPath)) {
        return false
    }

    return true
}

function shouldAttemptTokenRefresh(error: AxiosError): boolean {
    if (error.response?.status !== 401) {
        return false
    }

    const requestPath = resolveRequestPath(error)
    if (!requestPath) {
        return false
    }

    return !AUTH_REFRESH_EXCLUDED_PATHS.has(requestPath)
}

function persistTokenPair(tokenPair: TokenPair): void {
    localStorage.setItem('token', tokenPair.access_token)
    if (tokenPair.refresh_token) {
        localStorage.setItem('refresh_token', tokenPair.refresh_token)
    }
}

export function clearAuthStorage(): void {
    localStorage.removeItem('token')
    localStorage.removeItem('refresh_token')
    localStorage.removeItem('user')
}

function redirectToLogin(): void {
    if (!window.location.pathname.startsWith('/login')) {
        window.location.href = '/login'
    }
}

function handleTerminalUnauthorized(error: AxiosError): void {
    if (!shouldRedirectOnUnauthorized(error)) {
        return
    }

    clearAuthStorage()
    redirectToLogin()
}

async function refreshAccessToken(): Promise<void> {
    const storedRefreshToken = localStorage.getItem('refresh_token')
    if (!storedRefreshToken) {
        clearAuthStorage()
        throw new Error('Missing refresh token')
    }

    if (!refreshPromise) {
        refreshPromise = (async () => {
            try {
                const refreshResponse = await apiClient.post<TokenPair>(
                    AUTH_REFRESH_ENDPOINT,
                    null,
                    {
                        params: { refresh_token: storedRefreshToken },
                        headers: { [AUTH_REDIRECT_HEADER]: 'true' },
                    }
                )
                persistTokenPair(refreshResponse.data)
            } catch (error) {
                clearAuthStorage()
                throw error
            } finally {
                refreshPromise = null
            }
        })()
    }

    await refreshPromise
}

// Request interceptor: add token
apiClient.interceptors.request.use((config) => {
    const token = localStorage.getItem('token')
    if (token) {
        config.headers.Authorization = `Bearer ${token}`
    }
    return config
})

// Response interceptor: handle 401 errors
apiClient.interceptors.response.use(
    (response) => response,
    async (error: AxiosError) => {
        if (!shouldAttemptTokenRefresh(error)) {
            if (error.response?.status === 401) {
                handleTerminalUnauthorized(error)
            }
            return Promise.reject(error)
        }

        const originalConfig = error.config as RetryableRequestConfig | undefined
        if (!originalConfig || originalConfig.__isRetry) {
            if (error.response?.status === 401) {
                handleTerminalUnauthorized(error)
            }
            return Promise.reject(error)
        }

        originalConfig.__isRetry = true

        try {
            await refreshAccessToken()
            return apiClient.request(originalConfig)
        } catch (refreshError) {
            if (error.response?.status === 401) {
                handleTerminalUnauthorized(error)
            }
            return Promise.reject(refreshError)
        }
    }
)

function normalizeReleaseHistoryItem(item: ReleaseHistoryItem): ReleaseHistoryItem {
    return {
        ...item,
        tracker_type: item.primary_source?.source_type ?? item.tracker_type,
    }
}

function normalizeLatestCurrentReleaseSummary(item: LatestCurrentReleaseSummary): LatestCurrentReleaseSummary {
    return {
        ...item,
        tracker_type: item.primary_source_type ?? item.primary_source?.source_type ?? item.tracker_type,
    }
}

export const api = {
    getStats: () => apiClient.get<ReleaseStats>('/api/stats').then(res => res.data),
    getTrackers: (params?: { skip?: number, limit?: number }) =>
        apiClient.get<{ items: AggregateTracker[], total: number }>('/api/trackers', { params }).then(res => res.data),
    getLatestCurrentReleases: () => apiClient.get<LatestCurrentReleaseSummary[]>('/api/releases/latest').then(res =>
        res.data.map(normalizeLatestCurrentReleaseSummary),
    ),
    getReleaseHistory: (params?: { tracker?: string, skip?: number, limit?: number, search?: string, prerelease?: boolean }) =>
        apiClient.get<{ items: ReleaseHistoryItem[], total: number, skip?: number, limit?: number }>('/api/releases', { params }).then(res => ({
            ...res.data,
            items: res.data.items.map(normalizeReleaseHistoryItem),
        })),

    // Trackers
    createTracker: (data: CreateTrackerRequest) => apiClient.post<AggregateTracker>('/api/trackers', data).then(res => res.data),
    updateTracker: (name: string, data: UpdateTrackerRequest) => apiClient.put<AggregateTracker>(`/api/trackers/${name}`, data).then(res => res.data),
    deleteTracker: (name: string) => apiClient.delete(`/api/trackers/${name}`).then(res => res.data),
    checkTracker: (name: string) => apiClient.post(`/api/trackers/${name}/check`).then(res => res.data),
    getTracker: (name: string) => apiClient.get<AggregateTracker>(`/api/trackers/${name}`).then(res => res.data),
    getTrackerConfig: (name: string) => apiClient.get<AggregateTracker>(`/api/trackers/${name}/config`).then(res => res.data),
    getTrackerCurrentView: (trackerName: string) =>
        apiClient.get<TrackerCurrentView>(`/api/trackers/${trackerName}/current`).then(res => res.data),
    getTrackerReleaseHistory: (
        trackerName: string,
        params?: { skip?: number, limit?: number, search?: string, prerelease?: boolean },
    ) => apiClient.get<TrackerReleaseHistoryResponse>(`/api/trackers/${trackerName}/releases/history`, { params }).then(res => ({
        ...res.data,
        items: res.data.items.map(normalizeReleaseHistoryItem),
    })),

    // Credentials
    getCredentials: (params?: { skip?: number, limit?: number }) =>
        apiClient.get<{ items: ApiCredential[], total: number }>('/api/credentials', { params }).then(res => res.data),
    getCredentialReferences: (id: number) => apiClient.get<CredentialReferencesResponse>(`/api/credentials/${id}/references`).then(res => res.data),
    createCredential: (data: CreateCredentialRequest) => apiClient.post('/api/credentials', data).then(res => res.data),
    updateCredential: (id: number, data: UpdateCredentialRequest) => apiClient.put(`/api/credentials/${id}`, data).then(res => res.data),
    deleteCredential: (id: number) => apiClient.delete(`/api/credentials/${id}`).then(res => res.data),

    // Notifiers
    getNotifiers: (params?: { skip?: number, limit?: number }) =>
        apiClient.get<PaginatedResponse<Notifier>>('/api/notifiers', { params }).then(res => res.data),
    getNotifier: (id: number) => apiClient.get<Notifier>(`/api/notifiers/${id}`).then(res => res.data),
    createNotifier: (data: Partial<Notifier>) => apiClient.post<Notifier>('/api/notifiers', data).then(res => res.data),
    updateNotifier: (id: number, data: Partial<Notifier>) => apiClient.put<Notifier>(`/api/notifiers/${id}`, data).then(res => res.data),
    deleteNotifier: (id: number) => apiClient.delete(`/api/notifiers/${id}`).then(res => res.data),
    testNotifier: (id: number) => apiClient.post<boolean>(`/api/notifiers/${id}/test`).then(res => res.data),

    // Auth
    login: (data: AuthLoginRequest) => apiClient.post('/api/auth/login', data).then(res => res.data),
    register: (data: AuthRegisterRequest) => apiClient.post('/api/auth/register', data).then(res => res.data),
    getCurrentUser: (options?: { suppressAuthRedirect?: boolean }) =>
        apiClient.get<User>('/api/auth/me', {
            headers: options?.suppressAuthRedirect ? { [AUTH_REDIRECT_HEADER]: 'true' } : undefined,
        }).then(res => res.data),
    changePassword: (data: ChangePasswordRequest) => apiClient.post('/api/auth/change-password', data).then(res => res.data),

    // Settings
    getSettings: () => apiClient.get<SettingItem[]>('/api/settings').then(res => res.data),
    updateSetting: (data: UpdateSettingRequest) => apiClient.post<SettingItem>('/api/settings', data).then(res => res.data),
    deleteSetting: (key: string) => apiClient.delete(`/api/settings/${key}`).then(res => res.data),
    getSecurityKeys: () => apiClient.get<SecurityKeysStatus>('/api/settings/security-keys').then(res => res.data),
    rotateJwtSecret: (data: RotateSecurityKeyRequest) => apiClient.post<RotateJwtSecretResponse>('/api/settings/security-keys/jwt-secret', data).then(res => res.data),
    rotateEncryptionKey: (data: RotateSecurityKeyRequest) => apiClient.post<RotateEncryptionKeyResponse>('/api/settings/security-keys/encryption-key', data).then(res => res.data),

    // Runtime Connections
    getRuntimeConnections: (params?: { skip?: number, limit?: number }) =>
        apiClient.get<PaginatedResponse<RuntimeConnection>>('/api/runtime-connections', { params }).then(res => res.data),
    getRuntimeConnection: (id: number) => apiClient.get<RuntimeConnection>(`/api/runtime-connections/${id}`).then(res => res.data),
    discoverKubernetesNamespaces: (data: Partial<RuntimeConnection>) =>
        apiClient.post<{ items: string[] }>('/api/runtime-connections/discover-kubernetes-namespaces', data).then(res => res.data),
    createRuntimeConnection: (data: CreateRuntimeConnectionRequest) => apiClient.post<{ message: string, id: number }>('/api/runtime-connections', data).then(res => res.data),
    updateRuntimeConnection: (id: number, data: UpdateRuntimeConnectionRequest) => apiClient.put<{ message: string, updated_at: string }>(`/api/runtime-connections/${id}`, data).then(res => res.data),
    deleteRuntimeConnection: (id: number) => apiClient.delete<{ message: string }>(`/api/runtime-connections/${id}`).then(res => res.data),

    getExecutors: (params?: { skip?: number, limit?: number }) =>
        apiClient.get<PaginatedResponse<ExecutorListItem>>('/api/executors', { params }).then(res => res.data),
    getExecutor: (id: number) => apiClient.get<ExecutorDetail>(`/api/executors/${id}`).then(res => res.data),
    getExecutorConfig: (id: number) => apiClient.get<ExecutorConfig>(`/api/executors/${id}/config`).then(res => res.data),
    getExecutorHistory: (id: number, params?: { skip?: number, limit?: number, status?: 'success' | 'failed' | 'skipped', search?: string }) =>
        apiClient.get<PaginatedResponse<ExecutorRunHistory>>(`/api/executors/${id}/history`, { params }).then(res => res.data),
    clearExecutorHistory: (id: number) => apiClient.delete<{ message: string, deleted: number }>(`/api/executors/${id}/history`).then(res => res.data),
    discoverExecutorTargets: (runtimeConnectionId: number, params?: { namespace?: string }) =>
        apiClient.get<PaginatedResponse<RuntimeTargetDiscoveryItem>>(`/api/executors/runtime-connections/${runtimeConnectionId}/targets`, { params }).then(res => res.data),
    createExecutor: (data: CreateExecutorRequest) => apiClient.post('/api/executors', data).then(res => res.data),
    updateExecutor: (id: number, data: UpdateExecutorRequest) => apiClient.put(`/api/executors/${id}`, data).then(res => res.data),
    deleteExecutor: (id: number) => apiClient.delete(`/api/executors/${id}`).then(res => res.data),
    runExecutor: (id: number) => apiClient.post<ExecutorRunResponse>(`/api/executors/${id}/run`).then(res => res.data),
}
