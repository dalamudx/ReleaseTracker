import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react"
import { toast } from "sonner"
import { TASK_NOTIFICATION_READ_KEY, TASK_NOTIFICATION_DISMISSED_KEY } from "@/hooks/use-task-notification-read"
import { MemoryRouter } from "react-router"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { api } from "@/api/client"
import i18n from "@/i18n/config"
import { TaskNotificationPopover } from "@/components/layout/TaskNotificationPopover"
import type { QueueTask } from "@/api/task-types"

vi.mock("@/api/client", async () => {
    const { createTaskApiMock } = await import("./api-mock")
    return { api: createTaskApiMock() }
})

vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }))

function createTask(id: number, state: QueueTask["state"], target_label: string): QueueTask {
    return {
        id,
        kind: "fetch",
        state,
        target_label,
        attempts: 1,
        max_retries: 3,
        due_at: 1789705000,
        created_at: 1789704900,
        updated_at: 1789705000,
        error_code: null,
        message: null,
        result: {},
        target: { tracker_name: target_label },
        triggers: [{ trigger_mode: "webhook", created_at: 1789704900 }],
    }
}

function renderPopover() {
    const client = new QueryClient({
        defaultOptions: {
            queries: {
                retry: false,
                refetchInterval: false,
            },
        },
    })
    const view = render(
        <QueryClientProvider client={client}>
            <MemoryRouter>
                <TaskNotificationPopover />
            </MemoryRouter>
        </QueryClientProvider>,
    )
    return { ...view, client }
}

beforeEach(async () => {
    vi.resetAllMocks()
    localStorage.clear()
    window.dispatchEvent(new StorageEvent("storage", { key: null }))
    await i18n.changeLanguage("zh")
})

afterEach(() => {
    expect(api.clearFinishedTasks).not.toHaveBeenCalled()
    vi.restoreAllMocks()
})

const indicator = () => screen.getByRole("button", { name: "任务动态" }).querySelector("[data-indicator]")

function storedReads() {
    const value = JSON.parse(localStorage.getItem(TASK_NOTIFICATION_READ_KEY) || "{}")
    return Object.fromEntries(value.tasks || [])
}

async function openPopover() {
    fireEvent.click(screen.getByRole("button", { name: "任务动态" }))
    await screen.findByText("最近任务")
}

