import { render, screen } from "@testing-library/react"
import { describe, expect, it, vi } from "vitest"

import type { ExecutorListItem } from "@/api/types"
import { ExecutorList } from "@/components/executors/ExecutorList"

vi.mock("react-i18next", () => ({
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
  }
}

describe("ExecutorList target rendering", () => {
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
            },
          }),
        ]}
        loading={false}
        onEdit={vi.fn()}
        onDelete={vi.fn()}
        onRun={vi.fn()}
        onViewExecutionHistory={vi.fn()}
        selectedExecutorId={1}
      />,
    )

    expect(screen.getByText("release-api / abc123")).toBeInTheDocument()
    expect(screen.getByText("release-stack #11")).toBeInTheDocument()
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
        selectedExecutorId={null}
      />,
    )

    expect(screen.getByText("executors.referenceModes.chart")).toBeInTheDocument()
    expect(screen.queryByText("digest")).not.toBeInTheDocument()
    expect(screen.queryByText("DIGEST")).not.toBeInTheDocument()
  })
})
