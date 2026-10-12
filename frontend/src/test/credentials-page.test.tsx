import { describe, it, expect, vi } from "vitest"
import { render, screen } from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import CredentialsPage from "@/pages/Credentials"
import zhLocale from "@/i18n/locales/zh.json"
import enLocale from "@/i18n/locales/en.json"
import type { ApiCredential } from "@/api/types"

vi.mock("react-i18next", async (importOriginal) => {
  const actual = await importOriginal<typeof import("react-i18next")>()
  return {
    ...actual,
    useTranslation: () => ({
      t: (key: string, options?: Record<string, unknown>) => {
        if (key === "credentials.filters.totalCount") {
          return `共 ${options?.count ?? 0} 个凭证`
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

const mockCredentials: ApiCredential[] = [
  {
    id: 1,
    name: "github-pat",
    type: "github",
    token: "ghp_xxxx",
    description: "GitHub source access",
    created_at: "2026-09-20T10:00:00Z",
  },
  {
    id: 2,
    name: "k8s-cluster-token",
    type: "kubernetes_runtime",
    token: "ey...",
    description: "Production cluster",
    created_at: "2026-09-19T10:00:00Z",
    runtime_connections_count: 3,
  },
  {
    id: 3,
    name: "bastion-ssh",
    type: "ssh",
    token: "",
    description: "Jump host SSH key",
    created_at: "2026-09-18T10:00:00Z",
  },
]

vi.mock("@/hooks/queries", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/hooks/queries")>()
  return {
    ...actual,
    useCredentials: vi.fn(() => ({
      data: { items: mockCredentials, total: 3 },
      isLoading: false,
      isFetching: false,
    })),
    useDeleteCredential: () => ({ mutateAsync: vi.fn() }),
    useCreateCredential: () => ({ mutateAsync: vi.fn() }),
    useUpdateCredential: () => ({ mutateAsync: vi.fn() }),
    queryKeys: {
      ...actual.queryKeys,
      credentials: () => ["credentials"],
    },
  }
})

vi.mock("@/hooks/use-page-size", () => ({
  usePageSize: () => [20, vi.fn()],
}))

describe("CredentialsPage Visual & Filter Enhancements", () => {
  it("renders all category and type filter controls with summary and add button", () => {
    const queryClient = new QueryClient()
    render(
      <QueryClientProvider client={queryClient}>
        <CredentialsPage />
      </QueryClientProvider>
    )

    // 搜索输入框
    expect(screen.getByPlaceholderText("按名称、描述搜索...")).toBeInTheDocument()

    // 用途分类下拉框
    expect(screen.getByRole("combobox", { name: "凭证用途" })).toBeInTheDocument()

    // 具体类型下拉框
    expect(screen.getByRole("combobox", { name: "凭证类型" })).toBeInTheDocument()

    // 统计总数、刷新和添加按钮
    expect(screen.getByText("共 3 个凭证")).toBeInTheDocument()
    expect(screen.getByRole("button", { name: /添加凭证/ })).toBeInTheDocument()
    expect(screen.getByTitle("刷新")).toBeInTheDocument()

    // 表头为“引用”，不再单列运行时连接
    expect(screen.getByText("引用")).toBeInTheDocument()
    expect(screen.queryByText("运行时连接")).not.toBeInTheDocument()

    // 名称下方不再显示用途图标标签
    expect(screen.getByText("github-pat")).toBeInTheDocument()
    expect(screen.queryByText("来源服务")).not.toBeInTheDocument()
    expect(screen.queryByText("主机 SSH")).not.toBeInTheDocument()

    // 引用以图标加数量展示，并提供可读名称
    expect(screen.getByText("k8s-cluster-token")).toBeInTheDocument()
    expect(screen.getByLabelText("运行时连接: 3")).toHaveTextContent("3")

    expect(screen.getByText("bastion-ssh")).toBeInTheDocument()
  })

  it("verifies i18n keys for credential filters and table in zh and en locales", () => {
    expect(zhLocale.credentials.table.references).toBe("引用")
    expect(enLocale.credentials.table.references).toBe("References")

    expect(zhLocale.credentials.filters.allCategories).toBe("全部用途")
    expect(enLocale.credentials.filters.allCategories).toBe("All categories")

    expect(zhLocale.credentials.filters.categoryTracker).toBe("代码与仓库来源")
    expect(enLocale.credentials.filters.categoryTracker).toBe("Source repositories")

    expect(zhLocale.credentials.filters.categoryRuntime).toBe("容器与集群运行时")
    expect(enLocale.credentials.filters.categoryRuntime).toBe("Container & cluster runtimes")

    expect(zhLocale.credentials.filters.categorySsh).toBe("SSH 主机连接")
    expect(enLocale.credentials.filters.categorySsh).toBe("SSH connections")
  })
})
