import { describe, expect, it } from "vitest"

import {
    buildTrackerPayload,
    createDefaultValues,
    getEffectiveCustomChangelogSourceKey,
    getSupportedChangelogSources,
} from "@/components/trackers/trackerDialogHelpers"
import type { TrackerFormValues } from "@/components/trackers/trackerDialogHelpers"

describe("tracker dialog payload serialization", () => {
    it("serializes redesign tracker payload fields without legacy aliases", () => {
        const values = createDefaultValues()
        values.name = "payload-tracker"

        const payload = buildTrackerPayload(values, "channel-1")

        expect(payload.primary_changelog_source_key).toBe("channel-1")
        expect(payload.release_notes).toMatchObject({
            source: "release_notes",
        })
        expect("primary_changelog_channel_key" in payload).toBe(false)
        expect("tracker_channels" in payload).toBe(false)
        expect(payload.sources).toHaveLength(1)
        expect(payload.sources[0]).toMatchObject({
            source_key: "source-1",
            source_type: "github",
            source_rank: 0,
        })
        expect("channel_key" in payload.sources[0]).toBe(false)
        expect("channel_type" in payload.sources[0]).toBe(false)
        expect("channel_config" in payload.sources[0]).toBe(false)
        expect("channel_rank" in payload.sources[0]).toBe(false)
        expect(payload.channels).toHaveLength(1)
    })

    it("serializes custom changelog release notes config", () => {
        const values = createDefaultValues()
        values.name = "custom-changelog-tracker"
        values.release_notes = {
            source: "custom_changelog",
            changelog_source_key: "source-1",
            path_template: "CHANGELOG/CHANGELOG-{major}.{minor}.md",
            ref_strategy: "release_tag",
            ref: "",
            extraction_mode: "version_section_from_subheading",
            version_heading_template: "# {tag}",
            subheading_prefix: "Changelog since",
        }

        const payload = buildTrackerPayload(values, "source-1")

        expect(payload.release_notes).toMatchObject({
            source: "custom_changelog",
            changelog_source_key: "source-1",
            path_template: "CHANGELOG/CHANGELOG-{major}.{minor}.md",
            ref_strategy: "release_tag",
            extraction_mode: "version_section_from_subheading",
            version_heading_template: "# {tag}",
            subheading_prefix: "Changelog since",
        })
    })
})

describe("getSupportedChangelogSources", () => {
    it("returns empty array when no repository sources exist", () => {
        const values = createDefaultValues()
        values.sources = [
            {
                source_key: "helm-source",
                source_type: "helm",
                enabled: true,
                source_config: { repo: "https://charts.example.com", chart: "my-chart" },
                release_channels: [],
                source_rank: 0,
            },
        ]

        const supported = getSupportedChangelogSources(values)
        expect(supported).toHaveLength(0)
    })

    it("returns only github/gitlab/gitea sources", () => {
        const values = createDefaultValues()
        values.sources = [
            {
                source_key: "gh-source",
                source_type: "github",
                enabled: true,
                source_config: { repo: "owner/repo" },
                release_channels: [],
                source_rank: 0,
            },
            {
                source_key: "helm-source",
                source_type: "helm",
                enabled: true,
                source_config: { repo: "https://charts.example.com", chart: "my-chart" },
                release_channels: [],
                source_rank: 1,
            },
            {
                source_key: "gl-source",
                source_type: "gitlab",
                enabled: true,
                source_config: { project: "group/project" },
                release_channels: [],
                source_rank: 2,
            },
        ]

        const supported = getSupportedChangelogSources(values)
        expect(supported).toHaveLength(2)
        expect(supported.map((s) => s.source_key)).toEqual(["gh-source", "gl-source"])
    })
})

describe("getEffectiveCustomChangelogSourceKey", () => {
    it("returns empty string when no supported sources exist", () => {
        const values: Pick<TrackerFormValues, "sources" | "release_notes"> = {
            sources: [
                {
                    source_key: "helm-source",
                    source_type: "helm",
                    enabled: true,
                    source_config: { repo: "https://charts.example.com", chart: "my-chart" },
                    release_channels: [],
                    source_rank: 0,
                },
            ],
            release_notes: {
                source: "custom_changelog",
                changelog_source_key: "",
                path_template: "CHANGELOG.md",
                ref_strategy: "default_branch",
                ref: "",
                extraction_mode: "version_section",
                version_heading_template: "",
                subheading_prefix: "",
            },
        }

        expect(getEffectiveCustomChangelogSourceKey(values)).toBe("")
    })

    it("selects the configured source key when it matches a supported source", () => {
        const values: Pick<TrackerFormValues, "sources" | "release_notes"> = {
            sources: [
                {
                    source_key: "gh-source",
                    source_type: "github",
                    enabled: true,
                    source_config: { repo: "owner/repo" },
                    release_channels: [],
                    source_rank: 0,
                },
                {
                    source_key: "gl-source",
                    source_type: "gitlab",
                    enabled: true,
                    source_config: { project: "group/project" },
                    release_channels: [],
                    source_rank: 1,
                },
            ],
            release_notes: {
                source: "custom_changelog",
                changelog_source_key: "gl-source",
                path_template: "CHANGELOG.md",
                ref_strategy: "default_branch",
                ref: "",
                extraction_mode: "version_section",
                version_heading_template: "",
                subheading_prefix: "",
            },
        }

        expect(getEffectiveCustomChangelogSourceKey(values)).toBe("gl-source")
    })

    it("falls back to first supported source when configured key is not in supported sources", () => {
        const values: Pick<TrackerFormValues, "sources" | "release_notes"> = {
            sources: [
                {
                    source_key: "gh-source",
                    source_type: "github",
                    enabled: true,
                    source_config: { repo: "owner/repo" },
                    release_channels: [],
                    source_rank: 0,
                },
            ],
            release_notes: {
                source: "custom_changelog",
                changelog_source_key: "nonexistent-source",
                path_template: "CHANGELOG.md",
                ref_strategy: "default_branch",
                ref: "",
                extraction_mode: "version_section",
                version_heading_template: "",
                subheading_prefix: "",
            },
        }

        expect(getEffectiveCustomChangelogSourceKey(values)).toBe("gh-source")
    })
})
