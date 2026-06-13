import { fireEvent, render, screen, waitFor } from "@testing-library/react"
import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest"

import type { RuntimeConnection } from "@/api/types"
import { RuntimeConnectionDialog } from "@/components/runtime-connections/RuntimeConnectionDialog"
import { RuntimeConnectionList } from "@/components/runtime-connections/RuntimeConnectionList"
import { api } from "@/api/client"
import { toast } from "sonner"

vi.mock("sonner", () => ({
    toast: {
        success: vi.fn(),
        error: vi.fn(),
    },
}))

vi.mock("@/api/client", () => ({
    api: {
        createRuntimeConnection: vi.fn(),
        updateRuntimeConnection: vi.fn(),
        discoverKubernetesNamespaces: vi.fn(),
        discoverPortainerEndpoints: vi.fn(),
        getCredentials: vi.fn(),
    },
}))

vi.mock("react-i18next", () => ({
    useTranslation: () => ({
        t: (key: string) => key,
    }),
}))

function createRuntimeConnection(overrides: Partial<RuntimeConnection> = {}): RuntimeConnection {
    return {
        id: overrides.id ?? 1,
        name: overrides.name ?? "portainer-prod",
        type: overrides.type ?? "portainer",
        enabled: overrides.enabled ?? true,
        config: overrides.config ?? {
            base_url: "https://portainer.example.com",
            endpoint_id: 12,
            endpoint_name: "edge",
        },
        credential_id: overrides.credential_id ?? 7,
        credential_name: overrides.credential_name ?? "portainer-credential",
        secrets: overrides.secrets ?? {},
        endpoint: overrides.endpoint ?? null,
        description: overrides.description ?? "Production Portainer",
    }
}

