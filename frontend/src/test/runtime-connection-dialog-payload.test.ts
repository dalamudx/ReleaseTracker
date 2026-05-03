import { describe, expect, it } from "vitest"

import { buildPayload, buildUpdatePayload } from "@/components/runtime-connections/runtimeConnectionHelpers"

function createBaseValues() {
    return {
        name: "runtime",
        type: "docker" as const,
        enabled: true,
        description: "",
        credential_id: "",
        socket: "",
        tls_verify: false,
        api_version: "",
        context: "",
        namespaces: [] as string[],
        in_cluster: false,
        base_url: "",
        endpoint_id: "",
    }
}

describe("runtime connection dialog payload helpers", () => {
    it("serializes valid portainer payload with a credential reference", () => {
        const payload = buildPayload({
            ...createBaseValues(),
            name: " portainer-prod ",
            type: "portainer",
            description: " Portainer runtime ",
            credential_id: "9",
            base_url: " https://portainer.example.com ",
            endpoint_id: "12",
        })

        expect(payload).toEqual({
            name: "portainer-prod",
            type: "portainer",
            enabled: true,
            description: "Portainer runtime",
            config: {
                base_url: "https://portainer.example.com",
                endpoint_id: 12,
            },
            credential_id: 9,
            secrets: {},
        })
    })

    it("keeps invalid portainer endpoint id out of config for backend validation", () => {
        const payload = buildPayload({
            ...createBaseValues(),
            type: "portainer",
            credential_id: "9",
            base_url: "https://portainer.example.com",
            endpoint_id: "abc",
        })

        expect(payload.config).toEqual({ base_url: "https://portainer.example.com" })
    })

    it("serializes Docker and Podman endpoints as one socket field", () => {
        const unixPayload = buildPayload({
            ...createBaseValues(),
            socket: " unix:///var/run/docker.sock ",
        })

        expect(unixPayload.config).toEqual({ socket: "unix:///var/run/docker.sock" })

        const tcpPayload = buildPayload({
            ...createBaseValues(),
            type: "podman",
            socket: " tcp://127.0.0.1:2375 ",
        })

        expect(tcpPayload.config).toEqual({ socket: "tcp://127.0.0.1:2375" })
    })

    it("serializes runtime credential references without inline secrets", () => {
        const payload = buildPayload({
            ...createBaseValues(),
            name: "k3s",
            type: "kubernetes",
            credential_id: "7",
            context: "production",
        })

        expect(payload).toMatchObject({
            name: "k3s",
            type: "kubernetes",
            credential_id: 7,
            config: {
                context: "production",
            },
            secrets: {},
        })
    })

    it("omits secrets from update payload when no new secrets are provided", () => {
        const updatePayload = buildUpdatePayload({
            name: "portainer-prod",
            type: "portainer",
            enabled: true,
            description: null,
            config: {
                base_url: "https://portainer.example.com",
                endpoint_id: 12,
            },
            secrets: {},
        })

        expect(updatePayload).toEqual({
            name: "portainer-prod",
            type: "portainer",
            enabled: true,
            description: null,
            config: {
                base_url: "https://portainer.example.com",
                endpoint_id: 12,
            },
        })
    })
})
