import axios from 'axios'
import type { Release, ReleaseStats, TrackerStatus, ApiCredential } from './types'

const API_BASE = '' // Vite proxy will handle /api

export const apiClient = axios.create({
    baseURL: API_BASE,
    headers: {
        'Content-Type': 'application/json',
    },
})

export const api = {
    getStats: () => apiClient.get<ReleaseStats>('/api/stats').then(res => res.data),
    getTrackers: () => apiClient.get<TrackerStatus[]>('/api/trackers').then(res => res.data),
    getLatestReleases: () => apiClient.get<Release[]>('/api/releases/latest').then(res => res.data),
    getReleases: (params?: any) => apiClient.get<{ items: Release[], total: number }>('/api/releases', { params }).then(res => res.data),

    // Trackers
    createTracker: (data: any) => apiClient.post('/api/trackers', data).then(res => res.data),
    updateTracker: (name: string, data: any) => apiClient.put(`/api/trackers/${name}`, data).then(res => res.data),
    deleteTracker: (name: string) => apiClient.delete(`/api/trackers/${name}`).then(res => res.data),
    checkTracker: (name: string) => apiClient.post(`/api/trackers/${name}/check`).then(res => res.data),
    getTrackerConfig: (name: string) => apiClient.get(`/api/trackers/${name}/config`).then(res => res.data),

    // Credentials
    getCredentials: () => apiClient.get<ApiCredential[]>('/api/credentials').then(res => res.data),
    createCredential: (data: any) => apiClient.post('/api/credentials', data).then(res => res.data),
    updateCredential: (id: number, data: any) => apiClient.put(`/api/credentials/${id}`, data).then(res => res.data),
    deleteCredential: (id: number) => apiClient.delete(`/api/credentials/${id}`).then(res => res.data),
}
