import type { TFunction } from "i18next"

import type {
    ContainerExecutorTargetRef,
    DockerComposeExecutorTargetRef,
    ExecutorConfig,
    HelmReleaseExecutorTargetRef,
    ExecutorServiceBinding,
    ExecutorTargetRef,
    ExecutorUpdateMode,
    ImageReferenceMode,
    ImageSelectionMode,
    PortainerStackExecutorTargetRef,
    ReleaseChannelInput,
    RuntimeConnection,
    RuntimeTargetDiscoveryItem,
    RuntimeType,
    TrackerStatus,
} from "@/api/types"

export interface ExecutorFormValues {
    name: string
    runtime_type: RuntimeType
    runtime_connection_id: string
    tracker_name: string
    tracker_source_id: string
    channel_name: string
    enabled: boolean
    update_mode: ExecutorUpdateMode
    image_selection_mode: ImageSelectionMode
    image_reference_mode: ImageReferenceMode
    description: string
    maintenance_timezone: string
    maintenance_days: string[]
    maintenance_start_time: string
    maintenance_end_time: string
}

export type StepKey = "target" | "binding" | "policy" | "review"
export type ExecutorImplicitSubmitAction = "next" | "ignore"

export interface ExecutorReviewItem {
    label: string
    value: string
}

export interface ExecutorTargetDetailItem {
    label: string
    value: string
}

export interface ExecutorTargetDisplay {
    kind: "container" | "kubernetes" | "kubernetes_workload" | "helm_release" | "portainer_stack" | "docker_compose"
    title: string
    subtitle: string | null
    summary: string
    badges: string[]
    details: ExecutorTargetDetailItem[]
    cardDetails?: ExecutorTargetDetailItem[]
    groupedServices?: ExecutorGroupedServiceOption[]
}

export interface ExecutorDiscoverySingleTargetGroup {
    kind: "single_target"
    key: string
    target: RuntimeTargetDiscoveryItem
}

export interface ExecutorServiceBindingFormValue {
    service: string
    tracker_name: string
    tracker_source_id: string
    channel_name: string
}

export interface ExecutorGroupedServiceOption {
    service: string
    image: string | null
}

export interface ExecutorReviewImageChange {
    service: string
    sourceImage: string
    targetImage: string
    targetVersion: string | null
}

export type ExecutorDiscoveryTargetGroup = ExecutorDiscoverySingleTargetGroup

export const STEP_ORDER: StepKey[] = ["target", "binding", "policy", "review"]

export function getExecutorImplicitSubmitAction(step: StepKey): ExecutorImplicitSubmitAction {
    return step === "review" ? "ignore" : "next"
}

export function runExecutorImplicitSubmitAction(
    step: StepKey,
    handlers: {
        onNext: () => void
    },
) {
    if (getExecutorImplicitSubmitAction(step) === "next") {
        handlers.onNext()
    }
}

export const DAY_OPTIONS = [
    { value: "0", labelKey: "executors.days.monday" },
    { value: "1", labelKey: "executors.days.tuesday" },
    { value: "2", labelKey: "executors.days.wednesday" },
    { value: "3", labelKey: "executors.days.thursday" },
    { value: "4", labelKey: "executors.days.friday" },
    { value: "5", labelKey: "executors.days.saturday" },
    { value: "6", labelKey: "executors.days.sunday" },
] as const

export const EMPTY_TARGET_REF: ExecutorTargetRef = {}

type TrackerSource = TrackerStatus["sources"][number]

export function isContainerTrackerSourceType(sourceType: TrackerSource["source_type"] | undefined): boolean {
    return sourceType === "container"
}

export function isHelmTrackerSourceType(sourceType: TrackerSource["source_type"] | undefined): boolean {
    return sourceType === "helm"
}

export function hasEnabledReleaseChannel(source: TrackerSource): boolean {
    return (source.release_channels ?? []).some((channel) => channel.enabled)
}

export function isBindableTrackerSource(source: TrackerSource): boolean {
    return (isContainerTrackerSourceType(source.source_type) || isHelmTrackerSourceType(source.source_type)) && source.enabled && hasEnabledReleaseChannel(source)
}

export function isTrackerSourceCompatibleWithTarget(source: TrackerSource, targetRef: ExecutorTargetRef): boolean {
    return isHelmReleaseTarget(targetRef)
        ? isHelmTrackerSourceType(source.source_type)
        : isContainerTrackerSourceType(source.source_type)
}

export function getTrackerBindableSources(tracker: TrackerStatus | null, targetRef: ExecutorTargetRef = EMPTY_TARGET_REF): TrackerSource[] {
    if (!tracker) {
        return []
    }

    return tracker.sources.filter((source) => isBindableTrackerSource(source) && isTrackerSourceCompatibleWithTarget(source, targetRef))
}

export function getConfiguredKubernetesNamespaces(runtimeConnection: RuntimeConnection | null) {
    if (runtimeConnection?.type !== "kubernetes") {
        return []
    }

    const configuredNamespaces = runtimeConnection.config.namespaces
    if (Array.isArray(configuredNamespaces)) {
        const namespaces = configuredNamespaces
            .filter((namespace): namespace is string => typeof namespace === "string" && namespace.trim().length > 0)
            .map((namespace) => namespace.trim())
        if (namespaces.length > 0) {
            return [...new Set(namespaces)]
        }
    }

    const configuredNamespace = runtimeConnection.config.namespace
    if (typeof configuredNamespace === "string" && configuredNamespace.trim().length > 0) {
        return [configuredNamespace.trim()]
    }

    return []
}

export function buildExecutorTargetDiscoveryParams(
    runtimeConnection: RuntimeConnection,
    namespace: string,
) {
    return runtimeConnection.type === "kubernetes" ? { namespace } : undefined
}

export function filterTrackersWithBindableSources(trackers: TrackerStatus[], targetRef: ExecutorTargetRef = EMPTY_TARGET_REF): TrackerStatus[] {
    return trackers.filter((tracker) => getTrackerBindableSources(tracker, targetRef).length > 0)
}

