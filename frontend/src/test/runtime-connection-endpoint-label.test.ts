import { describe, expect, it } from "vitest"

import type { RuntimeConnection } from "@/api/types"
import { buildConnectionLabel, buildConnectionSummary } from "@/components/runtime-connections/runtimeConnectionHelpers"

function buildRuntimeConnection(overrides: Partial<RuntimeConnection>): RuntimeConnection {
    return {
        id: overrides.id ?? 1,
        name: overrides.name ?? "runtime",
        type: overrides.type ?? "kubernetes",
        enabled: overrides.enabled ?? true,
        config: overrides.config ?? {},
        secrets: overrides.secrets ?? {},
        endpoint: overrides.endpoint ?? null,
        description: overrides.description ?? null,
    }
}

describe("buildConnectionLabel", () => {
    it("prefers kubeconfig server endpoint over namespace/context for kubernetes runtimes", () => {
        const runtimeConnection = buildRuntimeConnection({
            type: "kubernetes",
            endpoint: "https://10.43.0.1:6443",
            config: { context: "production", namespace: "apps" },
        })

        expect(buildConnectionLabel(runtimeConnection)).toBe("https://10.43.0.1:6443")
    })

    it("falls back to in-cluster when no endpoint is available", () => {
        const runtimeConnection = buildRuntimeConnection({
            type: "kubernetes",
            endpoint: null,
            config: { in_cluster: true, namespace: "apps" },
        })

        expect(buildConnectionLabel(runtimeConnection)).toBe("in-cluster")
    })

    it("keeps non-kubernetes endpoint behavior unchanged", () => {
        const runtimeConnection = buildRuntimeConnection({
            type: "docker",
            config: { socket: "unix:///var/run/docker.sock", api_version: "v1.41" },
        })

        expect(buildConnectionLabel(runtimeConnection)).toBe("unix:///var/run/docker.sock / v1.41")
    })

    it("shows base url and endpoint id for portainer runtimes", () => {
        const runtimeConnection = buildRuntimeConnection({
            type: "portainer",
            config: { base_url: "https://portainer.example.com", endpoint_id: 3 },
        })

        expect(buildConnectionLabel(runtimeConnection)).toBe("https://portainer.example.com / endpoint 3")
        expect(buildConnectionSummary(runtimeConnection)).toEqual({
            primary: "https://portainer.example.com",
            secondary: "Endpoint 3",
        })
    })
})
