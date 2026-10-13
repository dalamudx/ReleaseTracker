import type { ExecutorListItem, RuntimeConnection } from "@/api/types"

/** A fresh connection for each test; callers override only the fields they exercise. */
export function createRuntimeConnection(overrides: Partial<RuntimeConnection> = {}): RuntimeConnection {
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

export function createExecutor(overrides: Partial<ExecutorListItem> = {}): ExecutorListItem {
  return {
    id: overrides.id ?? 1,
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
    status: overrides.status,
  }
}