export function buildRuntimeConnectionOptions(
    currentRuntimeConnection: RuntimeConnection | null,
    enabledRuntimeConnections: RuntimeConnection[],
): RuntimeConnection[] {
    if (!currentRuntimeConnection || currentRuntimeConnection.enabled) {
        return enabledRuntimeConnections
    }

    return [
        currentRuntimeConnection,
        ...enabledRuntimeConnections.filter((connection) => connection.id !== currentRuntimeConnection.id),
    ]
}

export function buildTrackerSourceOptions(
    currentBindableSource: TrackerSource | null,
    selectedTrackerBindableSources: TrackerSource[],
): TrackerSource[] {
    if (!currentBindableSource || currentBindableSource.enabled) {
        return selectedTrackerBindableSources
    }

    return [
        currentBindableSource,
        ...selectedTrackerBindableSources.filter((source) => source.id !== currentBindableSource.id),
    ]
}

export function buildScopedReleaseChannels(
    selectedBindableSource: TrackerSource | null,
    channelName: string,
): ReleaseChannelInput[] {
    const releaseChannels = selectedBindableSource?.release_channels ?? []
    const enabledChannels = releaseChannels.filter((channel) => channel.enabled)
    const currentChannel = releaseChannels.find((channel) => channel.name === channelName) ?? null

    if (!currentChannel || currentChannel.enabled) {
        return enabledChannels
    }

    return [
        currentChannel,
        ...enabledChannels.filter((channel) => channel.release_channel_key !== currentChannel.release_channel_key),
    ]
}

interface BuildExecutorReviewItemsParams {
    values: ExecutorFormValues
    t: TFunction
    selectedRuntimeConnection: RuntimeConnection | null
    selectedBindableSource: TrackerSource | null
    selectedTargetRef: ExecutorTargetRef
    serviceBindings?: ExecutorServiceBindingFormValue[]
}

interface GetExecutorValidationMessageParams {
    values: ExecutorFormValues
    t: TFunction
    selectedRuntimeConnection: RuntimeConnection | null
    isContainerRuntime: boolean
    selectedTracker: TrackerStatus | null
    selectedTrackerBindableSources: TrackerSource[]
    trackerSourceId: string
    selectedBindableSource: TrackerSource | null
    selectedTargetRef: ExecutorTargetRef
}

interface GetExecutorTargetValidationMessageParams {
    values: ExecutorFormValues
    t: TFunction
    selectedRuntimeConnection: RuntimeConnection | null
    selectedTargetRef: ExecutorTargetRef
}

interface GetExecutorBindingValidationMessageParams {
    values: ExecutorFormValues
    t: TFunction
    selectedRuntimeConnection: RuntimeConnection | null
    isContainerRuntime: boolean
    selectedTracker: TrackerStatus | null
    selectedTrackerBindableSources: TrackerSource[]
    trackerSourceId: string
    selectedBindableSource: TrackerSource | null
}

interface GetExecutorServiceBindingValidationMessageParams {
    bindings: ExecutorServiceBindingFormValue[]
    trackers: TrackerStatus[]
    t: TFunction
}

function stringifyTargetValue(value: unknown): string {
    return typeof value === "string" && value.trim().length > 0 ? value.trim() : ""
}

export function isPortainerStackTarget(
    targetRef: ExecutorTargetRef,
): targetRef is PortainerStackExecutorTargetRef {
    return targetRef.mode === "portainer_stack"
}

export function isDockerComposeTarget(
    targetRef: ExecutorTargetRef,
): targetRef is DockerComposeExecutorTargetRef {
    return targetRef.mode === "docker_compose"
}

export function isKubernetesTarget(runtimeType: RuntimeType): boolean {
    return runtimeType === "kubernetes"
}

export function isKubernetesWorkloadTarget(targetRef: ExecutorTargetRef): boolean {
    return targetRef.mode === "kubernetes_workload"
}

export function isHelmReleaseTarget(targetRef: ExecutorTargetRef): targetRef is HelmReleaseExecutorTargetRef {
    return targetRef.mode === "helm_release"
}

export function usesGroupedServiceBindings(targetRef: ExecutorTargetRef): boolean {
    return isPortainerStackTarget(targetRef) || isDockerComposeTarget(targetRef) || isKubernetesWorkloadTarget(targetRef)
}

export function normalizeExecutorServiceKey(service: string): string {
    return service.trim().toLowerCase()
}

export function getPortainerStackServiceOptions(targetRef: ExecutorTargetRef): ExecutorGroupedServiceOption[] {
    if (!usesGroupedServiceBindings(targetRef) || !Array.isArray(targetRef.services)) {
        return []
    }

    const seenServices = new Set<string>()
    const options: ExecutorGroupedServiceOption[] = []

    targetRef.services.forEach((item) => {
        if (!item || typeof item !== "object") {
            return
        }

        const service = stringifyTargetValue((item as { service?: unknown }).service)
        if (!service) {
            return
        }

        const normalizedService = normalizeExecutorServiceKey(service)
        if (seenServices.has(normalizedService)) {
            return
        }

        seenServices.add(normalizedService)
        const image = stringifyTargetValue((item as { image?: unknown }).image) || null
        options.push({ service, image })
    })

    return options
}

export function getGroupedBindingServiceOptions(
    targetRef: ExecutorTargetRef,
): ExecutorGroupedServiceOption[] {
    return getPortainerStackServiceOptions(targetRef)
}

export function isEquivalentPortainerStackTarget(
    left: ExecutorTargetRef,
    right: ExecutorTargetRef,
): boolean {
    if (!isPortainerStackTarget(left) || !isPortainerStackTarget(right)) {
        return false
    }

    return left.endpoint_id === right.endpoint_id
        && left.stack_id === right.stack_id
        && left.stack_name.trim() === right.stack_name.trim()
        && left.stack_type.trim().toLowerCase() === right.stack_type.trim().toLowerCase()
}

