import { act, fireEvent, render, screen, waitFor } from "@testing-library/react"
import { MemoryRouter } from "react-router-dom"
import { beforeEach, describe, expect, it, vi } from "vitest"

import type { ExecutorListItem, RuntimeConnection, TrackerStatus } from "@/api/types"

const {
  deleteExecutorMock,
  getExecutorsMock,
  getSettingsMock,
  getRuntimeConnectionsMock,
  getTrackersMock,
  runExecutorMock,
  tMock,
  toastErrorMock,
  toastSuccessMock,
} = vi.hoisted(() => ({
  deleteExecutorMock: vi.fn(),
  getExecutorsMock: vi.fn(),
  getSettingsMock: vi.fn(),
  getRuntimeConnectionsMock: vi.fn(),
  getTrackersMock: vi.fn(),
  runExecutorMock: vi.fn(),
  tMock: (key: string, options?: { count?: number }) => options?.count == null ? key : `${key}:${options.count}`,
  toastErrorMock: vi.fn(),
  toastSuccessMock: vi.fn(),
}))

vi.mock("@/api/client", () => ({
  api: {
    deleteExecutor: deleteExecutorMock,
    getExecutors: getExecutorsMock,
    getSettings: getSettingsMock,
    getRuntimeConnections: getRuntimeConnectionsMock,
    getTrackers: getTrackersMock,
    runExecutor: runExecutorMock,
  },
}))

vi.mock("sonner", () => ({
  toast: {
    error: toastErrorMock,
    success: toastSuccessMock,
  },
}))

vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: tMock,
    i18n: { language: "en" },
  }),
}))

vi.mock("@/components/executors/ExecutorList", () => ({
  ExecutorList: ({ executors, loading, onRun }: { executors: ExecutorListItem[]; loading: boolean; onRun: (executorId: number) => void }) => (
    <div data-testid="executor-list" data-loading={String(loading)}>
      {executors.map((executor) => (
        <button key={executor.id} type="button" onClick={() => executor.id && onRun(executor.id)}>
          run executor {executor.id}
        </button>
      ))}
    </div>
  ),
}))

vi.mock("@/components/executors/ExecutorSheet", () => ({
  ExecutorSheet: () => null,
}))

vi.mock("@/components/executors/ExecutorExecutionHistoryPanel", () => ({
  ExecutorExecutionHistoryPanel: () => null,
}))

vi.mock("@/components/ui/select", () => ({
  Select: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  SelectContent: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  SelectItem: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  SelectTrigger: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  SelectValue: () => <span />,
}))

import ExecutorsPage from "@/pages/Executors"

function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (reason?: unknown) => void
  const promise = new Promise<T>((promiseResolve, promiseReject) => {
    resolve = promiseResolve
    reject = promiseReject
  })

  return { promise, reject, resolve }
}

function createExecutor(overrides: Partial<ExecutorListItem> = {}): ExecutorListItem {
  return {
    id: overrides.id ?? 1,
    name: overrides.name ?? "release-api",
    runtime_type: overrides.runtime_type ?? "docker",
    runtime_connection_id: overrides.runtime_connection_id ?? 1,
    tracker_name: overrides.tracker_name ?? "release-tracker",
    tracker_source_id: overrides.tracker_source_id ?? 1,
    channel_name: overrides.channel_name ?? "stable",
    enabled: overrides.enabled ?? true,
    update_mode: overrides.update_mode ?? "manual",
    image_selection_mode: overrides.image_selection_mode ?? "replace_tag_on_current_image",
    image_reference_mode: overrides.image_reference_mode ?? "digest",
    target_ref: overrides.target_ref ?? {
      mode: "container",
      container_id: "abc123",
      container_name: "release-api",
    },
    maintenance_window: overrides.maintenance_window,
    description: overrides.description ?? null,
    runtime_connection_name: overrides.runtime_connection_name ?? "docker-prod",
    status: overrides.status ?? null,
  }
}

function createRuntimeConnection(overrides: Partial<RuntimeConnection> = {}): RuntimeConnection {
  return {
    id: overrides.id ?? 1,
    name: overrides.name ?? "docker-prod",
    type: overrides.type ?? "docker",
    enabled: overrides.enabled ?? true,
    config: overrides.config ?? {},
    secrets: overrides.secrets ?? {},
    endpoint: overrides.endpoint ?? null,
    description: overrides.description ?? null,
  }
}

function createTracker(overrides: Partial<TrackerStatus> = {}): TrackerStatus {
  return {
    id: overrides.id ?? 1,
    name: overrides.name ?? "release-tracker",
    enabled: overrides.enabled ?? true,
    description: overrides.description ?? null,
    changelog_policy: overrides.changelog_policy,
    primary_changelog_source_key: overrides.primary_changelog_source_key ?? "image",
    sources: overrides.sources ?? [],
    interval: overrides.interval ?? 360,
    version_sort_mode: overrides.version_sort_mode ?? "published_at",
    fetch_limit: overrides.fetch_limit ?? 10,
    fetch_timeout: overrides.fetch_timeout ?? 15,
    fallback_tags: overrides.fallback_tags ?? false,
    github_fetch_mode: overrides.github_fetch_mode ?? "rest_first",
    channels: overrides.channels ?? [],
    status: overrides.status ?? {
      enabled_source_count: 0,
      error: null,
      last_check: null,
      last_version: null,
      source_count: 0,
      source_types: [],
    },
  }
}

