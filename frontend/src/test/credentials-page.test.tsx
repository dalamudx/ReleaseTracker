import { describe, it, expect, vi } from "vitest"
import { render, screen } from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import CredentialsPage from "@/pages/Credentials"
import type { ApiCredential } from "@/api/types"

vi.mock("react-i18next", async (importOriginal) => ({
  ...await importOriginal<typeof import("react-i18next")>(),
  ...await import("./i18n-mock"),
}))

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
    expect(screen.getByPlaceholderText("credentials.searchPlaceholder")).toBeInTheDocument()
    expect(screen.getByRole("combobox", { name: "credentials.filters.categoryLabel" })).toBeInTheDocument()
    expect(screen.getByRole("combobox", { name: "credentials.filters.typeLabel" })).toBeInTheDocument()
    expect(screen.getByText("credentials.filters.totalCount:3")).toBeInTheDocument()
    expect(screen.getByRole("button", { name: "credentials.addNew" })).toBeInTheDocument()
    expect(screen.getByTitle("common.refresh")).toBeInTheDocument()
    expect(screen.getByText("credentials.table.references")).toBeInTheDocument()
    expect(screen.queryByText("credentials.references.runtimeConnections")).not.toBeInTheDocument()
    expect(screen.getByText("github-pat")).toBeInTheDocument()
    expect(screen.queryByText("credentials.filters.categoryTracker")).not.toBeInTheDocument()
    expect(screen.queryByText("credentials.filters.categorySsh")).not.toBeInTheDocument()
    expect(screen.getByText("k8s-cluster-token")).toBeInTheDocument()
    expect(screen.getByLabelText("credentials.references.runtimeConnections: 3")).toHaveTextContent("3")
    expect(screen.getByText("bastion-ssh")).toBeInTheDocument()
  })

})