export function isEquivalentDockerComposeTarget(
    left: ExecutorTargetRef,
    right: ExecutorTargetRef,
): boolean {
    if (!isDockerComposeTarget(left) || !isDockerComposeTarget(right)) {
        return false
    }

    const leftProject = left.project.trim()
    const rightProject = right.project.trim()
    if (!leftProject || leftProject !== rightProject) {
        return false
    }

    const leftWorkingDir = typeof left.working_dir === "string" ? left.working_dir.trim() : ""
    const rightWorkingDir = typeof right.working_dir === "string" ? right.working_dir.trim() : ""
    if (leftWorkingDir && rightWorkingDir) {
        return leftWorkingDir === rightWorkingDir
    }

    return true
}

export function mergeDockerComposeTargetRef(
    discoveredTargetRef: ExecutorTargetRef,
    currentTargetRef: ExecutorTargetRef,
): ExecutorTargetRef {
    if (!isDockerComposeTarget(discoveredTargetRef) || !isDockerComposeTarget(currentTargetRef)) {
        return discoveredTargetRef
    }

    const mergedTargetRef: DockerComposeExecutorTargetRef = { ...discoveredTargetRef }
    const discoveredWorkingDir = stringifyTargetValue(discoveredTargetRef.working_dir)
    const currentWorkingDir = stringifyTargetValue(currentTargetRef.working_dir)
    if (!discoveredWorkingDir && currentWorkingDir) {
        mergedTargetRef.working_dir = currentTargetRef.working_dir
    }

    const discoveredConfigFiles = Array.isArray(discoveredTargetRef.config_files)
        ? discoveredTargetRef.config_files.filter((item): item is string => typeof item === "string" && item.trim().length > 0)
        : []
    const currentConfigFiles = Array.isArray(currentTargetRef.config_files)
        ? currentTargetRef.config_files.filter((item): item is string => typeof item === "string" && item.trim().length > 0)
        : []
    if (discoveredConfigFiles.length === 0 && currentConfigFiles.length > 0) {
        mergedTargetRef.config_files = currentTargetRef.config_files
    }

    return mergedTargetRef
}

export function getAutoSelectedTrackerSourceId(selectedTrackerBindableSources: TrackerSource[]): string {
    if (selectedTrackerBindableSources.length !== 1) {
        return ""
    }

    const onlySourceId = selectedTrackerBindableSources[0].id
    return onlySourceId == null ? "" : String(onlySourceId)
}

export function buildExecutorServiceBindingValues(
    config: ExecutorConfig,
    trackers: TrackerStatus[],
): ExecutorServiceBindingFormValue[] {
    return (config.service_bindings ?? []).map((binding) => {
        const tracker = trackers.find((candidate) => candidate.sources.some((source) => source.id === binding.tracker_source_id)) ?? null

        return {
            service: binding.service,
            tracker_name: tracker?.name ?? "",
            tracker_source_id: String(binding.tracker_source_id),
            channel_name: binding.channel_name,
        }
    })
}

export function buildExecutorServiceBindingsPayload(
    bindings: ExecutorServiceBindingFormValue[],
    trackers: TrackerStatus[],
): ExecutorServiceBinding[] {
    return bindings.map((binding) => {
        const { effectiveTrackerSourceId } = resolveExecutorServiceBinding(binding, trackers)

        return {
            service: normalizeExecutorServiceKey(binding.service),
            tracker_source_id: Number(effectiveTrackerSourceId),
            channel_name: binding.channel_name,
        }
    })
}

export function resolveExecutorServiceBinding(
    binding: ExecutorServiceBindingFormValue,
    trackers: TrackerStatus[],
): {
    selectedTracker: TrackerStatus | null
    allTrackerContainerSources: TrackerSource[]
    selectedTrackerBindableSources: TrackerSource[]
    effectiveTrackerSourceId: string
    selectedBindableSource: TrackerSource | null
    trackerSourceOptions: TrackerSource[]
    scopedReleaseChannels: ReleaseChannelInput[]
} {
    const selectedTracker = trackers.find((tracker) => tracker.name === binding.tracker_name) ?? null
    const allTrackerContainerSources = (selectedTracker?.sources ?? []).filter((source) => isContainerTrackerSourceType(source.source_type))
    const selectedTrackerBindableSources = getTrackerBindableSources(selectedTracker)
    const effectiveTrackerSourceId = binding.tracker_source_id || getAutoSelectedTrackerSourceId(selectedTrackerBindableSources)
    const selectedBindableSource = allTrackerContainerSources.find((source) => String(source.id) === effectiveTrackerSourceId) ?? null

    return {
        selectedTracker,
        allTrackerContainerSources,
        selectedTrackerBindableSources,
        effectiveTrackerSourceId,
        selectedBindableSource,
        trackerSourceOptions: buildTrackerSourceOptions(selectedBindableSource, selectedTrackerBindableSources),
        scopedReleaseChannels: buildScopedReleaseChannels(selectedBindableSource, binding.channel_name),
    }
}

function getReleaseChannelStoredVersion(channel: ReleaseChannelInput | undefined): string | null {
    if (!channel) {
        return null
    }

    const version = (channel as { last_version?: unknown; latest_version?: unknown; current_version?: unknown }).last_version
        ?? (channel as { last_version?: unknown; latest_version?: unknown; current_version?: unknown }).latest_version
        ?? (channel as { last_version?: unknown; latest_version?: unknown; current_version?: unknown }).current_version

    return typeof version === "string" && version.trim().length > 0 ? version.trim() : null
}

export function resolveExecutorBindingTargetVersion(
    binding: ExecutorServiceBindingFormValue,
    trackers: TrackerStatus[],
): string | null {
    const { selectedTracker, selectedBindableSource } = resolveExecutorServiceBinding(binding, trackers)
    const matchedChannel = (selectedBindableSource?.release_channels ?? []).find((channel) => channel.name === binding.channel_name)
    const channelVersion = getReleaseChannelStoredVersion(matchedChannel)
    if (channelVersion) {
        return channelVersion
    }

    const statusVersion = selectedTracker?.status.last_version ?? selectedTracker?.last_version ?? null
    return typeof statusVersion === "string" && statusVersion.trim().length > 0 ? statusVersion.trim() : null
}

