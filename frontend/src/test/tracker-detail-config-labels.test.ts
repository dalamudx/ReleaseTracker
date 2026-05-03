import { describe, expect, it } from "vitest"

import { getTrackerChannelConfigValueLabel } from "@/components/trackers/trackerDetailHelpers"

const t = ((key: string) => {
  const labels: Record<string, string> = {
    "tracker.fields.githubFetchModeRest": "REST 优先",
    "tracker.fields.githubFetchModeGraphql": "GraphQL 优先",
  }
  return labels[key] ?? key
}) as Parameters<typeof getTrackerChannelConfigValueLabel>[2]

describe("tracker detail config value labels", () => {
  it("localizes GitHub fetch priority values", () => {
    expect(getTrackerChannelConfigValueLabel("fetch_mode", "rest_first", t)).toBe("REST 优先")
    expect(getTrackerChannelConfigValueLabel("fetch_mode", "graphql_first", t)).toBe("GraphQL 优先")
  })

  it("keeps unrelated config values unchanged", () => {
    expect(getTrackerChannelConfigValueLabel("repo", "owner/repo", t)).toBe("owner/repo")
  })
})
