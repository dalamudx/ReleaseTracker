import { describe, expect, it } from "vitest"

import type { RuntimeConnection } from "@/api/types"
import {
  buildExecutorTargetDiscoveryParams,
  getConfiguredKubernetesNamespaces,
} from "@/components/executors/executorSheetHelpers"

function createRuntimeConnection(overrides: Partial<RuntimeConnection> = {}): RuntimeConnection {
  return {
    id: overrides.id ?? 1,
    name: overrides.name ?? "k8s-prod",
    type: overrides.type ?? "kubernetes",
    enabled: overrides.enabled ?? true,
    config: overrides.config ?? { namespaces: ["apps"] },
    secrets: overrides.secrets ?? {},
    endpoint: overrides.endpoint ?? null,
    description: overrides.description ?? null,
  }
}

describe("ExecutorSheet Kubernetes discovery helpers", () => {
  it("uses only configured Kubernetes namespaces without default fallback", () => {
    expect(getConfiguredKubernetesNamespaces(createRuntimeConnection())).toEqual(["apps"])
    expect(
      getConfiguredKubernetesNamespaces(
        createRuntimeConnection({ config: { namespaces: [" apps ", "monitoring", "apps"] } }),
      ),
    ).toEqual(["apps", "monitoring"])
    expect(
      getConfiguredKubernetesNamespaces(createRuntimeConnection({ config: { namespace: " prod " } })),
    ).toEqual(["prod"])
    expect(getConfiguredKubernetesNamespaces(createRuntimeConnection({ config: {} }))).toEqual([])
  })

  it("passes namespace params only for Kubernetes target discovery", () => {
    expect(buildExecutorTargetDiscoveryParams(createRuntimeConnection(), "apps")).toEqual({
      namespace: "apps",
    })
    expect(
      buildExecutorTargetDiscoveryParams(
        createRuntimeConnection({ type: "docker", config: { socket: "unix:///var/run/docker.sock" } }),
        "apps",
      ),
    ).toBeUndefined()
  })
})