describe("notification read and clear behavior", () => {
    it("prioritizes unread failure over running and remembers viewed results", async () => {
        vi.mocked(api.getTasks).mockResolvedValue([
            createTask(2, "running", "running-service"), createTask(1, "failed", "failed-service"),
        ])
        const view = renderPopover()
        await waitFor(() => expect(indicator()).toHaveAttribute("data-indicator", "attention"))
        await openPopover()
        await waitFor(() => expect(indicator()).toHaveAttribute("data-indicator", "running"))
        expect(storedReads()[1]).toBe(1789705000)
        expect(storedReads()[2]).toBeUndefined()
        view.unmount()
        renderPopover()
        await waitFor(() => expect(indicator()).toHaveAttribute("data-indicator", "running"))
    })

    it("does not light the dot for maintenance queues and routine no-change results", async () => {
        vi.mocked(api.getTasks).mockResolvedValue([
            createTask(4, "queued", "queued"), createTask(3, "no_change", "no-change"),
            createTask(2, "skipped", "skipped"), createTask(1, "superseded", "old"),
        ])
        renderPopover()
        await openPopover()
        await screen.findByText("no-change")
        expect(indicator()).toBeNull()
        expect(screen.getByRole("button", { name: "清理最近任务" })).toBeEnabled()
    })

    it("keeps actionable attention and approval visible and never makes them clearable", async () => {
        const task = createTask(1, "needs_attention", "needs-verification")
        vi.mocked(api.getTasks).mockResolvedValue([task])
        const { client } = renderPopover()
        await openPopover()
        await screen.findByText("needs-verification")
        expect(indicator()).toHaveAttribute("data-indicator", "attention")
        expect(screen.getByRole("button", { name: "清理最近任务" })).toBeDisabled()
        const approval = { ...task, state: "queued" as const, approval_pending: true }
        act(() => client.setQueryData(["tasks", "recent-popover"], [approval]))
        await waitFor(() => expect(indicator()).toHaveAttribute("data-indicator", "warning"))
        expect(screen.getByRole("button", { name: "清理最近任务" })).toBeDisabled()
    })

    it("clears every settled notification without altering either queue query", async () => {
        const tasks = Array.from({ length: 6 }, (_, i) => createTask(6 - i, "succeeded", `task-${6 - i}`))
        vi.mocked(api.getTasks).mockResolvedValue(tasks)
        const { client } = renderPopover()
        client.setQueryData(["tasks", "list", {}], tasks)
        await openPopover()
        expect(await screen.findByText("task-1")).toBeVisible()
        await waitFor(() => expect(screen.getByRole("button", { name: "清理最近任务" })).toBeEnabled())
        fireEvent.click(screen.getByRole("button", { name: "清理最近任务" }))
        expect(await screen.findByText("暂无最近任务")).toBeVisible()
        const hidden = JSON.parse(localStorage.getItem(TASK_NOTIFICATION_DISMISSED_KEY)!).tasks
        expect(hidden.map(([id]: [number, number]) => id).sort()).toEqual([1, 2, 3, 4, 5, 6])
        expect(client.getQueryData(["tasks", "recent-popover"])).toEqual(tasks)
        expect(client.getQueryData(["tasks", "list", {}])).toEqual(tasks)
        expect(api.getTasks).toHaveBeenCalledTimes(2)
        expect(api.clearFinishedTasks).not.toHaveBeenCalled()
        expect(toast.success).toHaveBeenCalledWith("已清理 6 条最近任务")
    })

    it("loads beyond both the initial twenty rows and the first server page", async () => {
        const tasks = Array.from({ length: 65 }, (_, i) => createTask(65 - i, "succeeded", `paged-${65 - i}`))
        vi.mocked(api.getTasks).mockImplementation(async params => tasks.filter(task => !params?.before || task.id < params.before).slice(0, 50))
        renderPopover()
        await openPopover()
        expect(await screen.findByText("paged-46")).toBeVisible()
        expect(screen.queryByText("paged-45")).not.toBeInTheDocument()
        for (const last of [26, 6, 1]) {
            await waitFor(() => expect(screen.getByRole("button", { name: "加载更多任务" })).toBeEnabled())
            fireEvent.click(screen.getByRole("button", { name: "加载更多任务" }))
            expect(await screen.findByText(`paged-${last}`)).toBeVisible()
        }
        expect(api.getTasks).toHaveBeenCalledWith({ before: 16 })
        expect(screen.queryByRole("button", { name: "加载更多任务" })).not.toBeInTheDocument()
    })

    it("clears all pages beyond the old storage cap, retaining actionable tasks", async () => {
        const tasks = Array.from({ length: 1105 }, (_, i) => createTask(1105 - i, "succeeded", `bulk-${1105 - i}`))
        tasks[1].state = "running"
        tasks[2].state = "needs_attention"
        tasks[3].approval_pending = true
        tasks[900].state = "awaiting_approval"
        vi.mocked(api.getTasks).mockImplementation(async params => tasks.filter(task => !params?.before || task.id < params.before).slice(0, 50))
        const { client } = renderPopover()
        await openPopover()
        await waitFor(() => expect(screen.getByRole("button", { name: "清理最近任务" })).toBeEnabled())
        fireEvent.click(screen.getByRole("button", { name: "清理最近任务" }))
        await waitFor(() => expect(toast.success).toHaveBeenCalledWith("已清理 1101 条最近任务"))
        const hidden = JSON.parse(localStorage.getItem(TASK_NOTIFICATION_DISMISSED_KEY)!).tasks
        expect(hidden).toHaveLength(1101)
        expect(hidden.some(([id]: [number, number]) => id === 1)).toBe(true)
        expect(await screen.findByText("bulk-1104")).toBeVisible()
        expect(screen.getByText("bulk-1103")).toBeVisible()
        expect(screen.getByText("bulk-1102")).toBeVisible()
        expect(screen.queryByText("bulk-1105")).not.toBeInTheDocument()
        expect(screen.getByText("bulk-205")).toBeVisible()
        expect(screen.queryByRole("button", { name: "加载更多任务" })).not.toBeInTheDocument()
        expect(client.getQueryData(["tasks", "recent-popover"])).toEqual(tasks.slice(0, 50))
        expect(api.clearFinishedTasks).not.toHaveBeenCalled()
        act(() => window.dispatchEvent(new StorageEvent("storage", { key: TASK_NOTIFICATION_DISMISSED_KEY })))
        expect(screen.queryByText("bulk-1105")).not.toBeInTheDocument()
    })

    it("does not partially clear notifications when a later page fails", async () => {
        const tasks = Array.from({ length: 50 }, (_, i) => createTask(50 - i, "failed", `kept-${50 - i}`))
        vi.mocked(api.getTasks).mockImplementation(async params => {
            if (params?.before) throw new Error("network failure")
            return tasks
        })
        renderPopover()
        await openPopover()
        await waitFor(() => expect(screen.getByRole("button", { name: "清理最近任务" })).toBeEnabled())
        fireEvent.click(screen.getByRole("button", { name: "清理最近任务" }))
        await waitFor(() => expect(toast.error).toHaveBeenCalledWith("清理失败，未隐藏任何任务，请重试。"))
        expect(screen.getByText("kept-50")).toBeVisible()
        expect(localStorage.getItem(TASK_NOTIFICATION_DISMISSED_KEY)).toBeNull()
    })

    it("treats a newly settled version as unread until the popover is reopened", async () => {
        const task = createTask(1, "failed", "changed-result")
        localStorage.setItem(TASK_NOTIFICATION_READ_KEY, JSON.stringify({ version: 1, tasks: [[1, task.updated_at]] }))
        vi.mocked(api.getTasks).mockResolvedValue([task])
        const { client } = renderPopover()
        await waitFor(() => expect(client.getQueryData(["tasks", "recent-popover"])).toEqual([task]))
        expect(indicator()).toBeNull()
        const newer = { ...task, updated_at: task.updated_at + 1 }
        act(() => client.setQueryData(["tasks", "recent-popover"], [newer]))
        await waitFor(() => expect(indicator()).toHaveAttribute("data-indicator", "attention"))
        await openPopover()
        await waitFor(() => expect(indicator()).toBeNull())
        expect(storedReads()[1]).toBe(newer.updated_at)
    })

    it("keeps cleared notifications hidden after refresh, remount and storage-tab sync", async () => {
        const task = createTask(1, "succeeded", "hidden-result")
        vi.mocked(api.getTasks).mockResolvedValue([task])
        const first = renderPopover()
        await openPopover()
        await waitFor(() => expect(screen.getByRole("button", { name: "清理最近任务" })).toBeEnabled())
        fireEvent.click(screen.getByRole("button", { name: "清理最近任务" }))
        expect(await screen.findByText("暂无最近任务")).toBeVisible()
        fireEvent.click(screen.getByRole("button", { name: "刷新任务" }))
        await waitFor(() => expect(api.getTasks).toHaveBeenCalledTimes(3))
        expect(screen.queryByText("hidden-result")).not.toBeInTheDocument()
        first.unmount()
        renderPopover()
        await openPopover()
        expect(await screen.findByText("暂无最近任务")).toBeVisible()
        act(() => {
            localStorage.removeItem(TASK_NOTIFICATION_DISMISSED_KEY)
            window.dispatchEvent(new StorageEvent("storage", { key: TASK_NOTIFICATION_DISMISSED_KEY }))
        })
        expect(await screen.findByText("hidden-result")).toBeVisible()
    })

    it("shows newer results and actionable states of a dismissed task again", async () => {
        const task = createTask(1, "succeeded", "updated-task")
        vi.mocked(api.getTasks).mockResolvedValue([task])
        const { client } = renderPopover()
        await openPopover()
        await waitFor(() => expect(screen.getByRole("button", { name: "清理最近任务" })).toBeEnabled())
        fireEvent.click(screen.getByRole("button", { name: "清理最近任务" }))
        expect(await screen.findByText("暂无最近任务")).toBeVisible()
        fireEvent.click(screen.getByRole("button", { name: "任务动态" }))
        act(() => client.setQueryData(["tasks", "recent-popover"], [{ ...task, state: "failed", updated_at: task.updated_at + 1 }]))
        await waitFor(() => expect(indicator()).toHaveAttribute("data-indicator", "attention"))
        await openPopover()
        expect(await screen.findByText("updated-task")).toBeVisible()
        act(() => client.setQueryData(["tasks", "recent-popover"], [{ ...task, state: "needs_attention" }]))
        await waitFor(() => expect(screen.getByRole("button", { name: "清理最近任务" })).toBeDisabled())
        expect(screen.getByText("updated-task")).toBeVisible()
        act(() => client.setQueryData(["tasks", "recent-popover"], [{ ...task, approval_pending: true }]))
        expect(screen.getByText("updated-task")).toBeVisible()
        expect(screen.getByRole("button", { name: "清理最近任务" })).toBeDisabled()
    })

    it("synchronizes read state from another tab", async () => {
        const task = createTask(1, "succeeded", "cross-tab")
        vi.mocked(api.getTasks).mockResolvedValue([task])
        renderPopover()
        await waitFor(() => expect(indicator()).toHaveAttribute("data-indicator", "unread"))
        act(() => {
            localStorage.setItem(TASK_NOTIFICATION_READ_KEY, JSON.stringify({ version: 1, tasks: [[1, task.updated_at]] }))
            window.dispatchEvent(new StorageEvent("storage", { key: TASK_NOTIFICATION_READ_KEY }))
        })
        await waitFor(() => expect(indicator()).toBeNull())
    })

    it("handles malformed and unwritable local storage without blocking clearing", async () => {
        localStorage.setItem(TASK_NOTIFICATION_READ_KEY, "not-json")
        vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new Error("blocked") })
        vi.mocked(api.getTasks).mockResolvedValue([createTask(1, "failed", "storage-blocked")])
        renderPopover()
        await openPopover()
        await waitFor(() => expect(screen.getByRole("button", { name: "清理最近任务" })).toBeEnabled())
        expect(indicator()).toBeNull()
        fireEvent.click(screen.getByRole("button", { name: "清理最近任务" }))
        expect(await screen.findByText("暂无最近任务")).toBeVisible()
    })

    it("shows fetch errors rather than a misleading empty list", async () => {
        vi.mocked(api.getTasks).mockRejectedValue(new Error("offline"))
        renderPopover()
        await openPopover()
        expect(await screen.findByRole("alert")).toHaveTextContent("任务加载失败")
        expect(screen.getByRole("button", { name: "清理最近任务" })).toBeDisabled()
        expect(screen.queryByText("暂无最近任务")).not.toBeInTheDocument()
    })
})