export function buildExecutorImageTargetValue(
    image: string,
    targetVersion: string,
): string {
    let baseImage = image.trim()
    if (baseImage.includes("@")) {
        baseImage = baseImage.split("@", 1)[0]
    }

    const lastSlash = baseImage.lastIndexOf("/")
    const lastColon = baseImage.lastIndexOf(":")
    if (lastColon > lastSlash) {
        baseImage = baseImage.slice(0, lastColon)
    }

    return `${baseImage}:${targetVersion}`
}

export function buildExecutorReviewImageChanges({
    targetDisplay,
    serviceBindings,
    trackers,
    imageSelectionMode,
}: {
    targetDisplay: ExecutorTargetDisplay
    serviceBindings: ExecutorServiceBindingFormValue[]
    trackers: TrackerStatus[]
    imageSelectionMode: ImageSelectionMode
}): ExecutorReviewImageChange[] {
    if (!targetDisplay.groupedServices || serviceBindings.length === 0) {
        return []
    }

    return serviceBindings.map((binding) => {
        const sourceImage = targetDisplay.groupedServices?.find((item) => normalizeExecutorServiceKey(item.service) === normalizeExecutorServiceKey(binding.service))?.image ?? "-"
        const { selectedBindableSource } = resolveExecutorServiceBinding(binding, trackers)
        const targetVersion = resolveExecutorBindingTargetVersion(binding, trackers)
        const trackerImage = typeof selectedBindableSource?.source_config?.image === "string" ? selectedBindableSource.source_config.image.trim() : ""
        const targetBaseImage = imageSelectionMode === "use_tracker_image_and_tag" ? trackerImage : sourceImage
        const targetImage = targetVersion && targetBaseImage && targetBaseImage !== "-"
            ? buildExecutorImageTargetValue(targetBaseImage, targetVersion)
            : ""

        return {
            service: binding.service,
            sourceImage,
            targetImage,
            targetVersion,
        }
    })
}

function shortenContainerId(value: string): string {
    return value.length > 12 ? value.slice(0, 12) : value
}

function getTargetKindLabel(kind: ExecutorTargetDisplay["kind"], t: TFunction, runtimeType?: RuntimeType): string {
    if (kind === "docker_compose" && runtimeType === "podman") {
        const podmanComposeKey = "executors.target.kind.podman_compose"
        const translated = t(podmanComposeKey)

        return translated !== podmanComposeKey ? translated : "Podman Compose project"
    }

    const key = `executors.target.kind.${kind}`
    const translated = t(key)

    if (translated !== key) {
        return translated
    }

    if (kind === "kubernetes") {
        return "Kubernetes workload"
    }

    if (kind === "kubernetes_workload") {
        return "Kubernetes workload"
    }

    if (kind === "helm_release") {
        return "Helm release"
    }

    if (kind === "portainer_stack") {
        return "Portainer stack"
    }

    if (kind === "docker_compose") {
        return runtimeType === "podman" ? "Podman Compose project" : "Docker Compose project"
    }

    return t("executors.target.kind.container")
}

