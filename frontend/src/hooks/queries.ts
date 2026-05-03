/**
 * Centralized TanStack React Query hooks
 *
 * Wrap all API requests with useQuery/useMutation to provide:
 * - automatic caching to avoid repeated requests when switching pages
 * - background data revalidation when the window regains focus
 * - centralized loading/error state management
 */

import {
  useQuery,
  useMutation,
  useQueryClient,
  type UseQueryOptions,
} from "@tanstack/react-query"
import { api } from "@/api/client"
import type {
  CreateTrackerRequest,
  UpdateTrackerRequest,
  CreateCredentialRequest,
  UpdateCredentialRequest,
  CreateRuntimeConnectionRequest,
  UpdateRuntimeConnectionRequest,
  CreateExecutorRequest,
  UpdateExecutorRequest,
} from "@/api/types"

// ==================== Query Keys ====================

export const queryKeys = {
  // Dashboard
  stats: ["stats"] as const,
  latestCurrentReleases: ["releases", "latest-current"] as const,

  // Trackers
  trackers: (params?: { skip?: number; limit?: number }) =>
    ["trackers", params] as const,
  tracker: (name: string) => ["trackers", name] as const,
  trackerConfig: (name: string) => ["trackers", name, "config"] as const,
  trackerCurrentView: (name: string) => ["trackers", name, "current-view"] as const,
  trackerReleaseHistory: (
    name: string,
    params?: {
      skip?: number
      limit?: number
      search?: string
      prerelease?: boolean
    }
  ) => ["trackers", name, "release-history", params] as const,

  // Releases
  releaseHistory: (params?: {
    tracker?: string
    skip?: number
    limit?: number
    search?: string
    prerelease?: boolean
  }) => ["releases", "history", params] as const,

  // Credentials
  credentials: (params?: { skip?: number; limit?: number }) =>
    ["credentials", params] as const,

  // Notifiers
  notifiers: (params?: { skip?: number; limit?: number }) =>
    ["notifiers", params] as const,
  notifier: (id: number) => ["notifiers", id] as const,

  // Settings
  settings: ["settings"] as const,
  securityKeys: ["settings", "security-keys"] as const,

  // Runtime Connections
  runtimeConnections: (params?: { skip?: number; limit?: number }) =>
    ["runtime-connections", params] as const,
  runtimeConnection: (id: number) => ["runtime-connections", id] as const,

  // Executors
  executors: (params?: { skip?: number; limit?: number }) =>
    ["executors", params] as const,
  executor: (id: number) => ["executors", id] as const,
  executorHistory: (
    id: number,
    params?: {
      skip?: number
      limit?: number
      status?: "success" | "failed" | "skipped"
      search?: string
    }
  ) => ["executors", id, "history", params] as const,
}

// ==================== Dashboard ====================

export function useStats() {
  return useQuery({
    queryKey: queryKeys.stats,
    queryFn: () => api.getStats(),
    staleTime: 60_000, // Use cached data when switching pages within 1 minute
    refetchOnWindowFocus: true,
  })
}

export function useLatestCurrentReleases() {
  return useQuery({
    queryKey: queryKeys.latestCurrentReleases,
    queryFn: () => api.getLatestCurrentReleases(),
    staleTime: 60_000,
  })
}

// ==================== Trackers ====================

export function useTrackers(params?: { skip?: number; limit?: number }) {
  return useQuery({
    queryKey: queryKeys.trackers(params),
    queryFn: () => api.getTrackers(params),
    staleTime: 30_000,
  })
}

export function useTracker(name: string | null) {
  return useQuery({
    queryKey: queryKeys.tracker(name!),
    queryFn: () => api.getTracker(name!),
    enabled: !!name,
    staleTime: 30_000,
  })
}

export function useTrackerConfig(name: string | null) {
  return useQuery({
    queryKey: queryKeys.trackerConfig(name!),
    queryFn: () => api.getTrackerConfig(name!),
    enabled: !!name,
    staleTime: 30_000,
  })
}

export function useTrackerCurrentView(trackerName: string | null) {
  return useQuery({
    queryKey: queryKeys.trackerCurrentView(trackerName!),
    queryFn: () => api.getTrackerCurrentView(trackerName!),
    enabled: !!trackerName,
    staleTime: 30_000,
  })
}

