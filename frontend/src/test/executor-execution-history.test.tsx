import { fireEvent, render, screen, waitFor, within } from "@testing-library/react"
import { beforeEach, describe, expect, it, vi } from "vitest"

import type { ExecutorListItem, ExecutorRunHistory } from "@/api/types"

vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string) => key,
  }),
}))

const { clearExecutorHistoryMock, getExecutorHistoryMock, toastErrorMock, toastSuccessMock } = vi.hoisted(() => ({
  clearExecutorHistoryMock: vi.fn(),
  getExecutorHistoryMock: vi.fn(),
  toastErrorMock: vi.fn(),
  toastSuccessMock: vi.fn(),
}))

vi.mock("@/api/client", () => ({
  api: {
    clearExecutorHistory: clearExecutorHistoryMock,
    getExecutorHistory: getExecutorHistoryMock,
  },
}))

vi.mock("sonner", () => ({
  toast: {
    error: toastErrorMock,
    success: toastSuccessMock,
  },
}))

vi.mock("@/hooks/use-date-formatter", () => ({
  useDateFormatter: () => (value: string) => value,
}))

import { ExecutorExecutionHistoryPanel } from "@/components/executors/ExecutorExecutionHistoryPanel"

function createExecutor(overrides: Partial<ExecutorListItem> = {}): ExecutorListItem {
  return {
    id: overrides.id ?? 1,
    name: overrides.name ?? "sample-base-executor",
    runtime_type: overrides.runtime_type ?? "docker",
    runtime_connection_id: overrides.runtime_connection_id ?? 1,
    tracker_name: overrides.tracker_name ?? "sample-base-tracker",
    tracker_source_id: overrides.tracker_source_id ?? 1,
    channel_name: overrides.channel_name ?? "stable",
    enabled: overrides.enabled ?? true,
    update_mode: overrides.update_mode ?? "manual",
    image_selection_mode: overrides.image_selection_mode ?? "replace_tag_on_current_image",
    target_ref: overrides.target_ref ?? {
      container_name: "sample_base",
      container_id: "abc123",
    },
    maintenance_window: overrides.maintenance_window,
    description: overrides.description,
    runtime_connection_name: overrides.runtime_connection_name,
    status: overrides.status,
  }
}

function createHistoryItem(overrides: Partial<ExecutorRunHistory> = {}): ExecutorRunHistory {
  return {
    id: overrides.id ?? 10,
    executor_id: overrides.executor_id ?? 1,
    started_at: overrides.started_at ?? "2026-04-25T10:00:00Z",
    finished_at: overrides.finished_at ?? "2026-04-25T10:01:00Z",
    status: overrides.status ?? "success",
    from_version: "from_version" in overrides ? overrides.from_version : "docker.io/library/sample-base:24.04",
    to_version: "to_version" in overrides ? overrides.to_version : "docker.io/library/sample-base:24.10",
    message: overrides.message ?? "updated",
    diagnostics: overrides.diagnostics ?? null,
    created_at: overrides.created_at,
  }
}

