import { apiClient } from "./client"

export interface InstanceBackupEntry { name: string; size: number; created_at: number }
export interface InstanceBackupList { interval_hours: number; retention: number; running: boolean; items: InstanceBackupEntry[] }

export const backupApi = {
    list: () => apiClient.get<InstanceBackupList>("/api/backups").then(r => r.data),
    create: () => apiClient.post<InstanceBackupEntry>("/api/backups", { include_secrets_confirmed: true }).then(r => r.data),
    download: (name: string) => apiClient.get<Blob>(`/api/backups/${encodeURIComponent(name)}/download`, { responseType: "blob" }).then(r => r.data),
}
