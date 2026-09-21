import { describe, it, expect, vi } from "vitest"
import { render, screen } from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import HistoryPage from "@/pages/History"
import zhLocale from "@/i18n/locales/zh.json"
import enLocale from "@/i18n/locales/en.json"

vi.mock("react-i18next", async (importOriginal) => {
  const actual = await importOriginal<typeof import("react-i18next")>()
  return {
    ...actual,
    useTranslation: () => ({
      t: (key: string, options?: Record<string, unknown>) => {
        if (key === "history.filters.totalCount") {
          return `共 ${options?.count ?? 0} 条记录`
        }
        const parts = key.split(".")
        let cur: unknown = zhLocale
        for (const p of parts) {
          if (!cur) return key
          cur = (cur as Record<string, unknown>)[p]
        }
        return typeof cur === "string" ? cur : key
      },
      i18n: { language: "zh" },
    }),
  }
})

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
    expect(screen.getByPlaceholderText("搜索版本号或内容...")).toBeInTheDocument()

    // 追踪器筛选下拉框
    expect(screen.getByRole("combobox", { name: "追踪器" })).toBeInTheDocument()

    // 渠道类型筛选下拉框
    expect(screen.getByRole("combobox", { name: "发布渠道" })).toBeInTheDocument()

    // 版本类型筛选下拉框
    expect(screen.getByRole("combobox", { name: "发布类型" })).toBeInTheDocument()

    // 统计标签与刷新按钮
    expect(screen.getByText("共 2 条记录")).toBeInTheDocument()
    expect(screen.getByTitle("刷新")).toBeInTheDocument()

    // 渲染各版本条目
    expect(screen.getByText("nginx_frontend")).toBeInTheDocument()
    expect(screen.getByText("v3.6.0-dev")).toBeInTheDocument()
    expect(screen.getByText("backend_service")).toBeInTheDocument()
    expect(screen.getByText("1.2.0")).toBeInTheDocument()
  })

  it("verifies i18n keys for history filters in zh and en locales", () => {
    expect(zhLocale.history.filters.allTrackers).toBe("全部追踪器")
    expect(enLocale.history.filters.allTrackers).toBe("All trackers")

    expect(zhLocale.history.filters.allChannelTypes).toBe("全部渠道类型")
    expect(enLocale.history.filters.allChannelTypes).toBe("All source types")

    expect(zhLocale.history.filters.reset).toBe("重置筛选")
    expect(enLocale.history.filters.reset).toBe("Reset filters")
  })
})
