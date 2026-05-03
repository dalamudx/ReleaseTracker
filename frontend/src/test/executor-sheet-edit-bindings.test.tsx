import { fireEvent, render, screen, waitFor } from "@testing-library/react"
import { beforeEach, describe, expect, it, vi } from "vitest"

import type { ExecutorConfig, RuntimeConnection, TrackerStatus } from "@/api/types"
import { ExecutorSheet } from "@/components/executors/ExecutorSheet"

globalThis.ResizeObserver = class ResizeObserver {
  observe() {}
  unobserve() {}
  disconnect() {}
}

const {
  getExecutorConfigMock,
  tMock,
  updateExecutorMock,
} = vi.hoisted(() => ({
  getExecutorConfigMock: vi.fn(),
  tMock: (key: string, options?: Record<string, unknown>) => {
    if (key === "executors.discovery.runtimeSummary") {
      return `${String(options?.name)} (${String(options?.type)})`
    }
    if (key === "executors.target.serviceCountSummary") {
      return `${String(options?.count)} services`
    }
    return key
  },
  updateExecutorMock: vi.fn(),
}))

vi.mock("@/api/client", () => ({
  api: {
    getExecutorConfig: getExecutorConfigMock,
    updateExecutor: updateExecutorMock,
  },
}))

vi.mock("react-i18next", async (importOriginal) => {
  const actual = await importOriginal<typeof import("react-i18next")>()

  return {
    ...actual,
    useTranslation: () => ({
      t: tMock,
      i18n: { language: "en" },
    }),
  }
})

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
    sources: overrides.sources ?? [
      {
        id: 9,
        channel_key: "image",
        channel_type: "container",
        enabled: true,
        channel_config: { image: "ghcr.io/acme/api" },
        release_channels: [
          { release_channel_key: "image-stable", name: "stable", type: "release", enabled: true },
        ],
        channel_rank: 0,
        source_key: "image",
        source_type: "container",
        source_config: { image: "ghcr.io/acme/api" },
        source_rank: 0,
      },
    ],
    interval: overrides.interval ?? 360,
    version_sort_mode: overrides.version_sort_mode ?? "published_at",
    fetch_limit: overrides.fetch_limit ?? 10,
    fetch_timeout: overrides.fetch_timeout ?? 15,
    fallback_tags: overrides.fallback_tags ?? false,
    github_fetch_mode: overrides.github_fetch_mode ?? "rest_first",
    channels: overrides.channels ?? [],
    status: overrides.status ?? {
      last_check: null,
      last_version: "1.2.3",
      error: null,
      source_count: 1,
      enabled_source_count: 1,
      source_types: ["container"],
    },
  }
}

function createExecutorConfig(overrides: Partial<ExecutorConfig> = {}): ExecutorConfig {
  return {
    id: overrides.id ?? 5,
    name: overrides.name ?? "release-api-executor",
    runtime_type: overrides.runtime_type ?? "docker",
    runtime_connection_id: overrides.runtime_connection_id ?? 1,
    tracker_name: overrides.tracker_name ?? "release-tracker",
    tracker_source_id: overrides.tracker_source_id ?? 9,
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
    service_bindings: overrides.service_bindings ?? [],
    maintenance_window: overrides.maintenance_window ?? null,
    description: overrides.description ?? null,
  }
}

describe("ExecutorSheet edit bindings", () => {
  beforeEach(() => {
    getExecutorConfigMock.mockReset()
    updateExecutorMock.mockReset()
    updateExecutorMock.mockResolvedValue(undefined)
  })

  it("shows existing Kubernetes workload target and service binding when editing", async () => {
    getExecutorConfigMock.mockResolvedValue(createExecutorConfig({
      id: 12,
      name: "aether",
      runtime_type: "kubernetes",
      runtime_connection_id: 3,
      tracker_name: "aether",
      tracker_source_id: 40,
      target_ref: {
        mode: "kubernetes_workload",
        namespace: "infra",
        kind: "Deployment",
        name: "aether",
        services: [{ service: "aether-lkdsjfh", image: "reg.aoodc.com/fawney19/aether:0.7.0-rc21" }],
        service_count: 1,
      },
      service_bindings: [{ service: "aether-lkdsjfh", tracker_source_id: 40, channel_name: "stable" }],
    }))

    render(
      <ExecutorSheet
        open
        onOpenChange={vi.fn()}
        executorId={12}
        runtimeConnections={[createRuntimeConnection({ id: 3, name: "k3s", type: "kubernetes", config: { namespaces: ["apps"] } })]}
        trackers={[createTracker({
          name: "aether",
          sources: [
            {
              id: 40,
              channel_key: "image",
              channel_type: "container",
              enabled: true,
              channel_config: { image: "reg.aoodc.com/fawney19/aether" },
              release_channels: [
                { release_channel_key: "image-stable", name: "stable", type: "release", enabled: true },
              ],
              channel_rank: 0,
              source_key: "image",
              source_type: "container",
              source_config: { image: "reg.aoodc.com/fawney19/aether" },
              source_rank: 0,
            },
          ],
        })]}
        systemTimezone="UTC"
        onSuccess={vi.fn()}
      />,
    )

    expect(await screen.findByDisplayValue("aether")).toBeInTheDocument()
    expect(screen.queryByText("executors.binding.currentBinding")).not.toBeInTheDocument()
    expect(screen.queryByText("aether-lkdsjfh / aether / stable")).not.toBeInTheDocument()
    expect(screen.queryByText("executors.discovery.noTargetSelected")).not.toBeInTheDocument()
  })

  it("preserves the existing binding when an executor is opened for editing", async () => {
    getExecutorConfigMock.mockResolvedValue(createExecutorConfig())

    render(
      <ExecutorSheet
        open
        onOpenChange={vi.fn()}
        executorId={5}
        runtimeConnections={[createRuntimeConnection()]}
        trackers={[createTracker()]}
        systemTimezone="UTC"
        onSuccess={vi.fn()}
      />,
    )

    await waitFor(() => expect(screen.queryByText("common.loading")).not.toBeInTheDocument())

    fireEvent.click(screen.getByRole("button", { name: "executors.actions.continue" }))
    await waitFor(() => expect(screen.getByText("executors.sections.binding")).toBeInTheDocument())

    fireEvent.click(screen.getByRole("button", { name: "executors.actions.continue" }))
    await waitFor(() => expect(screen.getByText("executors.sections.policy")).toBeInTheDocument())

    fireEvent.click(screen.getByRole("button", { name: "executors.actions.continue" }))
    await waitFor(() => expect(screen.getByText("executors.review.tracker")).toBeInTheDocument())

    fireEvent.click(screen.getByRole("button", { name: "common.save" }))

    await waitFor(() => expect(updateExecutorMock).toHaveBeenCalledOnce())
    expect(updateExecutorMock).toHaveBeenCalledWith(5, expect.objectContaining({
      tracker_name: "release-tracker",
      tracker_source_id: 9,
      channel_name: "stable",
    }))
  })
})
