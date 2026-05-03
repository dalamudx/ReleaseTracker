import { fireEvent, render, screen } from "@testing-library/react"
import { useForm } from "react-hook-form"
import { describe, expect, it, vi } from "vitest"

import type { RuntimeConnection, RuntimeTargetDiscoveryItem, TrackerStatus } from "@/api/types"
import { ExecutorSheetBindingSection, ExecutorSheetReviewSection, ExecutorSheetTargetSection } from "@/components/executors/ExecutorSheetSections"
import type { ExecutorFormValues } from "@/components/executors/executorSheetHelpers"
import { Form } from "@/components/ui/form"

vi.mock("react-i18next", async (importOriginal) => {
  const actual = await importOriginal<typeof import("react-i18next")>()

  return {
    ...actual,
    useTranslation: () => ({
      t: (key: string, options?: Record<string, unknown>) => {
        if (key === "executors.review.maintenanceSummary") {
          return `Maintenance window: ${String(options?.timezone)}, ${String(options?.start)}–${String(options?.end)}.`
        }
        return key
      },
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

function createFormValues(overrides: Partial<ExecutorFormValues> = {}): ExecutorFormValues {
  return {
    name: overrides.name ?? "portainer-executor",
    runtime_type: overrides.runtime_type ?? "docker",
    runtime_connection_id: overrides.runtime_connection_id ?? "1",
    tracker_name: overrides.tracker_name ?? "",
    tracker_source_id: overrides.tracker_source_id ?? "",
    channel_name: overrides.channel_name ?? "",
    enabled: overrides.enabled ?? true,
    update_mode: overrides.update_mode ?? "manual",
    image_selection_mode: overrides.image_selection_mode ?? "replace_tag_on_current_image",
    image_reference_mode: overrides.image_reference_mode ?? "digest",
    description: overrides.description ?? "",
    maintenance_timezone: overrides.maintenance_timezone ?? "UTC",
    maintenance_days: overrides.maintenance_days ?? [],
    maintenance_start_time: overrides.maintenance_start_time ?? "02:00",
    maintenance_end_time: overrides.maintenance_end_time ?? "05:00",
  }
}

function createTracker(): TrackerStatus {
  return {
    id: 1,
    name: "release-tracker",
    enabled: true,
    description: null,
    changelog_policy: undefined,
    primary_changelog_source_key: "image",
    sources: [
      {
        id: 9,
        channel_key: "image",
        channel_type: "container",
        enabled: true,
        channel_config: { image: "ghcr.io/acme/api" },
        release_channels: [{ release_channel_key: "stable", name: "stable", type: "release", enabled: true }],
        channel_rank: 0,
        source_key: "image",
        source_type: "container",
        source_config: { image: "ghcr.io/acme/api" },
        source_rank: 0,
      },
    ],
    interval: 360,
    version_sort_mode: "published_at",
    fetch_limit: 10,
    fetch_timeout: 15,
    fallback_tags: false,
    github_fetch_mode: "rest_first",
    channels: [],
    status: {
      last_check: null,
      last_version: "1.2.3",
      error: null,
      source_count: 1,
      enabled_source_count: 1,
      source_types: ["container"],
    },
  }
}

function renderTargetSection({
  runtimeType = "docker",
  runtimeConnection = createRuntimeConnection(),
  discoveredTargets,
  selectedTargetRef,
  configuredDiscoveryNamespaces,
  selectedDiscoveryNamespace,
}: {
  runtimeType?: ExecutorFormValues["runtime_type"]
  runtimeConnection?: RuntimeConnection
  discoveredTargets: RuntimeTargetDiscoveryItem[]
  selectedTargetRef: Record<string, unknown>
  configuredDiscoveryNamespaces?: string[]
  selectedDiscoveryNamespace?: string
}) {
  const onDiscoverTargets = vi.fn()
  const onSelectDiscoveryNamespace = vi.fn()

  function Wrapper() {
    const form = useForm<ExecutorFormValues>({
      defaultValues: createFormValues({ runtime_type: runtimeType }),
    })

    return (
      <Form {...form}>
        <ExecutorSheetTargetSection
          form={form}
          runtimeType={runtimeType}
          selectedRuntimeConnection={runtimeConnection}
          enabledRuntimeConnections={[runtimeConnection]}
          discovering={false}
          discoveryMessage={null}
          selectedTargetRef={selectedTargetRef}
          discoveredTargets={discoveredTargets}
          configuredDiscoveryNamespaces={configuredDiscoveryNamespaces}
          selectedDiscoveryNamespace={selectedDiscoveryNamespace}
          onDiscoverTargets={onDiscoverTargets}
          onSelectDiscoveryNamespace={onSelectDiscoveryNamespace}
          onSelectRuntimeConnection={vi.fn()}
          onSelectTarget={vi.fn()}
        />
      </Form>
    )
  }

  return {
    onDiscoverTargets,
    onSelectDiscoveryNamespace,
    ...render(<Wrapper />),
  }
}

describe("ExecutorSheet Portainer grouping", () => {
  it("renders kubernetes namespace selector from configured namespaces", () => {
    renderTargetSection({
      runtimeType: "kubernetes",
      runtimeConnection: createRuntimeConnection({
        type: "kubernetes",
        config: { namespaces: ["apps", "monitoring"] },
      }),
      configuredDiscoveryNamespaces: ["apps", "monitoring"],
      selectedDiscoveryNamespace: "apps",
      discoveredTargets: [],
      selectedTargetRef: {},
    })

    expect(screen.queryByText("executors.discovery.namespace")).not.toBeInTheDocument()
    expect(screen.queryByText("executors.discovery.namespaceHint")).not.toBeInTheDocument()
    expect(screen.getAllByRole("combobox")[1]).toHaveTextContent("apps")
  })

  it("renders kubernetes workloads as one workload-level target with nested containers", () => {
    const kubernetesTarget: RuntimeTargetDiscoveryItem = {
      runtime_type: "kubernetes",
      name: "deployment/worker",
      image: null,
      target_ref: {
        mode: "kubernetes_workload",
        namespace: "apps",
        kind: "Deployment",
        name: "worker",
        services: [
          { service: "worker", image: "ghcr.io/acme/worker:1.0" },
          { service: "sidecar", image: "ghcr.io/acme/sidecar:1.0" },
        ],
        service_count: 2,
      },
    }

    renderTargetSection({
      runtimeType: "kubernetes",
      runtimeConnection: createRuntimeConnection({ type: "kubernetes", name: "k8s-prod" }),
      configuredDiscoveryNamespaces: ["apps"],
      selectedDiscoveryNamespace: "apps",
      discoveredTargets: [kubernetesTarget],
      selectedTargetRef: kubernetesTarget.target_ref,
    })

    expect(screen.getAllByTestId("executor-grouped-target-detail-group")).toHaveLength(2)
    expect(screen.getAllByText("worker").length).toBeGreaterThan(0)
    expect(screen.getAllByText("sidecar").length).toBeGreaterThan(0)
    expect(screen.queryByText("ghcr.io/acme/sidecar:1.0")).not.toBeInTheDocument()
    expect(screen.queryByText("executors.discovery.kubernetesHint")).not.toBeInTheDocument()
    expect(screen.getByText("executors.sections.discovery")).toBeInTheDocument()
    expect(screen.getByRole("button", { name: "executors.discovery.rebind" })).toBeInTheDocument()
  })

  it("uses selected namespace when discovering kubernetes targets", async () => {
    const { onDiscoverTargets } = renderTargetSection({
      runtimeType: "kubernetes",
      runtimeConnection: createRuntimeConnection({
        type: "kubernetes",
        config: { namespaces: ["apps"] },
      }),
      configuredDiscoveryNamespaces: ["apps"],
      selectedDiscoveryNamespace: "apps",
      discoveredTargets: [],
      selectedTargetRef: {},
    })

    fireEvent.click(screen.getByRole("button", { name: "executors.actions.discover" }))

    expect(onDiscoverTargets).toHaveBeenCalledTimes(1)
  })

  it("renders helm release app version in target detail grid", () => {
    const helmTarget: RuntimeTargetDiscoveryItem = {
      runtime_type: "kubernetes",
      name: "helm/jenkins-5-1772603248",
      image: null,
      target_ref: {
        mode: "helm_release",
        namespace: "jenkins",
        release_name: "jenkins-5-1772603248",
        chart_name: "jenkins",
        chart_version: "5.9.17",
        app_version: "2.555.1",
        workloads: [{ kind: "StatefulSet", name: "jenkins-5-1772603248" }],
        service_count: 1,
      },
    }

    renderTargetSection({
      runtimeType: "kubernetes",
      runtimeConnection: createRuntimeConnection({ type: "kubernetes", name: "k8s-prod" }),
      configuredDiscoveryNamespaces: ["jenkins"],
      selectedDiscoveryNamespace: "jenkins",
      discoveredTargets: [helmTarget],
      selectedTargetRef: helmTarget.target_ref,
    })

    expect(screen.getAllByTestId("executor-target-detail-grid")).toHaveLength(2)
    expect(screen.getAllByText("executors.target.details.appVersion")).toHaveLength(2)
    expect(screen.getAllByText("2.555.1")).toHaveLength(2)
    expect(screen.queryByText("StatefulSet/jenkins-5-1772603248")).not.toBeInTheDocument()
  })

  it("renders standalone container selections with the simple target detail grid", () => {
    const containerTarget: RuntimeTargetDiscoveryItem = {
      runtime_type: "docker",
      name: "nginx",
      image: "nginx:latest",
      target_ref: {
        mode: "container",
        container_name: "nginx",
        container_id: "abc123",
      },
    }

    renderTargetSection({
      discoveredTargets: [containerTarget],
      selectedTargetRef: containerTarget.target_ref,
    })

    expect(screen.getAllByTestId("executor-target-detail-grid").length).toBeGreaterThan(0)
    expect(screen.getAllByText("nginx").length).toBeGreaterThan(0)
    expect(screen.getAllByText("abc123").length).toBeGreaterThan(0)
    expect(screen.queryByText("nginx / abc123")).not.toBeInTheDocument()
    expect(screen.getByRole("button", { name: "executors.discovery.rebind" })).toBeInTheDocument()
    expect(screen.queryByText("Unsupported target")).not.toBeInTheDocument()
  })

  it("renders portainer stacks as one stack-level target with nested service metadata", () => {
    const portainerStackTarget: RuntimeTargetDiscoveryItem = {
      runtime_type: "portainer",
      name: "release-stack",
      image: null,
      target_ref: {
        mode: "portainer_stack",
        endpoint_id: 2,
        stack_id: 11,
        stack_name: "release-stack",
        stack_type: "standalone",
        entrypoint: "stack.yml",
        project_path: "/data/stacks/11",
        services: [
          { service: "api", image: "ghcr.io/acme/api:1.0" },
          { service: "worker", image: "ghcr.io/acme/worker:1.0" },
        ],
        service_count: 2,
      },
    }

    renderTargetSection({
      runtimeType: "portainer",
      runtimeConnection: createRuntimeConnection({ type: "portainer", name: "portainer-prod" }),
      discoveredTargets: [portainerStackTarget],
      selectedTargetRef: portainerStackTarget.target_ref,
    })

    expect(screen.getAllByTestId("executor-grouped-target-detail-group")).toHaveLength(2)
    expect(screen.getAllByText("release-stack").length).toBeGreaterThan(0)
    expect(screen.getAllByText("11").length).toBeGreaterThan(0)
    expect(screen.queryByText("release-stack #11")).not.toBeInTheDocument()
    expect(screen.getAllByText("api").length).toBeGreaterThan(0)
    expect(screen.getAllByText("worker").length).toBeGreaterThan(0)
    expect(screen.queryByText("ghcr.io/acme/api:1.0")).not.toBeInTheDocument()
    expect(screen.getByRole("button", { name: "executors.discovery.rebind" })).toBeInTheDocument()
  })

  it("renders compose projects as one project-level target with nested services", () => {
    const composeTarget: RuntimeTargetDiscoveryItem = {
      runtime_type: "podman",
      name: "jenkins-agent",
      image: "docker.io/jenkins/inbound-agent:trixie",
      target_ref: {
        mode: "docker_compose",
        project: "jenkins-agent",
        working_dir: "/data/podman/jenkins-agent",
        config_files: ["podman-compose.yaml"],
        services: [
          { service: "jenkins-agent", image: "docker.io/jenkins/inbound-agent:trixie", replica_count: 1 },
        ],
        service_count: 1,
      },
    }

    renderTargetSection({
      runtimeType: "podman",
      runtimeConnection: createRuntimeConnection({ type: "podman", name: "dev-socket" }),
      discoveredTargets: [composeTarget],
      selectedTargetRef: composeTarget.target_ref,
    })

    expect(screen.getAllByTestId("executor-grouped-target-detail-group")).toHaveLength(2)
    expect(screen.getAllByText("jenkins-agent").length).toBeGreaterThan(0)
    expect(screen.getAllByText("Podman Compose project").length).toBeGreaterThan(0)
    expect(screen.getAllByText("docker.io/jenkins/inbound-agent:trixie").length).toBeGreaterThan(0)
    expect(screen.queryByTestId("executor-target-detail-grid")).not.toBeInTheDocument()
    expect(screen.getByRole("button", { name: "executors.discovery.rebind" })).toBeInTheDocument()
  })

  it("keeps docker compose wording for docker runtime compose projects", () => {
    const composeTarget: RuntimeTargetDiscoveryItem = {
      runtime_type: "docker",
      name: "release-stack",
      image: null,
      target_ref: {
        mode: "docker_compose",
        project: "release-stack",
        working_dir: "/srv/release-stack",
        config_files: ["compose.yaml"],
        services: [
          { service: "api", image: "ghcr.io/acme/api:1.0", replica_count: 1 },
        ],
        service_count: 1,
      },
    }

    renderTargetSection({
      runtimeType: "docker",
      runtimeConnection: createRuntimeConnection({ type: "docker", name: "docker-prod" }),
      discoveredTargets: [composeTarget],
      selectedTargetRef: composeTarget.target_ref,
    })

    expect(screen.getAllByText("Docker Compose project").length).toBeGreaterThan(0)
    expect(screen.queryByText("Podman Compose project")).not.toBeInTheDocument()
  })

  it("reuses grouped binding rows for portainer stacks with discovered services", () => {
    const tracker = createTracker()

    function Wrapper() {
      const form = useForm<ExecutorFormValues>({
        defaultValues: createFormValues({ runtime_type: "portainer" }),
      })

      return (
        <Form {...form}>
          <ExecutorSheetBindingSection
            form={form}
            trackers={[tracker]}
            isContainerRuntime={false}
            trackerName=""
            effectiveTrackerSourceId=""
            selectedTrackerBindableSources={[]}
            scopedReleaseChannels={[]}
            runtimeType="portainer"
            selectedTargetRef={{
              mode: "portainer_stack",
              endpoint_id: 2,
              stack_id: 11,
              stack_name: "release-stack",
              stack_type: "standalone",
              services: [
                { service: "api", image: "ghcr.io/acme/api:1.0" },
                { service: "worker", image: "ghcr.io/acme/worker:1.0" },
              ],
              service_count: 2,
            }}
            serviceBindings={[
              {
                service: "api",
                tracker_name: "release-tracker",
                tracker_source_id: "9",
                channel_name: "stable",
              },
            ]}
            onAddServiceBinding={vi.fn()}
            onUpdateServiceBinding={vi.fn()}
            onRemoveServiceBinding={vi.fn()}
            onSelectTracker={vi.fn()}
            onSelectTrackerSource={vi.fn()}
            onSelectChannel={vi.fn()}
          />
        </Form>
      )
    }

    render(<Wrapper />)

    expect(screen.queryByText("executors.binding.groupedTargetHint")).not.toBeInTheDocument()
    expect(screen.queryByText("executors.binding.serviceBindingGroupHint")).not.toBeInTheDocument()
    expect(screen.getAllByText("api").length).toBeGreaterThan(0)
    expect(screen.queryByText("worker")).not.toBeInTheDocument()
    expect(screen.queryByText("ghcr.io/acme/api:1.0")).not.toBeInTheDocument()
    expect(screen.getByRole("button", { name: "executors.binding.addServiceBinding" })).toBeEnabled()
  })

  it("renders review service bindings, image changes, and global timezone summary", () => {
    render(
      <ExecutorSheetReviewSection
        reviewItems={[
          { label: "executors.review.name", value: "portainer-executor" },
          { label: "executors.review.serviceBindings", value: "api → release-tracker / stable" },
        ]}
        trackers={[createTracker()]}
        serviceBindings={[
          {
            service: "api",
            tracker_name: "release-tracker",
            tracker_source_id: "9",
            channel_name: "stable",
          },
        ]}
        runtimeType="portainer"
        selectedTargetRef={{
          mode: "portainer_stack",
          endpoint_id: 2,
          stack_id: 11,
          stack_name: "release-stack",
          stack_type: "standalone",
          services: [{ service: "api", image: "ghcr.io/acme/api:1.0" }],
          service_count: 1,
        }}
        imageSelectionMode="use_tracker_image_and_tag"
        validationMessage={null}
      />,
    )

    expect(screen.getByText("executors.review.imageChanges")).toBeInTheDocument()
    expect(screen.getByText("ghcr.io/acme/api:1.0")).toBeInTheDocument()
    expect(screen.getByText("ghcr.io/acme/api:1.2.3")).toBeInTheDocument()
    expect(screen.getByText("1.2.3")).toBeInTheDocument()
    expect(screen.queryByText("Maintenance window: Asia/Shanghai, 01:00–02:00.")).not.toBeInTheDocument()
    expect(screen.queryByText("release-stack #11")).not.toBeInTheDocument()
    expect(screen.getByText("release-tracker")).toBeInTheDocument()
    expect(screen.getByText("正式版")).toBeInTheDocument()
  })
})