describe("TaskNotificationPopover", () => {
    it("shows readiness progress without marking deployment complete", async () => {
        const task = createTask(3, "running", "service-a")
        task.kind = "deploy"
        task.result = { phase: "health_checking", health_check: { outcome: "pending", elapsed_seconds: 20 } }
        vi.mocked(api.getTasks).mockResolvedValue([task])
        renderPopover()
        fireEvent.click(screen.getByRole("button", { name: "任务动态" }))
        expect(await screen.findByText("等待就绪")).toBeVisible()
        expect(screen.getByRole("status")).toHaveTextContent("20")
    })
    it("renders trigger button and opens popover showing recent tasks", async () => {
        const mockTasks = [
            createTask(1, "running", "frontend-app"),
            createTask(2, "succeeded", "backend-service"),
        ]
        vi.mocked(api.getTasks).mockResolvedValue(mockTasks)

        renderPopover()

        const trigger = screen.getByRole("button", { name: "任务动态" })
        expect(trigger).toBeInTheDocument()

        fireEvent.click(trigger)

        expect(await screen.findByText("最近任务")).toBeVisible()
        expect(await screen.findByText("frontend-app")).toBeVisible()
        expect(await screen.findByText("backend-service")).toBeVisible()
        expect(screen.getByText("查看全部任务")).toBeVisible()
    })

    it("displays empty state when no recent tasks exist", async () => {
        vi.mocked(api.getTasks).mockResolvedValue([])

        renderPopover()

        const trigger = screen.getByRole("button", { name: "任务动态" })
        fireEvent.click(trigger)

        expect(await screen.findByText("暂无最近任务")).toBeVisible()
    })
})