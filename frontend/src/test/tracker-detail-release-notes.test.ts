import { describe, expect, it } from "vitest"
import type { TFunction } from "i18next"

import type { TrackerCurrentMatrixRow, TrackerCurrentSourceContribution } from "@/api/types"
import { getReleaseChannelBadgeText, getReleaseChannelDisplayLabel } from "@/components/dashboard/releaseNotesModalHelpers"
import { getPreferredTrackerCurrentContributionForRow } from "@/components/trackers/canonicalReleaseMatrixModel"

function buildContribution(overrides: Partial<TrackerCurrentSourceContribution>): TrackerCurrentSourceContribution {
    return {
        source_release_history_id: overrides.source_release_history_id ?? 1,
        tracker_name: overrides.tracker_name ?? "mixed-current-tracker",
        tracker_type: overrides.tracker_type,
        source_key: overrides.source_key ?? `source-${overrides.source_release_history_id ?? 1}`,
        source_type: overrides.source_type ?? "github",
        contribution_kind: overrides.contribution_kind ?? "supporting",
        version: overrides.version ?? "1.0.0",
        name: overrides.name ?? `Release ${overrides.version ?? "1.0.0"}`,
        tag_name: overrides.tag_name ?? `v${overrides.version ?? "1.0.0"}`,
        published_at: overrides.published_at ?? "2026-01-01T00:00:00Z",
        url: overrides.url ?? "https://example.com/release",
        changelog_url: overrides.changelog_url ?? null,
        prerelease: overrides.prerelease ?? false,
        body: overrides.body ?? null,
        digest: overrides.digest ?? `digest-${overrides.source_release_history_id ?? 1}`,
        app_version: overrides.app_version ?? null,
        chart_version: overrides.chart_version ?? null,
        observed_at: overrides.observed_at ?? "2026-01-01T00:00:00Z",
    }
}

describe("getPreferredTrackerCurrentContributionForRow", () => {
    it("prefers repo-backed contribution with notes for mixed-source current rows", () => {
        const row: Pick<TrackerCurrentMatrixRow, "source_contributions"> = {
            source_contributions: [
                buildContribution({
                    source_release_history_id: 1,
                    published_at: "2026-01-03T00:00:00Z",
                    body: null,
                    source_key: "image",
                    source_type: "container",
                }),
                buildContribution({
                    source_release_history_id: 2,
                    published_at: "2026-01-01T00:00:00Z",
                    body: "repo notes",
                    source_key: "repo",
                    source_type: "github",
                }),
                buildContribution({
                    source_release_history_id: 3,
                    published_at: "2026-01-02T00:00:00Z",
                    body: null,
                    source_key: "helm",
                    source_type: "helm",
                }),
            ],
        }

        const preferred = getPreferredTrackerCurrentContributionForRow(row)

        expect(preferred?.source_release_history_id).toBe(2)
        expect(preferred?.body).toBe("repo notes")
    })

    it("uses explicit channel names instead of prerelease fallback labels", () => {
        const t = ((key: string) => `translated:${key}`) as unknown as TFunction

        expect(getReleaseChannelDisplayLabel({ channel_name: null }, t)).toBeNull()
        expect(getReleaseChannelDisplayLabel({ channel_name: "stable" }, t)).toBe("translated:channel.stable")
        expect(getReleaseChannelDisplayLabel({ channel_name: "rollout-a" }, t)).toBe("rollout-a")
    })

    it("falls back to channel keys when channel_name is absent", () => {
        const t = ((key: string) => `translated:${key}`) as unknown as TFunction

        expect(getReleaseChannelDisplayLabel({ channel_name: null, channel_keys: ["repo-prerelease"] }, t)).toBe("translated:channel.prerelease")
    })

    it("uses localized channel labels for release note badges", () => {
        const zhLabels: Record<string, string> = {
            "channel.stable": "正式版",
            "channel.canary": "金丝雀版",
        }
        const t = ((key: string) => zhLabels[key] ?? key) as unknown as TFunction

        expect(getReleaseChannelBadgeText({ channel_name: "stable" }, t)).toBe("正式版")
        expect(getReleaseChannelBadgeText({ channel_name: "canary" }, t)).toBe("金丝雀版")
        expect(getReleaseChannelBadgeText({ channel_name: null, channel_keys: ["repo-canary"] }, t)).toBe("金丝雀版")
    })
})
