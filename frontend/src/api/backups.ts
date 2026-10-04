import { apiClient } from "./client"

export interface InstanceBackupEntry { name: string; size: number; created_at: number; in_use?: boolean }
export interface InstanceBackupList {
    directory?: string
    interval_hours: number
    retention: number
    daily_retention?: number
    weekly_retention?: number
    total_size?: number
    minimum_local_archives?: number
    restore_review_required?: boolean
    online_restore_available?: boolean
    safety_backup?: InstanceBackupEntry | null
    running: boolean
    items: InstanceBackupEntry[]
    last_success_at?: number | null
    last_failure_at?: number | null
    last_error_code?: string | null
    consecutive_failures?: number
    overdue?: boolean
}

export interface RestorePlan { id: string; name: string; fingerprint: string; expires_at: number; created_at: string | number | null; app_version: string | null; mutation_performed: false }
export interface RestoreReceipt { id: string; token: string }
export interface RestoreStatus { id: string; state: "running" | "succeeded" | "failed" | "blocked"; phase: string; error_code: string | null; rolled_back: boolean; review_required: boolean }

export const backupApi = {
    list: () => apiClient.get<InstanceBackupList>("/api/backups").then(r => r.data),
    create: () => apiClient.post<InstanceBackupEntry>("/api/backups", { include_secrets_confirmed: true }).then(r => r.data),
    delete: (name: string) => apiClient.delete<{ deleted: string }>(`/api/backups/${encodeURIComponent(name)}`, { data: { confirm_name: name } }).then(r => r.data),
    restorePlan: (name: string) => apiClient.post<RestorePlan>(`/api/backups/${encodeURIComponent(name)}/restore-plan`).then(r => r.data),
    cancelPlan: (name: string) => apiClient.delete(`/api/backups/${encodeURIComponent(name)}/restore-plan`),
    restore: (name: string, plan: RestorePlan) => apiClient.post<RestoreReceipt>(`/api/backups/${encodeURIComponent(name)}/restore`, { plan_id: plan.id, fingerprint: plan.fingerprint, confirm_name: name, data_loss_confirmed: true }).then(r => r.data),
    restoreStatus: async (receipt: RestoreReceipt, signal?: AbortSignal) => {
        const { appPath } = await import("@/lib/base-path")
        const response = await fetch(appPath(`/api/backups/restore-status/${encodeURIComponent(receipt.id)}`), { headers: { "X-Restore-Token": receipt.token }, cache: "no-store", signal })
        if (!response.ok) throw new Error("restore_status_unavailable")
        return response.json() as Promise<RestoreStatus>
    },
    reviewRestore: () => apiClient.post("/api/backups/restore-review", { reviewed: true }),
    downloadSafety: () => apiClient.get<Blob>("/api/backups/restore-safety/current/download", { responseType: "blob" }).then(r => r.data),
    download: (name: string) => apiClient.get<Blob>(`/api/backups/${encodeURIComponent(name)}/download`, { responseType: "blob" }).then(r => r.data),
}
