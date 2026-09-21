import { apiClient } from './client'

export interface NotificationTemplate {
    id: number | null
    name: string
    title: string
    body: string
    translations: Record<string, Record<string, string>>
    revision: number
    references_count?: number
}
export interface TemplateCatalog { items: NotificationTemplate[]; builtin: NotificationTemplate; events: string[] }
export interface TemplatePreview { title: string; body: string; content: string; locale: string; revision: number }
export const notificationTemplates = {
    list: () => apiClient.get<TemplateCatalog>('/api/notification-templates').then(r => r.data),
    save: (data: NotificationTemplate) => data.id === null
        ? apiClient.post<NotificationTemplate>('/api/notification-templates', data).then(r => r.data)
        : apiClient.put<NotificationTemplate>(`/api/notification-templates/${data.id}`, data).then(r => r.data),
    remove: (id: number) => apiClient.delete(`/api/notification-templates/${id}`),
    preview: (data: NotificationTemplate & { event: string; language: string; channel: string; scenario: string }) =>
        apiClient.post<TemplatePreview>('/api/notification-templates/preview', data).then(r => r.data),
}
