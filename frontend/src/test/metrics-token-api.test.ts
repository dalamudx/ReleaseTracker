import { afterEach, describe, expect, it } from "vitest"

import { api, apiClient } from "@/api/client"

const adapter = apiClient.defaults.adapter

afterEach(() => {
    apiClient.defaults.adapter = adapter
    document.cookie = "releasetracker-csrf=; max-age=0; path=/"
})

describe("metrics token API", () => {
    it("reads the configured flag and sets or revokes the token through the settings route", async () => {
        document.cookie = "releasetracker-csrf=csrf-test; path=/"
        const requests: Array<{ method?: string; url?: string; data?: unknown; csrf?: unknown }> = []
        apiClient.defaults.adapter = async (config) => {
            requests.push({
                method: config.method,
                url: config.url,
                data: typeof config.data === "string" ? JSON.parse(config.data) : config.data,
                csrf: config.headers["X-CSRF-Token"],
            })
            return { config, status: 200, statusText: "OK", headers: {}, data: { configured: true } }
        }

        await expect(api.getMetricsTokenStatus()).resolves.toEqual({ configured: true })
        await api.setMetricsToken("rotated-secret")
        // An empty token is the revocation contract.
        await api.setMetricsToken("")

        expect(requests).toEqual([
            { method: "get", url: "/api/settings/metrics-token", data: undefined, csrf: undefined },
            { method: "put", url: "/api/settings/metrics-token", data: { token: "rotated-secret" }, csrf: "csrf-test" },
            { method: "put", url: "/api/settings/metrics-token", data: { token: "" }, csrf: "csrf-test" },
        ])
    })
})
