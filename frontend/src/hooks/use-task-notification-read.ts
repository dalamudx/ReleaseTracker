import { useSyncExternalStore } from "react"
import type { QueueTask, TaskState } from "@/api/task-types"

export const TASK_NOTIFICATION_READ_KEY = "releasetracker:task-notifications:read:v1"
export const TASK_NOTIFICATION_DISMISSED_KEY = "releasetracker:task-notifications:dismissed:v1"
const MAX_TASKS = 1000
const EMPTY: Readonly<Record<number, number>> = {}

export const isSettledTask = (state: TaskState) =>
    ["succeeded", "no_change", "skipped", "failed", "cancelled", "superseded"].includes(state)

export const isClearableNotification = (task: QueueTask) =>
    isSettledTask(task.state) && !task.approval_pending

export const isUnreadTask = (task: QueueTask, read: Readonly<Record<number, number>>) =>
    (task.state === "succeeded" || task.state === "failed") &&
    (read[task.id] === undefined || task.updated_at > read[task.id])

export const isDismissedNotification = (task: QueueTask, dismissed: Readonly<Record<number, number>>) =>
    isClearableNotification(task) && dismissed[task.id] !== undefined && task.updated_at <= dismissed[task.id]

// Read and dismissed versions are notification-only state. Neither store writes
// to the queue or modifies the shared query cache. Keep the existing read key.
function versionStore(key: string) {
    const eventName = `${key}:changed`
    let cachedRaw: string | null | undefined
    let cached: Readonly<Record<number, number>> = EMPTY
    let memoryOnly = false

    function getSnapshot() {
        if (memoryOnly) return cached
        try {
            const raw = window.localStorage.getItem(key)
            if (raw === cachedRaw) return cached
            cachedRaw = raw
            cached = EMPTY
            if (raw) {
                const value = JSON.parse(raw)
                if (value?.version === 1 && Array.isArray(value.tasks)) {
                    const entries = value.tasks.filter((entry: unknown) =>
                        Array.isArray(entry) && entry.length === 2 &&
                        Number.isSafeInteger(entry[0]) && entry[0] > 0 &&
                        typeof entry[1] === "number" && Number.isFinite(entry[1]) && entry[1] >= 0,
                    ).slice(0, MAX_TASKS)
                    cached = Object.fromEntries(entries)
                }
            }
        } catch {
            // Blocked/corrupt browser storage must not break notifications.
        }
        return cached
    }

    function subscribe(listener: () => void) {
        const onStorage = (event: StorageEvent) => {
            if (event.key === key || event.key === null) {
                memoryOnly = false
                listener()
            }
        }
        window.addEventListener("storage", onStorage)
        window.addEventListener(eventName, listener)
        return () => {
            window.removeEventListener("storage", onStorage)
            window.removeEventListener(eventName, listener)
        }
    }

    function remember(tasks: readonly QueueTask[]) {
        const next = { ...getSnapshot() }
        let changed = 0
        for (const task of tasks) {
            if (isSettledTask(task.state) &&
                (next[task.id] === undefined || task.updated_at > next[task.id])) {
                next[task.id] = task.updated_at
                changed++
            }
        }
        if (!changed) return 0
        const entries = Object.entries(next)
            .map(([id, updatedAt]) => [Number(id), updatedAt])
            .sort((a, b) => b[1] - a[1] || b[0] - a[0])
            .slice(0, MAX_TASKS)
        const raw = JSON.stringify({ version: 1, tasks: entries })
        cached = Object.fromEntries(entries)
        cachedRaw = raw
        try {
            window.localStorage.setItem(key, raw)
        } catch {
            memoryOnly = true
        }
        window.dispatchEvent(new Event(eventName))
        return changed
    }
    return { getSnapshot, subscribe, remember }
}

const readStore = versionStore(TASK_NOTIFICATION_READ_KEY)
const dismissedStore = versionStore(TASK_NOTIFICATION_DISMISSED_KEY)
const markRead = readStore.remember
function dismissRead(tasks: readonly QueueTask[]) {
    const read = readStore.getSnapshot()
    return dismissedStore.remember(tasks.filter(task => isClearableNotification(task) &&
        read[task.id] !== undefined && task.updated_at <= read[task.id]))
}

export function useTaskNotificationRead() {
    const read = useSyncExternalStore(readStore.subscribe, readStore.getSnapshot, () => EMPTY)
    const dismissed = useSyncExternalStore(dismissedStore.subscribe, dismissedStore.getSnapshot, () => EMPTY)
    return { read, dismissed, markRead, dismissRead }
}