function renderExecutorsPage() {
  return render(
    <MemoryRouter>
      <ExecutorsPage />
    </MemoryRouter>,
  )
}

describe("ExecutorsPage run flow", () => {
  beforeEach(() => {
    vi.useRealTimers()
    deleteExecutorMock.mockReset()
    getExecutorsMock.mockReset()
    getSettingsMock.mockReset()
    getRuntimeConnectionsMock.mockReset()
    getTrackersMock.mockReset()
    runExecutorMock.mockReset()
    toastErrorMock.mockReset()
    toastSuccessMock.mockReset()
  })

  it("does not flash prerequisite prompts while initial executor data is loading", async () => {
    const executorsRequest = deferred<{ items: ExecutorListItem[]; total: number }>()
    const runtimeConnectionsRequest = deferred<{ items: RuntimeConnection[]; total: number }>()
    const trackersRequest = deferred<{ items: TrackerStatus[]; total: number }>()
    const settingsRequest = deferred<[]>()
    getExecutorsMock.mockReturnValue(executorsRequest.promise)
    getRuntimeConnectionsMock.mockReturnValue(runtimeConnectionsRequest.promise)
    getTrackersMock.mockReturnValue(trackersRequest.promise)
    getSettingsMock.mockReturnValue(settingsRequest.promise)

    renderExecutorsPage()

    expect(screen.getByTestId("executor-list")).toHaveAttribute("data-loading", "true")
    expect(screen.queryByText("executors.prerequisites.both.title")).not.toBeInTheDocument()
    expect(screen.queryByText("executors.prerequisites.runtime.title")).not.toBeInTheDocument()
    expect(screen.queryByText("executors.prerequisites.tracker.title")).not.toBeInTheDocument()

    await act(async () => {
      executorsRequest.resolve({ items: [], total: 0 })
      await executorsRequest.promise
    })

    expect(screen.getByTestId("executor-list")).toHaveAttribute("data-loading", "false")
    expect(screen.queryByText("executors.prerequisites.both.title")).not.toBeInTheDocument()

    await act(async () => {
      runtimeConnectionsRequest.resolve({ items: [], total: 0 })
      trackersRequest.resolve({ items: [], total: 0 })
      settingsRequest.resolve([])
      await Promise.all([runtimeConnectionsRequest.promise, trackersRequest.promise, settingsRequest.promise])
    })

    expect(screen.getByText("executors.prerequisites.both.title")).toBeInTheDocument()
  })

  it("submits manual runs without switching the executor list into loading", async () => {
    const refreshRequest = deferred<{ items: ExecutorListItem[]; total: number }>()
    getExecutorsMock
      .mockResolvedValueOnce({ items: [createExecutor()], total: 1 })
      .mockReturnValue(refreshRequest.promise)
    getRuntimeConnectionsMock.mockResolvedValue({ items: [createRuntimeConnection()], total: 1 })
    getTrackersMock.mockResolvedValue({ items: [createTracker()], total: 1 })
    getSettingsMock.mockResolvedValue([])
    runExecutorMock.mockResolvedValue({ status: "queued", run_id: 101 })

    renderExecutorsPage()

    await waitFor(() => {
      expect(screen.getByTestId("executor-list")).toHaveAttribute("data-loading", "false")
    })

    fireEvent.click(screen.getByRole("button", { name: "run executor 1" }))

    expect(runExecutorMock).toHaveBeenCalledWith(1)
    await act(async () => {
      await Promise.resolve()
    })

    expect(toastSuccessMock).toHaveBeenCalledWith("executors.toasts.runQueued")
    expect(screen.getByTestId("executor-list")).toHaveAttribute("data-loading", "false")
    expect(getExecutorsMock).toHaveBeenCalledTimes(2)

    await act(async () => {
      refreshRequest.resolve({ items: [createExecutor()], total: 1 })
      await refreshRequest.promise
    })

    expect(screen.getByTestId("executor-list")).toHaveAttribute("data-loading", "false")
  })

  it("does not enqueue disabled executors when manually run", async () => {
    getExecutorsMock.mockResolvedValue({ items: [createExecutor({ enabled: false })], total: 1 })
    getRuntimeConnectionsMock.mockResolvedValue({ items: [createRuntimeConnection()], total: 1 })
    getTrackersMock.mockResolvedValue({ items: [createTracker()], total: 1 })
    getSettingsMock.mockResolvedValue([])

    renderExecutorsPage()

    await waitFor(() => {
      expect(screen.getByTestId("executor-list")).toHaveAttribute("data-loading", "false")
    })

    fireEvent.click(screen.getByRole("button", { name: "run executor 1" }))

    expect(runExecutorMock).not.toHaveBeenCalled()
    expect(toastErrorMock).toHaveBeenCalledWith("executors.toasts.runDisabled")
    expect(toastSuccessMock).not.toHaveBeenCalledWith("executors.toasts.runQueued")
  })
})
