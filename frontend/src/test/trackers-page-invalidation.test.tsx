import { act, render } from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { beforeEach, describe, expect, it, vi } from "vitest"

import type { AggregateTracker } from "@/api/types"
import { buildTrackerFormValues, buildTrackerPayload, createDefaultValues, supportsReleaseTypeFilter } from "@/components/trackers/trackerDialogHelpers"
import { formatChannelSummary, getTrackerLastCheck, getTrackerLastVersion } from "@/components/trackers/trackerListHelpers"

const invalidateQueries = vi.fn().mockResolvedValue(undefined)

vi.mock("sonner", () => ({
  toast: {
    success: vi.fn(),
    error: vi.fn(),
  },
}))

vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string) => key,
  }),
}))

vi.mock("@/components/trackers/TrackerDetail", () => ({
  TrackerDetail: function MockTrackerDetail() {
    return null
  },
}))

vi.mock("@/hooks/queries", () => ({
  queryKeys: {
    trackers: (params?: { skip?: number; limit?: number }) => ["trackers", params] as const,
  },
  useTrackers: () => ({
    data: { items: [], total: 0 },
    isLoading: false,
  }),
  useDeleteTracker: () => ({
    mutateAsync: vi.fn(),
  }),
  useCheckTracker: () => ({
    mutateAsync: vi.fn(),
  }),
}))

const trackerDialogMock = vi.fn()

vi.mock("@/components/trackers/TrackerDialog", () => ({
  TrackerDialog: (props: { onSuccess: (trackerName: string) => Promise<void> }) => {
    trackerDialogMock(props)
    return null
  },
}))

import TrackersPage from "@/pages/Trackers"