export function buildExecutorTargetDisplay(
    runtimeType: RuntimeType,
    targetRef: ExecutorTargetRef,
    t: TFunction,
): ExecutorTargetDisplay {
    if (isKubernetesTarget(runtimeType)) {
        const namespace = stringifyTargetValue(targetRef.namespace)
        if (isHelmReleaseTarget(targetRef)) {
            const releaseName = stringifyTargetValue(targetRef.release_name)
            const chartName = stringifyTargetValue(targetRef.chart_name)
            const chartVersion = stringifyTargetValue(targetRef.chart_version)
            const appVersion = stringifyTargetValue(targetRef.app_version)
            const workloads = Array.isArray(targetRef.workloads)
                ? targetRef.workloads
                    .map((workload) => {
                        const workloadKind = stringifyTargetValue(workload.kind)
                        const workloadName = stringifyTargetValue(workload.name)
                        return [workloadKind, workloadName].filter(Boolean).join("/")
                    })
                    .filter(Boolean)
                : []
            const workloadCount = Number.isInteger(targetRef.service_count) ? String(targetRef.service_count) : (workloads.length > 0 ? String(workloads.length) : "")
            const cardDetails = [
                { label: t("executors.target.details.namespace"), value: namespace || "-" },
                { label: t("executors.target.details.releaseName"), value: releaseName || "-" },
                { label: t("executors.target.details.chartName"), value: chartName || "-" },
                { label: t("executors.target.details.chartVersion"), value: chartVersion || "-" },
                { label: t("executors.target.details.appVersion"), value: appVersion || "-" },
            ]

            return {
                kind: "helm_release",
                title: releaseName || "-",
                subtitle: namespace || null,
                summary: [namespace, releaseName, chartName].filter(Boolean).join(" / ") || "-",
                badges: [runtimeType, getTargetKindLabel("helm_release", t, runtimeType)],
                cardDetails,
                details: [
                    ...cardDetails,
                    { label: t("executors.target.details.workloads"), value: workloads.join(", ") || "-" },
                    { label: t("executors.target.details.workloadCount"), value: workloadCount || "-" },
                ],
            }
        }
        const kind = stringifyTargetValue(targetRef.kind)
        const name = stringifyTargetValue(targetRef.name)
        if (isKubernetesWorkloadTarget(targetRef)) {
            const services = getGroupedBindingServiceOptions(targetRef)
            const serviceCount = Number.isInteger(targetRef.service_count) ? String(targetRef.service_count) : (services.length > 0 ? String(services.length) : "")
            const serviceCountSummary = serviceCount ? t("executors.target.serviceCountSummary", { count: Number(serviceCount) }) : ""
            const serviceList = services.map((item) => item.service).join(", ")
            const summary = [namespace, kind, name, serviceCountSummary].filter(Boolean).join(" / ") || "-"
            const subtitle = [namespace, kind].filter(Boolean).join(" · ") || null

            return {
                kind: "kubernetes_workload",
                title: name || "-",
                subtitle,
                summary,
                badges: [runtimeType, getTargetKindLabel("kubernetes_workload", t, runtimeType)],
                groupedServices: services,
                details: [
                    { label: t("executors.target.details.namespace"), value: namespace || "-" },
                    { label: t("executors.target.details.kind"), value: kind || "-" },
                    { label: t("executors.target.details.workload"), value: name || "-" },
                    { label: t("executors.target.details.services"), value: serviceList || "-" },
                    { label: t("executors.target.details.serviceCount"), value: serviceCount || "-" },
                ],
            }
        }
        const container = stringifyTargetValue(targetRef.container)
        const summary = [namespace, kind, name, container].filter(Boolean).join(" / ") || "-"
        const subtitle = [namespace, kind].filter(Boolean).join(" · ") || null

        return {
            kind: "kubernetes",
            title: name || container || "-",
            subtitle,
            summary,
            badges: [runtimeType, getTargetKindLabel("kubernetes", t, runtimeType)],
            details: [
                { label: t("executors.target.details.namespace"), value: namespace || "-" },
                { label: t("executors.target.details.kind"), value: kind || "-" },
                { label: t("executors.target.details.workload"), value: name || "-" },
                { label: t("executors.target.details.container"), value: container || "-" },
            ],
        }
    }

    if (runtimeType === "portainer" || isPortainerStackTarget(targetRef)) {
        const endpointId = Number.isInteger(targetRef.endpoint_id) ? String(targetRef.endpoint_id) : ""
        const stackId = Number.isInteger(targetRef.stack_id) ? String(targetRef.stack_id) : ""
        const stackName = stringifyTargetValue(targetRef.stack_name)
        const stackType = stringifyTargetValue(targetRef.stack_type)
        const entrypoint = stringifyTargetValue(targetRef.entrypoint)
        const projectPath = stringifyTargetValue(targetRef.project_path)
        const services = getPortainerStackServiceOptions(targetRef)
        const serviceCount = Number.isInteger(targetRef.service_count) ? String(targetRef.service_count) : (services.length > 0 ? String(services.length) : "")
        const serviceList = services.map((item) => item.service).join(", ")
        const identity = [stackName, stackId ? `#${stackId}` : ""].filter(Boolean).join(" ") || "-"
        const subtitle = [endpointId ? `endpoint:${endpointId}` : "", stackType].filter(Boolean).join(" · ") || null

        return {
            kind: "portainer_stack",
            title: stackName || "-",
            subtitle,
            summary: identity,
            badges: [runtimeType, getTargetKindLabel("portainer_stack", t, runtimeType)],
            groupedServices: services,
            details: [
                { label: t("executors.target.details.endpointId"), value: endpointId || "-" },
                { label: t("executors.target.details.stackId"), value: stackId || "-" },
                { label: t("executors.target.details.stackName"), value: stackName || "-" },
                { label: t("executors.target.details.stackType"), value: stackType || "-" },
                { label: t("executors.target.details.services"), value: serviceList || "-" },
                { label: t("executors.target.details.serviceCount"), value: serviceCount || "-" },
                { label: t("executors.target.details.entrypoint"), value: entrypoint || "-" },
                { label: t("executors.target.details.projectPath"), value: projectPath || "-" },
            ],
        }
    }

    if (isDockerComposeTarget(targetRef)) {
        const project = stringifyTargetValue(targetRef.project)
        const workingDir = stringifyTargetValue(targetRef.working_dir)
        const configFiles = Array.isArray(targetRef.config_files) ? targetRef.config_files.filter((item): item is string => typeof item === "string" && item.trim().length > 0) : []
        const services = getGroupedBindingServiceOptions(targetRef)
        const serviceCount = Number.isInteger(targetRef.service_count) ? String(targetRef.service_count) : (services.length > 0 ? String(services.length) : "")
        const serviceCountSummary = serviceCount ? t("executors.target.serviceCountSummary", { count: Number(serviceCount) }) : ""
        const serviceList = services.map((item) => item.service).join(", ")

        return {
            kind: "docker_compose",
            title: project || "-",
            subtitle: workingDir || null,
            summary: [project, serviceCountSummary].filter(Boolean).join(" · ") || "-",
            badges: [runtimeType, getTargetKindLabel("docker_compose", t, runtimeType)],
            groupedServices: services,
            details: [
                { label: t("executors.target.details.project"), value: project || "-" },
                { label: t("executors.target.details.workingDir"), value: workingDir || "-" },
                { label: t("executors.target.details.configFiles"), value: configFiles.join(", ") || "-" },
                { label: t("executors.target.details.services"), value: serviceList || "-" },
                { label: t("executors.target.details.serviceCount"), value: serviceCount || "-" },
            ],
        }
    }

    const containerTargetRef = targetRef as ContainerExecutorTargetRef
    const containerName = stringifyTargetValue(containerTargetRef.container_name)
    const containerId = stringifyTargetValue(containerTargetRef.container_id)
    const displayContainerId = containerId ? shortenContainerId(containerId) : ""

    return {
        kind: "container",
        title: containerName || displayContainerId || "-",
        subtitle: containerName && displayContainerId ? displayContainerId : null,
        summary: [containerName, displayContainerId].filter(Boolean).join(" / ") || "-",
        badges: [runtimeType, getTargetKindLabel("container", t, runtimeType)],
        details: [
            { label: t("executors.target.details.container"), value: containerName || "-" },
            { label: t("executors.target.details.containerId"), value: displayContainerId || "-" },
        ],
    }
}

export function groupExecutorDiscoveryTargets(items: RuntimeTargetDiscoveryItem[]): ExecutorDiscoveryTargetGroup[] {
    return items.map((item) => ({
        kind: "single_target",
        key: `${item.runtime_type}-${item.name}-${JSON.stringify(item.target_ref)}`,
        target: item,
    }))
}

interface BuildExecutorPayloadParams {
    values: ExecutorFormValues
    effectiveTrackerSourceId: string
    selectedTargetRef: ExecutorTargetRef
    trackers?: TrackerStatus[]
    serviceBindings?: ExecutorServiceBindingFormValue[]
}

