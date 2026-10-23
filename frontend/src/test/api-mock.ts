import { vi } from "vitest"
import type { api } from "@/api/client"

/** Fresh task API mocks per test module, with a default rows-to-page bridge. */
export function createTaskApiMock() {
  const getTasks = vi.fn<typeof api.getTasks>()
  const getTaskPage = vi.fn<typeof api.getTaskPage>(async params => {
    const items = await getTasks(params)
    return { items, total: items.length }
  })
  return {
    getTasks,
    getTaskPage,
    getTask: vi.fn<typeof api.getTask>(),
    getDeploymentPlan: vi.fn<typeof api.getDeploymentPlan>(),
    approveDeployment: vi.fn<typeof api.approveDeployment>(),
    cancelTask: vi.fn<typeof api.cancelTask>(),
    retryTask: vi.fn<typeof api.retryTask>(),
    resolveTask: vi.fn<typeof api.resolveTask>(),
    recheckTask: vi.fn<typeof api.recheckTask>(),
    recheckBaseline: vi.fn<typeof api.recheckBaseline>(),
    clearFinishedTasks: vi.fn<typeof api.clearFinishedTasks>(),
  }
}
