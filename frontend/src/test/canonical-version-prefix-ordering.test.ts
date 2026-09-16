import { describe, expect, it } from "vitest"

import type { AggregateTracker, TrackerCurrentView, ReleaseHistoryItem } from "@/api/types"
import {
    buildTrackerAliasTableRows,
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
                            tracker_name: "sample-suite",
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
                            tracker_name: "sample-suite",
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
                    tracker_release_history_id: 26,
                    identity_key: "0.26.3-chart.2@no_digest",
                    version: "0.26.3",
                    digest: "digest-helm-newer",
                    published_at: "2026-03-04T00:00:00Z",
                    matched_channel_count: 0,
                    channel_keys: [],
                    primary_source: null,
                    source_contributions: [
                        {
                            source_release_history_id: 27,
                            tracker_name: "sample-suite",
                            source_key: "helm",
                            source_type: "helm",
                            contribution_kind: "primary",
                            version: "0.26.3",
                            name: "Helm 0.26.3 newer chart",
                            tag_name: "0.26.3-chart.2",
                            published_at: "2026-03-04T00:00:00Z",
                            url: "https://example.com/helm-newer",
                            changelog_url: null,
                            prerelease: false,
                            body: null,
                            digest: "digest-helm-newer",
                            app_version: "0.26.3",
                            chart_version: "1.0.6",
                            observed_at: "2026-03-04T00:10:00Z",
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
                            tracker_name: "sample-suite",
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
        expect(model.rows[0]?.helmChartVersion).toBe("1.0.6")
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
                tracker_name: "sample-flow",
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
                tracker_name: "sample-flow",
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
                tracker_name: "sample-flow",
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
                            tracker_name: "sample-suite",
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
                            tracker_name: "sample-suite",
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
                            tracker_name: "sample-suite",
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
                            tracker_name: "sample-suite",
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
                            tracker_name: "sample-suite",
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

    it("orders tracker history strictly by published time when configured", () => {
        const sources = [
            {
                id: 1, channel_key: "repo", channel_type: "gitea", enabled: true,
                channel_config: { repo: "owner/project" }, channel_rank: 0,
                source_key: "repo", source_type: "gitea", source_config: { repo: "owner/project" }, source_rank: 0,
                release_channels: [
                    { release_channel_key: "repo-stable", name: "stable", type: "release", enabled: true },
                    { release_channel_key: "repo-dev", name: "prerelease", type: "prerelease", include_pattern: ".*dev.*", enabled: true },
                ],
            },
            {
                id: 2, channel_key: "container", channel_type: "container", enabled: true,
                channel_config: { image: "canvas/nginx_frontend" }, channel_rank: 1,
                source_key: "container", source_type: "container", source_config: { image: "canvas/nginx_frontend" }, source_rank: 1,
                release_channels: [
                    { release_channel_key: "image-stable", name: "stable", exclude_pattern: ".*dev.*", enabled: true },
                    { release_channel_key: "image-dev", name: "prerelease", include_pattern: ".*dev.*", enabled: true },
                ],
            },
        ] satisfies AggregateTracker["sources"]
        const item = (
            id: number, sourceKey: "repo" | "container", version: string,
            publishedAt: string, prerelease: boolean,
        ): ReleaseHistoryItem => ({
            tracker_name: "nginx_frontend", tracker_release_history_id: id,
            identity_key: `${version}-${sourceKey}`, version, digest: `digest-${id}`,
            name: version, tag_name: version, published_at: publishedAt,
            url: `https://example.com/${version}`, changelog_url: null, prerelease,
            body: null, channel_name: prerelease ? "prerelease" : "stable",
            app_version: null, chart_version: null, commit_sha: null,
            primary_source: { source_key: sourceKey, source_type: sourceKey === "repo" ? "gitea" : "container", source_release_history_id: id },
            created_at: publishedAt,
        })
        const items = [
            item(1, "repo", "3.6.0", "2026-09-10T05:01:51Z", false),
            item(2, "container", "3.6.0", "2026-09-10T05:01:31Z", false),
            item(3, "repo", "3.6.0-dev-ce40a02fc70b", "2026-09-10T10:38:55Z", true),
            item(4, "container", "3.6.0-dev-ce40a02fc70b", "2026-09-10T10:38:30Z", false),
            item(5, "repo", "3.6.0-dev-0b0d27d3f61c", "2026-09-15T04:01:04Z", true),
            item(6, "container", "3.6.0-dev", "2026-09-15T04:00:43Z", false),
        ]

        const model = buildTrackerHistoryMatrixPresentationModel(sources, items, "published_at")
        expect(model.rows.map((row) => row.displayVersion)).toEqual([
            "3.6.0-dev-0b0d27d3f61c", "3.6.0-dev", "3.6.0-dev-ce40a02fc70b", "3.6.0",
        ])
    })

    it("uses authoritative contribution times so correlated rows stay in published order", () => {
        const sources = [
            {
                id: 1, channel_key: "repo", channel_type: "github", enabled: true,
                channel_config: { repo: "toeverything/AFFiNE" }, channel_rank: 0,
                source_key: "repo", source_type: "github",
                source_config: { repo: "toeverything/AFFiNE" }, source_rank: 0,
                release_channels: [
                    { release_channel_key: "repo-stable", name: "stable", type: "release", enabled: true },
                ],
            },
            {
                id: 2, channel_key: "container", channel_type: "container", enabled: true,
                channel_config: { image: "toeverything/affine" }, channel_rank: 1,
                source_key: "container", source_type: "container",
                source_config: { image: "toeverything/affine" }, source_rank: 1,
                release_channels: [
                    { release_channel_key: "image-stable", name: "stable", enabled: true },
                ],
            },
        ] satisfies AggregateTracker["sources"]
        const contribution = (
            id: number, sourceKey: "repo" | "container", publishedAt: string,
        ) => ({
            source_release_history_id: id, tracker_name: "affine", source_key: sourceKey,
            source_type: sourceKey === "repo" ? "github" as const : "container" as const,
            contribution_kind: sourceKey === "repo" ? "primary" as const : "supporting" as const,
            version: "0.27.4", name: "0.27.4", tag_name: "0.27.4",
            published_at: publishedAt, url: "https://example.com/0.27.4",
            changelog_url: null, prerelease: false, body: null,
            digest: sourceKey === "container" ? `sha256:${"b".repeat(64)}` : null,
            published_at_source: sourceKey === "container" ? "first_observed" as const : "source" as const,
            app_version: null, chart_version: null, observed_at: publishedAt,
            aliases: sourceKey === "container" ? ["0.27.4"] : [],
        })
        const items = [
            {
                tracker_name: "affine", tracker_release_history_id: 4, identity_key: "0.27.4",
                version: "0.27.4", digest: `sha256:${"b".repeat(64)}`,
                name: "0.27.4", tag_name: "v0.27.4", published_at: "2026-08-18T16:51:39Z",
                url: "https://example.com/v0.27.4", changelog_url: null, prerelease: false,
                body: null, channel_name: "stable", primary_source: {
                    source_key: "repo", source_type: "github", source_release_history_id: 40,
                },
                source_contributions: [
                    contribution(40, "repo", "2026-08-18T16:51:39Z"),
                    contribution(41, "container", "2026-09-15T19:29:42Z"),
                ],
                artifacts: [{
                    artifact_type: "container_image", digest: `sha256:${"b".repeat(64)}`,
                    version: "0.27.4", published_at: "2026-09-15T19:29:42Z",
                    aliases: ["0.27.4"], source_keys: ["container"],
                }],
                created_at: "2026-08-18T16:51:39Z",
            },
            {
                tracker_name: "affine", tracker_release_history_id: 1, identity_key: "0.27.1",
                version: "0.27.1", digest: `sha256:${"a".repeat(64)}`,
                name: "0.27.1", tag_name: "0.27.1", published_at: "2026-09-15T19:29:31Z",
                url: "https://example.com/0.27.1", changelog_url: null, prerelease: false,
                body: null, channel_name: "stable", primary_source: {
                    source_key: "container", source_type: "container", source_release_history_id: 10,
                },
                created_at: "2026-09-15T19:29:31Z",
            },
            {
                tracker_name: "affine", tracker_release_history_id: 26, identity_key: "0.26.0",
                version: "0.26.0", digest: "repo-0260", name: "0.26.0", tag_name: "v0.26.0",
                published_at: "2026-03-05T14:28:07Z", url: "https://example.com/v0.26.0",
                changelog_url: null, prerelease: false, body: null, channel_name: "stable",
                primary_source: { source_key: "repo", source_type: "github", source_release_history_id: 26 },
                created_at: "2026-03-05T14:28:07Z",
            },
            {
                tracker_name: "affine", tracker_release_history_id: 25, identity_key: "0.25.8",
                version: "0.25.8", digest: `sha256:${"c".repeat(64)}`, name: "0.25.8", tag_name: "0.25.8",
                published_at: "2026-09-15T19:20:11Z", url: "https://example.com/0.25.8",
                changelog_url: null, prerelease: false, body: null, channel_name: "stable",
                primary_source: { source_key: "container", source_type: "container", source_release_history_id: 25 },
                source_contributions: [{
                    ...contribution(25, "container", "2026-09-15T19:20:11Z"),
                    version: "0.25.8", name: "0.25.8", tag_name: "0.25.8",
                    aliases: ["0.25.8"],
                }],
                created_at: "2026-09-15T19:20:11Z",
            },
        ] satisfies ReleaseHistoryItem[]

        const model = buildTrackerHistoryMatrixPresentationModel(sources, items, "published_at")

        expect(model.rows.map((row) => row.displayVersion)).toEqual([
            "0.27.4", "0.27.1", "0.26.0", "0.25.8",
        ])
        expect(model.rows[0]?.publishedAt).toBe("2026-09-15T19:29:42Z")
        expect(model.rows[0]?.sourceTypeBadges).toEqual(["github", "container"])
        expect(model.rows[0]?.artifacts).toEqual(items[0]?.artifacts)
    })

    it("keeps exact release tag as display version and exposes same-artifact aliases", () => {
        const version = "3.6.0-dev-0b0d27d3f61c"
        const aliases = [version, "3.6.0-dev", "3.6-dev", "dev"]
        const model = buildTrackerCurrentMatrixPresentationModel({
            columns: [],
            rows: [{
                tracker_release_history_id: 1,
                identity_key: version,
                version,
                digest: "sha256:ec114",
                published_at: "2026-09-15T04:01:04Z",
                matched_channel_count: 1,
                channel_keys: ["prerelease"],
                primary_source: null,
                aliases,
                artifacts: [
                    {
                        artifact_type: "container_image", digest: "sha256:ec114",
                        version, published_at: "2026-09-15T04:01:04Z",
                        aliases, source_keys: ["container"],
                    },
                    {
                        artifact_type: "container_image", digest: "sha256:previous",
                        version, published_at: "2026-09-14T04:01:04Z",
                        aliases: [version], source_keys: ["container"],
                    },
                ],
                source_contributions: [{
                    source_release_history_id: 2,
                    tracker_name: "nginx_frontend",
                    source_key: "container",
                    source_type: "container",
                    contribution_kind: "supporting",
                    version: "3.6.0-dev",
                    name: "3.6.0-dev",
                    tag_name: "3.6.0-dev",
                    published_at: "2026-09-15T04:00:43Z",
                    url: "https://registry.example.com/3.6.0-dev",
                    changelog_url: null,
                    prerelease: true,
                    body: null,
                    digest: "sha256:ec114",
                    app_version: null,
                    chart_version: null,
                    observed_at: "2026-09-15T04:02:00Z",
                    aliases,
                }],
                cells: { prerelease: { channel_key: "prerelease", channel_type: "prerelease", selected: true } },
            }],
        } satisfies TrackerCurrentView["matrix"], "published_at")

        expect(model.rows).toHaveLength(1)
        expect(model.rows[0]?.displayVersion).toBe(version)
        expect(model.rows[0]?.aliases).toEqual(aliases)
        expect(model.rows[0]?.artifacts).toEqual([
            {
                artifact_type: "container_image", digest: "sha256:ec114",
                version, published_at: "2026-09-15T04:01:04Z",
                aliases, source_keys: ["container"],
            },
            {
                artifact_type: "container_image", digest: "sha256:previous",
                version, published_at: "2026-09-14T04:01:04Z",
                aliases: [version], source_keys: ["container"],
            },
        ])
        expect(model.rows[0]?.sourceTypeBadges).toEqual(["container"])

        const aliasRows = buildTrackerAliasTableRows(model.rows[0]!)
        expect(aliasRows.map((row) => ({
            alias: row.alias,
            sourceKey: row.sourceKey,
            artifactType: row.artifactType,
            artifactDigest: row.artifactDigest,
        }))).toEqual([
            {
                alias: "3.6-dev", sourceKey: "container",
                artifactType: "container_image", artifactDigest: "sha256:ec114",
            },
            {
                alias: "3.6.0-dev", sourceKey: "container",
                artifactType: "container_image", artifactDigest: "sha256:ec114",
            },
            {
                alias: "dev", sourceKey: "container",
                artifactType: "container_image", artifactDigest: "sha256:ec114",
            },
        ])
    })
})