export function createDefaultExecutorValues(defaultRuntimeConnection?: RuntimeConnection): ExecutorFormValues {
    const defaultRuntimeType =
        defaultRuntimeConnection?.type === "docker"
        || defaultRuntimeConnection?.type === "podman"
        || defaultRuntimeConnection?.type === "kubernetes"
        || defaultRuntimeConnection?.type === "portainer"
            ? defaultRuntimeConnection.type
            : "docker"

    return {
        name: "",
        runtime_type: defaultRuntimeType,
        runtime_connection_id: defaultRuntimeConnection ? String(defaultRuntimeConnection.id) : "",
        tracker_name: "",
        tracker_source_id: "",
        channel_name: "",
        enabled: true,
        update_mode: "manual",
        image_selection_mode: "replace_tag_on_current_image",
        image_reference_mode: "digest",
        description: "",
        maintenance_timezone: "UTC",
        maintenance_days: [],
        maintenance_start_time: "02:00",
        maintenance_end_time: "05:00",
    }
}

export function buildExecutorFormValues(config: ExecutorConfig): ExecutorFormValues {
    return {
        name: config.name,
        runtime_type: config.runtime_type,
        runtime_connection_id: String(config.runtime_connection_id),
        tracker_name: config.tracker_name,
        tracker_source_id: config.tracker_source_id ? String(config.tracker_source_id) : "",
        channel_name: config.channel_name ?? "",
        enabled: config.enabled,
        update_mode: config.update_mode,
        image_selection_mode: config.image_selection_mode ?? "replace_tag_on_current_image",
        image_reference_mode: config.image_reference_mode ?? "digest",
        description: config.description ?? "",
        maintenance_timezone: config.maintenance_window?.timezone ?? "UTC",
        maintenance_days: (config.maintenance_window?.days_of_week ?? []).map((day) => String(day)),
        maintenance_start_time: config.maintenance_window?.start_time ?? "02:00",
        maintenance_end_time: config.maintenance_window?.end_time ?? "05:00",
    }
}

export function buildExecutorReviewItems({
    values,
    t,
    selectedRuntimeConnection,
    selectedBindableSource,
    selectedTargetRef,
    serviceBindings = [],
}: BuildExecutorReviewItemsParams): ExecutorReviewItem[] {
    const targetDisplay = buildExecutorTargetDisplay(values.runtime_type, selectedTargetRef, t)
    const updateModeValue = values.update_mode === "maintenance_window"
        ? `${t(`executors.modes.${values.update_mode}`)} ${values.maintenance_start_time} - ${values.maintenance_end_time}`
        : t(`executors.modes.${values.update_mode}`)

    if (usesGroupedServiceBindings(selectedTargetRef)) {
        return [
            { label: t("executors.review.name"), value: values.name || "-" },
            {
                label: t("executors.review.runtime"),
                value: selectedRuntimeConnection ? `${selectedRuntimeConnection.name} (${selectedRuntimeConnection.type})` : "-",
            },
            { label: t("executors.review.targetType"), value: getTargetKindLabel(targetDisplay.kind, t) },
            { label: t("executors.review.target"), value: formatTargetRef(values.runtime_type, selectedTargetRef) },
            {
                label: t("executors.review.serviceBindings"),
                value: serviceBindings.length > 0
                    ? serviceBindings.map((binding) => `${binding.service} → ${binding.tracker_name || "-"} / ${binding.channel_name || "-"}`).join("; ")
                    : "-",
            },
            { label: t("executors.review.mode"), value: updateModeValue },
            { label: t("executors.review.imageStrategy"), value: t(`executors.imageStrategy.${values.image_selection_mode}`) },
            { label: t("executors.review.imageReference"), value: t(`executors.imageReferenceStrategy.${values.image_reference_mode}`) },
            { label: t("executors.review.status"), value: values.enabled ? t("common.enabled") : t("common.disabled") },
        ]
    }

    const reviewItems = [
        { label: t("executors.review.name"), value: values.name || "-" },
        {
            label: t("executors.review.runtime"),
            value: selectedRuntimeConnection ? `${selectedRuntimeConnection.name} (${selectedRuntimeConnection.type})` : "-",
        },
        { label: t("executors.review.tracker"), value: values.tracker_name || "-" },
        { label: t("executors.review.source"), value: selectedBindableSource?.source_key || "-" },
        { label: t("executors.review.targetType"), value: getTargetKindLabel(targetDisplay.kind, t) },
        { label: t("executors.review.target"), value: formatTargetRef(values.runtime_type, selectedTargetRef) },
        { label: t("executors.review.mode"), value: updateModeValue },
    ]

    if (!isHelmReleaseTarget(selectedTargetRef)) {
        reviewItems.push(
            { label: t("executors.review.imageStrategy"), value: t(`executors.imageStrategy.${values.image_selection_mode}`) },
            { label: t("executors.review.imageReference"), value: t(`executors.imageReferenceStrategy.${values.image_reference_mode}`) },
        )
    }

    reviewItems.push({ label: t("executors.review.status"), value: values.enabled ? t("common.enabled") : t("common.disabled") })
    return reviewItems
}

