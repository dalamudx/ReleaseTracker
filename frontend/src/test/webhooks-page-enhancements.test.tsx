import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { fireEvent, render, screen } from "@testing-library/react"
import { describe, expect, it, vi } from "vitest"

import { RepositoryWebhookSettings } from "@/components/settings/RepositoryWebhookSettings"
import WebhooksPage from "@/pages/Webhooks"

vi.mock("react-i18next", () => ({
    useTranslation: () => ({
        t: (key: string, options?: Record<string, unknown>) => {
            if (key === "webhooks.repository.totalCount") {
                return `共 ${options?.count ?? 0} 个配置`
            }
            return key
        },
    }),
}))

vi.mock("sonner", () => ({
    toast: { success: vi.fn(), error: vi.fn() },
}))

const mockRepoHooks = [
    {
        id: "hook-1",
        tracker_source_id: 101,
        tracker_name: "nginx-ingress",
        source_key: "release",
        provider: "github" as const,
        enabled: true,
        auth_mode: "hmac" as const,
        secret_configured: true,
        endpoint_url: "https://hub.example.test/api/webhooks/repository/hook-1",
        release_published: true,
        workflow_success: true,
        linked_source_ids: [102],
        branches: ["main"],
        workflows: ["ci.yml"],
        created_at: 1000,
        updated_at: 1000,
    },
    {
        id: "hook-2",
        tracker_source_id: 201,
        tracker_name: "affine-core",
        source_key: "app",
        provider: "gitlab" as const,
        enabled: false,
        auth_mode: "gitlab_signing" as const,
        secret_configured: true,
        endpoint_url: "https://hub.example.test/api/webhooks/repository/hook-2",
        release_published: true,
        workflow_success: false,
        linked_source_ids: [],
        branches: [],
        workflows: [],
        created_at: 2000,
        updated_at: 2000,
    },
]

vi.mock("@/hooks/queries", () => ({
    useNotifiers: () => ({
        data: { items: [], total: 3 },
        isLoading: false,
    }),
    useRepositoryWebhooks: () => ({
        data: mockRepoHooks,
        isLoading: false,
        refetch: vi.fn(),
        isFetching: false,
    }),
    useTrackers: () => ({
        data: {
            items: [
                {
                    name: "nginx-ingress",
                    sources: [
                        { id: 101, source_key: "release", source_type: "github", channel_type: "github" },
                        { id: 102, source_key: "docker", source_type: "container", channel_type: "container" },
                    ],
                },
            ],
        },
    }),
    useRepositoryWebhookDeliveries: () => ({
        data: [],
        isLoading: false,
    }),
    useCreateRepositoryWebhook: () => ({ mutateAsync: vi.fn(), isPending: false }),
    useUpdateRepositoryWebhook: () => ({ mutateAsync: vi.fn(), isPending: false }),
    useDeleteRepositoryWebhook: () => ({ mutateAsync: vi.fn(), isPending: false }),
    useCreateNotifier: () => ({ mutateAsync: vi.fn() }),
    useUpdateNotifier: () => ({ mutateAsync: vi.fn() }),
    useDeleteNotifier: () => ({ mutateAsync: vi.fn() }),
    useTestNotifier: () => ({ mutateAsync: vi.fn() }),
}))

vi.mock("@/hooks/use-page-size", () => ({
    usePageSize: () => [20, vi.fn()],
}))

describe("WebhooksPage & RepositoryWebhookSettings Enhancements", () => {
    it("renders tabs with count badges for outgoing and repository webhooks", () => {
        const queryClient = new QueryClient()
        render(
            <QueryClientProvider client={queryClient}>
                <WebhooksPage />
            </QueryClientProvider>
        )

        expect(screen.getByRole("tab", { name: /webhooks\.tabs\.outgoing/ })).toBeInTheDocument()
        expect(screen.getByText("3")).toBeInTheDocument()

        const repoTab = screen.getByRole("tab", { name: /webhooks\.tabs\.repository/ })
        expect(repoTab).toBeInTheDocument()
        expect(screen.getByText("2")).toBeInTheDocument()
    })

    it("filters repository webhook rows by search keyword", () => {
        const queryClient = new QueryClient()
        render(
            <QueryClientProvider client={queryClient}>
                <RepositoryWebhookSettings />
            </QueryClientProvider>
        )

        expect(screen.getByText("nginx-ingress")).toBeInTheDocument()
        expect(screen.getByText("affine-core")).toBeInTheDocument()
        expect(screen.getByText("共 2 个配置")).toBeInTheDocument()

        // Type in search input
        const searchInput = screen.getByPlaceholderText("webhooks.repository.searchPlaceholder")
        fireEvent.change(searchInput, { target: { value: "affine" } })

        expect(screen.queryByText("nginx-ingress")).not.toBeInTheDocument()
        expect(screen.getByText("affine-core")).toBeInTheDocument()
        expect(screen.getByText("共 1 个配置")).toBeInTheDocument()

        // Clear search
        const clearBtn = screen.getByTitle("common.clear")
        fireEvent.click(clearBtn)

        expect(screen.getByText("nginx-ingress")).toBeInTheDocument()
        expect(screen.getByText("affine-core")).toBeInTheDocument()
    })
})
