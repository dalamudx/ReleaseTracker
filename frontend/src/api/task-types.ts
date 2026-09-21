import type { ReadinessResult } from "@/lib/readiness"

export type TaskState = "queued" | "running" | "retry_wait" | "succeeded" | "no_change" | "skipped" | "failed" | "cancelled" | "superseded" | "needs_attention"
export interface TaskReceipt { task_id: number; status: TaskState }
export interface QueueTask {
    id: number
    kind: "fetch" | "deploy" | "recover"
    target_label: string
    state: TaskState
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