export function getExecutorTargetValidationMessage({
    values,
    t,
    selectedRuntimeConnection,
    selectedTargetRef,
}: GetExecutorTargetValidationMessageParams): string | null {
    if (!values.name.trim()) {
        return t("executors.validation.nameRequired")
    }

    if (!selectedRuntimeConnection) {
        return t("executors.validation.runtimeRequired")
    }

    if (selectedRuntimeConnection.type !== values.runtime_type) {
        return t("executors.validation.runtimeMismatch")
    }

    if (values.runtime_type === "kubernetes") {
        if (isHelmReleaseTarget(selectedTargetRef)) {
            const hasHelmRelease = [selectedTargetRef.namespace, selectedTargetRef.release_name].every(
                (value) => typeof value === "string" && value.trim().length > 0,
            )
            return hasHelmRelease ? null : t("executors.validation.helmReleaseTargetRequired")
        }
        if (!isKubernetesWorkloadTarget(selectedTargetRef)) {
            return t("executors.validation.kubernetesSingleContainer")
        }
        const hasWorkload = [selectedTargetRef.namespace, selectedTargetRef.kind, selectedTargetRef.name].every(
            (value) => typeof value === "string" && value.trim().length > 0,
        )
        if (!hasWorkload) {
            return t("executors.validation.kubernetesSingleContainer")
        }
        return null
    }

    if (values.runtime_type === "portainer") {
        if (!isPortainerStackTarget(selectedTargetRef)) {
            return t("executors.validation.portainerStackTargetRequired")
        }

        const hasRequiredIdentity = Number.isInteger(selectedTargetRef.endpoint_id)
            && Number.isInteger(selectedTargetRef.stack_id)
            && typeof selectedTargetRef.stack_name === "string"
            && selectedTargetRef.stack_name.trim().length > 0
            && typeof selectedTargetRef.stack_type === "string"
            && selectedTargetRef.stack_type.trim().length > 0
        if (!hasRequiredIdentity) {
            return t("executors.validation.portainerStackTargetIncomplete")
        }
    }

    if ((values.runtime_type === "docker" || values.runtime_type === "podman") && !hasContainerRuntimeTarget(selectedTargetRef)) {
        return t("executors.validation.containerTargetRequired")
    }

    return null
}

export function getExecutorBindingValidationMessage({
    values,
    t,
    selectedRuntimeConnection,
    isContainerRuntime,
    selectedTracker,
    selectedTrackerBindableSources,
    trackerSourceId,
    selectedBindableSource,
}: GetExecutorBindingValidationMessageParams): string | null {
    if (!selectedRuntimeConnection) {
        return t("executors.validation.runtimeRequired")
    }

    if (!values.tracker_name) {
        return t("executors.validation.trackerRequired")
    }

    if (isContainerRuntime) {
        if (trackerSourceId && !selectedBindableSource) {
            return t("executors.validation.trackerSourceMissing")
        }

        if (selectedBindableSource && !selectedBindableSource.enabled) {
            return t("executors.validation.trackerSourceUnavailable")
        }

        if (!selectedTracker || selectedTrackerBindableSources.length === 0) {
            return t("executors.validation.trackerImageStrategyIncompatible")
        }

        if (selectedTrackerBindableSources.length > 1 && !trackerSourceId) {
            return t("executors.validation.trackerSourceRequired")
        }
    }

    if (!values.channel_name) {
        return t("executors.validation.channelRequired")
    }

    if (selectedBindableSource) {
        const matchingChannel = (selectedBindableSource.release_channels ?? []).find((channel) => channel.name === values.channel_name) ?? null
        if (matchingChannel === null) {
            return t("executors.validation.channelMissing")
        }
        if (matchingChannel.enabled === false) {
            return t("executors.validation.channelUnavailable")
        }
    }

    return null
}

export function getExecutorServiceBindingValidationMessage({
    bindings,
    trackers,
    t,
}: GetExecutorServiceBindingValidationMessageParams): string | null {
    if (bindings.length === 0) {
        return t("executors.validation.serviceBindingRequired")
    }

    const seenServices = new Set<string>()

    for (const binding of bindings) {
        if (!binding.service.trim()) {
            return t("executors.validation.serviceSelectionRequired")
        }

        const normalizedService = normalizeExecutorServiceKey(binding.service)
        if (seenServices.has(normalizedService)) {
            return t("executors.validation.duplicateServiceBinding")
        }
        seenServices.add(normalizedService)

        if (!binding.tracker_name) {
            return t("executors.validation.trackerRequired")
        }

        const {
            selectedTracker,
            selectedTrackerBindableSources,
            effectiveTrackerSourceId,
            selectedBindableSource,
        } = resolveExecutorServiceBinding(binding, trackers)

        if (effectiveTrackerSourceId && !selectedBindableSource) {
            return t("executors.validation.trackerSourceMissing")
        }

        if (selectedBindableSource && !selectedBindableSource.enabled) {
            return t("executors.validation.trackerSourceUnavailable")
        }

        if (!selectedTracker || selectedTrackerBindableSources.length === 0) {
            return t("executors.validation.trackerImageStrategyIncompatible")
        }

        if (selectedTrackerBindableSources.length > 1 && !effectiveTrackerSourceId) {
            return t("executors.validation.trackerSourceRequired")
        }

        if (!binding.channel_name) {
            return t("executors.validation.channelRequired")
        }

        if (selectedBindableSource) {
            const matchingChannel = (selectedBindableSource.release_channels ?? []).find((channel) => channel.name === binding.channel_name) ?? null
            if (matchingChannel === null) {
                return t("executors.validation.channelMissing")
            }
            if (matchingChannel.enabled === false) {
                return t("executors.validation.channelUnavailable")
            }
        }
    }

    return null
}

export function getExecutorValidationMessage({
    values,
    t,
    selectedRuntimeConnection,
    isContainerRuntime,
    selectedTracker,
    selectedTrackerBindableSources,
    trackerSourceId,
    selectedBindableSource,
    selectedTargetRef,
}: GetExecutorValidationMessageParams): string | null {
    const targetValidationMessage = getExecutorTargetValidationMessage({
        values,
        t,
        selectedRuntimeConnection,
        selectedTargetRef,
    })
    if (targetValidationMessage) {
        return targetValidationMessage
    }

    const bindingValidationMessage = getExecutorBindingValidationMessage({
        values,
        t,
        selectedRuntimeConnection,
        isContainerRuntime,
        selectedTracker,
        selectedTrackerBindableSources,
        trackerSourceId,
        selectedBindableSource,
    })
    if (bindingValidationMessage) {
        return bindingValidationMessage
    }

    if (!isHelmReleaseTarget(selectedTargetRef) && values.image_selection_mode === "use_tracker_image_and_tag" && !selectedBindableSource?.source_config?.image) {
        return t("executors.validation.trackerImageStrategyIncompatible")
    }

    if (values.update_mode === "maintenance_window") {
        if (!values.maintenance_start_time || !values.maintenance_end_time) {
            return t("executors.validation.maintenanceTimeRequired")
        }
    }

    return null
}

