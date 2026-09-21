import { fireEvent, render, screen } from "@testing-library/react"
import { describe, expect, it, vi } from "vitest"
import { TrackerDetail } from "@/components/trackers/TrackerDetail"

const queries = vi.hoisted(() => {
  const refetch = vi.fn().mockResolvedValue(undefined)
  const tracker = {
    id: 1,
    name: "test-tracker",
    enabled: true,
    description: "Sample description",
    primary_changelog_source_key: "git",
    status: {
      error: null,
      last_check: "2026-09-17T12:00:00Z",
      last_version: "1.0.0",
      source_count: 2,
      enabled_source_count: 2,
    },
    sources: [
      {
        source_key: "git",
        source_type: "github",
        enabled: true,
        source_config: { repo: "org/repo" },
        release_channels: [{ release_channel_key: "stable", name: "stable", enabled: true }],
      },
      {
        source_key: "img",
        source_type: "container",
        enabled: true,
        source_config: { image: "org/app" },
        release_channels: [{ release_channel_key: "img-stable", name: "stable", enabled: true }],
      },
    ],
    channels: [{ release_channel_key: "stable", name: "stable", enabled: true }],
  }

  return {
    tracker: { data: tracker, isLoading: false, isError: false, refetch },
    current: {
      data: {
        status: { last_version: "1.0.0" },
        latest_release: { version: "1.0.0" },
      },
      isLoading: false,
      isError: false,
      refetch,
    },
    history: {
      data: {
        items: [
          {
            tracker_name: "test-tracker",
            tracker_release_history_id: 1,
            identity_key: "1.0.0",
            version: "1.0.0",
            digest: "sha256:abc",
            name: "1.0.0",
            tag_name: "v1.0.0",
            published_at: "2026-09-17T10:00:00Z",
            url: "https://example.com",
            changelog_url: null,
            prerelease: false,
            body: "release notes",
            channel_name: "stable",
            app_version: null,
            chart_version: null,
            commit_sha: "sha256:abc",
            primary_source: { source_key: "git", source_type: "github", source_release_history_id: 1 },
            source_contributions: [],
            aliases: ["1.0.0"],
            artifacts: [],
            created_at: "2026-09-17T10:00:00Z",
          },
        ],
      },
      isLoading: false,
      isError: false,
      refetch,
    },
  }
})

vi.mock("react-i18next", () => ({
  initReactI18next: { type: "3rdParty", init: vi.fn() },
  useTranslation: () => ({
    t: (key: string) => key,
    i18n: { language: "zh" },
  }),
}))

vi.mock("@/hooks/queries", () => ({
  useTracker: () => queries.tracker,
  useTrackerCurrentView: () => queries.current,
  useTrackerReleaseHistory: () => queries.history,
}))

describe("TrackerDetail layout improvements", () => {
  it("renders check and edit action buttons in summary card and triggers callbacks", () => {
    const onCheck = vi.fn()
    const onEdit = vi.fn()

    render(
      <TrackerDetail
        trackerName="test-tracker"
        refreshKey={0}
        onCheck={onCheck}
        onEdit={onEdit}
      />
    )

    const checkBtn = screen.getByRole("button", { name: /common.check/ })
    const editBtn = screen.getByRole("button", { name: /common.edit/ })
    expect(checkBtn).toBeInTheDocument()
    expect(editBtn).toBeInTheDocument()

    fireEvent.click(checkBtn)
    expect(onCheck).toHaveBeenCalledWith("test-tracker")

    fireEvent.click(editBtn)
    expect(onEdit).toHaveBeenCalledWith("test-tracker")
  })

  it("displays loading spinner when checking is true", () => {
    render(
      <TrackerDetail
        trackerName="test-tracker"
        refreshKey={0}
        onCheck={vi.fn()}
        onEdit={vi.fn()}
        checking={true}
      />
    )

    const checkBtn = screen.getByRole("button", { name: /common.check/ })
    expect(checkBtn).toBeDisabled()
  })

  it("renders collapsible source channels section", () => {
    render(<TrackerDetail trackerName="test-tracker" refreshKey={0} />)

    expect(screen.getByText("trackers.aggregate.detail.trackerChannelsTitle")).toBeInTheDocument()
    const toggleBtn = screen.getByRole("button", { name: "Toggle channels" })
    expect(toggleBtn).toBeInTheDocument()

    // Initially open: source keys are visible
    expect(screen.getAllByText("git").length).toBeGreaterThanOrEqual(1)
    expect(screen.getByText("img")).toBeInTheDocument()

    // Collapse
    fireEvent.click(toggleBtn)
  })

  it("exposes published relative time on uncollapsed version row", () => {
    render(<TrackerDetail trackerName="test-tracker" refreshKey={0} />)

    // Version row displays version name
    expect(screen.getAllByText("1.0.0").length).toBeGreaterThanOrEqual(1)
    // Should display published time
    expect(screen.getByTitle(/2026/)).toBeInTheDocument()
  })
})
