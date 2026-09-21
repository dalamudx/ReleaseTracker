import type { QueueTask } from "@/api/task-types"

export function canRecheckReadiness(task: Pick<QueueTask, "kind" | "state" | "result">): boolean {
    return (task.kind === "deploy" || task.kind === "recover")
        && !["queued", "running", "retry_wait"].includes(task.state)
        && task.result?.phase === "completed"
        && ["timeout", "unhealthy", "unknown", "unsupported", "superseded"].includes(task.result.health_check?.outcome ?? "")
        && task.result.health_recheck?.outcome !== "pending"
}

export const READINESS_FIELDS = [
    { key: "readiness_timeout_seconds", defaultValue: 600, min: 1, max: 86400 },
    { key: "readiness_interval_seconds", defaultValue: 5, min: 1, max: 3600 },
    { key: "readiness_attempt_timeout_seconds", defaultValue: 10, min: 1, max: 3600 },
    { key: "readiness_stable_seconds", defaultValue: 10, min: 0, max: 3600 },
] as const

export type HealthOutcome = "pending" | "healthy" | "unhealthy" | "timeout" | "unknown" | "unsupported" | "superseded" | "not_checked"
export interface ReadinessResult {
    outcome: HealthOutcome
    services?: Array<{ service: string; status: string; method?: string; message?: string }>
    elapsed_seconds?: number
}