export function buildExecutorPayload({
    values,
    effectiveTrackerSourceId,
    selectedTargetRef,
    trackers = [],
    serviceBindings = [],
}: BuildExecutorPayloadParams): ExecutorConfig {
    const resolvedComposeBindings = usesGroupedServiceBindings(selectedTargetRef)
        ? buildExecutorServiceBindingsPayload(serviceBindings, trackers)
        : []

    return {
        name: values.name,
        runtime_type: values.runtime_type,
        runtime_connection_id: Number(values.runtime_connection_id),
        tracker_name: usesGroupedServiceBindings(selectedTargetRef) ? (serviceBindings[0]?.tracker_name ?? "") : values.tracker_name,
        tracker_source_id: usesGroupedServiceBindings(selectedTargetRef)
            ? (resolvedComposeBindings[0]?.tracker_source_id ?? null)
            : (effectiveTrackerSourceId ? Number(effectiveTrackerSourceId) : null),
        channel_name: usesGroupedServiceBindings(selectedTargetRef) ? (serviceBindings[0]?.channel_name || null) : (values.channel_name || null),
        enabled: values.enabled,
        update_mode: values.update_mode,
        image_selection_mode: values.image_selection_mode,
        image_reference_mode: values.image_reference_mode,
        target_ref: selectedTargetRef,
        service_bindings: resolvedComposeBindings,
        maintenance_window: values.update_mode === "maintenance_window"
            ? {
                timezone: values.maintenance_timezone,
                days_of_week: values.maintenance_days.map((day) => Number(day)).sort((left, right) => left - right),
                start_time: values.maintenance_start_time,
                end_time: values.maintenance_end_time,
            }
            : null,
        description: values.description || null,
    }
}

function formatApiErrorDetail(detail: unknown): string | null {
    if (typeof detail === "string") {
        return detail.trim() || null
    }

    if (Array.isArray(detail)) {
        const messages = detail.map((entry) => {
            if (typeof entry === "string") {
                return entry
            }

            if (entry && typeof entry === "object") {
                const message = (entry as { msg?: unknown }).msg
                if (typeof message === "string" && message.trim()) {
                    return message
                }

                try {
                    return JSON.stringify(entry)
                } catch {
                    return String(entry)
                }
            }

            return String(entry)
        }).filter((message) => message.trim().length > 0)

        return messages.length > 0 ? messages.join("; ") : null
    }

    if (detail && typeof detail === "object") {
        const message = (detail as { msg?: unknown }).msg
        if (typeof message === "string" && message.trim()) {
            return message
        }

        try {
            return JSON.stringify(detail)
        } catch {
            return String(detail)
        }
    }

    if (detail == null) {
        return null
    }

    return String(detail)
}

export function getApiErrorDetailMessage(error: unknown): string | null {
    const detail = (error as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail
    return formatApiErrorDetail(detail)
}

export function formatTargetRef(runtimeType: RuntimeType, targetRef: ExecutorTargetRef): string {
    if (runtimeType === "kubernetes") {
        if (isHelmReleaseTarget(targetRef)) {
            return [targetRef.namespace, targetRef.release_name, targetRef.chart_name]
                .filter((value): value is string => typeof value === "string" && value.trim().length > 0)
                .join(" / ") || "-"
        }
        if (isKubernetesWorkloadTarget(targetRef)) {
            return [targetRef.namespace, targetRef.kind, targetRef.name]
                .filter((value): value is string => typeof value === "string" && value.trim().length > 0)
                .join(" / ") || "-"
        }
        return [targetRef.namespace, targetRef.kind, targetRef.name, targetRef.container]
            .filter((value): value is string => typeof value === "string" && value.trim().length > 0)
            .join(" / ") || "-"
    }

    if (runtimeType === "portainer" || isPortainerStackTarget(targetRef)) {
        const stackName = typeof targetRef.stack_name === "string" ? targetRef.stack_name.trim() : ""
        const stackId = Number.isInteger(targetRef.stack_id) ? String(targetRef.stack_id) : ""
        const endpointId = Number.isInteger(targetRef.endpoint_id) ? String(targetRef.endpoint_id) : ""
        const identity = [stackName, stackId ? `#${stackId}` : ""].filter(Boolean).join(" ")
        const endpoint = endpointId ? `endpoint:${endpointId}` : ""
        return [identity, endpoint].filter(Boolean).join(" / ") || "-"
    }

    if (isDockerComposeTarget(targetRef)) {
        return [targetRef.project, targetRef.working_dir]
            .filter((value): value is string => typeof value === "string" && value.trim().length > 0)
            .join(" / ") || "-"
    }

    return [targetRef.container_name, targetRef.container_id]
        .filter((value): value is string => typeof value === "string" && value.trim().length > 0)
        .join(" / ") || "-"
}

export function hasContainerRuntimeTarget(targetRef: ExecutorTargetRef): boolean {
    if (isKubernetesWorkloadTarget(targetRef)) {
        return [targetRef.namespace, targetRef.kind, targetRef.name].every(
            (value) => typeof value === "string" && value.trim().length > 0,
        )
    }

    if (isDockerComposeTarget(targetRef)) {
        const hasProject = typeof targetRef.project === "string" && targetRef.project.trim().length > 0
        return hasProject
    }

    if (targetRef.mode === "portainer_stack") {
        const hasEndpointId = Number.isInteger(targetRef.endpoint_id)
        const hasStackId = Number.isInteger(targetRef.stack_id)
        const hasStackName = typeof targetRef.stack_name === "string" && targetRef.stack_name.trim().length > 0
        const hasStackType = typeof targetRef.stack_type === "string" && targetRef.stack_type.trim().length > 0
        return hasEndpointId && hasStackId && hasStackName && hasStackType
    }

    if (isHelmReleaseTarget(targetRef)) {
        return [targetRef.namespace, targetRef.release_name].every(
            (value) => typeof value === "string" && value.trim().length > 0,
        )
    }

    return [targetRef.container_name, targetRef.container_id].some(
        (value) => typeof value === "string" && value.trim().length > 0,
    )
}
