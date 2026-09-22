import type { ReadinessResult } from "@/lib/readiness"

export type TaskState = "queued" | "awaiting_approval" | "running" | "retry_wait" | "succeeded" | "no_change" | "skipped" | "failed" | "cancelled" | "superseded" | "needs_attention"
export interface TaskReceipt { task_id: number; status: TaskState }
export interface DeploymentPlan {
    id: number
    task_id: number
    fingerprint: string
    state: string
    reason: string
    expires_at: number
    summary: { target_label?: string; identity_key?: string; configuration_fingerprint?: string; recovery_scope?: string; includes_application_data?: boolean; automatic_rollback?: boolean; source_count?: number }
}
export interface QueueTask {
    id: number
    kind: "fetch" | "deploy" | "recover"
    target_label: string
    state: TaskState
    approval_pending?: boolean
    attempts: number
    max_retries: number
    due_at: number
    created_at: number
    updated_at: number
    error_code: string | null
    message: string | null
    result: { phase?: string; readiness_recheck?: boolean; health_check?: ReadinessResult; health_recheck?: ReadinessResult; health_rechecks?: ReadinessResult[]; run_id?: number; source_fetch_run_ids?: Record<string, number> } | null
    target: { tracker_name?: string; executor_id?: number }
    attempt_history?: Array<{ id: number; attempt: number; state: string; error_code: string | null; started_at: number; finished_at: number | null }>
    triggers?: Array<{ trigger_mode: string; created_at: number }>
}
export const isActiveTask = (state: string) => ["queued", "running", "retry_wait"].includes(state)
