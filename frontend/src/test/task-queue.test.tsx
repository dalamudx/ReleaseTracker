import { beforeEach, describe, expect, it, vi } from "vitest"
import { fireEvent, render, screen, waitFor } from "@testing-library/react"
import { MemoryRouter } from "react-router"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { api } from "@/api/client"
import i18n from "@/i18n/config"
import TasksPage from "@/pages/Tasks"
import type { QueueTask } from "@/api/task-types"

vi.mock("@/api/client", () => ({ api: { getTasks: vi.fn(), getTask: vi.fn(), cancelTask: vi.fn(), retryTask: vi.fn(), resolveTask: vi.fn(), clearFinishedTasks: vi.fn() } }))

function task(state: QueueTask["state"], kind: QueueTask["kind"] = "fetch"): QueueTask {
    return { id: 17, kind, state, target_label: "nginx-test", attempts: 1, max_retries: 3, due_at: 1789705000, created_at: 1789704900, updated_at: 1789705000, error_code: null, message: null, result: {}, target: {} }
}
function show() {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false, refetchInterval: false } } })
    return render(<QueryClientProvider client={client}><MemoryRouter><TasksPage /></MemoryRouter></QueryClientProvider>)
}
beforeEach(async () => { vi.clearAllMocks(); await i18n.changeLanguage("zh") })

