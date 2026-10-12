import { describe, expect, it } from "vitest"
import { getArtifactReleaseTimes, getTrackerChannelConfigEntries, getTrackerChannelConfigValueLabel } from "@/components/trackers/trackerDetailHelpers"

const labels: Record<string, string> = {
  "tracker.fields.githubFetchModeRest": "REST 优先",
  "tracker.fields.githubFetchModeGraphql": "GraphQL 优先",
}
const t = ((key: string) => labels[key] ?? key) as Parameters<typeof getTrackerChannelConfigValueLabel>[2]

describe("tracker detail labels and release times", () => {
  it("preserves GitHub labels", () => {
    expect(getTrackerChannelConfigValueLabel("fetch_mode", "rest_first", t)).toBe("REST 优先")
    expect(getTrackerChannelConfigValueLabel("fetch_mode", "graphql_first", t)).toBe("GraphQL 优先")
  })
  it.each(["auto", "prefer_real", "first_observed"])("hides retired mode %s", (mode) => {
    expect(getTrackerChannelConfigEntries({ image: "team/app", published_at_mode: mode })).toEqual([["image", "team/app"]])
  })
  it("does not show discovery, build, or legacy container publication time", () => {
    const artifact = { artifact_type: "container_image", published_at: "2026-10-11T12:00:00Z",
      artifact_created_at: "2026-10-10T12:00:00Z", first_observed_at: "2026-10-12T12:00:00Z" }
    expect(getArtifactReleaseTimes(artifact)).toEqual([])
  })
  it("only shows a known container push time", () => {
    expect(getArtifactReleaseTimes({ artifact_type: "container_image", registry_pushed_at: "2026-10-11T12:00:00Z" }))
      .toEqual([{ kind: "pushed", value: "2026-10-11T12:00:00Z" }])
  })
  it("distinguishes GHCR registration and registry modification dates", () => {
    expect(getArtifactReleaseTimes({ artifact_type: "container_image", registry_created_at: "2026-10-11T12:00:00Z" }))
      .toEqual([{ kind: "registered", value: "2026-10-11T12:00:00Z" }])
    expect(getArtifactReleaseTimes({ artifact_type: "container_image", registry_modified_at: "2026-10-11T12:00:00Z" }))
      .toEqual([{ kind: "modified", value: "2026-10-11T12:00:00Z" }])
    expect(getArtifactReleaseTimes({ artifact_type: "container_image", registry_pushed_at: "2026-10-12T12:00:00Z",
      registry_created_at: "2026-10-11T12:00:00Z", registry_modified_at: "2026-10-13T12:00:00Z" }).map(({kind}) => kind))
      .toEqual(["pushed", "registered", "modified"])
  })
  it("preserves GitHub publication and Helm source creation", () => {
    expect(getArtifactReleaseTimes({ artifact_type: "helm_chart", source_created_at: "2026-10-11T12:00:00Z" }))
      .toEqual([{ kind: "index", value: "2026-10-11T12:00:00Z" }])
    expect(getArtifactReleaseTimes({ published_at: "2026-10-11T12:00:00Z" }))
      .toEqual([{ kind: "publication", value: "2026-10-11T12:00:00Z" }])
  })
  it("does not crash or invent dates for missing/invalid times", () => {
    expect(getArtifactReleaseTimes({ published_at: "invalid", registry_pushed_at: null, source_created_at: "" })).toEqual([])
  })
})