describe("ExecutorExecutionHistoryPanel image rendering", () => {
  beforeEach(() => {
    clearExecutorHistoryMock.mockReset()
    getExecutorHistoryMock.mockReset()
    toastErrorMock.mockReset()
    toastSuccessMock.mockReset()
  })

  it("keeps images alongside waiting and per-service readiness", async () => {
    getExecutorHistoryMock.mockResolvedValue({ items: [createHistoryItem({
      status: "health_checking", from_version: "registry.example.test/team/service-a:1.1.0", to_version: "registry.example.test/team/service-a:1.2.0",
      diagnostics: { kind: "docker", summary: {updated_count: 1, skipped_count: 0, failed_count: 0, group_message: null}, services: [], health_check: {outcome: "pending", services: [{service: "service-a", status: "pending", method: "native", message: "starting"}]} },
    })], total: 1 })
    render(<ExecutorExecutionHistoryPanel executor={createExecutor()} refreshKey={0} />)
    expect(await screen.findByText("readiness.waiting")).toBeVisible()
    expect(screen.getByTestId("executor-history-image-change-list")).toHaveTextContent("registry.example.test/team/service-a:1.2.0")
    expect(screen.getByRole("status")).toHaveTextContent("service-a: readiness.outcome.pending · native — starting")
  })
  it("renders backend from/to image refs verbatim under image columns", async () => {
    getExecutorHistoryMock.mockResolvedValue({
      items: [
        createHistoryItem({
          from_version: "docker.io/library/sample-base:24.04",
          to_version: "docker.io/library/sample-base:24.10",
        }),
      ],
      total: 1,
    })

    render(<ExecutorExecutionHistoryPanel executor={createExecutor()} refreshKey={0} />)

    await waitFor(() => {
      expect(getExecutorHistoryMock).toHaveBeenCalledWith(1, {
        skip: 0,
        limit: 10,
        status: undefined,
        search: undefined,
      })
    })

    const historyItems = await screen.findAllByTestId("executor-history-item")
    expect(historyItems).toHaveLength(1)
    expect(within(historyItems[0]).getByTestId("executor-history-from-image")).toHaveTextContent("docker.io/library/sample-base:24.04")
    expect(within(historyItems[0]).getByTestId("executor-history-to-image")).toHaveTextContent("docker.io/library/sample-base:24.10")
    expect(screen.getByText("executors.history.table.fromImage")).toBeInTheDocument()
    expect(screen.getByText("executors.history.table.toImage")).toBeInTheDocument()
  })

  it("renders '-' when from_version or to_version is missing", async () => {
    getExecutorHistoryMock.mockResolvedValue({
      items: [
        createHistoryItem({
          from_version: null,
          to_version: undefined,
          message: "missing-images",
        }),
      ],
      total: 1,
    })

    render(<ExecutorExecutionHistoryPanel executor={createExecutor()} refreshKey={0} />)

    const messageCell = await screen.findByTestId("executor-history-message")
    expect(messageCell).toHaveTextContent("missing-images")

    expect(screen.getByTestId("executor-history-from-image")).toHaveTextContent("-")
    expect(screen.getByTestId("executor-history-to-image")).toHaveTextContent("-")
  })

  it.each(["docker_compose", "podman_compose", "ssh_compose", "portainer_stack", "kubernetes_workload"])("renders %s services as separate aligned image-change rows", async (kind) => {
    getExecutorHistoryMock.mockResolvedValue({
      items: [
        createHistoryItem({
          from_version: null,
          to_version: null,
          message: "docker-compose run finished",
          diagnostics: {
            kind,
            summary: {
              updated_count: 1,
              skipped_count: 1,
              failed_count: 0,
              group_message: "group update completed",
            },
            services: [
              {
                service: "api",
                status: "success",
                from_version: "ghcr.io/acme/api:1.0.0",
                to_version: "ghcr.io/acme/api:1.1.0",
                message: "updated",
              },
              {
                service: "worker",
                status: "skipped",
                from_version: "ghcr.io/acme/worker:2.0.0",
                to_version: "ghcr.io/acme/worker:2.0.0",
                message: "runtime already at target image",
              },
            ],
          },
        }),
      ],
      total: 1,
    })

    render(<ExecutorExecutionHistoryPanel executor={createExecutor()} refreshKey={0} />)

    const imageChangeList = await screen.findByTestId("executor-history-image-change-list")
    expect(within(imageChangeList).getByText("api")).toBeInTheDocument()
    expect(within(imageChangeList).getByText("worker")).toBeInTheDocument()
    expect(within(imageChangeList).getByText("ghcr.io/acme/api:1.0.0")).toBeInTheDocument()
    expect(within(imageChangeList).getByText("ghcr.io/acme/api:1.1.0")).toBeInTheDocument()
    expect(within(imageChangeList).getAllByText("ghcr.io/acme/worker:2.0.0")).toHaveLength(2)
    expect(within(imageChangeList).getAllByTestId("executor-history-from-image")).toHaveLength(2)
    expect(within(imageChangeList).getAllByTestId("executor-history-to-image")).toHaveLength(2)
    // Pagination and run identity stay at execution level; services are nested display rows.
    expect(screen.getAllByTestId("executor-history-item")).toHaveLength(1)
    expect(within(imageChangeList).queryByRole("table")).not.toBeInTheDocument()
    expect(within(imageChangeList).getByText("executors.history.table.fromImage")).toBeVisible()
    expect(within(imageChangeList).getByText("executors.history.table.toImage")).toBeVisible()
    const rows = within(imageChangeList).getAllByTestId("executor-history-service-change")
    expect(rows).toHaveLength(2)
    expect(rows[0]).toHaveAccessibleName("api")
    expect(within(rows[0]).getByTestId("executor-history-from-image")).toHaveTextContent("ghcr.io/acme/api:1.0.0")
    expect(within(rows[0]).getByTestId("executor-history-to-image")).toHaveTextContent("ghcr.io/acme/api:1.1.0")
    expect(rows[1]).toHaveAccessibleName("worker")
    expect(within(rows[1]).getAllByText("ghcr.io/acme/worker:2.0.0")).toHaveLength(2)
  })

  it("preserves the single image change when service diagnostics are empty", async () => {
    getExecutorHistoryMock.mockResolvedValue({items: [createHistoryItem({diagnostics: {
      kind: "docker_compose",
      summary: {updated_count: 0, skipped_count: 0, failed_count: 0, group_message: null},
      services: [],
    }})], total: 1})
    render(<ExecutorExecutionHistoryPanel executor={createExecutor()} refreshKey={0} />)
    expect(await screen.findByTestId("executor-history-from-image")).toHaveTextContent("docker.io/library/sample-base:24.04")
    expect(screen.getByTestId("executor-history-to-image")).toHaveTextContent("docker.io/library/sample-base:24.10")
  })

  it("renders Portainer stack executor context in the history header", async () => {
    getExecutorHistoryMock.mockResolvedValue({
      items: [createHistoryItem()],
      total: 1,
    })

    render(
      <ExecutorExecutionHistoryPanel
        executor={createExecutor({
          runtime_type: "portainer",
          target_ref: {
            mode: "portainer_stack",
            endpoint_id: 2,
            stack_id: 11,
            stack_name: "release-stack",
            stack_type: "standalone",
          },
        })}
        refreshKey={0}
      />,
    )

    const infoCard = await screen.findByTestId("executor-target-info-card")
    expect(within(infoCard).getByText("release-stack")).toBeInTheDocument()
    expect(within(infoCard).getByText("Endpoint #2")).toBeInTheDocument()
    expect(within(infoCard).getByText("Portainer stack")).toBeInTheDocument()
  })

  it("renders Helm release history as version changes", async () => {
    getExecutorHistoryMock.mockResolvedValue({
      items: [
        createHistoryItem({
          from_version: "0.7.0",
          to_version: "0.8.0",
          message: "helm upgraded",
        }),
      ],
      total: 1,
    })

    render(
      <ExecutorExecutionHistoryPanel
        executor={createExecutor({
          name: "certd-executor",
          runtime_type: "kubernetes",
          target_ref: {
            mode: "helm_release",
            namespace: "apps",
            release_name: "certd",
            chart_name: "certd-chart",
          },
        })}
        refreshKey={0}
      />,
    )

    const infoCard = await screen.findByTestId("executor-target-info-card")
    expect(within(infoCard).getByText("certd")).toBeInTheDocument()
    expect(within(infoCard).getByText("apps")).toBeInTheDocument()
    expect(within(infoCard).getByText("Helm release")).toBeInTheDocument()
    expect(screen.getByText("executors.review.versionChanges")).toBeInTheDocument()
    expect(screen.getByText("executors.history.table.fromVersion")).toBeInTheDocument()
    expect(screen.getByText("executors.history.table.toVersion")).toBeInTheDocument()
    expect(screen.getByTestId("executor-history-from-version")).toHaveTextContent("0.7.0")
    expect(screen.getByTestId("executor-history-to-version")).toHaveTextContent("0.8.0")
    expect(screen.queryByText("executors.review.imageChanges")).not.toBeInTheDocument()
  })

  it("renders unified 4-field metadata card for SSH Compose target", async () => {
    getExecutorHistoryMock.mockResolvedValue({
      items: [createHistoryItem()],
      total: 1,
    })

    render(
      <ExecutorExecutionHistoryPanel
        executor={createExecutor({
          name: "test",
          runtime_type: "ssh",
          runtime_connection_name: "example-ssh",
          enabled: true,
          update_mode: "manual",
          tracker_name: "affine",
          channel_name: "stable",
          target_ref: {
            mode: "ssh_compose",
            project: "sample_project",
            working_dir: "/srv/sample_project",
            tool: "podman-compose",
            write_strategy: "source",
            config_files: ["compose.yml"],
            env_files: [],
            profiles: [],
          },
        })}
        refreshKey={0}
      />,
    )

    const infoCard = await screen.findByTestId("executor-target-info-card")
    expect(within(infoCard).getByText("SSH Compose")).toBeInTheDocument()
    expect(within(infoCard).getByText("podman-compose")).toBeInTheDocument()
    expect(within(infoCard).getByText("SSH · example-ssh")).toBeInTheDocument()
    expect(within(infoCard).getByText("sample_project")).toBeInTheDocument()
    expect(within(infoCard).getByText("/srv/sample_project")).toBeInTheDocument()
    expect(within(infoCard).getByText("affine / channel.stable")).toBeInTheDocument()
  })

  it("parses semicolon-delimited multi-service strings into separate aligned service rows even without diagnostics.services", async () => {
    getExecutorHistoryMock.mockResolvedValue({
      items: [
        createHistoryItem({
          from_version: "service-a: registry.example.test/team/service-a:1.0.0; service-b: registry.example.test/team/service-b:2.0.0",
          to_version: "service-a: registry.example.test/team/service-a:1.1.0; service-b: registry.example.test/team/service-b:2.1.0",
          diagnostics: null,
        }),
      ],
      total: 1,
    })

    render(<ExecutorExecutionHistoryPanel executor={createExecutor()} refreshKey={0} />)

    const imageChangeList = await screen.findByTestId("executor-history-image-change-list")
    const rows = within(imageChangeList).getAllByTestId("executor-history-service-change")
    expect(rows).toHaveLength(2)

    expect(rows[0]).toHaveAccessibleName("service-a")
    expect(within(rows[0]).getByTestId("executor-history-from-image")).toHaveTextContent("registry.example.test/team/service-a:1.0.0")
    expect(within(rows[0]).getByTestId("executor-history-to-image")).toHaveTextContent("registry.example.test/team/service-a:1.1.0")

    expect(rows[1]).toHaveAccessibleName("service-b")
    expect(within(rows[1]).getByTestId("executor-history-from-image")).toHaveTextContent("registry.example.test/team/service-b:2.0.0")
    expect(within(rows[1]).getByTestId("executor-history-to-image")).toHaveTextContent("registry.example.test/team/service-b:2.1.0")
  })

  it("clears executor history after confirmation", async () => {
    getExecutorHistoryMock.mockResolvedValue({
      items: [createHistoryItem()],
      total: 1,
    })
    clearExecutorHistoryMock.mockResolvedValue({ message: "ok", deleted: 1 })

    render(<ExecutorExecutionHistoryPanel executor={createExecutor()} refreshKey={0} />)

    expect(await screen.findByTestId("executor-history-item")).toBeInTheDocument()

    fireEvent.click(screen.getByRole("button", { name: "executors.history.clearRecords" }))
    fireEvent.click(screen.getByRole("button", { name: "executors.history.clearConfirm" }))

    await waitFor(() => {
      expect(clearExecutorHistoryMock).toHaveBeenCalledWith(1)
    })

    expect(await screen.findByText("executors.history.noResults")).toBeInTheDocument()
    expect(screen.queryByTestId("executor-history-item")).not.toBeInTheDocument()
    expect(toastSuccessMock).toHaveBeenCalledWith("executors.history.clearSuccess")
  })

  it("keeps clear action available when current filters hide existing history", async () => {
    getExecutorHistoryMock
      .mockResolvedValueOnce({
        items: [createHistoryItem()],
        total: 1,
      })
      .mockResolvedValueOnce({
        items: [],
        total: 0,
      })
    clearExecutorHistoryMock.mockResolvedValue({ message: "ok", deleted: 1 })

    render(<ExecutorExecutionHistoryPanel executor={createExecutor()} refreshKey={0} />)

    expect(await screen.findByTestId("executor-history-item")).toBeInTheDocument()

    fireEvent.change(screen.getByPlaceholderText("executors.history.searchPlaceholder"), {
      target: { value: "no match" },
    })

    expect(await screen.findByText("executors.history.noResults")).toBeInTheDocument()

    const clearButton = screen.getByRole("button", { name: "executors.history.clearRecords" })
    expect(clearButton).toBeEnabled()

    fireEvent.click(clearButton)
    fireEvent.click(screen.getByRole("button", { name: "executors.history.clearConfirm" }))

    await waitFor(() => {
      expect(clearExecutorHistoryMock).toHaveBeenCalledWith(1)
    })
  })
})
