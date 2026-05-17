import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react"
import type { ReactElement } from "react"
import { beforeEach, describe, expect, it, vi } from "vitest"

import type { ExecutorListItem, PaginatedSnapshots, SnapshotListItem } from "@/api/types"

const {
  deleteExecutorSnapshotMock,
  getExecutorSnapshotsMock,
  lockExecutorSnapshotMock,
  unlockExecutorSnapshotMock,
  toastErrorMock,
  toastSuccessMock,
  translateMock,
} = vi.hoisted(() => ({
  deleteExecutorSnapshotMock: vi.fn(),
  getExecutorSnapshotsMock: vi.fn(),
  lockExecutorSnapshotMock: vi.fn(),
  unlockExecutorSnapshotMock: vi.fn(),
  toastErrorMock: vi.fn(),
  toastSuccessMock: vi.fn(),
  translateMock: (key: string) => key,
}))

vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: translateMock,
    i18n: { language: "en" },
  }),
}))

vi.mock("@/api/client", () => ({
  api: {
    deleteExecutorSnapshot: deleteExecutorSnapshotMock,
    getExecutorSnapshots: getExecutorSnapshotsMock,
    lockExecutorSnapshot: lockExecutorSnapshotMock,
    unlockExecutorSnapshot: unlockExecutorSnapshotMock,
  },
}))

vi.mock("sonner", () => ({
  toast: {
    error: toastErrorMock,
    success: toastSuccessMock,
  },
}))

vi.mock("@/hooks/use-page-size", () => ({
  usePageSize: () => [10, vi.fn()] as const,
}))

vi.mock("@/components/common/DataPagination", () => ({
  DataPagination: () => <div data-testid="data-pagination" />,
}))

vi.mock("@/components/executors/ExecutorRollbackDialog", () => ({
  ExecutorRollbackDialog: () => <div data-testid="executor-rollback-dialog" />,
}))

import { ExecutorSnapshotsPanel } from "@/components/executors/ExecutorSnapshotsPanel"

function createExecutor(overrides: Partial<ExecutorListItem> = {}): ExecutorListItem {
  return {
    id: overrides.id ?? 1,
    name: overrides.name ?? "sample-executor",
    runtime_type: overrides.runtime_type ?? "docker",
    runtime_connection_id: overrides.runtime_connection_id ?? 1,
    tracker_name: overrides.tracker_name ?? "sample-tracker",
    tracker_source_id: overrides.tracker_source_id ?? 1,
    channel_name: overrides.channel_name ?? "stable",
    enabled: overrides.enabled ?? true,
    update_mode: overrides.update_mode ?? "manual",
    image_selection_mode: overrides.image_selection_mode ?? "replace_tag_on_current_image",
    image_reference_mode: overrides.image_reference_mode ?? "digest",
    target_ref: overrides.target_ref ?? {
      mode: "container",
      container_id: "abc123",
      container_name: "sample",
    },
    maintenance_window: overrides.maintenance_window,
    description: overrides.description,
    runtime_connection_name: overrides.runtime_connection_name,
    status: overrides.status,
  }
}

function createSnapshot(overrides: Partial<SnapshotListItem> = {}): SnapshotListItem {
  return {
    id: overrides.id ?? 42,
    created_at: overrides.created_at ?? "2026-04-25T10:00:00Z",
    trigger: overrides.trigger ?? "pre_update",
    image_at_capture: "image_at_capture" in overrides ? overrides.image_at_capture! : "docker.io/library/sample:1.0.0",
    executor_run_id: "executor_run_id" in overrides ? overrides.executor_run_id! : 100,
    unredacted_persisted: overrides.unredacted_persisted ?? false,
    locked: overrides.locked ?? false,
  }
}

function paginatedSnapshots(items: SnapshotListItem[]): PaginatedSnapshots {
  return {
    items,
    total: items.length,
    page: 1,
    page_size: 10,
  }
}

