import { describe, expect, it, vi } from "vitest"
import type { TFunction } from "i18next"

import type { RuntimeConnection, TrackerStatus } from "@/api/types"
import {
    buildExecutorImageTargetValue,
    buildExecutorReviewImageChanges,
    buildExecutorTargetDisplay,
    buildExecutorFormValues,
    buildExecutorReviewItems,
    buildExecutorPayload,
    buildRuntimeConnectionOptions,
    buildScopedReleaseChannels,
    buildTrackerSourceOptions,
    filterTrackersWithBindableSources,
    formatTargetRef,
    getGroupedBindingServiceOptions,
    getExecutorServiceBindingValidationMessage,
    getExecutorImplicitSubmitAction,
    getExecutorBindingValidationMessage,
    getExecutorTargetValidationMessage,
    getExecutorValidationMessage,
    hasContainerRuntimeTarget,
    isBindableTrackerSource,
    isEquivalentDockerComposeTarget,
    isEquivalentPortainerStackTarget,
    mergeDockerComposeTargetRef,
    resolveExecutorServiceBinding,
    runExecutorImplicitSubmitAction,
    STEP_ORDER,
    type ExecutorServiceBindingFormValue,
    type ExecutorFormValues,
    usesGroupedServiceBindings,
} from "@/components/executors/executorSheetHelpers"

function createRuntimeConnection(overrides: Partial<RuntimeConnection>): RuntimeConnection {
    return {
        id: overrides.id ?? 1,
        name: overrides.name ?? "runtime",
        type: overrides.type ?? "docker",
        enabled: overrides.enabled ?? true,
        config: overrides.config ?? {},
        secrets: overrides.secrets ?? {},
        endpoint: overrides.endpoint ?? null,
        description: overrides.description ?? null,
    }
}

function createTracker(overrides: Partial<TrackerStatus>): TrackerStatus {
    return {
        id: overrides.id ?? 1,
        name: overrides.name ?? "tracker",
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
            last_check: null,
            last_version: null,
            error: null,
            source_count: 0,
            enabled_source_count: 0,
            source_types: [],
        },
        created_at: overrides.created_at,
        updated_at: overrides.updated_at,
        type: overrides.type,
        last_check: overrides.last_check,
        last_version: overrides.last_version,
        error: overrides.error,
        channel_count: overrides.channel_count,
    }
}

function createTrackerSource(
    overrides: Partial<TrackerStatus["sources"][number]>,
): TrackerStatus["sources"][number] {
    return {
        id: overrides.id ?? 1,
        channel_key: overrides.channel_key ?? `channel-${overrides.id ?? 1}`,
        channel_type: overrides.channel_type ?? "container",
        enabled: overrides.enabled ?? true,
        channel_config: overrides.channel_config ?? { image: "ghcr.io/acme/app" },
        release_channels: overrides.release_channels ?? [],
        channel_rank: overrides.channel_rank ?? 0,
        source_key: overrides.source_key ?? "image",
        source_type: overrides.source_type ?? "container",
        source_config: overrides.source_config ?? { image: "ghcr.io/acme/app" },
        source_rank: overrides.source_rank ?? 0,
        aggregate_tracker_id: overrides.aggregate_tracker_id,
        credential_name: overrides.credential_name,
        created_at: overrides.created_at,
        updated_at: overrides.updated_at,
    }
}

function createValues(overrides: Partial<ExecutorFormValues>): ExecutorFormValues {
    return {
        name: overrides.name ?? "executor",
        runtime_type: overrides.runtime_type ?? "docker",
        runtime_connection_id: overrides.runtime_connection_id ?? "1",
        tracker_name: overrides.tracker_name ?? "tracker",
        tracker_source_id: overrides.tracker_source_id ?? "1",
        channel_name: overrides.channel_name ?? "stable",
        enabled: overrides.enabled ?? true,
        update_mode: overrides.update_mode ?? "manual",
        image_selection_mode: overrides.image_selection_mode ?? "replace_tag_on_current_image",
        image_reference_mode: overrides.image_reference_mode ?? "digest",
        description: overrides.description ?? "",
        maintenance_timezone: overrides.maintenance_timezone ?? "UTC",
        maintenance_days: overrides.maintenance_days ?? [],
        maintenance_start_time: overrides.maintenance_start_time ?? "02:00",
        maintenance_end_time: overrides.maintenance_end_time ?? "05:00",
        health_check_strategy: overrides.health_check_strategy ?? "none",
        health_check_failure_policy: overrides.health_check_failure_policy ?? "mark_failed",
        health_check_grace_period_seconds: overrides.health_check_grace_period_seconds ?? "0",
        health_check_attempt_timeout_seconds: overrides.health_check_attempt_timeout_seconds ?? "0",
        health_check_interval_seconds: overrides.health_check_interval_seconds ?? "0",
        health_check_probe_window_seconds: overrides.health_check_probe_window_seconds ?? "0",
        health_check_http_host: overrides.health_check_http_host ?? "",
        health_check_http_path: overrides.health_check_http_path ?? "/health",
        health_check_http_port: overrides.health_check_http_port ?? "",
        health_check_http_scheme: overrides.health_check_http_scheme ?? "http",
        health_check_http_method: overrides.health_check_http_method ?? "GET",
        health_check_http_expected_status_codes: overrides.health_check_http_expected_status_codes ?? "",
        health_check_tcp_host: overrides.health_check_tcp_host ?? "",
        health_check_tcp_port: overrides.health_check_tcp_port ?? "",
    }
}

function createServiceBinding(overrides: Partial<ExecutorServiceBindingFormValue> = {}): ExecutorServiceBindingFormValue {
    return {
        service: overrides.service ?? "api",
        tracker_name: overrides.tracker_name ?? "tracker",
        tracker_source_id: overrides.tracker_source_id ?? "1",
        channel_name: overrides.channel_name ?? "stable",
    }
}

