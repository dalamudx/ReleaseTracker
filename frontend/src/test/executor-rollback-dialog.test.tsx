import { act, fireEvent, render, screen, waitFor } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

import type { ExecutorListItem, RollbackResponse, SnapshotListItem } from "@/api/types"

const {
  mutateAsyncMock,
  onOpenChangeMock,
  onSuccessMock,
  toastErrorMock,
  toastLoadingMock,
  toastSuccessMock,
  translateMock,
} = vi.hoisted(() => ({
  mutateAsyncMock: vi.fn(),
  onOpenChangeMock: vi.fn(),
  onSuccessMock: vi.fn(),
  toastErrorMock: vi.fn(),
  toastLoadingMock: vi.fn(() => "rollback-toast"),
  toastSuccessMock: vi.fn(),
  translateMock: (key: string, options?: { defaultValue?: string }) => options?.defaultValue ?? key,
}))

vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: translateMock,
  }),
}))

vi.mock("sonner", () => ({
  toast: {
    error: toastErrorMock,
    loading: toastLoadingMock,
    success: toastSuccessMock,
  },
}))

vi.mock("@/hooks/queries", () => ({
  useRollbackExecutor: () => ({
    mutateAsync: mutateAsyncMock,
  }),
}))

import { ExecutorRollbackDialog } from "@/components/executors/ExecutorRollbackDialog"

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
    id: overrides.id ?? 7,
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
    status: overrides.status ?? {
      executor_id: overrides.id ?? 7,
      last_run_at: null,
      last_result: null,
      last_error: null,
      last_version: "docker.io/library/sample:2.0.0",
    },
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

function createRollbackResponse(overrides: Partial<RollbackResponse> = {}): RollbackResponse {
  return {
    run: overrides.run ?? {
      id: 101,
      executor_id: 7,
      started_at: "2026-04-25T10:01:00Z",
      status: "success",
      from_version: "docker.io/library/sample:2.0.0",
      to_version: "docker.io/library/sample:1.0.0",
      message: "manual rollback to snapshot 42",
      diagnostics: null,
    },
    recovery_outcome: overrides.recovery_outcome ?? "succeeded",
    recovery_error: overrides.recovery_error,
  }
}

function renderDialog() {
  return render(
    <ExecutorRollbackDialog
      executor={createExecutor()}
      snapshot={createSnapshot()}
      open
      onOpenChange={onOpenChangeMock}
      onSuccess={onSuccessMock}
    />,
  )
}

describe("ExecutorRollbackDialog", () => {
  beforeEach(() => {
    mutateAsyncMock.mockReset()
    onOpenChangeMock.mockReset()
    onSuccessMock.mockReset()
    toastErrorMock.mockReset()
    toastLoadingMock.mockClear()
    toastSuccessMock.mockReset()
    vi.spyOn(console, "error").mockImplementation(() => undefined)
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it("shows rollback request feedback immediately before the request resolves", async () => {
    const rollbackRequest = deferred<RollbackResponse>()
    mutateAsyncMock.mockReturnValue(rollbackRequest.promise)

    renderDialog()

    fireEvent.change(screen.getByLabelText("executors.rollback.dialog.confirmPrompt"), {
      target: { value: "sample-executor" },
    })
    fireEvent.click(screen.getByRole("button", { name: "executors.rollback.dialog.confirmLabel" }))

    expect(mutateAsyncMock).toHaveBeenCalledWith({ executorId: 7, snapshotId: 42 })
    expect(toastLoadingMock).toHaveBeenCalledWith("executors.rollback.toasts.submitting")
    expect(toastSuccessMock).not.toHaveBeenCalled()
    expect(toastErrorMock).not.toHaveBeenCalled()

    await act(async () => {
      rollbackRequest.resolve(createRollbackResponse())
      await rollbackRequest.promise
    })

    expect(toastSuccessMock).toHaveBeenCalledWith("executors.rollback.toasts.success", { id: "rollback-toast" })
    expect(onSuccessMock).toHaveBeenCalledTimes(1)
    await waitFor(() => {
      expect(onOpenChangeMock).toHaveBeenCalledWith(false)
    })
  })

  it("replaces the pending toast when rollback enqueue fails", async () => {
    mutateAsyncMock.mockRejectedValue({ response: { status: 409 } })

    renderDialog()

    fireEvent.change(screen.getByLabelText("executors.rollback.dialog.confirmPrompt"), {
      target: { value: "sample-executor" },
    })
    fireEvent.click(screen.getByRole("button", { name: "executors.rollback.dialog.confirmLabel" }))

    expect(toastLoadingMock).toHaveBeenCalledWith("executors.rollback.toasts.submitting")

    await waitFor(() => {
      expect(toastErrorMock).toHaveBeenCalledWith("executors.rollback.toasts.conflict", {
        id: "rollback-toast",
        description: undefined,
      })
    })
    expect(toastSuccessMock).not.toHaveBeenCalled()
  })

  it("keeps API failure details visible on general rollback failures", async () => {
    mutateAsyncMock.mockRejectedValue({
      response: {
        status: 500,
        data: { detail: "runtime adapter failed" },
      },
    })

    renderDialog()

    fireEvent.change(screen.getByLabelText("executors.rollback.dialog.confirmPrompt"), {
      target: { value: "sample-executor" },
    })
    fireEvent.click(screen.getByRole("button", { name: "executors.rollback.dialog.confirmLabel" }))

    await waitFor(() => {
      expect(toastErrorMock).toHaveBeenCalledWith("runtime adapter failed", { id: "rollback-toast" })
    })
  })
})
