import { useEffect, useRef } from "react"
import { useQueryClient } from "@tanstack/react-query"
import type { QueueTask } from "@/api/task-types"

type FetchTaskSnapshot = Pick<QueueTask, "id" | "kind" | "state" | "updated_at">
const finishedStates = new Set(["succeeded", "no_change", "failed", "skipped", "cancelled", "superseded", "needs_attention"])

export function completedFetchChanged(previous: readonly FetchTaskSnapshot[] | undefined, current: readonly FetchTaskSnapshot[]): boolean {
    // The initial recent-task response is a baseline, not a new completion event.
    if (previous === undefined) return false
    const seen = new Map(previous.map(task => [task.id, task]))
    return current.some(task => {
        if (task.kind !== "fetch" || !finishedStates.has(task.state)) return false
        const old = seen.get(task.id)
        return !old || old.state !== task.state || old.updated_at !== task.updated_at
    })
}

export function useCompletedFetchInvalidation(tasks: readonly FetchTaskSnapshot[] | undefined) {
    const cache = useQueryClient()
    const previous = useRef<readonly FetchTaskSnapshot[] | undefined>(undefined)
    useEffect(() => {
        if (tasks === undefined) return
        const changed = completedFetchChanged(previous.current, tasks)
        previous.current = tasks
        if (!changed) return
        // Reuse existing recent-task polling; do not add another polling loop.
        void cache.invalidateQueries({ queryKey: ["trackers"] })
        void cache.invalidateQueries({ queryKey: ["releases"] })
        void cache.invalidateQueries({ queryKey: ["stats"] })
    }, [tasks, cache])
}
