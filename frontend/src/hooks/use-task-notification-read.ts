import { useSyncExternalStore } from "react"
import type { QueueTask, TaskState } from "@/api/task-types"

export const TASK_NOTIFICATION_READ_KEY = "releasetracker:task-notifications:read:v1"
const READ_EVENT = "releasetracker:task-notifications:read"
const MAX_READ_TASKS = 1000
const EMPTY_READ: Readonly<Record<number, number>> = {}
let cachedRaw: string | null | undefined
let cachedRead = EMPTY_READ
let memoryOnly = false

export const isSettledTask = (state: TaskState) =>
    ["succeeded", "no_change", "skipped", "failed", "cancelled", "superseded"].includes(state)

export const isUnreadTask = (task: QueueTask, read: Readonly<Record<number, number>>) =>
    (task.state === "succeeded" || task.state === "failed") &&
    (read[task.id] === undefined || task.updated_at > read[task.id])

function getSnapshot() {
    if (memoryOnly) return cachedRead
    try {
        const raw = window.localStorage.getItem(TASK_NOTIFICATION_READ_KEY)
        if (raw === cachedRaw) return cachedRead
        cachedRaw = raw
        cachedRead = EMPTY_READ
        if (raw) {
            const value = JSON.parse(raw)
            if (value.version === 1 && Array.isArray(value.tasks)) {
                const entries = value.tasks.filter((entry: unknown) =>
                    Array.isArray(entry) && entry.length === 2 &&
                    Number.isSafeInteger(entry[0]) && entry[0] > 0 &&
                    typeof entry[1] === "number" && Number.isFinite(entry[1]) && entry[1] >= 0,
                ).slice(0, MAX_READ_TASKS)
                cachedRead = Object.fromEntries(entries)
            }
        }
    } catch {
        // Storage can be blocked or contain a corrupt value. Reading must still work.
    }
    return cachedRead
}

function subscribe(listener: () => void) {
    const onStorage = (event: StorageEvent) => {
        if (event.key === TASK_NOTIFICATION_READ_KEY || event.key === null) {
            memoryOnly = false
            listener()
        }
    }
    window.addEventListener("storage", onStorage)
    window.addEventListener(READ_EVENT, listener)
    return () => {
        window.removeEventListener("storage", onStorage)
        window.removeEventListener(READ_EVENT, listener)
    }
}

function markRead(tasks: readonly QueueTask[]) {
    const current = getSnapshot()
    const next = { ...current }
    let changed = false
    for (const task of tasks) {
        if (isSettledTask(task.state) &&
            (next[task.id] === undefined || task.updated_at > next[task.id])) {
            next[task.id] = task.updated_at
            changed = true
        }
    }
    if (!changed) return
    const entries = Object.entries(next)
        .map(([id, updatedAt]) => [Number(id), updatedAt])
        .sort((a, b) => b[1] - a[1] || b[0] - a[0])
        .slice(0, MAX_READ_TASKS)
    const raw = JSON.stringify({ version: 1, tasks: entries })
    cachedRead = Object.fromEntries(entries)
    cachedRaw = raw
    try {
        window.localStorage.setItem(TASK_NOTIFICATION_READ_KEY, raw)
    } catch {
        memoryOnly = true
    }
    window.dispatchEvent(new Event(READ_EVENT))
}

export function useTaskNotificationRead() {
    const read = useSyncExternalStore(subscribe, getSnapshot, () => EMPTY_READ)
    return { read, markRead }
}
