import axios from 'axios'
import type {
    Release,
    TrackerStatus,
    ReleaseStats,
    ApiCredential,
    Notifier,
    SettingItem,
    EnvInfo,
    PaginatedResponse,
    User,
    CreateTrackerRequest,
    UpdateTrackerRequest,
    CreateCredentialRequest,
    UpdateCredentialRequest,
    AuthLoginRequest,
    AuthRegisterRequest,
    ChangePasswordRequest,
    UpdateSettingRequest
} from "./types"

const API_BASE = '' // Vite 代理处理 /api

export const apiClient = axios.create({
    baseURL: API_BASE,
    headers: {
        'Content-Type': 'application/json',
    },
})

// 请求拦截器：添加 Token
apiClient.interceptors.request.use((config) => {
    const token = localStorage.getItem('token')
    if (token) {
        config.headers.Authorization = `Bearer ${token}`
    }
    return config
})

// 响应拦截器：处理 401 错误
apiClient.interceptors.response.use(
    (response) => response,
    (error) => {
        if (error.response?.status === 401) {
            // Token 过期或无效，清除本地存储并跳转登录
            localStorage.removeItem('token')
            // 使用 window.location.href 强制跳转，确保状态重置
            // 避免在非组件环境中使用 useNavigate 带来的复杂性
            if (!window.location.pathname.startsWith('/login')) {
                window.location.href = '/login'
            }
        }
        return Promise.reject(error)
    }
)

export const api = {
    getStats: () => apiClient.get<ReleaseStats>('/api/stats').then(res => res.data),
    getTrackers: (params?: { skip?: number, limit?: number }) =>
        apiClient.get<{ items: TrackerStatus[], total: number }>('/api/trackers', { params }).then(res => res.data),
    getLatestReleases: () => apiClient.get<Release[]>('/api/releases/latest').then(res => res.data),
    getReleases: (params?: unknown) => apiClient.get<{ items: Release[], total: number }>('/api/releases', { params }).then(res => res.data),

    // Trackers
    createTracker: (data: CreateTrackerRequest) => apiClient.post('/api/trackers', data).then(res => res.data),
    updateTracker: (name: string, data: UpdateTrackerRequest) => apiClient.put(`/api/trackers/${name}`, data).then(res => res.data),
    deleteTracker: (name: string) => apiClient.delete(`/api/trackers/${name}`).then(res => res.data),
    checkTracker: (name: string) => apiClient.post(`/api/trackers/${name}/check`).then(res => res.data),
    getTrackerConfig: (name: string) => apiClient.get(`/api/trackers/${name}/config`).then(res => res.data),

    // Credentials
    getCredentials: (params?: { skip?: number, limit?: number }) =>
        apiClient.get<{ items: ApiCredential[], total: number }>('/api/credentials', { params }).then(res => res.data),
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
    getCurrentUser: () => apiClient.get<User>('/api/auth/me').then(res => res.data),
    changePassword: (data: ChangePasswordRequest) => apiClient.post('/api/auth/change-password', data).then(res => res.data),

    // Settings
    getSettings: () => apiClient.get<SettingItem[]>('/api/settings').then(res => res.data),
    updateSetting: (data: UpdateSettingRequest) => apiClient.post<SettingItem>('/api/settings', data).then(res => res.data),
    deleteSetting: (key: string) => apiClient.delete(`/api/settings/${key}`).then(res => res.data),
    getEnvInfo: () => apiClient.get<EnvInfo[]>('/api/settings/env').then(res => res.data),
}
