import { beforeEach, describe, expect, it, vi } from "vitest"
import { fireEvent, render, screen } from "@testing-library/react"
import { MemoryRouter } from "react-router"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { api } from "@/api/client"
import i18n from "@/i18n/config"
import { TaskNotificationPopover } from "@/components/layout/TaskNotificationPopover"
import type { QueueTask } from "@/api/task-types"

vi.mock("@/api/client", () => ({
    api: {
        getTasks: vi.fn(),
    },
}))

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
    return render(
        <QueryClientProvider client={client}>
            <MemoryRouter>
                <TaskNotificationPopover />
            </MemoryRouter>
        </QueryClientProvider>,
    )
}

beforeEach(async () => {
    vi.clearAllMocks()
    await i18n.changeLanguage("zh")
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