describe("persistent task queue", () => {
    it("shows a retryable load failure instead of an empty queue", async () => {
        vi.mocked(api.getTasks)
            .mockRejectedValueOnce(new Error("temporarily unavailable"))
            .mockResolvedValueOnce([])
        show()

        expect(await screen.findByRole("alert")).toHaveTextContent(i18n.t("common.loadFailedTitle"))
        fireEvent.click(screen.getByRole("button", { name: i18n.t("common.retry") }))
        await waitFor(() => expect(api.getTasks).toHaveBeenCalledTimes(2))
        expect(await screen.findByText(i18n.t("tasks.empty"))).toBeVisible()
    })

    it("shows readiness wait while task remains running", async () => {
        const active = task("running", "deploy")
        active.result = { phase: "health_checking", health_check: { outcome: "pending", elapsed_seconds: 25, services: [{ service: "service-a", status: "pending", method: "native" }] } }
        vi.mocked(api.getTasks).mockResolvedValue([active])
        show()
        await screen.findByText("nginx-test")
        expect(screen.getByTestId("task-summary-state")).toHaveTextContent("等待就绪")
        expect(screen.getByRole("status")).toHaveTextContent("service-a")
    })
    it("keeps diagnostics in details without fetching every collapsed task", async () => {
        const failed = {...task("failed"), error_code: "upstream_timeout", message: "registry request timed out"}
        vi.mocked(api.getTasks).mockResolvedValue([failed])
        vi.mocked(api.getTask).mockResolvedValue(failed)
        show()
        await screen.findByText("nginx-test")
        expect(screen.getByTestId("task-summary")).toHaveTextContent("失败")
        expect(screen.queryByText(failed.message)).not.toBeInTheDocument()
        expect(api.getTask).not.toHaveBeenCalled()
        fireEvent.click(screen.getByRole("button", {name: i18n.t("tasks.details", {id: 17})}))
        expect(await screen.findByText(failed.message)).toBeVisible()
        expect(screen.getByText(i18n.t("tasks.errors.upstream_timeout"))).toBeVisible()
    })

    it("confirms before clearing and refreshes task lists while preserving active tasks", async () => {
        const done = task("succeeded")
        const active = {...task("running"), id: 18, target_label: "active-job"}
        vi.mocked(api.getTasks).mockResolvedValue([done, active])
        vi.mocked(api.clearFinishedTasks).mockImplementation(async () => {
            vi.mocked(api.getTasks).mockResolvedValue([active])
            return {cleared: 1}
        })
        show()
        await screen.findByText("nginx-test")
        fireEvent.click(screen.getByRole("button", {name: i18n.t("tasks.clearFinished")}))
        expect(screen.getByRole("alertdialog")).toBeVisible()
        expect(screen.getByText(i18n.t("tasks.clearDescription"))).toBeVisible()
        expect(api.clearFinishedTasks).not.toHaveBeenCalled()
        fireEvent.click(screen.getByRole("button", {name: i18n.t("common.cancel")}))
        expect(api.clearFinishedTasks).not.toHaveBeenCalled()
        fireEvent.click(screen.getByRole("button", {name: i18n.t("tasks.clearFinished")}))
        fireEvent.click(screen.getByRole("button", {name: i18n.t("tasks.clearConfirm")}))
        await waitFor(() => expect(screen.queryByText("nginx-test")).not.toBeInTheDocument())
        expect(screen.getByText("active-job")).toBeVisible()
        expect(api.clearFinishedTasks).toHaveBeenCalledTimes(1)
    })
    it("keeps the confirmation open and task data intact when clearing fails", async () => {
        vi.mocked(api.getTasks).mockResolvedValue([task("failed")])
        vi.mocked(api.clearFinishedTasks).mockRejectedValue(new Error("unavailable"))
        show()
        await screen.findByText("nginx-test")
        fireEvent.click(screen.getByRole("button", {name: i18n.t("tasks.clearFinished")}))
        fireEvent.click(screen.getByRole("button", {name: i18n.t("tasks.clearConfirm")}))
        await waitFor(() => expect(api.clearFinishedTasks).toHaveBeenCalledTimes(1))
        await waitFor(() => expect(screen.getByRole("button", {name: i18n.t("tasks.clearConfirm")})).toBeEnabled())
        expect(screen.getByRole("alertdialog")).toBeVisible()
        expect(screen.getByText("nginx-test")).toBeVisible()
    })
    it("shows retry attempt, due time and linked source runs without claiming completion", async () => {
        const queued = task("retry_wait")
        vi.mocked(api.getTasks).mockResolvedValue([queued])
        vi.mocked(api.getTask).mockResolvedValue({ ...queued, result: { source_fetch_run_ids: { "2": 88 } }, attempt_history: [{ id: 1, attempt: 1, state: "retry_wait", started_at: 1789704900, finished_at: 1789705000, error_code: "upstream_timeout" }], triggers: [] })
        show()
        expect(await screen.findByText("nginx-test")).toBeVisible()
        expect(screen.getByText("等待重试")).toBeVisible()
        fireEvent.click(screen.getByRole("button", { name: i18n.t("tasks.details", { id: 17 }) }))
        expect(await screen.findByText(i18n.t("tasks.sourceRun", { source: "2", run: 88 }))).toBeVisible()
        expect(screen.getByRole("button", { name: i18n.t("common.cancel") })).toBeEnabled()
        expect(screen.queryByRole("button", { name: i18n.t("tasks.retry") })).not.toBeInTheDocument()
    })
    it("does not offer unsafe deployment retries or running cancellation", async () => {
        const deployment = task("needs_attention", "deploy")
        vi.mocked(api.getTasks).mockResolvedValue([deployment])
        vi.mocked(api.getTask).mockResolvedValue(deployment)
        show()
        await screen.findByText("nginx-test")
        fireEvent.click(screen.getByRole("button", { name: i18n.t("tasks.details", { id: 17 }) }))
        expect(screen.getByText(i18n.t("tasks.attention"))).toBeVisible()
        expect(screen.queryByRole("button", { name: i18n.t("common.cancel") })).not.toBeInTheDocument()
        expect(screen.queryByRole("button", { name: i18n.t("tasks.retry") })).not.toBeInTheDocument()
    })
    it("requires explicit human attestation before releasing an interrupted deployment", async () => {
        const deployment = task("needs_attention", "deploy")
        vi.mocked(api.getTasks).mockResolvedValue([deployment])
        vi.mocked(api.getTask).mockResolvedValue(deployment)
        vi.mocked(api.resolveTask).mockResolvedValue({ status: "failed" })
        show()
        await screen.findByText("nginx-test")
        fireEvent.click(screen.getByRole("button", { name: i18n.t("tasks.details", { id: 17 }) }))
        fireEvent.click(screen.getByRole("button", { name: i18n.t("tasks.resolve") }))
        expect(screen.getByRole("alertdialog")).toBeVisible()
        expect(api.resolveTask).not.toHaveBeenCalled()
        fireEvent.click(screen.getByRole("button", { name: i18n.t("tasks.resolveConfirm") }))
        await waitFor(() => expect(api.resolveTask).toHaveBeenCalledWith(17))
    })
    it("lets an administrator explicitly retry a terminal failed fetch", async () => {
        const failed = task("failed")
        vi.mocked(api.getTasks).mockResolvedValue([failed])
        vi.mocked(api.getTask).mockResolvedValue(failed)
        vi.mocked(api.retryTask).mockResolvedValue({ task_id: 18, status: "queued" })
        show()
        await screen.findByText("nginx-test")
        fireEvent.click(screen.getByRole("button", { name: i18n.t("tasks.details", { id: 17 }) }))
        fireEvent.click(screen.getByRole("button", { name: i18n.t("tasks.retry") }))
        await waitFor(() => expect(api.retryTask).toHaveBeenCalledWith(17))
    })
})
