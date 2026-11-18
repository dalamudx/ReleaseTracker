/** Instance-level policies shared with the backend's persisted settings defaults and bounds. */

export const INSTANCE_POLICY_SETTING_KEYS = {
    mutationConcurrency: "system.mutation_concurrency",
    workerPollSeconds: "system.worker_poll_seconds",
    readinessPullGraceSeconds: "system.readiness_pull_grace_seconds",
    runtimeHealthIntervalSeconds: "system.runtime_health_interval_seconds",
    onlineRestore: "system.online_restore",
    preMigrationBackup: "system.pre_migration_backup",
    corsOrigins: "system.cors_origins",
    forwardedAllowIps: "system.forwarded_allow_ips",
} as const

export const INSTANCE_POLICY_DEFAULTS: Record<string, string> = {
    [INSTANCE_POLICY_SETTING_KEYS.mutationConcurrency]: "1",
    [INSTANCE_POLICY_SETTING_KEYS.workerPollSeconds]: "2",
    [INSTANCE_POLICY_SETTING_KEYS.readinessPullGraceSeconds]: "1800",
    [INSTANCE_POLICY_SETTING_KEYS.runtimeHealthIntervalSeconds]: "0",
    [INSTANCE_POLICY_SETTING_KEYS.onlineRestore]: "true",
    [INSTANCE_POLICY_SETTING_KEYS.preMigrationBackup]: "true",
    [INSTANCE_POLICY_SETTING_KEYS.corsOrigins]: "",
    [INSTANCE_POLICY_SETTING_KEYS.forwardedAllowIps]: "127.0.0.1",
}

// Mirrors the backend bounds so invalid input is rejected before the request.
export const INSTANCE_POLICY_INTEGER_BOUNDS: Record<string, readonly [number, number]> = {
    [INSTANCE_POLICY_SETTING_KEYS.mutationConcurrency]: [1, 3],
    [INSTANCE_POLICY_SETTING_KEYS.workerPollSeconds]: [1, 60],
    [INSTANCE_POLICY_SETTING_KEYS.readinessPullGraceSeconds]: [0, 3600],
    [INSTANCE_POLICY_SETTING_KEYS.runtimeHealthIntervalSeconds]: [0, 86400],
}

export function isValidPolicyInteger(key: string, value: string): boolean {
    const bounds = INSTANCE_POLICY_INTEGER_BOUNDS[key]
    const trimmed = value.trim()
    if (!bounds || !/^\d+$/.test(trimmed)) return false
    const number = Number(trimmed)
    if (String(number) !== trimmed || number < bounds[0] || number > bounds[1]) return false
    return key !== INSTANCE_POLICY_SETTING_KEYS.runtimeHealthIntervalSeconds || number === 0 || number >= 300
}

/** Newline-delimited policy lists drop blank lines and trailing whitespace. */
export function formatPolicyList(value: string): string {
    return value.split("\n").map((line) => line.trim()).filter(Boolean).join("\n")
}