describe("Portainer runtime connection UI", () => {
    beforeAll(() => {
        class ResizeObserverMock {
            observe() {}
            unobserve() {}
            disconnect() {}
        }

        vi.stubGlobal("ResizeObserver", ResizeObserverMock)
        Element.prototype.scrollIntoView = vi.fn()
    })

    beforeEach(() => {
        vi.clearAllMocks()
        vi.mocked(api.getCredentials).mockResolvedValue({ items: [], total: 0 })
    })

    it("renders dedicated Portainer edit fields without Docker runtime inputs", async () => {
        render(
            <RuntimeConnectionDialog
                open
                onOpenChange={vi.fn()}
                runtimeConnection={createRuntimeConnection()}
                onSuccess={vi.fn()}
            />,
        )

        await waitFor(() => expect(api.getCredentials).toHaveBeenCalled())

        expect(screen.getByDisplayValue("https://portainer.example.com")).toBeInTheDocument()
        expect(screen.getByRole("combobox", { name: "runtimeConnections.dialog.fields.endpointName" })).toHaveTextContent("edge(#12)")
        expect(screen.getByText("runtimeConnections.dialog.fields.noCredential")).toBeInTheDocument()
        expect(screen.queryByPlaceholderText("runtimeConnections.dialog.placeholders.apiKey")).not.toBeInTheDocument()
        expect(screen.queryByPlaceholderText("unix:///var/run/docker.sock")).not.toBeInTheDocument()
        expect(screen.queryByPlaceholderText("unix:///var/run/docker.sock or tcp://host:2376")).not.toBeInTheDocument()
        expect(screen.queryByText("runtimeConnections.dialog.fields.username")).not.toBeInTheDocument()
    })

    it("renders Portainer list rows with base url and endpoint summary", () => {
        render(
            <RuntimeConnectionList
                runtimeConnections={[createRuntimeConnection()]}
                loading={false}
                onEdit={vi.fn()}
                onDelete={vi.fn()}
            />,
        )

        expect(screen.getByText("https://portainer.example.com")).toBeInTheDocument()
        expect(screen.getByText("edge(#12)")).toBeInTheDocument()
        expect(screen.getByText("credential")).toBeInTheDocument()
        expect(screen.getByText("portainer-credential")).toBeInTheDocument()
    })

    it("refreshes and persists the current Portainer endpoint name when discovering in edit mode", async () => {
        vi.mocked(api.discoverPortainerEndpoints).mockResolvedValue({
            items: [
                { id: 12, name: "storage" },
                { id: 13, name: "edge" },
            ],
        })
        vi.mocked(api.updateRuntimeConnection).mockResolvedValue({ message: "updated", updated_at: "2026-06-13T00:00:00" })

        render(
            <RuntimeConnectionDialog
                open
                onOpenChange={vi.fn()}
                runtimeConnection={createRuntimeConnection()}
                onSuccess={vi.fn()}
            />,
        )

        await waitFor(() => expect(api.getCredentials).toHaveBeenCalled())
        expect(screen.getByRole("combobox", { name: "runtimeConnections.dialog.fields.endpointName" })).toHaveTextContent("edge(#12)")

        fireEvent.click(screen.getByRole("button", { name: "runtimeConnections.dialog.actions.discoverPortainerEndpoints" }))
        await waitFor(() => expect(api.discoverPortainerEndpoints).toHaveBeenCalled())
        expect(screen.getByRole("combobox", { name: "runtimeConnections.dialog.fields.endpointName" })).toHaveTextContent("storage(#12)")

        fireEvent.click(screen.getByRole("button", { name: "common.save" }))
        await waitFor(() => expect(api.updateRuntimeConnection).toHaveBeenCalled())

        expect(vi.mocked(api.updateRuntimeConnection).mock.calls[0][1]).toMatchObject({
            type: "portainer",
            credential_id: 7,
            config: {
                base_url: "https://portainer.example.com",
                endpoint_id: 12,
                endpoint_name: "storage",
            },
        })
    })

    it("discovers Kubernetes namespaces using a selected runtime credential", async () => {
        vi.mocked(api.getCredentials).mockResolvedValue({
            items: [
                {
                    id: 7,
                    name: "k3s-credential",
                    type: "kubernetes_runtime",
                    token: "",
                    secrets: { kubeconfig: "********" },
                    secret_keys: ["kubeconfig"],
                    description: null,
                    created_at: "2026-05-03T00:00:00",
                },
            ],
            total: 1,
        })
        vi.mocked(api.discoverKubernetesNamespaces).mockResolvedValue({ items: ["default", "apps"] })

        render(
            <RuntimeConnectionDialog
                open
                onOpenChange={vi.fn()}
                runtimeConnection={null}
                onSuccess={vi.fn()}
            />,
        )

        fireEvent.change(screen.getByPlaceholderText("runtimeConnections.dialog.placeholders.name"), {
            target: { value: "k3s" },
        })
        fireEvent.click(screen.getByRole("combobox", { name: "runtimeConnections.dialog.fields.type" }))
        fireEvent.click(screen.getByRole("option", { name: "Kubernetes" }))
        fireEvent.click(screen.getByRole("combobox", { name: "runtimeConnections.dialog.fields.credential" }))
        await waitFor(() => expect(screen.getByRole("option", { name: "k3s-credential" })).toBeInTheDocument())
        fireEvent.click(screen.getByRole("option", { name: "k3s-credential" }))

        fireEvent.click(screen.getByRole("button", { name: "runtimeConnections.dialog.actions.discoverNamespaces" }))
        await waitFor(() => expect(api.discoverKubernetesNamespaces).toHaveBeenCalled())

        expect(vi.mocked(api.discoverKubernetesNamespaces).mock.calls[0][0]).toMatchObject({
            type: "kubernetes",
            credential_id: 7,
            secrets: {},
        })
    })

    it("requires a Kubernetes credential unless in-cluster config is enabled", async () => {
        vi.mocked(api.createRuntimeConnection).mockResolvedValue({ message: "created", id: 1 })

        render(
            <RuntimeConnectionDialog
                open
                onOpenChange={vi.fn()}
                runtimeConnection={null}
                onSuccess={vi.fn()}
            />,
        )

        fireEvent.change(screen.getByPlaceholderText("runtimeConnections.dialog.placeholders.name"), {
            target: { value: "k3s" },
        })
        fireEvent.click(screen.getByRole("combobox", { name: "runtimeConnections.dialog.fields.type" }))
        fireEvent.click(screen.getByRole("option", { name: "Kubernetes" }))

        fireEvent.click(screen.getByRole("button", { name: "common.save" }))

        await waitFor(() => expect(toast.error).toHaveBeenCalledWith("runtimeConnections.dialog.errors.kubernetesCredentialRequired"))
        expect(api.createRuntimeConnection).not.toHaveBeenCalled()
    })

    it("allows Kubernetes save without credentials when in-cluster config is enabled", async () => {
        vi.mocked(api.createRuntimeConnection).mockResolvedValue({ message: "created", id: 1 })

        render(
            <RuntimeConnectionDialog
                open
                onOpenChange={vi.fn()}
                runtimeConnection={null}
                onSuccess={vi.fn()}
            />,
        )

        fireEvent.change(screen.getByPlaceholderText("runtimeConnections.dialog.placeholders.name"), {
            target: { value: "k3s" },
        })
        fireEvent.click(screen.getByRole("combobox", { name: "runtimeConnections.dialog.fields.type" }))
        fireEvent.click(screen.getByRole("option", { name: "Kubernetes" }))
        fireEvent.click(screen.getByRole("switch", { name: "runtimeConnections.dialog.fields.inCluster" }))

        await waitFor(() => expect(screen.getByRole("combobox", { name: "runtimeConnections.dialog.fields.credential" })).toBeDisabled())

        fireEvent.click(screen.getByRole("button", { name: "common.save" }))
        await waitFor(() => expect(api.createRuntimeConnection).toHaveBeenCalled())

        expect(vi.mocked(api.createRuntimeConnection).mock.calls[0][0]).toMatchObject({
            type: "kubernetes",
            credential_id: null,
            config: { in_cluster: true },
            secrets: {},
        })
    })

    it("requires a Portainer credential before saving", async () => {
        render(
            <RuntimeConnectionDialog
                open
                onOpenChange={vi.fn()}
                runtimeConnection={null}
                onSuccess={vi.fn()}
            />,
        )

        fireEvent.change(screen.getByPlaceholderText("runtimeConnections.dialog.placeholders.name"), {
            target: { value: "portainer" },
        })
        fireEvent.click(screen.getByRole("combobox", { name: "runtimeConnections.dialog.fields.type" }))
        fireEvent.click(screen.getByRole("option", { name: "Portainer" }))
        fireEvent.change(screen.getByPlaceholderText("runtimeConnections.dialog.placeholders.baseUrl"), {
            target: { value: "https://portainer.example.com" },
        })

        fireEvent.click(screen.getByRole("button", { name: "common.save" }))

        await waitFor(() => expect(toast.error).toHaveBeenCalledWith("runtimeConnections.dialog.errors.portainerCredentialRequired"))
        expect(api.createRuntimeConnection).not.toHaveBeenCalled()
    })

    it("discovers Portainer endpoints and saves the selected endpoint id", async () => {
        vi.mocked(api.getCredentials).mockResolvedValue({
            items: [
                {
                    id: 7,
                    name: "portainer-credential",
                    type: "portainer_runtime",
                    token: "",
                    secrets: { api_key: "********" },
                    secret_keys: ["api_key"],
                    description: null,
                    created_at: "2026-05-03T00:00:00",
                },
            ],
            total: 1,
        })
        vi.mocked(api.discoverPortainerEndpoints).mockResolvedValue({
            items: [
                { id: 2, name: "prod-docker", type: "docker", status: "up" },
                { id: 4, name: "staging" },
            ],
        })
        vi.mocked(api.createRuntimeConnection).mockResolvedValue({ message: "created", id: 1 })

        render(
            <RuntimeConnectionDialog
                open
                onOpenChange={vi.fn()}
                runtimeConnection={null}
                onSuccess={vi.fn()}
            />,
        )

        fireEvent.change(screen.getByPlaceholderText("runtimeConnections.dialog.placeholders.name"), {
            target: { value: "portainer" },
        })
        fireEvent.click(screen.getByRole("combobox", { name: "runtimeConnections.dialog.fields.type" }))
        fireEvent.click(screen.getByRole("option", { name: "Portainer" }))
        fireEvent.change(screen.getByPlaceholderText("runtimeConnections.dialog.placeholders.baseUrl"), {
            target: { value: "https://portainer.example.com" },
        })
        fireEvent.click(screen.getByRole("combobox", { name: "runtimeConnections.dialog.fields.credential" }))
        await waitFor(() => expect(screen.getByRole("option", { name: "portainer-credential" })).toBeInTheDocument())
        fireEvent.click(screen.getByRole("option", { name: "portainer-credential" }))

        fireEvent.click(screen.getByRole("button", { name: "runtimeConnections.dialog.actions.discoverPortainerEndpoints" }))
        await waitFor(() => expect(api.discoverPortainerEndpoints).toHaveBeenCalled())
        expect(vi.mocked(api.discoverPortainerEndpoints).mock.calls[0][0]).toMatchObject({
            type: "portainer",
            credential_id: 7,
            config: {
                base_url: "https://portainer.example.com",
            },
            secrets: {},
        })

        fireEvent.click(screen.getByRole("combobox", { name: "runtimeConnections.dialog.fields.endpointName" }))
        await waitFor(() => expect(screen.getByRole("option", { name: "prod-docker(#2)" })).toBeInTheDocument())
        fireEvent.click(screen.getByRole("option", { name: "prod-docker(#2)" }))

        fireEvent.click(screen.getByRole("button", { name: "common.save" }))
        await waitFor(() => expect(api.createRuntimeConnection).toHaveBeenCalled())

        expect(vi.mocked(api.createRuntimeConnection).mock.calls[0][0]).toMatchObject({
            type: "portainer",
            credential_id: 7,
            config: {
                base_url: "https://portainer.example.com",
                endpoint_id: 2,
                endpoint_name: "prod-docker",
            },
            secrets: {},
        })
    })

    it("does not automatically save all discovered Kubernetes namespaces", async () => {
        vi.mocked(api.getCredentials).mockResolvedValue({
            items: [
                {
                    id: 7,
                    name: "k3s-credential",
                    type: "kubernetes_runtime",
                    token: "",
                    secrets: { kubeconfig: "********" },
                    secret_keys: ["kubeconfig"],
                    description: null,
                    created_at: "2026-05-03T00:00:00",
                },
            ],
            total: 1,
        })
        vi.mocked(api.discoverKubernetesNamespaces).mockResolvedValue({ items: ["default", "apps"] })
        vi.mocked(api.createRuntimeConnection).mockResolvedValue({ message: "created", id: 1 })

        render(
            <RuntimeConnectionDialog
                open
                onOpenChange={vi.fn()}
                runtimeConnection={null}
                onSuccess={vi.fn()}
            />,
        )

        fireEvent.change(screen.getByPlaceholderText("runtimeConnections.dialog.placeholders.name"), {
            target: { value: "k3s" },
        })
        fireEvent.click(screen.getByRole("combobox", { name: "runtimeConnections.dialog.fields.type" }))
        fireEvent.click(screen.getByRole("option", { name: "Kubernetes" }))
        fireEvent.click(screen.getByRole("combobox", { name: "runtimeConnections.dialog.fields.credential" }))
        await waitFor(() => expect(screen.getByRole("option", { name: "k3s-credential" })).toBeInTheDocument())
        fireEvent.click(screen.getByRole("option", { name: "k3s-credential" }))

        fireEvent.click(screen.getByRole("button", { name: "runtimeConnections.dialog.actions.discoverNamespaces" }))
        await waitFor(() => expect(api.discoverKubernetesNamespaces).toHaveBeenCalled())

        fireEvent.click(screen.getByRole("button", { name: "common.save" }))
        await waitFor(() => expect(api.createRuntimeConnection).toHaveBeenCalled())

        expect(vi.mocked(api.createRuntimeConnection).mock.calls[0][0].config).not.toHaveProperty("namespaces")
    })
})
