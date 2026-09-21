import { beforeEach, describe, expect, it, vi } from "vitest"
import { fireEvent, render, screen, waitFor } from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { MemoryRouter } from "react-router"
import { toast } from "sonner"
import { api } from "@/api/client"
import type { QueueTask } from "@/api/task-types"
import i18n from "@/i18n/config"
import { canRecheckReadiness, type HealthOutcome } from "@/lib/readiness"
import TasksPage from "@/pages/Tasks"

vi.mock("@/api/client", () => ({ api: { getTasks: vi.fn(), getTask: vi.fn(), recheckTask: vi.fn(), retryTask: vi.fn(), clearFinishedTasks: vi.fn() } }))
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }))
const task = (): QueueTask => ({ id: 17, kind: "deploy", state: "failed", target_label: "service-a", attempts: 1, max_retries: 0, due_at: 1789705000, created_at: 1789704900, updated_at: 1789705000, error_code: null, message: null, result: { phase: "completed", health_check: { outcome: "timeout" } }, target: {} })
function show() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, refetchInterval: false } } })
  return render(<QueryClientProvider client={client}><MemoryRouter><TasksPage /></MemoryRouter></QueryClientProvider>)
}
beforeEach(async () => { vi.clearAllMocks(); await i18n.changeLanguage("en") })

describe("read-only readiness recheck", () => {
  it.each<HealthOutcome>(["timeout", "unhealthy", "unknown", "unsupported", "superseded"])("allows explicit completed deployment/recovery %s recheck", outcome => {
    for (const kind of ["deploy", "recover"] as const) expect(canRecheckReadiness({ ...task(), kind, result: { phase: "completed", health_check: { outcome } } })).toBe(true)
  })
  it.each<HealthOutcome>(["healthy", "pending", "not_checked"])("does not recheck %s", outcome => {
    expect(canRecheckReadiness({ ...task(), result: { phase: "completed", health_check: { outcome } } })).toBe(false)
  })
  it("excludes active, incomplete, fetch and already checking tasks", () => {
    for (const state of ["queued", "running", "retry_wait"] as const) expect(canRecheckReadiness({ ...task(), state })).toBe(false)
    expect(canRecheckReadiness({ ...task(), kind: "fetch" })).toBe(false)
    expect(canRecheckReadiness({ ...task(), result: null })).toBe(false)
    expect(canRecheckReadiness({ ...task(), result: { ...task().result, phase: "health_checking" } })).toBe(false)
    expect(canRecheckReadiness({ ...task(), result: { ...task().result, health_recheck: { outcome: "pending" } } })).toBe(false)
  })
  it("queues only after explicit click, disables duplicate submission and preserves original failure", async () => {
    const original = task()
    vi.mocked(api.getTasks).mockResolvedValue([original])
    vi.mocked(api.getTask).mockResolvedValue(original)
    let finish!: (receipt: { task_id: number; status: "queued" }) => void
    vi.mocked(api.recheckTask).mockImplementation(() => new Promise(resolve => { finish = resolve }))
    show()
    await screen.findByText("service-a")
    fireEvent.click(screen.getByRole("button", { name: i18n.t("tasks.details", { id: 17 }) }))
    const button = screen.getByRole("button", { name: "Recheck readiness" })
    expect(api.recheckTask).not.toHaveBeenCalled()
    expect(screen.getByText(/Read-only check/)).toBeVisible()
    fireEvent.click(button)
    await waitFor(() => expect(button).toBeDisabled())
    expect(api.recheckTask).toHaveBeenCalledExactlyOnceWith(17)
    finish({ task_id: 18, status: "queued" })
    await waitFor(() => expect(toast.success).toHaveBeenCalledWith("Readiness recheck for “service-a” queued."))
    expect(api.retryTask).not.toHaveBeenCalled()
    expect(screen.getByTestId("task-summary-state")).toHaveTextContent("Failed")
    expect(screen.getByText("Readiness: Timed out")).toBeVisible()
  })
  it("keeps original outcome alongside latest recheck and reports submission errors", async () => {
    const original = { ...task(), result: { ...task().result, health_recheck: { outcome: "healthy" as const } } }
    vi.mocked(api.getTasks).mockResolvedValue([original])
    vi.mocked(api.getTask).mockResolvedValue(original)
    vi.mocked(api.recheckTask).mockRejectedValue(new Error("unavailable"))
    show()
    await screen.findByText("service-a")
    expect(screen.getByText("Readiness: Timed out")).toBeVisible()
    expect(screen.getByText("Latest readiness recheck: Healthy")).toBeVisible()
    fireEvent.click(screen.getByRole("button", { name: i18n.t("tasks.details", { id: 17 }) }))
    fireEvent.click(screen.getByRole("button", { name: "Recheck readiness" }))
    await waitFor(() => expect(toast.error).toHaveBeenCalledWith("Could not queue readiness recheck."))
    expect(toast.success).not.toHaveBeenCalled()
  })
})
