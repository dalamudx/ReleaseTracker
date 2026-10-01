import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react"
import { toast } from "sonner"
import { TASK_NOTIFICATION_READ_KEY } from "@/hooks/use-task-notification-read"
import { MemoryRouter } from "react-router"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { api } from "@/api/client"
import i18n from "@/i18n/config"
import { TaskNotificationPopover } from "@/components/layout/TaskNotificationPopover"
import type { QueueTask } from "@/api/task-types"

vi.mock("@/api/client", () => ({
    api: {
        getTasks: vi.fn(),
        clearFinishedTasks: vi.fn(),
    },
}))

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

afterEach(() => vi.restoreAllMocks())

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
        expect(screen.getByRole("button", { name: "清理已读" })).toBeEnabled()
    })

    it("keeps actionable attention and approval visible and never makes them clearable", async () => {
        const task = createTask(1, "needs_attention", "needs-verification")
        vi.mocked(api.getTasks).mockResolvedValue([task])
        const { client } = renderPopover()
        await openPopover()
        await screen.findByText("needs-verification")
        expect(indicator()).toHaveAttribute("data-indicator", "attention")
        expect(screen.getByRole("button", { name: "清理已读" })).toBeDisabled()
        const approval = { ...task, state: "queued" as const, approval_pending: true }
        act(() => client.setQueryData(["tasks", "recent-popover"], [approval]))
        await waitFor(() => expect(indicator()).toHaveAttribute("data-indicator", "warning"))
        expect(screen.getByRole("button", { name: "清理已读" })).toBeDisabled()
    })

    it("clears only observed rows and refreshes the remaining unseen row", async () => {
        const tasks = Array.from({ length: 6 }, (_, i) => createTask(6 - i, "succeeded", `task-${6 - i}`))
        vi.mocked(api.getTasks).mockResolvedValueOnce(tasks).mockResolvedValue([tasks[5]])
        vi.mocked(api.clearFinishedTasks).mockResolvedValue({ cleared: 5 })
        renderPopover()
        await openPopover()
        await waitFor(() => expect(screen.getByRole("button", { name: "清理已读" })).toBeEnabled())
        expect(storedReads()[1]).toBeUndefined()
        fireEvent.click(screen.getByRole("button", { name: "清理已读" }))
        await waitFor(() => expect(api.clearFinishedTasks).toHaveBeenCalledWith(
            tasks.slice(0, 5).map((task) => ({ id: task.id, updated_at: task.updated_at })),
        ))
        expect(await screen.findByText("task-1")).toBeVisible()
        expect(screen.queryByText("task-6")).not.toBeInTheDocument()
        expect(toast.success).toHaveBeenCalledWith("已清理 5 条已读任务")
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

    it("reports clear failure without hiding rows", async () => {
        vi.mocked(api.getTasks).mockResolvedValue([createTask(1, "failed", "preserved-task")])
        vi.mocked(api.clearFinishedTasks).mockRejectedValue(new Error("offline"))
        renderPopover()
        await openPopover()
        const clear = screen.getByRole("button", { name: "清理已读" })
        await waitFor(() => expect(clear).toBeEnabled())
        fireEvent.click(clear)
        await waitFor(() => expect(toast.error).toHaveBeenCalledWith("清除结束任务失败"))
        expect(screen.getByText("preserved-task")).toBeVisible()
        expect(clear).toBeEnabled()
    })

    it("disables clearing while a request is pending and refreshes to empty on success", async () => {
        let finish: (result: { cleared: number }) => void = () => undefined
        vi.mocked(api.getTasks).mockResolvedValueOnce([createTask(1, "succeeded", "pending-clear")]).mockResolvedValue([])
        vi.mocked(api.clearFinishedTasks).mockImplementation(() => new Promise((resolve) => { finish = resolve }))
        renderPopover()
        await openPopover()
        const clear = screen.getByRole("button", { name: "清理已读" })
        await waitFor(() => expect(clear).toBeEnabled())
        fireEvent.click(clear)
        await waitFor(() => expect(clear).toBeDisabled())
        fireEvent.click(clear)
        expect(api.clearFinishedTasks).toHaveBeenCalledTimes(1)
        act(() => finish({ cleared: 1 }))
        expect(await screen.findByText("暂无最近任务")).toBeVisible()
        expect(clear).toBeDisabled()
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
        await waitFor(() => expect(screen.getByRole("button", { name: "清理已读" })).toBeEnabled())
        expect(indicator()).toBeNull()
    })

    it("shows fetch errors rather than a misleading empty list", async () => {
        vi.mocked(api.getTasks).mockRejectedValue(new Error("offline"))
        renderPopover()
        await openPopover()
        expect(await screen.findByRole("alert")).toHaveTextContent("任务加载失败")
        expect(screen.getByRole("button", { name: "清理已读" })).toBeDisabled()
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
