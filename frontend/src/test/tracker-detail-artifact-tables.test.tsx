import { fireEvent, render, screen, within } from "@testing-library/react"
import { describe, expect, it, vi } from "vitest"

import { TrackerDetail } from "@/components/trackers/TrackerDetail"

const queries = vi.hoisted(() => {
  const refetch = vi.fn().mockResolvedValue(undefined)
  const source = {
    id: 1,
    channel_key: "container",
    channel_type: "container",
    channel_config: { image: "owner/image" },
    channel_rank: 0,
    source_key: "container",
    source_type: "container",
    source_config: { image: "owner/image" },
    source_rank: 0,
    enabled: true,
    release_channels: [{
      release_channel_key: "container-stable",
      name: "stable",
      type: "release",
      enabled: true,
    }],
  }
  const contribution = {
    source_release_history_id: 10,
    tracker_name: "sample",
    source_key: "container",
    source_type: "container",
    contribution_kind: "primary",
    version: "1.2.3",
    name: "1.2.3",
    tag_name: "1.2.3",
    published_at: "2026-09-15T00:00:00Z",
    published_at_source: "artifact_created",
    url: "https://registry.example/owner/image:1.2.3",
    changelog_url: null,
    prerelease: false,
    body: null,
    digest: "sha256:0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
    app_version: null,
    chart_version: null,
    observed_at: "2026-09-15T00:01:00Z",
    aliases: ["1.2.3", "gamma", "stable", "zeta"],
  }
  const artifact = {
    artifact_type: "container_image",
    digest: "sha256:0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
    version: "1.2.3",
    published_at: "2026-09-15T00:00:00Z",
    aliases: ["1.2.3", "gamma", "stable", "zeta"],
    source_keys: ["container"],
  }

  return {
    tracker: {
      data: {
        id: 1,
        name: "sample",
        description: null,
        enabled: true,
        version_sort_mode: "semver",
        primary_changelog_source_key: null,
        sources: [source],
        status: {
          source_count: 1,
          enabled_source_count: 1,
          last_version: "1.2.3",
        },
      },
      isLoading: false,
      isError: false,
      refetch,
    },
    current: {
      data: {
        status: { last_version: "1.2.3" },
        latest_release: { version: "1.2.3" },
      },
      isLoading: false,
      isError: false,
      refetch,
    },
    history: {
      data: {
        items: [{
          tracker_name: "sample",
          tracker_release_history_id: 1,
          identity_key: "1.2.3",
          version: "1.2.3",
          digest: "sha256:0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
          name: "1.2.3",
          tag_name: "1.2.3",
          published_at: "2026-09-15T00:00:00Z",
          url: "https://registry.example/owner/image:1.2.3",
          changelog_url: null,
          prerelease: false,
          body: null,
          channel_name: "stable",
          app_version: null,
          chart_version: null,
          commit_sha: "sha256:0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
          primary_source: {
            source_key: "container",
            source_type: "container",
            source_release_history_id: 10,
          },
          source_contributions: [contribution],
          aliases: ["1.2.3", "gamma", "stable", "zeta"],
          artifacts: [artifact],
          created_at: "2026-09-15T00:00:00Z",
        }],
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
    i18n: { language: "en" },
  }),
}))

vi.mock("@/hooks/queries", () => ({
  useTracker: () => queries.tracker,
  useTrackerCurrentView: () => queries.current,
  useTrackerReleaseHistory: () => queries.history,
}))

describe("TrackerDetail grouped artifact table", () => {
  it("renders collapsible logical versions with aliases merged into the artifact-version column", () => {
    render(<TrackerDetail trackerName="sample" refreshKey={0} />)

    expect(screen.getAllByRole("table")).toHaveLength(1)
    const table = screen.getByRole("table")
    expect(table).toHaveClass("table-fixed")
    expect(table.parentElement).toHaveClass("overflow-hidden")
    expect(screen.getByText("trackers.aggregate.detail.artifactTable.source")).toBeInTheDocument()
    expect(screen.getByText("trackers.aggregate.detail.artifactTable.version")).toBeInTheDocument()
    expect(screen.queryByText("trackers.aggregate.detail.artifactTable.aliases")).not.toBeInTheDocument()
    const artifactVersionCell = screen.getByText("stable").closest("td")
    expect(artifactVersionCell).toHaveTextContent("1.2.3")
    expect(within(artifactVersionCell as HTMLTableCellElement).getAllByText("1.2.3")).toHaveLength(1)
    expect(artifactVersionCell?.querySelectorAll("ul > li > span[aria-hidden=true]")).toHaveLength(0)
    expect(artifactVersionCell?.querySelector('ul code[title]')).not.toBeInTheDocument()
    expect(artifactVersionCell?.parentElement?.querySelectorAll("td.align-middle")).toHaveLength(4)
    expect(table.querySelector('code[title="container"]')).not.toBeInTheDocument()
    expect(table.querySelector('[title="2026-09-15T00:00:00Z"]')).not.toBeInTheDocument()
    expect(artifactVersionCell?.querySelector("ul")).toBeInTheDocument()

    const collapseButton = screen.getByRole("button", {
      name: "trackers.aggregate.detail.collapseVersion",
    })
    const versionGroupRow = collapseButton.closest("tr") as HTMLTableRowElement
    expect(within(versionGroupRow).getByText("container")).toBeInTheDocument()

    fireEvent.click(collapseButton)
    expect(screen.queryByText("trackers.aggregate.detail.artifactTable.source")).not.toBeInTheDocument()
    expect(within(versionGroupRow).getByText("container")).toBeInTheDocument()
    expect(screen.queryByText("stable")).not.toBeInTheDocument()
    expect(screen.queryByText("sha256:0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef")).not.toBeInTheDocument()

    fireEvent.click(screen.getByRole("button", {
      name: "trackers.aggregate.detail.expandVersion",
    }))
    expect(screen.getByText("trackers.aggregate.detail.artifactTable.source")).toBeInTheDocument()
    expect(screen.getByText("stable")).toBeInTheDocument()
    expect(screen.queryByText("zeta")).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole("button", {
      name: "trackers.aggregate.detail.showMoreAliases",
    }))
    expect(screen.getByText("zeta")).toBeInTheDocument()
    const digest = "sha256:0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
    const digestNodes = screen.getAllByText(digest)
    expect(digestNodes).toHaveLength(2) // Desktop column and narrow-screen inline value.
    for (const node of digestNodes) {
      expect(node.textContent).toBe(digest)
      expect(node).toHaveAttribute("title", digest)
      expect(node).toHaveClass("min-w-0", "flex-1", "truncate")
    }
    expect(screen.queryByText("sha256:01234567…abcdef")).not.toBeInTheDocument()
    const writeText = vi.fn().mockResolvedValue(undefined)
    vi.stubGlobal("navigator", { clipboard: { writeText } })
    try {
      fireEvent.click(screen.getAllByRole("button", {
        name: "trackers.aggregate.detail.copyDigest",
      })[0])
      expect(writeText).toHaveBeenCalledWith(digest)
    } finally {
      vi.unstubAllGlobals()
    }
    expect(screen.getAllByRole("button", {
      name: "trackers.aggregate.detail.copyDigest",
    })).toHaveLength(2)
  })
})