export function useTrackerReleaseHistory(
  trackerName: string | null,
  params?: {
    skip?: number
    limit?: number
    search?: string
    prerelease?: boolean
  }
) {
  return useQuery({
    queryKey: queryKeys.trackerReleaseHistory(trackerName!, params),
    queryFn: () => api.getTrackerReleaseHistory(trackerName!, params),
    enabled: !!trackerName,
    staleTime: 30_000,
  })
}

export function useCreateTracker() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (data: CreateTrackerRequest) => api.createTracker(data),
    onSuccess: () => {
      // After creation succeeds, invalidate the Trackers list cache to force refetch
      queryClient.invalidateQueries({ queryKey: ["trackers"] })
      queryClient.invalidateQueries({ queryKey: queryKeys.stats })
    },
  })
}

export function useUpdateTracker() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ name, data }: { name: string; data: UpdateTrackerRequest }) =>
      api.updateTracker(name, data),
    onSuccess: (_data, { name }) => {
      queryClient.invalidateQueries({ queryKey: ["trackers"] })
      queryClient.invalidateQueries({ queryKey: queryKeys.tracker(name) })
    },
  })
}

export function useDeleteTracker() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (name: string) => api.deleteTracker(name),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["trackers"] })
      queryClient.invalidateQueries({ queryKey: queryKeys.stats })
    },
  })
}

export function useCheckTracker() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (name: string) => api.checkTracker(name),
    onSuccess: (_data, name) => {
      // Refresh the Trackers list and detail after checks complete
      queryClient.invalidateQueries({ queryKey: ["trackers"] })
      queryClient.invalidateQueries({ queryKey: queryKeys.tracker(name) })
      queryClient.invalidateQueries({ queryKey: queryKeys.trackerCurrentView(name) })
      queryClient.invalidateQueries({ queryKey: ["releases", "history"] })
      queryClient.invalidateQueries({ queryKey: queryKeys.trackerReleaseHistory(name) })
      queryClient.invalidateQueries({ queryKey: queryKeys.latestCurrentReleases })
      queryClient.invalidateQueries({ queryKey: queryKeys.stats })
    },
  })
}

// ==================== Releases ====================

export function useReleaseHistory(params?: {
  tracker?: string
  skip?: number
  limit?: number
  search?: string
  prerelease?: boolean
}) {
  return useQuery({
    queryKey: queryKeys.releaseHistory(params),
    queryFn: () => api.getReleaseHistory(params),
    staleTime: 30_000,
  })
}

// ==================== Credentials ====================

export function useCredentials(params?: { skip?: number; limit?: number }) {
  return useQuery({
    queryKey: queryKeys.credentials(params),
    queryFn: () => api.getCredentials(params),
    staleTime: 60_000,
  })
}

export function useCreateCredential() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (data: CreateCredentialRequest) => api.createCredential(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["credentials"] })
    },
  })
}

export function useUpdateCredential() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ id, data }: { id: number; data: UpdateCredentialRequest }) =>
      api.updateCredential(id, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["credentials"] })
    },
  })
}

export function useDeleteCredential() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (id: number) => api.deleteCredential(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["credentials"] })
    },
  })
}

// ==================== Notifiers ====================

export function useNotifiers(params?: { skip?: number; limit?: number }) {
  return useQuery({
    queryKey: queryKeys.notifiers(params),
    queryFn: () => api.getNotifiers(params),
    staleTime: 60_000,
  })
}

export function useNotifier(id: number) {
  return useQuery({
    queryKey: queryKeys.notifier(id),
    queryFn: () => api.getNotifier(id),
    staleTime: 60_000,
  })
}

export function useCreateNotifier() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (data: Parameters<typeof api.createNotifier>[0]) =>
      api.createNotifier(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["notifiers"] })
    },
  })
}

export function useUpdateNotifier() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({
      id,
      data,
    }: {
      id: number
      data: Parameters<typeof api.updateNotifier>[1]
    }) => api.updateNotifier(id, data),
    onSuccess: (_data, { id }) => {
      queryClient.invalidateQueries({ queryKey: ["notifiers"] })
      queryClient.invalidateQueries({ queryKey: queryKeys.notifier(id) })
    },
  })
}

export function useDeleteNotifier() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (id: number) => api.deleteNotifier(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["notifiers"] })
    },
  })
}

