import axios from 'axios'
import type { Release, ReleaseStats, TrackerStatus, ApiCredential } from './types'

const API_BASE = '' // Vite proxy will handle /api

export const apiClient = axios.create({
    baseURL: API_BASE,
    headers: {
        'Content-Type': 'application/json',
    },
})

// Request interceptor to add token
apiClient.interceptors.request.use((config) => {
    const token = localStorage.getItem('token')
    if (token) {
        config.headers.Authorization = `Bearer ${token}`
    }
    return config
})

export const api = {
    getStats: () => apiClient.get<ReleaseStats>('/api/stats').then(res => res.data),
    getTrackers: (params?: { skip?: number, limit?: number }) =>
        apiClient.get<{ items: TrackerStatus[], total: number }>('/api/trackers', { params }).then(res => res.data),
    getLatestReleases: () => apiClient.get<Release[]>('/api/releases/latest').then(res => res.data),
    getReleases: (params?: any) => apiClient.get<{ items: Release[], total: number }>('/api/releases', { params }).then(res => res.data),

    // Trackers
    createTracker: (data: any) => apiClient.post('/api/trackers', data).then(res => res.data),
    updateTracker: (name: string, data: any) => apiClient.put(`/api/trackers/${name}`, data).then(res => res.data),
    deleteTracker: (name: string) => apiClient.delete(`/api/trackers/${name}`).then(res => res.data),
    checkTracker: (name: string) => apiClient.post(`/api/trackers/${name}/check`).then(res => res.data),
    getTrackerConfig: (name: string) => apiClient.get(`/api/trackers/${name}/config`).then(res => res.data),

    // Credentials
    getCredentials: (params?: { skip?: number, limit?: number }) =>
        apiClient.get<{ items: ApiCredential[], total: number }>('/api/credentials', { params }).then(res => res.data),
    createCredential: (data: any) => apiClient.post('/api/credentials', data).then(res => res.data),
    updateCredential: (id: number, data: any) => apiClient.put(`/api/credentials/${id}`, data).then(res => res.data),
    deleteCredential: (id: number) => apiClient.delete(`/api/credentials/${id}`).then(res => res.data),

    // Auth
    login: (data: any) => apiClient.post('/api/auth/login', data).then(res => res.data),
    register: (data: any) => apiClient.post('/api/auth/register', data).then(res => res.data),
    getCurrentUser: () => apiClient.get<User>('/api/auth/me').then(res => res.data),
    changePassword: (data: any) => apiClient.post('/api/auth/change-password', data).then(res => res.data),
}

import type { User } from './types'
