import { describe, it, expect, vi } from "vitest"
import { fireEvent, render, screen } from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import HistoryPage from "@/pages/History"

vi.mock("react-i18next", async (importOriginal) => ({
  ...await importOriginal<typeof import("react-i18next")>(),
  ...await import("./i18n-mock"),
}))

const mockTrackers = [
  {
    name: "nginx_frontend",
    sources: [
      {
        source_key: "gitea_src",
        source_type: "gitea",
        release_channels: [{ name: "stable" }, { name: "prerelease" }],
      },
    ],
  },
  {
    name: "backend_service",
    sources: [
      {
        source_key: "docker_src",
        source_type: "container",
        release_channels: [{ name: "canary" }],
      },
    ],
  },
]

const mockReleases = [
  {
    tracker_release_history_id: 101,
    tracker_name: "nginx_frontend",
    tag_name: "v3.6.0-dev",
    version: "3.6.0-dev",
    prerelease: true,
    published_at: "2026-09-20T10:00:00Z",
    primary_source: { source_type: "gitea", source_key: "gitea_src", source_release_history_id: 1 },
    body: "## Changelog\n- Bugfix A",
    url: "https://gitea.example.test/release/v3.6.0-dev",
  },
  {
    tracker_release_history_id: 102,
    tracker_name: "backend_service",
    tag_name: "1.2.0",
    version: "1.2.0",
    prerelease: false,
    published_at: "2026-09-19T08:00:00Z",
    primary_source: { source_type: "container", source_key: "docker_src", source_release_history_id: 2 },
    body: null,
    url: null,
  },
]

vi.mock("@/hooks/queries", () => ({
  useTrackers: () => ({ data: { items: mockTrackers, total: 2 } }),
  useReleaseHistory: vi.fn(() => ({
    data: { items: mockReleases, total: 2 },
    isLoading: false,
    isFetching: false,
  })),
}))

vi.mock("@/hooks/use-page-size", () => ({
  usePageSize: () => [20, vi.fn()],
}))

describe("HistoryPage Filters and UI Interaction", () => {
  it("renders all enriched filter controls and handles reset", () => {
    const queryClient = new QueryClient()
    render(
      <QueryClientProvider client={queryClient}>
        <HistoryPage />
      </QueryClientProvider>
    )

    // 搜索框
    expect(screen.getByPlaceholderText("history.searchPlaceholder")).toBeInTheDocument()
    expect(screen.getByRole("combobox", { name: "history.filters.trackerLabel" })).toBeInTheDocument()
    expect(screen.getByRole("combobox", { name: "history.filters.channelTypeLabel" })).toBeInTheDocument()
    expect(screen.getByRole("combobox", { name: "history.filters.typeLabel" })).toBeInTheDocument()
    expect(screen.getByText("history.filters.totalCount:2")).toBeInTheDocument()
    expect(screen.getByTitle("common.refresh")).toBeInTheDocument()

    // 渲染各版本条目
    expect(screen.getByText("nginx_frontend")).toBeInTheDocument()
    expect(screen.getByText("v3.6.0-dev")).toBeInTheDocument()
    expect(screen.getByText("backend_service")).toBeInTheDocument()
    expect(screen.getByText("1.2.0")).toBeInTheDocument()
    const searchInput = screen.getByPlaceholderText("history.searchPlaceholder")
    fireEvent.change(searchInput, { target: { value: "release" } })
    expect(searchInput).toHaveValue("release")
    fireEvent.click(screen.getByRole("button", { name: "history.filters.reset" }))
    expect(searchInput).toHaveValue("")
  })

})