describe("executor binding edit state helpers", () => {
    it("never allows implicit form submission to save", () => {
        expect(getExecutorImplicitSubmitAction("target")).toBe("next")
        expect(getExecutorImplicitSubmitAction("binding")).toBe("next")
        expect(getExecutorImplicitSubmitAction("policy")).toBe("next")
        expect(getExecutorImplicitSubmitAction("review")).toBe("ignore")
    })

    it("dispatches policy implicit form submit to next", () => {
        const onNext = vi.fn()

        runExecutorImplicitSubmitAction("policy", { onNext })

        expect(onNext).toHaveBeenCalledOnce()
    })

    it("ignores review implicit form submit because saving is explicit", () => {
        const onNext = vi.fn()

        runExecutorImplicitSubmitAction("review", { onNext })

        expect(onNext).not.toHaveBeenCalled()
    })

    it("defaults and hydrates the executor image reference strategy", () => {
        const hydrated = buildExecutorFormValues({
            name: "executor",
            runtime_type: "docker",
            runtime_connection_id: 1,
            tracker_name: "tracker",
            tracker_source_id: 1,
            channel_name: "stable",
            enabled: true,
            update_mode: "manual",
            image_selection_mode: "replace_tag_on_current_image",
            image_reference_mode: "tag",
            target_ref: { mode: "container", container_id: "abc" },
            service_bindings: [],
            maintenance_window: null,
            description: null,
        })

        expect(createValues({}).image_reference_mode).toBe("digest")
        expect(hydrated.image_reference_mode).toBe("tag")
    })


    it("hydrates and builds editable HTTP health-check probe settings", () => {
        const hydrated = buildExecutorFormValues({
            name: "executor",
            runtime_type: "docker",
            runtime_connection_id: 1,
            tracker_name: "tracker",
            tracker_source_id: 1,
            channel_name: "stable",
            enabled: true,
            update_mode: "manual",
            image_selection_mode: "replace_tag_on_current_image",
            image_reference_mode: "digest",
            target_ref: { mode: "container", container_id: "abc" },
            service_bindings: [],
            maintenance_window: null,
            description: null,
            health_check: {
                strategy: "manual_http",
                use_default_strategy: false,
                failure_policy: "mark_failed",
                grace_period_seconds: 5,
                attempt_timeout_seconds: 2,
                interval_seconds: 3,
                probe_window_seconds: 30,
                services: ["api"],
                http: {
                    path: "/ready",
                    port: 8080,
                    scheme: "https",
                    method: "HEAD",
                    host: "api.internal",
                    expected_status_codes: [200, 204],
                    expected_body_regex: "ok",
                    headers: { "X-Probe": "1" },
                    tls_skip_verify: true,
                },
                tcp: null,
            },
        })

        expect(hydrated.health_check_http_host).toBe("api.internal")
        expect(hydrated.health_check_http_path).toBe("/ready")
        expect(hydrated.health_check_http_port).toBe("8080")
        expect(hydrated.health_check_http_scheme).toBe("https")
        expect(hydrated.health_check_http_method).toBe("HEAD")
        expect(hydrated.health_check_http_expected_status_codes).toBe("200,204")

        const payload = buildExecutorPayload({
            values: {
                ...hydrated,
                health_check_strategy: "manual_http",
                health_check_http_host: "manual.internal",
                health_check_http_path: "/live",
                health_check_http_port: "9090",
                health_check_http_scheme: "http",
                health_check_http_method: "GET",
                health_check_http_expected_status_codes: "200, 204",
            },
            effectiveTrackerSourceId: "1",
            selectedTargetRef: { mode: "container", container_name: "api" },
            existingHealthCheck: {
                strategy: "manual_http",
                use_default_strategy: false,
                failure_policy: "mark_failed",
                grace_period_seconds: 5,
                attempt_timeout_seconds: 2,
                interval_seconds: 3,
                probe_window_seconds: 30,
                services: ["api"],
                http: { expected_body_regex: "ok", headers: { "X-Probe": "1" }, host: "api.internal", path: "/ready", tls_skip_verify: true },
                tcp: null,
            },
        })

        expect(payload.health_check?.http).toEqual({
            path: "/live",
            host: "manual.internal",
            port: 9090,
            scheme: "http",
            method: "GET",
            expected_status_codes: [200, 204],
            expected_body_regex: "ok",
            headers: { "X-Probe": "1" },
            tls_skip_verify: true,
        })
    })

    it("validates health-check probe values before save", () => {
        const t = ((key: string) => key) as unknown as TFunction
        const baseParams = {
            t,
            selectedRuntimeConnection: createRuntimeConnection({ id: 1, type: "docker", enabled: true }),
            isContainerRuntime: true,
            selectedTracker: createTracker({
                sources: [
                    createTrackerSource({
                        id: 1,
                        source_key: "image",
                        source_type: "container",
                        enabled: true,
                        source_config: { image: "ghcr.io/acme/app" },
                        release_channels: [{ release_channel_key: "stable", name: "stable", type: "release", enabled: true }],
                        source_rank: 0,
                    }),
                ],
            }),
            selectedTrackerBindableSources: [createTrackerSource({ id: 1, source_config: { image: "ghcr.io/acme/app" } })],
            trackerSourceId: "1",
            selectedBindableSource: createTrackerSource({
                id: 1,
                source_config: { image: "ghcr.io/acme/app" },
                release_channels: [{ release_channel_key: "stable", name: "stable", type: "release", enabled: true }],
            }),
            selectedTargetRef: { container_name: "app", container_id: "abc" },
        }

        expect(getExecutorValidationMessage({
            ...baseParams,
            values: createValues({
                health_check_strategy: "manual_http",
                health_check_attempt_timeout_seconds: "2",
                health_check_interval_seconds: "3",
                health_check_probe_window_seconds: "30",
                health_check_http_host: "api.internal",
                health_check_http_path: "health",
            }),
        })).toBe("executors.validation.healthCheckHttpPathInvalid")

        expect(getExecutorValidationMessage({
            ...baseParams,
            values: createValues({
                health_check_strategy: "manual_tcp",
                health_check_attempt_timeout_seconds: "2",
                health_check_interval_seconds: "3",
                health_check_probe_window_seconds: "30",
                health_check_tcp_host: "127.0.0.1",
                health_check_tcp_port: "",
            }),
        })).toBe("executors.validation.healthCheckTcpPortRequired")
    })

    it("keeps currently bound disabled runtime visible in options", () => {
        const disabledRuntime = createRuntimeConnection({ id: 9, name: "legacy-runtime", enabled: false })
        const enabledRuntime = createRuntimeConnection({ id: 1, name: "active-runtime", enabled: true })

        const options = buildRuntimeConnectionOptions(disabledRuntime, [enabledRuntime])

        expect(options.map((item) => item.name)).toEqual(["legacy-runtime", "active-runtime"])
    })

    it("keeps currently bound disabled source visible in options", () => {
        const disabledSource = createTrackerSource({
            id: 9,
            source_key: "legacy-image",
            source_type: "container",
            enabled: false,
            source_config: { image: "ghcr.io/acme/legacy" },
            release_channels: [],
            source_rank: 0,
        })
        const enabledSource = createTrackerSource({
            id: 1,
            source_key: "active-image",
            source_type: "container",
            enabled: true,
            source_config: { image: "ghcr.io/acme/active" },
            release_channels: [],
            source_rank: 1,
        })

        const options = buildTrackerSourceOptions(disabledSource, [enabledSource])

        expect(options.map((item) => item.source_key)).toEqual(["legacy-image", "active-image"])
    })

    it("keeps currently bound disabled channel visible in scoped channel options", () => {
        const tracker = createTracker({
            sources: [
                createTrackerSource({
                    id: 1,
                    source_key: "image",
                    source_type: "container",
                    enabled: true,
                    source_config: { image: "ghcr.io/acme/app" },
                    release_channels: [
                        { release_channel_key: "image-stable", name: "stable", type: "release", enabled: false },
                        { release_channel_key: "image-canary", name: "canary", type: "prerelease", enabled: true },
                    ],
                    source_rank: 0,
                }),
            ],
        })

        const channels = buildScopedReleaseChannels(tracker.sources[0], "stable")

        expect(channels.map((item) => item.name)).toEqual(["stable", "canary"])
        expect(channels[0]?.enabled).toBe(false)
    })

    it("returns explicit source/channel validation messages for unavailable bindings", () => {
        const t = ((key: string) => key) as unknown as TFunction
        const tracker = createTracker({
            sources: [
                createTrackerSource({
                    id: 1,
                    source_key: "image",
                    source_type: "container",
                    enabled: false,
                    source_config: { image: "ghcr.io/acme/app" },
                    release_channels: [
                        { release_channel_key: "image-stable", name: "stable", type: "release", enabled: false },
                    ],
                    source_rank: 0,
                }),
            ],
        })

        const message = getExecutorValidationMessage({
            values: createValues({ channel_name: "stable", tracker_source_id: "1" }),
            t,
            selectedRuntimeConnection: createRuntimeConnection({ id: 1, type: "docker", enabled: true }),
            isContainerRuntime: true,
            selectedTracker: tracker,
            selectedTrackerBindableSources: [],
            trackerSourceId: "1",
            selectedBindableSource: tracker.sources[0],
            selectedTargetRef: { container_name: "app", container_id: "abc" },
        })

        expect(message).toBe("executors.validation.trackerSourceUnavailable")
    })

    it("returns explicit message when the currently bound source is missing", () => {
        const t = ((key: string) => key) as unknown as TFunction

        const message = getExecutorValidationMessage({
            values: createValues({ channel_name: "stable", tracker_source_id: "999" }),
            t,
            selectedRuntimeConnection: createRuntimeConnection({ id: 1, type: "docker", enabled: true }),
            isContainerRuntime: true,
            selectedTracker: createTracker({ sources: [] }),
            selectedTrackerBindableSources: [],
            trackerSourceId: "999",
            selectedBindableSource: null,
            selectedTargetRef: { container_name: "app", container_id: "abc" },
        })

        expect(message).toBe("executors.validation.trackerSourceMissing")
    })

    it("returns explicit message when the currently bound channel is missing", () => {
        const t = ((key: string) => key) as unknown as TFunction
        const tracker = createTracker({
            sources: [
                createTrackerSource({
                    id: 1,
                    source_key: "image",
                    source_type: "container",
                    enabled: true,
                    source_config: { image: "ghcr.io/acme/app" },
                    release_channels: [
                        { release_channel_key: "image-canary", name: "canary", type: "prerelease", enabled: true },
                    ],
                    source_rank: 0,
                }),
            ],
        })

        const message = getExecutorValidationMessage({
            values: createValues({ channel_name: "stable", tracker_source_id: "1" }),
            t,
            selectedRuntimeConnection: createRuntimeConnection({ id: 1, type: "docker", enabled: true }),
            isContainerRuntime: true,
            selectedTracker: tracker,
            selectedTrackerBindableSources: tracker.sources,
            trackerSourceId: "1",
            selectedBindableSource: tracker.sources[0],
            selectedTargetRef: { container_name: "app", container_id: "abc" },
        })

        expect(message).toBe("executors.validation.channelMissing")
    })

    it("returns explicit message when the currently bound channel is disabled", () => {
        const t = ((key: string) => key) as unknown as TFunction
        const tracker = createTracker({
            sources: [
                createTrackerSource({
                    id: 1,
                    source_key: "image",
                    source_type: "container",
                    enabled: true,
                    source_config: { image: "ghcr.io/acme/app" },
                    release_channels: [
                        { release_channel_key: "image-stable", name: "stable", type: "release", enabled: false },
                    ],
                    source_rank: 0,
                }),
            ],
        })

        const message = getExecutorValidationMessage({
            values: createValues({ channel_name: "stable", tracker_source_id: "1" }),
            t,
            selectedRuntimeConnection: createRuntimeConnection({ id: 1, type: "docker", enabled: true }),
            isContainerRuntime: true,
            selectedTracker: tracker,
            selectedTrackerBindableSources: tracker.sources,
            trackerSourceId: "1",
            selectedBindableSource: tracker.sources[0],
            selectedTargetRef: { container_name: "app", container_id: "abc" },
        })

        expect(message).toBe("executors.validation.channelUnavailable")
    })

    it("filters binding tracker options to enabled container sources with enabled release channels", () => {
        const trackers = [
            createTracker({
                name: "bindable",
                sources: [
                    createTrackerSource({
                        id: 1,
                        source_key: "image",
                        source_type: "container",
                        enabled: true,
                        source_config: { image: "ghcr.io/acme/app" },
                        release_channels: [{ release_channel_key: "stable", name: "stable", type: "release", enabled: true }],
                        source_rank: 0,
                    }),
                ],
            }),
            createTracker({
                name: "disabled-channel",
                sources: [
                    createTrackerSource({
                        id: 2,
                        source_key: "image",
                        source_type: "container",
                        enabled: true,
                        source_config: { image: "ghcr.io/acme/app" },
                        release_channels: [{ release_channel_key: "stable", name: "stable", type: "release", enabled: false }],
                        source_rank: 0,
                    }),
                ],
            }),
            createTracker({
                name: "wrong-source-type",
                sources: [
                    createTrackerSource({
                        id: 3,
                        source_key: "github",
                        source_type: "github",
                        enabled: true,
                        source_config: {},
                        release_channels: [{ release_channel_key: "stable", name: "stable", type: "release", enabled: true }],
                        source_rank: 0,
                    }),
                ],
            }),
        ]

        expect(filterTrackersWithBindableSources(trackers).map((tracker) => tracker.name)).toEqual(["bindable"])
    })

    it("treats live container source types as bindable for executor binding", () => {
        const liveContainerSource = createTrackerSource({
            id: 7,
            source_key: "live-container",
            source_type: "container",
            enabled: true,
            source_config: { image: "ghcr.io/acme/live" },
            release_channels: [{ release_channel_key: "stable", name: "stable", type: "release", enabled: true }],
            source_rank: 0,
        })

        expect(isBindableTrackerSource(liveContainerSource)).toBe(true)
        expect(filterTrackersWithBindableSources([
            createTracker({
                name: "live-container-tracker",
                sources: [liveContainerSource],
            }),
        ]).map((tracker) => tracker.name)).toEqual(["live-container-tracker"])
    })

    it("resolves grouped service binding rows against live container tracker sources", () => {
        const tracker = createTracker({
            name: "live-container-tracker",
            sources: [
                createTrackerSource({
                    id: 7,
                    source_key: "live-container",
                    source_type: "container",
                    enabled: true,
                    source_config: { image: "ghcr.io/acme/live" },
                    release_channels: [{ release_channel_key: "stable", name: "stable", type: "release", enabled: true }],
                    source_rank: 0,
                }),
            ],
        })

        const resolved = resolveExecutorServiceBinding(createServiceBinding({
            tracker_name: "live-container-tracker",
            tracker_source_id: "7",
            channel_name: "stable",
        }), [tracker])

        expect(resolved.selectedTracker?.name).toBe("live-container-tracker")
        expect(resolved.selectedTrackerBindableSources).toHaveLength(1)
        expect(resolved.selectedBindableSource?.source_type).toBe("container")
        expect(resolved.trackerSourceOptions.map((source) => source.id)).toEqual([7])
        expect(resolved.scopedReleaseChannels.map((channel) => channel.name)).toEqual(["stable"])
    })

    it("keeps target validation separate from binding validation", () => {
        const t = ((key: string) => key) as unknown as TFunction
        const values = createValues({ tracker_name: "", tracker_source_id: "", channel_name: "" })

        const targetMessage = getExecutorTargetValidationMessage({
            values,
            t,
            selectedRuntimeConnection: createRuntimeConnection({ id: 1, type: "docker", enabled: true }),
            selectedTargetRef: { container_name: "app", container_id: "abc" },
        })

        const bindingMessage = getExecutorBindingValidationMessage({
            values,
            t,
            selectedRuntimeConnection: createRuntimeConnection({ id: 1, type: "docker", enabled: true }),
            isContainerRuntime: true,
            selectedTracker: null,
            selectedTrackerBindableSources: [],
            trackerSourceId: "",
            selectedBindableSource: null,
        })

        expect(targetMessage).toBeNull()
        expect(bindingMessage).toBe("executors.validation.trackerRequired")
    })

    it("uses the reordered target-binding-policy-review step sequence", () => {
        expect(STEP_ORDER).toEqual(["target", "binding", "policy", "review"])
    })

    it("formats standalone container targets with stable identity", () => {
        const label = formatTargetRef("docker", {
            mode: "container",
            container_name: "release-api",
            container_id: "abc123",
        })

        expect(label).toBe("release-api / abc123")
    })

    it("formats portainer stack targets with stable authority fields", () => {
        const label = formatTargetRef("portainer", {
            mode: "portainer_stack",
            endpoint_id: 2,
            stack_id: 11,
            stack_name: "release-stack",
            stack_type: "standalone",
            entrypoint: "stack.yml",
            project_path: "/data/stacks/11",
        })

        expect(label).toBe("release-stack #11 / endpoint:2")
    })

    it("builds portainer stack display details for review and list views", () => {
        const t = ((key: string) => key) as unknown as TFunction
        const display = buildExecutorTargetDisplay("portainer", {
            mode: "portainer_stack",
            endpoint_id: 2,
            stack_id: 11,
            stack_name: "release-stack",
            stack_type: "standalone",
            entrypoint: "stack.yml",
            project_path: "/data/stacks/11",
        }, t)

        expect(display.kind).toBe("portainer_stack")
        expect(display.title).toBe("release-stack")
        expect(display.summary).toBe("release-stack #11")
        expect(display.badges).toEqual(["portainer", "Portainer stack"])
        expect(display.details).toEqual([
            { label: "executors.target.details.endpointId", value: "2" },
            { label: "executors.target.details.stackId", value: "11" },
            { label: "executors.target.details.stackName", value: "release-stack" },
            { label: "executors.target.details.stackType", value: "standalone" },
            { label: "executors.target.details.services", value: "-" },
            { label: "executors.target.details.serviceCount", value: "-" },
            { label: "executors.target.details.entrypoint", value: "stack.yml" },
            { label: "executors.target.details.projectPath", value: "/data/stacks/11" },
        ])
    })

    it("labels compose targets according to the selected container runtime", () => {
        const t = ((key: string) => {
            if (key === "executors.target.kind.docker_compose") {
                return "Docker Compose project"
            }
            if (key === "executors.target.kind.podman_compose") {
                return "Podman Compose project"
            }
            return key
        }) as unknown as TFunction

        const targetRef = {
            mode: "docker_compose" as const,
            project: "release-stack",
            services: [{ service: "api", image: "ghcr.io/acme/api:1.0", replica_count: 1 }],
            service_count: 1,
        }

        expect(buildExecutorTargetDisplay("docker", targetRef, t).badges).toEqual(["docker", "Docker Compose project"])
        expect(buildExecutorTargetDisplay("podman", targetRef, t).badges).toEqual(["podman", "Podman Compose project"])
    })

    it("builds kubernetes workload display details as a grouped target", () => {
        const t = ((key: string, options?: { count?: number }) => {
            if (key === "executors.target.kind.kubernetes_workload") {
                return "Kubernetes workload"
            }
            if (key === "executors.target.serviceCountSummary") {
                return `${options?.count ?? 0} services`
            }
            return key
        }) as unknown as TFunction

        const display = buildExecutorTargetDisplay("kubernetes", {
            mode: "kubernetes_workload",
            namespace: "apps",
            kind: "Deployment",
            name: "worker",
            services: [
                { service: "worker", image: "ghcr.io/acme/worker:1.0" },
                { service: "sidecar", image: "ghcr.io/acme/sidecar:1.0" },
            ],
            service_count: 2,
        }, t)

        expect(display.kind).toBe("kubernetes_workload")
        expect(display.title).toBe("worker")
        expect(display.summary).toBe("apps / Deployment / worker / 2 services")
        expect(display.groupedServices).toEqual([
            { service: "worker", image: "ghcr.io/acme/worker:1.0" },
            { service: "sidecar", image: "ghcr.io/acme/sidecar:1.0" },
        ])
        expect(formatTargetRef("kubernetes", {
            mode: "kubernetes_workload",
            namespace: "apps",
            kind: "Deployment",
            name: "worker",
        })).toBe("apps / Deployment / worker")
    })

    it("builds helm release display details as a single target", () => {
        const t = ((key: string) => {
            if (key === "executors.target.kind.helm_release") {
                return "Helm release"
            }
            return key
        }) as unknown as TFunction
        const targetRef = {
            mode: "helm_release" as const,
            namespace: "apps",
            release_name: "certd",
            chart_name: "certd-chart",
            chart_version: "0.8.0",
            app_version: "2.0.0",
            workloads: [
                { kind: "Deployment", name: "certd-api" },
                { kind: "StatefulSet", name: "certd-db" },
            ],
            service_count: 2,
        }

        const display = buildExecutorTargetDisplay("kubernetes", targetRef, t)

        expect(display.kind).toBe("helm_release")
        expect(display.title).toBe("certd")
        expect(display.summary).toBe("apps / certd / certd-chart")
        expect(display.badges).toEqual(["kubernetes", "Helm release"])
        expect(display.groupedServices).toBeUndefined()
        expect(display.cardDetails).toEqual([
            { label: "executors.target.details.namespace", value: "apps" },
            { label: "executors.target.details.releaseName", value: "certd" },
            { label: "executors.target.details.chartName", value: "certd-chart" },
            { label: "executors.target.details.chartVersion", value: "0.8.0" },
            { label: "executors.target.details.appVersion", value: "2.0.0" },
        ])
        expect(display.details).toEqual([
            ...(display.cardDetails ?? []),
            { label: "executors.target.details.workloads", value: "Deployment/certd-api, StatefulSet/certd-db" },
            { label: "executors.target.details.workloadCount", value: "2" },
        ])
        expect(formatTargetRef("kubernetes", targetRef)).toBe("apps / certd / certd-chart")
        expect(hasContainerRuntimeTarget(targetRef)).toBe(true)
    })

    it("treats standalone container targets as valid container runtime targets", () => {
        expect(
            hasContainerRuntimeTarget({
                mode: "container",
                container_name: "release-api",
                container_id: "abc123",
            }),
        ).toBe(true)
    })

    it("returns container-target validation when a docker executor has no discovered container target", () => {
        const t = ((key: string) => key) as unknown as TFunction

        const message = getExecutorValidationMessage({
            values: createValues({ channel_name: "stable", tracker_source_id: "1" }),
            t,
            selectedRuntimeConnection: createRuntimeConnection({ id: 1, type: "docker", enabled: true }),
            isContainerRuntime: true,
            selectedTracker: createTracker({
                sources: [
                    createTrackerSource({
                        id: 1,
                        source_key: "image",
                        source_type: "container",
                        enabled: true,
                        source_config: { image: "ghcr.io/acme/app" },
                        release_channels: [{ release_channel_key: "stable", name: "stable", type: "release", enabled: true }],
                        source_rank: 0,
                    }),
                ],
            }),
            selectedTrackerBindableSources: [
                createTrackerSource({
                    id: 1,
                    source_key: "image",
                    source_type: "container",
                    enabled: true,
                    source_config: { image: "ghcr.io/acme/app" },
                    release_channels: [{ release_channel_key: "stable", name: "stable", type: "release", enabled: true }],
                    source_rank: 0,
                }),
            ],
            trackerSourceId: "1",
            selectedBindableSource: createTrackerSource({
                id: 1,
                source_key: "image",
                source_type: "container",
                enabled: true,
                source_config: { image: "ghcr.io/acme/app" },
                release_channels: [{ release_channel_key: "stable", name: "stable", type: "release", enabled: true }],
                source_rank: 0,
            }),
            selectedTargetRef: {},
        })

        expect(message).toBe("executors.validation.containerTargetRequired")
    })

    it("returns validation message when portainer runtime target is not a portainer stack", () => {
        const t = ((key: string) => key) as unknown as TFunction

        const message = getExecutorTargetValidationMessage({
            values: createValues({ runtime_type: "portainer" }),
            t,
            selectedRuntimeConnection: createRuntimeConnection({ id: 1, type: "portainer", enabled: true }),
            selectedTargetRef: { mode: "container", container_name: "sample-web" },
        })

        expect(message).toBe("executors.validation.portainerStackTargetRequired")
    })

    it("returns validation message when portainer stack is missing canonical identity", () => {
        const t = ((key: string) => key) as unknown as TFunction

        const message = getExecutorTargetValidationMessage({
            values: createValues({ runtime_type: "portainer" }),
            t,
            selectedRuntimeConnection: createRuntimeConnection({ id: 1, type: "portainer", enabled: true }),
            selectedTargetRef: {
                mode: "portainer_stack",
                endpoint_id: 2,
                stack_id: 11,
                stack_name: "",
                stack_type: "standalone",
            },
        })

        expect(message).toBe("executors.validation.portainerStackTargetIncomplete")
    })

    it("rejects duplicate grouped service bindings in one executor", () => {
        const t = ((key: string) => key) as unknown as TFunction
        const tracker = createTracker({
            name: "tracker",
            sources: [
                createTrackerSource({
                    id: 1,
                    source_key: "image",
                    source_type: "container",
                    enabled: true,
                    source_config: { image: "ghcr.io/acme/app" },
                    release_channels: [{ release_channel_key: "stable", name: "stable", type: "release", enabled: true }],
                    source_rank: 0,
                }),
            ],
        })

        const message = getExecutorServiceBindingValidationMessage({
            bindings: [
                createServiceBinding({ service: "api" }),
                createServiceBinding({ service: "API" }),
            ],
            trackers: [tracker],
            t,
        })

        expect(message).toBe("executors.validation.duplicateServiceBinding")
    })

    it("preserves hidden health-check probe config when editing basic fields", () => {
        const payload = buildExecutorPayload({
            values: createValues({
                health_check_strategy: "manual_http",
                health_check_failure_policy: "mark_failed_and_recover",
                health_check_grace_period_seconds: "5",
                health_check_attempt_timeout_seconds: "2",
                health_check_interval_seconds: "3",
                health_check_probe_window_seconds: "30",
                health_check_http_host: "api.internal",
                health_check_http_path: "/health",
                health_check_http_port: "8080",
                health_check_http_expected_status_codes: "204",
            }),
            effectiveTrackerSourceId: "1",
            selectedTargetRef: { mode: "container", container_name: "api" },
            existingHealthCheck: {
                strategy: "manual_http",
                use_default_strategy: false,
                failure_policy: "mark_failed",
                grace_period_seconds: 0,
                attempt_timeout_seconds: 1,
                interval_seconds: 1,
                probe_window_seconds: 10,
                services: ["api"],
                http: { host: "api.internal", path: "/health", port: 8080, expected_status_codes: [204] },
                tcp: null,
            },
        })

        expect(payload.health_check).toMatchObject({
            strategy: "manual_http",
            failure_policy: "mark_failed_and_recover",
            grace_period_seconds: 5,
            attempt_timeout_seconds: 2,
            interval_seconds: 3,
            probe_window_seconds: 30,
            services: ["api"],
            http: { host: "api.internal", path: "/health", port: 8080, expected_status_codes: [204] },
            tcp: null,
        })
    })

    it("clears hidden health-check probe config when disabling health checks", () => {
        const payload = buildExecutorPayload({
            values: createValues({
                health_check_strategy: "none",
                health_check_failure_policy: "mark_failed_and_recover",
            }),
            effectiveTrackerSourceId: "1",
            selectedTargetRef: { mode: "container", container_name: "api" },
            existingHealthCheck: {
                strategy: "manual_tcp",
                use_default_strategy: false,
                failure_policy: "mark_failed_and_recover",
                grace_period_seconds: 5,
                attempt_timeout_seconds: 2,
                interval_seconds: 3,
                probe_window_seconds: 30,
                services: ["api"],
                http: null,
                tcp: { host: "api.internal", port: 8080 },
            },
        })

        expect(payload.health_check).toMatchObject({
            strategy: "none",
            failure_policy: "mark_failed",
            services: null,
            http: null,
            tcp: null,
        })
    })

    it("builds portainer stack executor payloads with child service bindings", () => {
        const trackerA = createTracker({
            name: "tracker-a",
            sources: [
                createTrackerSource({
                    id: 11,
                    source_key: "image-a",
                    source_type: "container",
                    enabled: true,
                    source_config: { image: "ghcr.io/acme/api" },
                    release_channels: [{ release_channel_key: "stable", name: "stable", type: "release", enabled: true }],
                    source_rank: 0,
                }),
            ],
        })
        const trackerB = createTracker({
            name: "tracker-b",
            sources: [
                createTrackerSource({
                    id: 22,
                    source_key: "image-b",
                    source_type: "container",
                    enabled: true,
                    source_config: { image: "ghcr.io/acme/worker" },
                    release_channels: [{ release_channel_key: "canary", name: "canary", type: "prerelease", enabled: true }],
                    source_rank: 0,
                }),
            ],
        })

        const payload = buildExecutorPayload({
            values: createValues({ runtime_type: "portainer", tracker_name: "", tracker_source_id: "", channel_name: "", image_reference_mode: "tag" }),
            effectiveTrackerSourceId: "",
            selectedTargetRef: {
                mode: "portainer_stack",
                endpoint_id: 2,
                stack_id: 11,
                stack_name: "release-stack",
                stack_type: "standalone",
            },
            serviceBindings: [
                createServiceBinding({ service: "api", tracker_name: "tracker-a", tracker_source_id: "11", channel_name: "stable" }),
                createServiceBinding({ service: "worker", tracker_name: "tracker-b", tracker_source_id: "22", channel_name: "canary" }),
            ],
            trackers: [trackerA, trackerB],
        })

        expect(payload.tracker_name).toBe("tracker-a")
        expect(payload.image_reference_mode).toBe("tag")
        expect(payload.tracker_source_id).toBe(11)
        expect(payload.channel_name).toBe("stable")
        expect(payload.service_bindings).toEqual([
            { service: "api", tracker_source_id: 11, channel_name: "stable" },
            { service: "worker", tracker_source_id: 22, channel_name: "canary" },
        ])
    })

    it("serializes the resolved source id for grouped bindings with a single bindable source", () => {
        const tracker = createTracker({
            name: "tracker",
            sources: [
                createTrackerSource({
                    id: 77,
                    source_key: "image",
                    source_type: "container",
                    enabled: true,
                    source_config: { image: "ghcr.io/acme/app" },
                    release_channels: [{ release_channel_key: "stable", name: "stable", type: "release", enabled: true }],
                    source_rank: 0,
                }),
            ],
        })

        const payload = buildExecutorPayload({
            values: createValues({ runtime_type: "portainer", tracker_name: "", tracker_source_id: "", channel_name: "" }),
            effectiveTrackerSourceId: "",
            selectedTargetRef: {
                mode: "portainer_stack",
                endpoint_id: 2,
                stack_id: 11,
                stack_name: "release-stack",
                stack_type: "standalone",
            },
            serviceBindings: [
                createServiceBinding({ service: "api", tracker_name: "tracker", tracker_source_id: "", channel_name: "stable" }),
            ],
            trackers: [tracker],
        })

        expect(payload.tracker_source_id).toBe(77)
        expect(payload.service_bindings).toEqual([
            { service: "api", tracker_source_id: 77, channel_name: "stable" },
        ])
    })

    it("preserves explicit grouped-binding source selection when multiple bindable sources exist", () => {
        const tracker = createTracker({
            name: "tracker",
            sources: [
                createTrackerSource({
                    id: 11,
                    source_key: "stable-image",
                    source_type: "container",
                    enabled: true,
                    source_config: { image: "ghcr.io/acme/app" },
                    release_channels: [{ release_channel_key: "stable", name: "stable", type: "release", enabled: true }],
                    source_rank: 0,
                }),
                createTrackerSource({
                    id: 22,
                    source_key: "canary-image",
                    source_type: "container",
                    enabled: true,
                    source_config: { image: "ghcr.io/acme/app-canary" },
                    release_channels: [{ release_channel_key: "canary", name: "canary", type: "prerelease", enabled: true }],
                    source_rank: 1,
                }),
            ],
        })

        const payload = buildExecutorPayload({
            values: createValues({ runtime_type: "portainer", tracker_name: "", tracker_source_id: "", channel_name: "" }),
            effectiveTrackerSourceId: "",
            selectedTargetRef: {
                mode: "portainer_stack",
                endpoint_id: 2,
                stack_id: 11,
                stack_name: "release-stack",
                stack_type: "standalone",
            },
            serviceBindings: [
                createServiceBinding({ service: "api", tracker_name: "tracker", tracker_source_id: "22", channel_name: "canary" }),
            ],
            trackers: [tracker],
        })

        expect(payload.tracker_source_id).toBe(22)
        expect(payload.service_bindings).toEqual([
            { service: "api", tracker_source_id: 22, channel_name: "canary" },
        ])
    })

    it("treats portainer stack targets as grouped service-binding executors", () => {
        expect(usesGroupedServiceBindings({
            mode: "portainer_stack",
            endpoint_id: 2,
            stack_id: 11,
            stack_name: "release-stack",
            stack_type: "standalone",
        })).toBe(true)
    })

    it("treats kubernetes workload targets as grouped service-binding executors", () => {
        expect(usesGroupedServiceBindings({
            mode: "kubernetes_workload",
            namespace: "apps",
            kind: "Deployment",
            name: "worker",
        })).toBe(true)
    })

    it("treats helm release targets as single-binding executors", () => {
        expect(usesGroupedServiceBindings({
            mode: "helm_release",
            namespace: "apps",
            release_name: "certd",
            chart_name: "certd-chart",
        })).toBe(false)
    })

    it("filters helm release binding tracker options to helm sources", () => {
        const helmTargetRef = {
            mode: "helm_release" as const,
            namespace: "apps",
            release_name: "certd",
            chart_name: "certd-chart",
        }
        const trackers = [
            createTracker({
                name: "docker-tracker",
                sources: [
                    createTrackerSource({
                        id: 1,
                        source_key: "image",
                        source_type: "container",
                        enabled: true,
                        source_config: { image: "ghcr.io/acme/certd" },
                        release_channels: [{ release_channel_key: "stable", name: "stable", type: "release", enabled: true }],
                    }),
                ],
            }),
            createTracker({
                name: "helm-tracker",
                sources: [
                    createTrackerSource({
                        id: 2,
                        source_key: "chart",
                        source_type: "helm",
                        channel_type: "helm",
                        enabled: true,
                        source_config: { repo: "https://charts.example", chart: "certd-chart" },
                        channel_config: { chart: "certd-chart" },
                        release_channels: [{ release_channel_key: "stable", name: "stable", type: "release", enabled: true }],
                    }),
                ],
            }),
        ]

        expect(isBindableTrackerSource(trackers[1].sources[0])).toBe(true)
        expect(filterTrackersWithBindableSources(trackers, helmTargetRef).map((tracker) => tracker.name)).toEqual(["helm-tracker"])
        expect(filterTrackersWithBindableSources(trackers, {
            mode: "kubernetes_workload",
            namespace: "apps",
            kind: "Deployment",
            name: "certd",
        }).map((tracker) => tracker.name)).toEqual(["docker-tracker"])
    })

    it("derives unique grouped binding services from portainer stack metadata", () => {
        expect(getGroupedBindingServiceOptions({
            mode: "portainer_stack",
            endpoint_id: 2,
            stack_id: 11,
            stack_name: "release-stack",
            stack_type: "standalone",
            services: [
                { service: "api", image: "ghcr.io/acme/api:1.0" },
                { service: "worker", image: "ghcr.io/acme/worker:1.0" },
                { service: "API", image: "ghcr.io/acme/api:1.1" },
            ],
        })).toEqual([
            { service: "api", image: "ghcr.io/acme/api:1.0" },
            { service: "worker", image: "ghcr.io/acme/worker:1.0" },
        ])
    })

    it("derives unique grouped binding services from kubernetes workload containers", () => {
        expect(getGroupedBindingServiceOptions({
            mode: "kubernetes_workload",
            namespace: "apps",
            kind: "Deployment",
            name: "worker",
            services: [
                { service: "worker", image: "ghcr.io/acme/worker:1.0" },
                { service: "sidecar", image: "ghcr.io/acme/sidecar:1.0" },
                { service: "WORKER", image: "ghcr.io/acme/worker:1.1" },
            ],
        })).toEqual([
            { service: "worker", image: "ghcr.io/acme/worker:1.0" },
            { service: "sidecar", image: "ghcr.io/acme/sidecar:1.0" },
        ])
    })

    it("matches portainer stack targets by canonical stack identity even when discovery metadata changes", () => {
        expect(isEquivalentPortainerStackTarget(
            {
                mode: "portainer_stack",
                endpoint_id: 2,
                stack_id: 11,
                stack_name: "release-stack",
                stack_type: "standalone",
            },
            {
                mode: "portainer_stack",
                endpoint_id: 2,
                stack_id: 11,
                stack_name: "release-stack",
                stack_type: "Standalone",
                services: [{ service: "api", image: "ghcr.io/acme/api:1.0" }],
                service_count: 1,
            },
        )).toBe(true)
    })

    it("matches compose targets by project identity even when discovery metadata changes", () => {
        expect(isEquivalentDockerComposeTarget(
            {
                mode: "docker_compose",
                project: "sample-worker",
                working_dir: "/data/podman/sample-worker",
                config_files: ["podman-compose.yaml"],
                services: [
                    { service: "sample-worker", image: "docker.io/example/worker-agent:latest", replica_count: 1 },
                ],
                service_count: 1,
            },
            {
                mode: "docker_compose",
                project: "sample-worker",
                working_dir: "/data/podman/sample-worker",
                config_files: ["compose.yaml", "compose.override.yaml"],
                services: [
                    { service: "sample-worker", image: "docker.io/example/worker-agent:trixie", replica_count: 2 },
                    { service: "sidecar", image: "ghcr.io/acme/sidecar:1.0", replica_count: 1 },
                ],
                service_count: 2,
            },
        )).toBe(true)
    })

    it("does not match compose targets with the same project name from different known working directories", () => {
        expect(isEquivalentDockerComposeTarget(
            {
                mode: "docker_compose",
                project: "release-stack",
                working_dir: "/srv/prod/release-stack",
            },
            {
                mode: "docker_compose",
                project: "release-stack",
                working_dir: "/srv/staging/release-stack",
            },
        )).toBe(false)
    })

    it("preserves compose working directory and config files when sample-cachecovery omits stable metadata", () => {
        expect(mergeDockerComposeTargetRef(
            {
                mode: "docker_compose",
                project: "sample-worker",
                services: [
                    { service: "sample-worker", image: "docker.io/example/worker-agent:trixie", replica_count: 2 },
                ],
                service_count: 1,
            },
            {
                mode: "docker_compose",
                project: "sample-worker",
                working_dir: "/data/podman/sample-worker",
                config_files: ["podman-compose.yaml"],
                services: [
                    { service: "sample-worker", image: "docker.io/example/worker-agent:latest", replica_count: 1 },
                ],
                service_count: 1,
            },
        )).toEqual({
            mode: "docker_compose",
            project: "sample-worker",
            working_dir: "/data/podman/sample-worker",
            config_files: ["podman-compose.yaml"],
            services: [
                { service: "sample-worker", image: "docker.io/example/worker-agent:trixie", replica_count: 2 },
            ],
            service_count: 1,
        })
    })

    it("keeps sample-cachecovered compose working directory and config files when they are present", () => {
        expect(mergeDockerComposeTargetRef(
            {
                mode: "docker_compose",
                project: "sample-worker",
                working_dir: "/data/podman/sample-worker-next",
                config_files: ["compose.yaml"],
                services: [
                    { service: "sample-worker", image: "docker.io/example/worker-agent:trixie", replica_count: 2 },
                ],
                service_count: 1,
            },
            {
                mode: "docker_compose",
                project: "sample-worker",
                working_dir: "/data/podman/sample-worker",
                config_files: ["podman-compose.yaml"],
            },
        )).toEqual({
            mode: "docker_compose",
            project: "sample-worker",
            working_dir: "/data/podman/sample-worker-next",
            config_files: ["compose.yaml"],
            services: [
                { service: "sample-worker", image: "docker.io/example/worker-agent:trixie", replica_count: 2 },
            ],
            service_count: 1,
        })
    })

    it("builds portainer stack executor payloads with grouped service bindings", () => {
        const trackerA = createTracker({
            name: "tracker-a",
            sources: [
                createTrackerSource({
                    id: 11,
                    source_key: "image-a",
                    source_type: "container",
                    enabled: true,
                    source_config: { image: "ghcr.io/acme/api" },
                    release_channels: [{ release_channel_key: "stable", name: "stable", type: "release", enabled: true }],
                    source_rank: 0,
                }),
            ],
        })
        const trackerB = createTracker({
            name: "tracker-b",
            sources: [
                createTrackerSource({
                    id: 22,
                    source_key: "image-b",
                    source_type: "container",
                    enabled: true,
                    source_config: { image: "ghcr.io/acme/worker" },
                    release_channels: [{ release_channel_key: "canary", name: "canary", type: "prerelease", enabled: true }],
                    source_rank: 0,
                }),
            ],
        })

        const payload = buildExecutorPayload({
            values: createValues({ runtime_type: "portainer", tracker_name: "", tracker_source_id: "", channel_name: "" }),
            effectiveTrackerSourceId: "",
            selectedTargetRef: {
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
            },
            serviceBindings: [
                createServiceBinding({ service: "api", tracker_name: "tracker-a", tracker_source_id: "11", channel_name: "stable" }),
                createServiceBinding({ service: "worker", tracker_name: "tracker-b", tracker_source_id: "22", channel_name: "canary" }),
            ],
            trackers: [trackerA, trackerB],
        })

        expect(payload.tracker_name).toBe("tracker-a")
        expect(payload.tracker_source_id).toBe(11)
        expect(payload.channel_name).toBe("stable")
        expect(payload.service_bindings).toEqual([
            { service: "api", tracker_source_id: 11, channel_name: "stable" },
            { service: "worker", tracker_source_id: 22, channel_name: "canary" },
        ])
    })

    it("builds helm release review items without image-only strategy fields", () => {
        const t = ((key: string) => key) as unknown as TFunction

        const reviewItems = buildExecutorReviewItems({
            values: createValues({
                runtime_type: "kubernetes",
                tracker_name: "certd-chart",
                tracker_source_id: "2",
                channel_name: "stable",
                update_mode: "maintenance_window",
            }),
            t,
            selectedRuntimeConnection: createRuntimeConnection({ id: 7, name: "k3s", type: "kubernetes" }),
            selectedBindableSource: createTrackerSource({ id: 2, source_key: "chart", source_type: "helm" }),
            selectedTargetRef: {
                mode: "helm_release",
                namespace: "apps",
                release_name: "certd",
                chart_name: "certd-chart",
            },
        })

        expect(reviewItems).toContainEqual({ label: "executors.review.mode", value: "executors.modes.maintenance_window 02:00 - 05:00" })
        expect(reviewItems.map((item) => item.label)).not.toContain("executors.review.imageStrategy")
        expect(reviewItems.map((item) => item.label)).not.toContain("executors.review.imageReference")
    })

    it("builds helm release executor payloads without service bindings", () => {
        const targetRef = {
            mode: "helm_release" as const,
            namespace: "apps",
            release_name: "certd",
            chart_name: "certd-chart",
        }

        const payload = buildExecutorPayload({
            values: createValues({
                runtime_type: "kubernetes",
                runtime_connection_id: "7",
                tracker_name: "certd-chart",
                tracker_source_id: "2",
                channel_name: "stable",
                image_selection_mode: "use_tracker_image_and_tag",
                image_reference_mode: "tag",
            }),
            effectiveTrackerSourceId: "2",
            selectedTargetRef: targetRef,
            serviceBindings: [createServiceBinding({ service: "api", tracker_source_id: "9" })],
            trackers: [],
        })

        expect(payload.runtime_type).toBe("kubernetes")
        expect(payload.runtime_connection_id).toBe(7)
        expect(payload.tracker_name).toBe("certd-chart")
        expect(payload.tracker_source_id).toBe(2)
        expect(payload.channel_name).toBe("stable")
        expect(payload.target_ref).toEqual(targetRef)
        expect(payload.service_bindings).toEqual([])
    })

    it("does not build image review changes for helm release targets", () => {
        const t = ((key: string) => key) as unknown as TFunction
        const targetDisplay = buildExecutorTargetDisplay("kubernetes", {
            mode: "helm_release",
            namespace: "apps",
            release_name: "certd",
            chart_name: "certd-chart",
        }, t)

        expect(buildExecutorReviewImageChanges({
            targetDisplay,
            serviceBindings: [createServiceBinding({ service: "api" })],
            trackers: [createTracker({ sources: [createTrackerSource({})] })],
            imageSelectionMode: "use_tracker_image_and_tag",
        })).toEqual([])
    })

    it("builds review items for portainer stack grouped bindings", () => {
        const t = ((key: string) => key) as unknown as TFunction

        const reviewItems = buildExecutorReviewItems({
            values: createValues({ name: "portainer-executor", runtime_type: "portainer" }),
            t,
            selectedRuntimeConnection: createRuntimeConnection({ id: 3, name: "portainer-prod", type: "portainer", enabled: true }),
            selectedBindableSource: null,
            selectedTargetRef: {
                mode: "portainer_stack",
                endpoint_id: 2,
                stack_id: 11,
                stack_name: "release-stack",
                stack_type: "standalone",
            },
            serviceBindings: [
                createServiceBinding({ service: "api", tracker_name: "tracker-a", tracker_source_id: "11", channel_name: "stable" }),
                createServiceBinding({ service: "worker", tracker_name: "tracker-b", tracker_source_id: "22", channel_name: "canary" }),
            ],
        })

        expect(reviewItems).toContainEqual({
            label: "executors.review.serviceBindings",
            value: "api → tracker-a / stable; worker → tracker-b / canary",
        })
        expect(reviewItems).toContainEqual({
            label: "executors.review.imageReference",
            value: "executors.imageReferenceStrategy.digest",
        })
    })

    it("builds actual target image changes from stored binding versions", () => {
        const t = ((key: string, options?: Record<string, unknown>) => options?.count ? `${options.count} ${key}` : key) as unknown as TFunction
        const targetDisplay = buildExecutorTargetDisplay("portainer", {
            mode: "portainer_stack",
            endpoint_id: 2,
            stack_id: 11,
            stack_name: "release-stack",
            stack_type: "standalone",
            services: [{ service: "api", image: "ghcr.io/acme/current-api:1.0" }],
            service_count: 1,
        }, t)
        const tracker = createTracker({
            name: "tracker",
            status: {
                last_check: null,
                last_version: "2.0.0",
                error: null,
                source_count: 1,
                enabled_source_count: 1,
                source_types: ["container"],
            },
            sources: [
                createTrackerSource({
                    id: 9,
                    source_config: { image: "ghcr.io/acme/tracker-api" },
                    release_channels: [{ release_channel_key: "stable", name: "stable", type: "release", enabled: true }],
                }),
            ],
        })

        const changes = buildExecutorReviewImageChanges({
            targetDisplay,
            serviceBindings: [createServiceBinding({ service: "api", tracker_source_id: "9", channel_name: "stable" })],
            trackers: [tracker],
            imageSelectionMode: "use_tracker_image_and_tag",
        })

        expect(changes).toEqual([
            {
                service: "api",
                sourceImage: "ghcr.io/acme/current-api:1.0",
                targetImage: "ghcr.io/acme/tracker-api:2.0.0",
                targetVersion: "2.0.0",
            },
        ])
    })

    it("keeps the current image name when replacing only the tag", () => {
        expect(buildExecutorImageTargetValue("registry.local:5000/acme/api@sha256:abc", "2.1.0")).toBe("registry.local:5000/acme/api:2.1.0")
        expect(buildExecutorImageTargetValue("registry.local:5000/acme/api:1.0", "2.1.0")).toBe("registry.local:5000/acme/api:2.1.0")
    })
})
