import { describe, expect, it, vi, afterEach } from "vitest"

import { api, apiClient } from "@/api/client"
import { queryKeys } from "@/hooks/queries"
import { buildReleaseIdentityPrefix } from "@/pages/historyHelpers"

describe("release query semantics", () => {
    afterEach(() => {
        vi.restoreAllMocks()
    })

    it("uses explicit history query keys", () => {
        expect(queryKeys.releaseHistory({ limit: 20, skip: 0, search: "nginx" })).toEqual([
            "releases",
            "history",
            { limit: 20, skip: 0, search: "nginx" },
        ])
        expect(queryKeys.trackerCurrentView("stable-tracker")).toEqual([
            "trackers",
            "stable-tracker",
            "current-view",
        ])
        expect(queryKeys.latestCurrentReleases).toEqual(["releases", "latest-current"])
    })

    it("builds a short digest or commit hash release identity prefix", () => {
        expect(buildReleaseIdentityPrefix({
            digest: "sha256:1234567890abcdef",
            commit_sha: "abcdef1234567890",
        })).toBe("1234567890ab")
        expect(buildReleaseIdentityPrefix({
            digest: "",
            commit_sha: "abcdef1234567890",
        })).toBe("abcdef123456")
        expect(buildReleaseIdentityPrefix({ digest: "", commit_sha: null })).toBeNull()
    })

    it("calls the history-only endpoint for release history", async () => {
        const getSpy = vi.spyOn(apiClient, "get").mockResolvedValue({
            data: { items: [], total: 0, skip: 0, limit: 20 },
        })

        await api.getReleaseHistory({ limit: 20, skip: 0, search: "nginx" })

        expect(getSpy).toHaveBeenCalledWith("/api/releases", {
            params: { limit: 20, skip: 0, search: "nginx" },
        })
    })

    it("returns redesign tracker fields without legacy response alias bridging", async () => {
        vi.spyOn(apiClient, "get").mockResolvedValue({
            data: {
                items: [
                    {
                        name: "stable-tracker",
                        enabled: true,
                        description: null,
                        changelog_policy: "primary_source",
                        primary_changelog_source_key: "source-1",
                        sources: [
                            {
                                source_key: "source-1",
                                source_type: "github",
                                enabled: true,
                                credential_name: null,
                                source_config: { repo: "owner/repo" },
                                release_channels: [
                                    {
                                        release_channel_key: "source-1-0-stable",
                                        name: "stable",
                                        type: "release",
                                        enabled: true,
                                    },
                                ],
                                source_rank: 0,
                            },
                        ],
                        interval: 360,
                        version_sort_mode: "published_at",
                        fetch_limit: 10,
                        fetch_timeout: 15,
                        fallback_tags: false,
                        github_fetch_mode: "rest_first",
                        channels: [
                            {
                                release_channel_key: "source-1-0-stable",
                                name: "stable",
                                type: "release",
                                enabled: true,
                            },
                        ],
                        status: {
                            last_check: null,
                            last_version: null,
                            error: null,
                            source_count: 1,
                            enabled_source_count: 1,
                            source_types: ["github"],
                        },
                    },
                ],
                total: 1,
            },
        })

        const result = await api.getTrackers({ limit: 20, skip: 0 })

        expect(result.items).toHaveLength(1)
        expect(result.items[0].primary_changelog_source_key).toBe("source-1")
        expect(result.items[0].sources[0]?.source_key).toBe("source-1")
        expect(result.items[0].changelog_policy).toBe("primary_source")
        expect("primary_changelog_channel_key" in result.items[0]).toBe(false)
        expect("tracker_channels" in result.items[0]).toBe(false)
    })

    it("calls the explicit current-view and latest-current endpoints", async () => {
        const getSpy = vi.spyOn(apiClient, "get")
            .mockResolvedValueOnce({
                data: {
                    tracker: { name: "stable-tracker", primary_changelog_source_key: null, sources: [] },
                    status: { last_check: null, last_version: null, error: null },
                    latest_release: null,
                    matrix: { columns: [], rows: [] },
                    projected_at: null,
                },
            })
            .mockResolvedValueOnce({ data: [] })

        await api.getTrackerCurrentView("stable-tracker")
        await api.getLatestCurrentReleases()

        expect(getSpy).toHaveBeenNthCalledWith(1, "/api/trackers/stable-tracker/current")
        expect(getSpy).toHaveBeenNthCalledWith(2, "/api/releases/latest")
    })
})