describe("TrackersPage tracker invalidation", () => {
  beforeEach(() => {
    invalidateQueries.mockClear()
    trackerDialogMock.mockClear()
    localStorage.clear()
  })

  it("invalidates the full trackers query family after dialog success", async () => {
    const queryClient = new QueryClient()
    vi.spyOn(queryClient, "invalidateQueries").mockImplementation(invalidateQueries)

    render(
      <QueryClientProvider client={queryClient}>
        <TrackersPage />
      </QueryClientProvider>,
    )

    const [{ onSuccess }] = trackerDialogMock.mock.calls.map(([props]) => props)
    await act(async () => {
      await onSuccess("qa-tracker")
    })

    expect(invalidateQueries).toHaveBeenCalledWith({ queryKey: ["trackers"] })
  })

  it("builds tracker edit state from redesign sources only", () => {
    const values = buildTrackerFormValues({
      name: "qa-tracker",
      enabled: true,
      description: null,
      changelog_policy: "primary_source",
      primary_changelog_source_key: "repo-source",
      primary_changelog_channel_key: null,
      sources: [
        {
          source_key: "repo-source",
          source_type: "github",
          enabled: true,
          credential_name: null,
          source_config: { repo: "owner/repo", fetch_mode: "graphql_first" },
          release_channels: [
            { release_channel_key: "repo-source-0-stable", name: "stable", type: "release", enabled: true },
          ],
          source_rank: 0,
        },
      ],
      tracker_channels: [],
      interval: 360,
      version_sort_mode: "published_at",
      fetch_limit: 10,
      fetch_timeout: 15,
      fallback_tags: false,
      github_fetch_mode: "rest_first",
      channels: [],
      status: {
        last_check: null,
        last_version: null,
        error: null,
        source_count: 1,
        enabled_source_count: 1,
        source_types: ["github"],
        tracker_channel_count: 0,
        enabled_tracker_channel_count: 0,
        tracker_channel_types: [],
      },
    } as unknown as AggregateTracker)

    expect(values.primary_changelog_source_key).toBe("repo-source")
    expect(values.changelog_policy).toBe("primary_source")
    expect(values.sources).toHaveLength(1)
    expect(values.sources[0]).toMatchObject({
      source_key: "repo-source",
      source_type: "github",
      source_config: { repo: "owner/repo", fetch_mode: "graphql_first" },
      source_rank: 0,
    })
    expect("tracker_channels" in values).toBe(false)
  })

  it("serializes tracker dialog submits as redesign-only source payloads", () => {
    const values = createDefaultValues()
    values.name = "payload-tracker"
    values.sources[0].source_key = "repo-source"
    values.sources[0].source_config.fetch_mode = "graphql_first"

    const payload = buildTrackerPayload(values, "repo-source")

    expect(payload.changelog_policy).toBe("primary_source")
    expect(payload.primary_changelog_source_key).toBe("repo-source")
    expect("primary_changelog_channel_key" in payload).toBe(false)
    expect("tracker_channels" in payload).toBe(false)
    expect(payload.sources).toHaveLength(1)
    expect(payload.sources[0]).toMatchObject({
      source_key: "repo-source",
      source_type: "github",
      source_rank: 0,
      source_config: {
        fetch_mode: "graphql_first",
      },
    })
    expect("channel_key" in payload.sources[0]).toBe(false)
    expect("channel_type" in payload.sources[0]).toBe(false)
    expect("channel_config" in payload.sources[0]).toBe(false)
    expect("channel_rank" in payload.sources[0]).toBe(false)
  })

  it("omits release type filters for container and helm sources", () => {
    const values = createDefaultValues()
    values.name = "non-git-tracker"
    values.sources = [
      {
        source_key: "image",
        source_type: "container",
        enabled: true,
        credential_name: "",
        source_config: { image: "library/nginx", registry: "" },
        source_rank: 0,
        release_channels: [
          { release_channel_key: "image-0-stable", name: "stable", type: "prerelease", enabled: true },
        ],
      },
      {
        source_key: "chart",
        source_type: "helm",
        enabled: true,
        credential_name: "",
        source_config: { repo: "https://charts.example.com", chart: "app" },
        source_rank: 1,
        release_channels: [
          { release_channel_key: "chart-0-stable", name: "stable", type: "release", enabled: true },
        ],
      },
      {
        source_key: "repo",
        source_type: "github",
        enabled: true,
        credential_name: "",
        source_config: { repo: "owner/repo", fetch_mode: "rest_first" },
        source_rank: 2,
        release_channels: [
          { release_channel_key: "repo-0-canary", name: "canary", type: "prerelease", enabled: true },
        ],
      },
    ]

    const payload = buildTrackerPayload(values, "repo")

    expect(supportsReleaseTypeFilter("container")).toBe(false)
    expect(supportsReleaseTypeFilter("helm")).toBe(false)
    expect(supportsReleaseTypeFilter("github")).toBe(true)
    expect(payload.sources[0].release_channels[0].type).toBeNull()
    expect(payload.sources[1].release_channels[0].type).toBeNull()
    expect(payload.sources[2].release_channels[0].type).toBe("prerelease")
  })

  it("formats tracker source badges from redesign status and source fields without legacy arrays", () => {
    expect(formatChannelSummary({
      name: "qa-tracker",
      enabled: true,
      description: null,
      changelog_policy: "primary_source",
      primary_changelog_source_key: "repo-source",
      primary_changelog_channel_key: null,
      sources: [
        {
          source_key: "repo-source",
          source_type: "github",
          enabled: true,
          credential_name: null,
          source_config: { repo: "owner/repo" },
          release_channels: [],
          source_rank: 0,
        },
      ],
      tracker_channels: undefined as never,
      interval: 360,
      version_sort_mode: "published_at",
      fetch_limit: 10,
      fetch_timeout: 15,
      fallback_tags: false,
      github_fetch_mode: "rest_first",
      channels: [],
      status: {
        last_check: null,
        last_version: null,
        error: null,
        source_count: 1,
        enabled_source_count: 1,
        source_types: ["github"],
        tracker_channel_count: undefined as never,
        enabled_tracker_channel_count: undefined as never,
        tracker_channel_types: undefined as never,
      },
    } as unknown as AggregateTracker)).toEqual(["github"])
  })

  it("reads latest version and last check from redesign status fields", () => {
    const tracker = {
      name: "qa-tracker",
      enabled: true,
      description: null,
      changelog_policy: "primary_source",
      primary_changelog_source_key: "repo-source",
      sources: [],
      interval: 360,
      version_sort_mode: "published_at",
      fetch_limit: 10,
      fetch_timeout: 15,
      fallback_tags: false,
      github_fetch_mode: "rest_first",
      channels: [],
      status: {
        last_check: "2026-04-22T12:00:00Z",
        last_version: "1.2.3",
        error: null,
        source_count: 0,
        enabled_source_count: 0,
        source_types: [],
      },
      last_check: null,
      last_version: null,
      error: null,
    } as unknown as AggregateTracker

    expect(getTrackerLastVersion(tracker)).toBe("1.2.3")
    expect(getTrackerLastCheck(tracker)).toBe("2026-04-22T12:00:00Z")
  })
})
