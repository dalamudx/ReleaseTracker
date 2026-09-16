import { fireEvent, render, screen } from "@testing-library/react"
import { beforeEach, describe, expect, it, vi } from "vitest"

const mutateAsync = vi.fn()
let mockedDeliveries: Array<Record<string, unknown>> = []

vi.mock("react-i18next", () => ({
    useTranslation: () => ({ t: (key: string) => key }),
}))

vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }))

vi.mock("@/hooks/queries", () => ({
    useRepositoryWebhooks: () => ({
        isLoading: false,
        data: [{
            id: "hook-id",
            tracker_source_id: 11,
            tracker_name: "sample",
            source_key: "upstream",
            provider: "github",
            enabled: true,
            auth_mode: "hmac",
            secret_configured: true,
            endpoint_url: "https://tracker.test/api/webhooks/repository/hook-id",
            release_published: true,
            workflow_success: true,
            linked_source_ids: [12],
            branches: ["main"],
            workflows: ["publish.yml"],
            created_at: 1,
            updated_at: 1,
        }],
    }),
    useTrackers: () => ({
        data: { items: [{
            name: "sample",
            sources: [
                { id: 11, source_key: "upstream", source_type: "github", channel_type: "github" },
                { id: 12, source_key: "image", source_type: "container", channel_type: "container" },
            ],
        }] },
    }),
    useRepositoryWebhookDeliveries: () => ({ data: mockedDeliveries }),
    useCreateRepositoryWebhook: () => ({ mutateAsync, isPending: false }),
    useUpdateRepositoryWebhook: () => ({ mutateAsync, isPending: false }),
    useDeleteRepositoryWebhook: () => ({ mutateAsync, isPending: false }),
}))

import { RepositoryWebhookSettings } from "@/components/settings/RepositoryWebhookSettings"

describe("RepositoryWebhookSettings", () => {
    beforeEach(() => {
        mutateAsync.mockReset()
        mockedDeliveries = []
    })

    it("shows repository identity, both event types, endpoint and safe edit form", () => {
        render(<RepositoryWebhookSettings />)
        expect(screen.getByText("sample")).toBeInTheDocument()
        expect(screen.getByText("upstream · github")).toBeInTheDocument()
        expect(screen.getByText("webhooks.repository.release")).toBeInTheDocument()
        expect(screen.getByText("webhooks.repository.workflow")).toBeInTheDocument()
        expect(screen.getByText("https://tracker.test/api/webhooks/repository/hook-id")).toBeInTheDocument()

        fireEvent.click(screen.getByRole("button", { name: "common.edit" }))
        expect(screen.getByRole("dialog")).toBeInTheDocument()
        expect(screen.getByPlaceholderText("webhooks.repository.secretUnchanged")).toHaveValue("")
        expect(screen.queryByDisplayValue("0123456789abcdef")).not.toBeInTheDocument()
        expect(screen.getByText("webhooks.repository.branchesHelp")).toBeInTheDocument()
        expect(screen.getByText("webhooks.repository.workflowsHelp")).toBeInTheDocument()
    })

    it("preserves comma-separated filters while editing and submits normalized lists", async () => {
        render(<RepositoryWebhookSettings />)
        fireEvent.click(screen.getByRole("button", { name: "common.edit" }))
        const branches = screen.getByLabelText("webhooks.repository.branches")
        fireEvent.change(branches, { target: { value: "main, release/*" } })
        expect(branches).toHaveValue("main, release/*")
        fireEvent.click(screen.getByRole("button", { name: "common.save" }))
        expect(mutateAsync).toHaveBeenCalledWith(expect.objectContaining({
            id: "hook-id",
            data: expect.objectContaining({ branches: ["main", "release/*"] }),
        }))
    })

    it("shows per-source delivery feedback without exposing payloads", () => {
        mockedDeliveries = [{
            id: 1, state: "deferred", reason: "", duplicates: 0, received_at: 1,
            summary: { kind: "workflow", workflow: "publish.yml" },
            requests: [{
                tracker_source_id: 12, source_key: "image", state: "deferred",
                reason: "source_cooldown", due_at: 2, attempts: 1, source_fetch_run_id: null,
            }],
        }]
        render(<RepositoryWebhookSettings />)
        fireEvent.click(screen.getByRole("button", { name: "webhooks.repository.deliveries" }))
        const dialog = screen.getByRole("dialog")
        expect(dialog).toHaveTextContent("image")
        expect(dialog).toHaveTextContent("webhooks.states.deferred")
        expect(dialog).toHaveTextContent("source_cooldown")
        expect(dialog).not.toHaveTextContent("payload")
    })
})
