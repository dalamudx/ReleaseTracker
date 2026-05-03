import { describe, expect, it } from "vitest"

import { buildTrackerPayload, createDefaultValues } from "@/components/trackers/trackerDialogHelpers"

describe("tracker dialog payload serialization", () => {
    it("serializes redesign tracker payload fields without legacy aliases", () => {
        const values = createDefaultValues()
        values.name = "payload-tracker"

        const payload = buildTrackerPayload(values, "channel-1")

        expect(payload.primary_changelog_source_key).toBe("channel-1")
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
})
