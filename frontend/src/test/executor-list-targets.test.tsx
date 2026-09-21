import { fireEvent, render, screen, within } from "@testing-library/react"
import { describe, expect, it, vi } from "vitest"

import type { ExecutorListItem } from "@/api/types"
import { ExecutorList } from "@/components/executors/ExecutorList"

vi.mock("react-i18next", () => ({
  initReactI18next: {
    type: "3rdParty",
    init: vi.fn(),
  },
  useTranslation: () => ({
    t: (key: string) => key,
    i18n: { language: "en" },
  }),
}))

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
    target_ref: overrides.target_ref ?? {
      mode: "container",
      container_name: "release-api",
      container_id: "abc123",
    },
    maintenance_window: overrides.maintenance_window,
    description: overrides.description ?? null,
    runtime_connection_name: overrides.runtime_connection_name ?? "docker-prod",
    status: overrides.status ?? null,
    ...overrides,
  }
}

describe("ExecutorList target rendering", () => {
  it("shows the tracker-style marker only on the selected row without clipping focus", () => {
    const props = {
      executors: [createExecutor(), createExecutor({ id: 2, name: "service-b" })],
      loading: false, onSelect: vi.fn(), onEdit: vi.fn(), onDelete: vi.fn(), onRun: vi.fn(), onViewExecutionHistory: vi.fn(),
    }
    const { rerender } = render(<ExecutorList {...props} selectedExecutorId={1} />)
    const rows = screen.getAllByTestId("executor-row")
    expect(rows[0]).toHaveAttribute("data-state", "selected")
    expect(rows[0]).toHaveAttribute("aria-selected", "true")
    expect(rows[1]).toHaveAttribute("aria-selected", "false")
    expect(rows[1]).not.toHaveAttribute("data-state")
    expect(within(rows[0]).getByTestId("executor-selection-marker")).toHaveAttribute("aria-hidden", "true")
    expect(within(rows[1]).queryByTestId("executor-selection-marker")).toBeNull()
    expect(rows[0].querySelector("td")).not.toHaveClass("overflow-hidden")
    for (const cell of rows[0].querySelectorAll("td")) {
      expect(cell).not.toHaveClass("bg-background")
    }
    rerender(<ExecutorList {...props} selectedExecutorId={2} />)
    expect(rows[0]).not.toHaveAttribute("data-state")
    expect(rows[1]).toHaveAttribute("data-state", "selected")
    expect(within(rows[0]).queryByTestId("executor-selection-marker")).toBeNull()
    expect(within(rows[1]).getByTestId("executor-selection-marker")).toBeInTheDocument()
    rerender(<ExecutorList {...props} selectedExecutorId={null} />)
    expect(rows.every(row => row.getAttribute("aria-selected") === "false")).toBe(true)
    expect(screen.queryByTestId("executor-selection-marker")).toBeNull()
    expect(props.onRun).not.toHaveBeenCalled()
  })

  it("opens history only from its button, not the row or name", () => {
    const onHistory = vi.fn()
    const onSelect = vi.fn()
    const onRun = vi.fn()
    render(<ExecutorList executors={[createExecutor()]} loading={false} onEdit={vi.fn()} onDelete={vi.fn()} onRun={onRun} onViewExecutionHistory={onHistory} onSelect={onSelect} selectedExecutorId={null} />)
    const row = screen.getByTestId("executor-row")
    fireEvent.click(row)
    fireEvent.click(within(row).getByText("release-api", { selector: "span" }))
    expect(onHistory).not.toHaveBeenCalled()
    expect(onSelect).toHaveBeenCalledTimes(2)
    fireEvent.keyDown(row, { key: "Enter" })
    fireEvent.keyDown(row, { key: " " })
    expect(onSelect).toHaveBeenCalledTimes(4)
    expect(onSelect).toHaveBeenLastCalledWith(1)
    onSelect.mockClear()
    expect(screen.queryByRole("button", {name: "release-api"})).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole("button", {name: "executors.actions.viewExecutionHistory"}))
    expect(onHistory).toHaveBeenCalledTimes(1)
    expect(onHistory).toHaveBeenLastCalledWith(1)
    expect(onRun).not.toHaveBeenCalled()
    const runButton = screen.getByRole("button", {name: "executors.actions.runNow"})
    fireEvent.keyDown(runButton, {key: "Enter"})
    fireEvent.click(runButton)
    expect(onRun).toHaveBeenCalledWith(1)
    expect(onSelect).not.toHaveBeenCalled()
  })
  it.each(["submitting", "disabled", "invalid"])("blocks run buttons when %s", reason => {
    const onRun = vi.fn()
    const executor = createExecutor({enabled: reason !== "disabled", invalid_config_error: reason === "invalid" ? "Invalid configuration" : null})
    render(<ExecutorList executors={[executor]} loading={false} onEdit={vi.fn()} onDelete={vi.fn()} onRun={onRun} onViewExecutionHistory={vi.fn()} onSelect={vi.fn()} selectedExecutorId={null} submittingExecutorIds={reason === "submitting" ? new Set([1]) : undefined} />)
    const run = screen.getByRole("button", {name: "executors.actions.runNow"})
    expect(run).toBeDisabled()
    fireEvent.click(run)
    expect(onRun).not.toHaveBeenCalled()
    expect(screen.getByRole("button", {name: "executors.actions.viewExecutionHistory"})).toBeEnabled()
  })

  it("renders supported container and Portainer stack targets without legacy fallback copy", () => {
    render(
      <ExecutorList
        executors={[
          createExecutor(),
          createExecutor({
            id: 2,
            name: "release-stack-executor",
            runtime_type: "portainer",
            runtime_connection_name: "portainer-prod",
            target_ref: {
              mode: "portainer_stack",
              endpoint_id: 2,
              stack_id: 11,
              stack_name: "release-stack",
              stack_type: "standalone",
              service_count: 3,
            },
          }),
        ]}
        loading={false}
        onEdit={vi.fn()}
        onDelete={vi.fn()}
        onRun={vi.fn()}
        onViewExecutionHistory={vi.fn()}
        onSelect={vi.fn()}
        selectedExecutorId={1}
      />,
    )

    const targetCells = screen.getAllByTestId("executor-target-cell")
    expect(within(targetCells[0]).getByText("release-api")).toBeInTheDocument()
    expect(within(targetCells[0]).getByText("executors.target.kind.container")).toBeInTheDocument()
    expect(within(targetCells[0]).getByTestId("executor-target-service-count")).toHaveAttribute("data-count", "1")
    expect(within(targetCells[1]).getByText("release-stack")).toBeInTheDocument()
    expect(within(targetCells[1]).getByText("Portainer stack")).toBeInTheDocument()
    expect(within(targetCells[1]).getByTestId("executor-target-service-count")).toHaveAttribute("data-count", "3")
    expect(screen.getAllByText("Portainer stack").length).toBeGreaterThan(0)
    expect(screen.queryByText("Unsupported target")).not.toBeInTheDocument()
  })

  it("shows Chart instead of image reference mode for Helm release executors", () => {
    render(
      <ExecutorList
        executors={[
          createExecutor({
            id: 3,
            name: "certd-executor",
            runtime_type: "kubernetes",
            runtime_connection_name: "k3s",
            tracker_name: "certd-chart",
            image_selection_mode: "replace_tag_on_current_image",
            image_reference_mode: "digest",
            target_ref: {
              mode: "helm_release",
              namespace: "apps",
              release_name: "certd",
              chart_name: "certd-chart",
            },
          }),
        ]}
        loading={false}
        onEdit={vi.fn()}
        onDelete={vi.fn()}
        onRun={vi.fn()}
        onViewExecutionHistory={vi.fn()}
        onSelect={vi.fn()}
        selectedExecutorId={null}
      />,
    )

    expect(screen.getByText("executors.referenceModes.chart")).toBeInTheDocument()
    expect(screen.queryByText("digest")).not.toBeInTheDocument()
    expect(screen.queryByText("DIGEST")).not.toBeInTheDocument()
  })
})