export function useTestNotifier() {
  return useMutation({
    mutationFn: (id: number) => api.testNotifier(id),
  })
}

// ==================== Settings ====================

export function useSettings() {
  return useQuery({
    queryKey: queryKeys.settings,
    queryFn: () => api.getSettings(),
    staleTime: 120_000, // System settings change infrequently, so cache for 2 minutes
  })
}

export function useUpdateSetting() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (data: { key: string; value: unknown }) => api.updateSetting(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.settings })
    },
  })
}

export function useSecurityKeys() {
  return useQuery({
    queryKey: queryKeys.securityKeys,
    queryFn: () => api.getSecurityKeys(),
    staleTime: 30_000,
  })
}

export function useRotateJwtSecret() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: api.rotateJwtSecret,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.securityKeys })
    },
  })
}

export function useRotateEncryptionKey() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: api.rotateEncryptionKey,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.securityKeys })
    },
  })
}

// ==================== Runtime Connections ====================

export function useRuntimeConnections(params?: {
  skip?: number
  limit?: number
}) {
  return useQuery({
    queryKey: queryKeys.runtimeConnections(params),
    queryFn: () => api.getRuntimeConnections(params),
    staleTime: 60_000,
  })
}

export function useRuntimeConnection(id: number | null) {
  return useQuery({
    queryKey: queryKeys.runtimeConnection(id!),
    queryFn: () => api.getRuntimeConnection(id!),
    enabled: id !== null,
    staleTime: 60_000,
  })
}

export function useCreateRuntimeConnection() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (data: CreateRuntimeConnectionRequest) =>
      api.createRuntimeConnection(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["runtime-connections"] })
    },
  })
}

export function useUpdateRuntimeConnection() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({
      id,
      data,
    }: {
      id: number
      data: UpdateRuntimeConnectionRequest
    }) => api.updateRuntimeConnection(id, data),
    onSuccess: (_data, { id }) => {
      queryClient.invalidateQueries({ queryKey: ["runtime-connections"] })
      queryClient.invalidateQueries({
        queryKey: queryKeys.runtimeConnection(id),
      })
    },
  })
}

export function useDeleteRuntimeConnection() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (id: number) => api.deleteRuntimeConnection(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["runtime-connections"] })
    },
  })
}

// ==================== Executors ====================

export function useExecutors(params?: { skip?: number; limit?: number }) {
  return useQuery({
    queryKey: queryKeys.executors(params),
    queryFn: () => api.getExecutors(params),
    staleTime: 30_000,
  })
}

export function useExecutor(id: number | null) {
  return useQuery({
    queryKey: queryKeys.executor(id!),
    queryFn: () => api.getExecutor(id!),
    enabled: id !== null,
    staleTime: 15_000,
    // Executors are stateful, so refresh every 5 seconds to track live status
    refetchInterval: 5_000,
  })
}

export function useExecutorHistory(
  id: number | null,
  params?: {
    skip?: number
    limit?: number
    status?: "success" | "failed" | "skipped"
    search?: string
  }
) {
  return useQuery({
    queryKey: queryKeys.executorHistory(id!, params),
    queryFn: () => api.getExecutorHistory(id!, params),
    enabled: id !== null,
    staleTime: 15_000,
    refetchInterval: 8_000, // Refresh history status periodically while execution is in progress
  } as UseQueryOptions<Awaited<ReturnType<typeof api.getExecutorHistory>>>)
}

export function useCreateExecutor() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (data: CreateExecutorRequest) => api.createExecutor(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["executors"] })
    },
  })
}

export function useUpdateExecutor() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ id, data }: { id: number; data: UpdateExecutorRequest }) =>
      api.updateExecutor(id, data),
    onSuccess: (_data, { id }) => {
      queryClient.invalidateQueries({ queryKey: ["executors"] })
      queryClient.invalidateQueries({ queryKey: queryKeys.executor(id) })
    },
  })
}

export function useDeleteExecutor() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (id: number) => api.deleteExecutor(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["executors"] })
    },
  })
}

export function useRunExecutor() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (id: number) => api.runExecutor(id),
    onSuccess: (_data, id) => {
      // After triggering a run, refresh executor detail and history
      queryClient.invalidateQueries({ queryKey: queryKeys.executor(id) })
      queryClient.invalidateQueries({
        queryKey: ["executors", id, "history"],
      })
    },
  })
}