function renderWithQueryClient(ui: ReactElement) {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  })

  return render(
    <QueryClientProvider client={queryClient}>
      {ui}
    </QueryClientProvider>,
  )
}

describe("ExecutorSnapshotsPanel", () => {
  beforeEach(() => {
    deleteExecutorSnapshotMock.mockReset()
    getExecutorSnapshotsMock.mockReset()
    lockExecutorSnapshotMock.mockReset()
    unlockExecutorSnapshotMock.mockReset()
    toastErrorMock.mockReset()
    toastSuccessMock.mockReset()
  })

  it("renders delete action immediately before rollback for each snapshot", async () => {
    getExecutorSnapshotsMock.mockResolvedValue(paginatedSnapshots([createSnapshot()]))

    renderWithQueryClient(<ExecutorSnapshotsPanel executor={createExecutor()} refreshKey={0} />)

    const snapshotItem = await screen.findByTestId("executor-snapshot-item")
    const buttons = within(snapshotItem).getAllByRole("button")

    // lock button + delete button + rollback button
    expect(buttons).toHaveLength(3)
    expect(buttons[1]).toHaveAccessibleName("executors.snapshots.actions.delete")
    expect(buttons[2]).toHaveAccessibleName("executors.snapshots.actions.rollback")
  })

  it("deletes a snapshot after confirmation and reloads the list", async () => {
    getExecutorSnapshotsMock.mockImplementation(() =>
      Promise.resolve(
        deleteExecutorSnapshotMock.mock.calls.length > 0
          ? paginatedSnapshots([])
          : paginatedSnapshots([createSnapshot({ id: 42 })]),
      ),
    )
    deleteExecutorSnapshotMock.mockResolvedValue({ message: "ok", deleted: 1 })

    renderWithQueryClient(<ExecutorSnapshotsPanel executor={createExecutor({ id: 7 })} refreshKey={0} />)

    const snapshotItem = await screen.findByTestId("executor-snapshot-item")
    fireEvent.click(within(snapshotItem).getByRole("button", { name: "executors.snapshots.actions.delete" }))
    fireEvent.click(screen.getByRole("button", { name: "executors.snapshots.deleteDialog.confirm" }))

    await waitFor(() => {
      expect(deleteExecutorSnapshotMock).toHaveBeenCalledWith(7, 42)
    })

    expect(toastSuccessMock).toHaveBeenCalledWith("executors.snapshots.toasts.deleteSuccess")
    expect(await screen.findByText("executors.snapshots.noSnapshots")).toBeInTheDocument()
    expect(getExecutorSnapshotsMock).toHaveBeenCalledTimes(2)
  })

  it("shows an error toast when snapshot deletion fails", async () => {
    getExecutorSnapshotsMock.mockResolvedValue(paginatedSnapshots([createSnapshot({ id: 42 })]))
    deleteExecutorSnapshotMock.mockRejectedValue(new Error("delete failed"))

    renderWithQueryClient(<ExecutorSnapshotsPanel executor={createExecutor({ id: 7 })} refreshKey={0} />)

    const snapshotItem = await screen.findByTestId("executor-snapshot-item")
    fireEvent.click(within(snapshotItem).getByRole("button", { name: "executors.snapshots.actions.delete" }))
    fireEvent.click(screen.getByRole("button", { name: "executors.snapshots.deleteDialog.confirm" }))

    await waitFor(() => {
      expect(deleteExecutorSnapshotMock).toHaveBeenCalledWith(7, 42)
    })

    expect(toastErrorMock).toHaveBeenCalledWith("executors.snapshots.toasts.deleteFailed")
  })

  it("disables the delete button for locked snapshots", async () => {
    getExecutorSnapshotsMock.mockResolvedValue(
      paginatedSnapshots([createSnapshot({ id: 42, locked: true })]),
    )

    renderWithQueryClient(<ExecutorSnapshotsPanel executor={createExecutor({ id: 1 })} refreshKey={0} />)

    const snapshotItem = await screen.findByTestId("executor-snapshot-item")
    const deleteButton = within(snapshotItem).getByRole("button", {
      name: "executors.snapshots.actions.delete",
    })
    expect(deleteButton).toBeDisabled()
  })

  it("shows a locked badge for locked snapshots", async () => {
    getExecutorSnapshotsMock.mockResolvedValue(
      paginatedSnapshots([createSnapshot({ id: 42, locked: true })]),
    )

    renderWithQueryClient(<ExecutorSnapshotsPanel executor={createExecutor({ id: 1 })} refreshKey={0} />)

    await screen.findByTestId("executor-snapshot-locked-badge")
  })

  it("does not show a locked badge for unlocked snapshots", async () => {
    getExecutorSnapshotsMock.mockResolvedValue(
      paginatedSnapshots([createSnapshot({ id: 42, locked: false })]),
    )

    renderWithQueryClient(<ExecutorSnapshotsPanel executor={createExecutor({ id: 1 })} refreshKey={0} />)

    await screen.findByTestId("executor-snapshot-item")
    expect(screen.queryByTestId("executor-snapshot-locked-badge")).toBeNull()
  })

  it("calls lockExecutorSnapshot and shows success toast when locking an unlocked snapshot", async () => {
    getExecutorSnapshotsMock.mockResolvedValue(
      paginatedSnapshots([createSnapshot({ id: 42, locked: false })]),
    )
    lockExecutorSnapshotMock.mockResolvedValue({ message: "Snapshot locked", locked: true })

    renderWithQueryClient(<ExecutorSnapshotsPanel executor={createExecutor({ id: 7 })} refreshKey={0} />)

    const snapshotItem = await screen.findByTestId("executor-snapshot-item")
    fireEvent.click(within(snapshotItem).getByTestId("executor-snapshot-lock"))

    await waitFor(() => {
      expect(lockExecutorSnapshotMock).toHaveBeenCalledWith(7, 42)
    })
    expect(toastSuccessMock).toHaveBeenCalledWith("executors.snapshots.toasts.lockSuccess")
  })

  it("calls unlockExecutorSnapshot and shows success toast when unlocking a locked snapshot", async () => {
    getExecutorSnapshotsMock.mockResolvedValue(
      paginatedSnapshots([createSnapshot({ id: 42, locked: true })]),
    )
    unlockExecutorSnapshotMock.mockResolvedValue({ message: "Snapshot unlocked", locked: false })

    renderWithQueryClient(<ExecutorSnapshotsPanel executor={createExecutor({ id: 7 })} refreshKey={0} />)

    const snapshotItem = await screen.findByTestId("executor-snapshot-item")
    fireEvent.click(within(snapshotItem).getByTestId("executor-snapshot-unlock"))

    await waitFor(() => {
      expect(unlockExecutorSnapshotMock).toHaveBeenCalledWith(7, 42)
    })
    expect(toastSuccessMock).toHaveBeenCalledWith("executors.snapshots.toasts.unlockSuccess")
  })

  it("shows an error toast when locking fails", async () => {
    getExecutorSnapshotsMock.mockResolvedValue(
      paginatedSnapshots([createSnapshot({ id: 42, locked: false })]),
    )
    lockExecutorSnapshotMock.mockRejectedValue(new Error("lock failed"))

    renderWithQueryClient(<ExecutorSnapshotsPanel executor={createExecutor({ id: 7 })} refreshKey={0} />)

    const snapshotItem = await screen.findByTestId("executor-snapshot-item")
    fireEvent.click(within(snapshotItem).getByTestId("executor-snapshot-lock"))

    await waitFor(() => {
      expect(lockExecutorSnapshotMock).toHaveBeenCalledWith(7, 42)
    })
    expect(toastErrorMock).toHaveBeenCalledWith("executors.snapshots.toasts.lockFailed")
  })
})
