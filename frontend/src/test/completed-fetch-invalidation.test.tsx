import { renderHook } from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { describe, expect, it, vi } from "vitest"
import { completedFetchChanged, useCompletedFetchInvalidation } from "@/hooks/use-completed-fetch-invalidation"
import type { QueueTask } from "@/api/task-types"

type Snapshot = Pick<QueueTask, "id" | "kind" | "state" | "updated_at">
const task = (state: QueueTask["state"], id = 1, kind: QueueTask["kind"] = "fetch", updated_at = 10): Snapshot => ({ id, kind, state, updated_at })

describe("completed fetch cache refresh", () => {
    it("does not replay historical completions on the initial response", () => {
        expect(completedFetchChanged(undefined, [task("succeeded")])).toBe(false)
    })
    it("refreshes when an observed fetch finishes", () => {
        expect(completedFetchChanged([task("running")], [task("succeeded")])).toBe(true)
    })
    it("handles a fast fetch first seen already completed", () => {
        expect(completedFetchChanged([], [task("succeeded")])).toBe(true)
    })
    it("does not repeatedly refresh unchanged terminal tasks", () => {
        expect(completedFetchChanged([task("succeeded")], [task("succeeded")])).toBe(false)
    })
    it("refreshes a new completion of a reused task", () => {
        expect(completedFetchChanged([task("succeeded")], [task("succeeded", 1, "fetch", 11)])).toBe(true)
    })
    it("does not refresh for waiting fetches or non-fetch tasks", () => {
        expect(completedFetchChanged([], [task("running"), task("retry_wait", 2), task("succeeded", 3, "deploy")])).toBe(false)
    })
    it("refreshes persisted status when a fetch fails", () => {
        expect(completedFetchChanged([task("running")], [task("failed")])).toBe(true)
    })
    it("invalidates actual tracker/current/history queries only on completion", () => {
        const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
        const invalidate = vi.spyOn(client, "invalidateQueries")
        const wrapper = ({ children }: { children: React.ReactNode }) => <QueryClientProvider client={client}>{children}</QueryClientProvider>
        const { rerender, unmount } = renderHook(({ tasks }: { tasks: Snapshot[] | undefined }) => useCompletedFetchInvalidation(tasks), { initialProps: { tasks: undefined as Snapshot[] | undefined }, wrapper })
        rerender({ tasks: [task("running")] })
        expect(invalidate).not.toHaveBeenCalled()
        rerender({ tasks: [task("succeeded")] })
        expect(invalidate.mock.calls.map(([input]) => input?.queryKey)).toEqual([["trackers"], ["releases"], ["stats"]])
        rerender({ tasks: [task("succeeded")] })
        expect(invalidate).toHaveBeenCalledTimes(3)
        unmount()
        client.clear()
    })
})
