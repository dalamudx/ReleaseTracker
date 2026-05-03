import { describe, expect, it } from "vitest"

import type { AggregateTracker, TrackerCurrentView, ReleaseHistoryItem } from "@/api/types"
import {
    buildTrackerCurrentMatrixPresentationModel,
    buildTrackerHistoryMatrixPresentationModel,
} from "@/components/trackers/canonicalReleaseMatrixModel"

describe("tracker current matrix presentation model", () => {
    it("preserves backend row order while normalizing display-only version prefixes", () => {
        const model = buildTrackerCurrentMatrixPresentationModel({
            columns: [
                {
                    channel_key: "stable",
                    channel_type: "release",
                    enabled: true,
                    channel_rank: 0,
                },
            ],
            rows: [
                {
                    tracker_release_history_id: 2,
                    identity_key: "version/2026.2.2",
                    version: "version/2026.2.2",
                    digest: "digest-2",
                    published_at: "2026-02-10T00:00:00Z",
                    matched_channel_count: 1,
                    channel_keys: ["stable"],
                    primary_source: null,
                    source_contributions: [],
                    cells: { stable: { channel_key: "stable", channel_type: "release", selected: true } },
                },
                {
                    tracker_release_history_id: 1,
                    identity_key: "version/2026.2.1",
                    version: "version/2026.2.1",
                    digest: "digest-1",
                    published_at: "2026-02-20T00:00:00Z",
                    matched_channel_count: 1,
                    channel_keys: ["stable"],
                    primary_source: null,
                    source_contributions: [],
                    cells: { stable: { channel_key: "stable", channel_type: "release", selected: true } },
                },
            ],
        } satisfies TrackerCurrentView["matrix"])

        expect(model.rows[0]?.displayVersion).toBe("2026.2.2")
        expect(model.rows[1]?.displayVersion).toBe("2026.2.1")
        expect(model.rows[0]?.trackerReleaseHistoryId).toBe(2)
        expect(model.rows[1]?.trackerReleaseHistoryId).toBe(1)
    })

    it("extracts helm chart version from current row contributions without recomputing row membership", () => {
        const model = buildTrackerCurrentMatrixPresentationModel({
            columns: [],
            rows: [
                {
                    tracker_release_history_id: 10,
                    identity_key: "1.2.3",
                    version: "1.2.3",
                    digest: "digest-10",
                    published_at: "2026-03-01T00:00:00Z",
                    matched_channel_count: 0,
                    channel_keys: [],
                    primary_source: null,
                    source_contributions: [
                        {
                            source_release_history_id: 99,
                            tracker_name: "helm-tracker",
                            source_key: "helm",
                            source_type: "helm",
                            contribution_kind: "primary",
                            version: "1.2.3",
                            name: "Chart 1.2.3",
                            tag_name: "chart-1.2.3",
                            published_at: "2026-03-01T00:00:00Z",
                            url: "https://example.com/chart",
                            changelog_url: null,
                            prerelease: false,
                            body: null,
                            digest: "digest-99",
                            app_version: "1.2.3",
                            chart_version: "9.9.9",
                            observed_at: "2026-03-01T00:10:00Z",
                        },
                    ],
                    cells: {},
                },
            ],
        })

        expect(model.rows[0]?.helmChartVersion).toBe("9.9.9")
    })

    it("sorts prerelease numeric suffixes in descending semver order", () => {
        const model = buildTrackerCurrentMatrixPresentationModel({
            columns: [],
            rows: [
                {
                    tracker_release_history_id: 1,
                    identity_key: "0.7.0-rc9",
                    version: "0.7.0-rc9",
                    digest: "digest-rc9",
                    published_at: "2026-04-22T05:17:00Z",
                    matched_channel_count: 0,
                    channel_keys: [],
                    primary_source: null,
                    source_contributions: [],
                    cells: {},
                },
                {
                    tracker_release_history_id: 2,
                    identity_key: "0.7.0-rc14",
                    version: "0.7.0-rc14",
                    digest: "digest-rc14",
                    published_at: "2026-04-23T09:22:00Z",
                    matched_channel_count: 0,
                    channel_keys: [],
                    primary_source: null,
                    source_contributions: [],
                    cells: {},
                },
                {
                    tracker_release_history_id: 3,
                    identity_key: "0.7.0-rc2",
                    version: "0.7.0-rc2",
                    digest: "digest-rc2",
                    published_at: "2026-04-21T03:00:00Z",
                    matched_channel_count: 0,
                    channel_keys: [],
                    primary_source: null,
                    source_contributions: [],
                    cells: {},
                },
            ],
        } satisfies TrackerCurrentView["matrix"])

        expect(model.rows.map((row) => row.displayVersion)).toEqual([
            "0.7.0-rc14",
            "0.7.0-rc9",
            "0.7.0-rc2",
        ])
    })

    it("keeps stable releases ahead of prereleases with the same numeric core", () => {
        const model = buildTrackerCurrentMatrixPresentationModel({
            columns: [],
            rows: [
                {
                    tracker_release_history_id: 1,
                    identity_key: "0.7.0-rc14",
                    version: "0.7.0-rc14",
                    digest: "digest-rc14",
                    published_at: "2026-04-23T09:22:00Z",
                    matched_channel_count: 0,
                    channel_keys: [],
                    primary_source: null,
                    source_contributions: [],
                    cells: {},
                },
                {
                    tracker_release_history_id: 2,
                    identity_key: "0.7.0",
                    version: "0.7.0",
                    digest: "digest-stable",
                    published_at: "2026-04-24T09:22:00Z",
                    matched_channel_count: 0,
                    channel_keys: [],
                    primary_source: null,
                    source_contributions: [],
                    cells: {},
                },
            ],
        } satisfies TrackerCurrentView["matrix"])

        expect(model.rows.map((row) => row.displayVersion)).toEqual([
            "0.7.0",
            "0.7.0-rc14",
        ])
    })

    it("groups rows by shared display version and aggregates source type badges", () => {
        const model = buildTrackerCurrentMatrixPresentationModel({
            columns: [
                {
                    channel_key: "beta-channel",
                    channel_type: "prerelease",
                    enabled: false,
                    channel_rank: 0,
                },
                {
                    channel_key: "stable-channel",
                    channel_type: "release",
                    enabled: true,
                    channel_rank: 1,
                },
            ],
            rows: [
                {
                    tracker_release_history_id: 7,
                    identity_key: "1.2.3@no_digest",
                    version: "1.2.3",
                    digest: "digest-7",
                    published_at: "2026-03-02T00:00:00Z",
                    matched_channel_count: 1,
                    channel_keys: ["stable-channel"],
                    primary_source: null,
                    source_contributions: [],
                    cells: {
                        "stable-channel": {
                            channel_key: "stable-channel",
                            channel_type: "release",
                            selected: true,
                        },
                    },
                },
                {
                    tracker_release_history_id: 8,
                    identity_key: "1.2.3@sha256:abc",
                    version: "1.2.3",
                    digest: "sha256:abc",
                    published_at: "2026-03-03T00:00:00Z",
                    matched_channel_count: 0,
                    channel_keys: [],
                    primary_source: null,
                    source_contributions: [
                        {
                            source_release_history_id: 11,
                            tracker_name: "mixed-source",
                            source_key: "image",
                            source_type: "container",
                            contribution_kind: "primary",
                            version: "1.2.3",
                            name: "Container 1.2.3",
                            tag_name: "1.2.3",
                            published_at: "2026-03-03T00:00:00Z",
                            url: "https://example.com/container",
                            changelog_url: null,
                            prerelease: false,
                            body: null,
                            digest: "sha256:abc",
                            app_version: null,
                            chart_version: null,
                            observed_at: "2026-03-03T00:10:00Z",
                        },
                    ],
                    cells: {},
                },
            ],
        } satisfies TrackerCurrentView["matrix"])

        expect(model.rows).toHaveLength(1)
        expect(model.rows[0]?.selectedChannelKeys).toEqual(["stable-channel"])
        expect(model.rows[0]?.matchedChannelCount).toBe(1)
        expect(model.rows[0]?.sourceTypeBadges).toEqual(["container"])
    })

    it("groups repo, helm, and container contributions with the same version into one row", () => {
        const model = buildTrackerCurrentMatrixPresentationModel({
            columns: [
                {
                    channel_key: "stable",
                    channel_type: "release",
                    enabled: true,
                    channel_rank: 0,
                },
            ],
            rows: [
                {
                    tracker_release_history_id: 20,
                    identity_key: "0.26.3@no_digest",
                    version: "0.26.3",
                    digest: "digest-repo",
                    published_at: "2026-03-01T00:00:00Z",
                    matched_channel_count: 1,
                    channel_keys: ["stable"],
                    primary_source: null,
                    source_contributions: [
                        {
                            source_release_history_id: 21,
                            tracker_name: "affine",
                            source_key: "repo",
                            source_type: "github",
                            contribution_kind: "primary",
                            version: "0.26.3",
                            name: "Repo 0.26.3",
                            tag_name: "v0.26.3",
                            published_at: "2026-03-01T00:00:00Z",
                            url: "https://example.com/repo",
                            changelog_url: null,
                            prerelease: false,
                            body: null,
                            digest: "digest-repo",
                            app_version: null,
                            chart_version: null,
                            observed_at: "2026-03-01T00:10:00Z",
                        },
                    ],
                    cells: {
                        stable: { channel_key: "stable", channel_type: "release", selected: true },
                    },
                },
                {
                    tracker_release_history_id: 22,
                    identity_key: "0.26.3-chart.1@no_digest",
                    version: "0.26.3",
                    digest: "digest-helm",
                    published_at: "2026-03-02T00:00:00Z",
                    matched_channel_count: 0,
                    channel_keys: [],
                    primary_source: null,
                    source_contributions: [
                        {
                            source_release_history_id: 23,
                            tracker_name: "affine",
                            source_key: "helm",
                            source_type: "helm",
                            contribution_kind: "primary",
                            version: "0.26.3",
                            name: "Helm 0.26.3",
                            tag_name: "0.26.3-chart.1",
                            published_at: "2026-03-02T00:00:00Z",
                            url: "https://example.com/helm",
                            changelog_url: null,
                            prerelease: false,
                            body: null,
                            digest: "digest-helm",
                            app_version: "0.26.3",
                            chart_version: "0.26.3-chart.1",
                            observed_at: "2026-03-02T00:10:00Z",
                        },
                    ],
                    cells: {},
                },
                {
                    tracker_release_history_id: 24,
                    identity_key: "0.26.3@sha256:def",
                    version: "0.26.3",
                    digest: "sha256:def",
                    published_at: "2026-03-03T00:00:00Z",
                    matched_channel_count: 0,
                    channel_keys: [],
                    primary_source: null,
                    source_contributions: [
                        {
                            source_release_history_id: 25,
                            tracker_name: "affine",
                            source_key: "image",
                            source_type: "container",
                            contribution_kind: "primary",
                            version: "0.26.3",
                            name: "Container 0.26.3",
                            tag_name: "0.26.3",
                            published_at: "2026-03-03T00:00:00Z",
                            url: "https://example.com/container",
                            changelog_url: null,
                            prerelease: false,
                            body: null,
                            digest: "sha256:def",
                            app_version: null,
                            chart_version: null,
                            observed_at: "2026-03-03T00:10:00Z",
                        },
                    ],
                    cells: {},
                },
            ],
        } satisfies TrackerCurrentView["matrix"])

        expect(model.rows).toHaveLength(1)
        expect(model.rows[0]?.displayVersion).toBe("0.26.3")
        expect(model.rows[0]?.sourceTypeBadges).toEqual(["github", "helm", "container"])
        expect(model.rows[0]?.selectedChannelKeys).toEqual(["stable"])
    })

    it("builds the tracker version view from history rows that match channel regexes", () => {
        const tracker = {
            sources: [
                {
                    channel_key: "repo",
                    channel_type: "github",
                    enabled: true,
                    source_key: "repo",
                    source_type: "github",
                    channel_config: {},
                    channel_rank: 0,
                    release_channels: [
                        {
                            release_channel_key: "repo-stable",
                            name: "stable",
                            type: "release",
                            enabled: true,
                            exclude_pattern: "(stable)",
                        },
                    ],
                },
                {
                    channel_key: "container",
                    channel_type: "container",
                    enabled: true,
                    source_key: "container",
                    source_type: "container",
                    channel_config: {},
                    channel_rank: 1,
                    release_channels: [
                        {
                            release_channel_key: "container-stable",
                            name: "stable",
                            type: "release",
                            enabled: true,
                        },
                    ],
                },
                {
                    channel_key: "helm",
                    channel_type: "helm",
                    enabled: true,
                    source_key: "helm",
                    source_type: "helm",
                    channel_config: {},
                    channel_rank: 2,
                    release_channels: [
                        {
                            release_channel_key: "helm-stable",
                            name: "stable",
                            type: "release",
                            enabled: true,
                        },
                    ],
                },
            ],
        } satisfies Pick<AggregateTracker, "sources">

        const items = [
            {
                tracker_name: "n8n",
                tracker_release_history_id: 1,
                identity_key: "stable@no_digest",
                version: "stable",
                digest: "digest-stable-tag",
                name: "stable",
                tag_name: "stable",
                published_at: "2026-04-22T10:23:51Z",
                url: "https://example.com/stable",
                changelog_url: null,
                prerelease: false,
                body: null,
                channel_name: null,
                app_version: null,
                chart_version: null,
                commit_sha: null,
                primary_source: { source_key: "repo", source_type: "github", source_release_history_id: 1 },
                created_at: "2026-04-23T14:40:49Z",
            },
            {
                tracker_name: "n8n",
                tracker_release_history_id: 2,
                identity_key: "2.17.5@no_digest",
                version: "2.17.5",
                digest: "digest-helm",
                name: "2.17.5",
                tag_name: "1.0.41",
                published_at: "2026-04-22T10:41:47Z",
                url: "https://example.com/helm",
                changelog_url: null,
                prerelease: false,
                body: null,
                channel_name: null,
                app_version: "2.17.5",
                chart_version: "1.0.41",
                commit_sha: null,
                primary_source: { source_key: "helm", source_type: "helm", source_release_history_id: 2 },
                created_at: "2026-04-23T14:41:07Z",
            },
            {
                tracker_name: "n8n",
                tracker_release_history_id: 3,
                identity_key: "2.18.1@sha256:abc",
                version: "2.18.1",
                digest: "sha256:abc",
                name: "latest",
                tag_name: "latest",
                published_at: "2026-04-23T15:00:01Z",
                url: "https://example.com/container",
                changelog_url: null,
                prerelease: false,
                body: null,
                channel_name: null,
                app_version: null,
                chart_version: null,
                commit_sha: null,
                primary_source: { source_key: "container", source_type: "container", source_release_history_id: 3 },
                created_at: "2026-04-23T14:41:07Z",
            },
        ] satisfies ReleaseHistoryItem[]

        const model = buildTrackerHistoryMatrixPresentationModel(tracker.sources, items)

        expect(model.rows.map((row) => row.displayVersion)).toEqual(["2.18.1", "2.17.5"])
        expect(model.rows.some((row) => row.displayVersion === "stable")).toBe(false)
    })

    it("orders rows by shared source count first, then by version descending", () => {
        const model = buildTrackerCurrentMatrixPresentationModel({
            columns: [],
            rows: [
                {
                    tracker_release_history_id: 1,
                    identity_key: "0.26.6@no_digest",
                    version: "0.26.6",
                    digest: "digest-1",
                    published_at: "2026-03-03T00:00:00Z",
                    matched_channel_count: 0,
                    channel_keys: [],
                    primary_source: null,
                    source_contributions: [
                        {
                            source_release_history_id: 1,
                            tracker_name: "affine",
                            source_key: "container",
                            source_type: "container",
                            contribution_kind: "primary",
                            version: "0.26.6",
                            name: "Container 0.26.6",
                            tag_name: "0.26.6",
                            published_at: "2026-03-03T00:00:00Z",
                            url: "https://example.com/container-0266",
                            changelog_url: null,
                            prerelease: false,
                            body: null,
                            digest: "digest-1",
                            app_version: null,
                            chart_version: null,
                            observed_at: "2026-03-03T00:10:00Z",
                        },
                    ],
                    cells: {},
                },
                {
                    tracker_release_history_id: 2,
                    identity_key: "0.26.3@no_digest",
                    version: "0.26.3",
                    digest: "digest-2",
                    published_at: "2026-03-02T00:00:00Z",
                    matched_channel_count: 0,
                    channel_keys: [],
                    primary_source: null,
                    source_contributions: [
                        {
                            source_release_history_id: 2,
                            tracker_name: "affine",
                            source_key: "repo",
                            source_type: "github",
                            contribution_kind: "primary",
                            version: "0.26.3",
                            name: "Repo 0.26.3",
                            tag_name: "v0.26.3",
                            published_at: "2026-03-02T00:00:00Z",
                            url: "https://example.com/repo-0263",
                            changelog_url: null,
                            prerelease: false,
                            body: null,
                            digest: "digest-2",
                            app_version: null,
                            chart_version: null,
                            observed_at: "2026-03-02T00:10:00Z",
                        },
                        {
                            source_release_history_id: 3,
                            tracker_name: "affine",
                            source_key: "helm",
                            source_type: "helm",
                            contribution_kind: "supporting",
                            version: "0.26.3",
                            name: "Helm 0.26.3",
                            tag_name: "0.26.3-chart.1",
                            published_at: "2026-03-02T00:00:00Z",
                            url: "https://example.com/helm-0263",
                            changelog_url: null,
                            prerelease: false,
                            body: null,
                            digest: "digest-3",
                            app_version: "0.26.3",
                            chart_version: "1.0.5",
                            observed_at: "2026-03-02T00:10:00Z",
                        },
                    ],
                    cells: {},
                },
                {
                    tracker_release_history_id: 4,
                    identity_key: "0.26.4@no_digest",
                    version: "0.26.4",
                    digest: "digest-4",
                    published_at: "2026-03-04T00:00:00Z",
                    matched_channel_count: 0,
                    channel_keys: [],
                    primary_source: null,
                    source_contributions: [
                        {
                            source_release_history_id: 4,
                            tracker_name: "affine",
                            source_key: "repo",
                            source_type: "github",
                            contribution_kind: "primary",
                            version: "0.26.4",
                            name: "Repo 0.26.4",
                            tag_name: "v0.26.4",
                            published_at: "2026-03-04T00:00:00Z",
                            url: "https://example.com/repo-0264",
                            changelog_url: null,
                            prerelease: false,
                            body: null,
                            digest: "digest-4",
                            app_version: null,
                            chart_version: null,
                            observed_at: "2026-03-04T00:10:00Z",
                        },
                        {
                            source_release_history_id: 5,
                            tracker_name: "affine",
                            source_key: "helm",
                            source_type: "helm",
                            contribution_kind: "supporting",
                            version: "0.26.4",
                            name: "Helm 0.26.4",
                            tag_name: "0.26.4-chart.1",
                            published_at: "2026-03-04T00:00:00Z",
                            url: "https://example.com/helm-0264",
                            changelog_url: null,
                            prerelease: false,
                            body: null,
                            digest: "digest-5",
                            app_version: "0.26.4",
                            chart_version: "1.0.6",
                            observed_at: "2026-03-04T00:10:00Z",
                        },
                    ],
                    cells: {},
                },
            ],
        } satisfies TrackerCurrentView["matrix"])

        expect(model.rows.map((row) => row.displayVersion)).toEqual(["0.26.4", "0.26.3", "0.26.6"])
        expect(model.rows.map((row) => row.sourceTypeBadges.length)).toEqual([2, 2, 1])
    })
})
