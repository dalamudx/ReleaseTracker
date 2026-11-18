import { fireEvent, render, screen, waitFor } from "@testing-library/react"
import { beforeEach, describe, expect, it, vi } from "vitest"

import type { SettingItem } from "@/api/types"
import settingsEnglish from "@/i18n/locales/system-settings.en.json"
import settingsChinese from "@/i18n/locales/system-settings.zh.json"

const { metricsTokenState, setMetricsTokenMock, settingsState, updateSettingMock } = vi.hoisted(() => ({
    metricsTokenState: { configured: false, token: "" },
    setMetricsTokenMock: vi.fn(),
    settingsState: { items: [] as SettingItem[] },
    updateSettingMock: vi.fn(),
}))

vi.mock("react-i18next", async (importOriginal) => ({
    ...await importOriginal<typeof import("react-i18next")>(),
    ...await import("./i18n-mock"),
}))

vi.mock("sonner", () => ({
    toast: {
        error: vi.fn(),
        success: vi.fn(),
    },
}))

vi.mock("@/api/client", () => ({
    clearAuthStorage: vi.fn(),
}))

vi.mock("@/components/admin/OIDCProvidersManagement", () => ({
    OIDCProvidersManagement: () => null,
}))

vi.mock("@/hooks/queries", () => ({
    useCleanupReleaseHistory: () => ({ isPending: false, mutateAsync: vi.fn() }),
    useCleanupSnapshotHistory: () => ({ isPending: false, mutateAsync: vi.fn() }),
    useMetricsTokenStatus: () => ({ data: metricsTokenState }),
    useRotateEncryptionKey: () => ({ isPending: false, mutateAsync: vi.fn() }),
    useRotateJwtSecret: () => ({ isPending: false, mutateAsync: vi.fn() }),
    useSecurityKeys: () => ({
        data: {
            encryption_key: {
                fingerprint: "enc-fp",
                inventory: {
                    credentials_token: 0,
                    credentials_secrets: 0,
                    oauth_provider_client_secret: 0,
                    runtime_connection_secrets: 0,
                },
                undecryptable_count: 0,
            },
            jwt_secret: { active_sessions: 0, fingerprint: "jwt-fp" },
        },
    }),
    useSetMetricsToken: () => ({ isPending: false, mutateAsync: setMetricsTokenMock }),
    useSettings: () => ({ data: settingsState.items }),
    useUpdateSetting: () => ({ isPending: false, mutateAsync: updateSettingMock }),
}))

import { SystemSettingsPage } from "@/pages/SystemSettings"

const label = (path: string) => `systemSettings.global.${path}.label`
const metricsToken = "systemSettings.global.inbound.metricsToken"

function renderPage(settings: SettingItem[] = []) {
    settingsState.items = settings
    return render(<SystemSettingsPage />)
}

describe("SystemSettingsPage instance policies", () => {
    beforeEach(() => {
        settingsState.items = []
        metricsTokenState.configured = false
        metricsTokenState.token = ""
        setMetricsTokenMock.mockReset()
        setMetricsTokenMock.mockResolvedValue({ configured: true })
        updateSettingMock.mockReset()
        updateSettingMock.mockResolvedValue({})
    })

    it("loads persisted policies and falls back to the shared defaults", () => {
        renderPage([
            { key: "system.mutation_concurrency", value: "3" },
            { key: "system.runtime_health_interval_seconds", value: "600" },
            { key: "system.cors_origins", value: "https://app.example.com" },
            { key: "system.online_restore", value: "false" },
        ])

        expect(screen.getByLabelText(label("baseUrl"))).toHaveValue("")
        expect(screen.getByLabelText(label("queue.mutationConcurrency"))).toHaveValue(3)
        expect(screen.getByLabelText(label("queue.workerPollSeconds"))).toHaveValue(2)
        expect(screen.getByLabelText(label("pullGrace"))).toHaveValue(1800)
        expect(screen.getByLabelText(label("runtimeHealth"))).toHaveValue(600)
        expect(screen.getByText("https://app.example.com")).toBeInTheDocument()
        expect(screen.getByText("127.0.0.1")).toBeInTheDocument()
        expect(screen.getByRole("switch", { name: label("onlineRestore") })).not.toBeChecked()
        expect(screen.getByRole("switch", { name: label("preMigrationBackup") })).toBeChecked()
        expect(screen.getByText(`${metricsToken}.notConfigured`)).toBeInTheDocument()
        expect(screen.queryByRole("button", { name: `${metricsToken}.revoke` })).not.toBeInTheDocument()
    })

    it("names the stored address as the public base URL in both locales", () => {
        expect(settingsEnglish.global.baseUrl.title).toBe("Public Base URL")
        expect(settingsChinese.global.baseUrl.title).toBe("公开基础 URL")
        for (const copy of [settingsEnglish.global.baseUrl, settingsChinese.global.baseUrl]) {
            expect(JSON.stringify(copy)).not.toMatch(/frontend url|应用公开访问地址/i)
        }
    })

    it("saves numbers, switches and newline-delimited lists as canonical strings", async () => {
        renderPage()

        fireEvent.change(screen.getByLabelText(label("queue.mutationConcurrency")), { target: { value: "2" } })
        fireEvent.change(screen.getByLabelText(label("queue.workerPollSeconds")), { target: { value: "30" } })
        fireEvent.change(screen.getByLabelText(label("pullGrace")), { target: { value: "900" } })
        fireEvent.change(screen.getByLabelText(label("runtimeHealth")), { target: { value: "600" } })
        fireEvent.click(screen.getByRole("button", { name: "common.delete 127.0.0.1" }))
        const proxies = screen.getByLabelText(label("inbound.forwardedAllowIps"))
        fireEvent.paste(proxies, { clipboardData: { getData: () => " 10.0.0.0/8 \n\n192.168.0.0/16" } })
        const origin = screen.getByLabelText(label("inbound.corsOrigins"))
        for (const value of ["https://app.example.com", "https://app.example.com", "https://admin.example.com "]) {
            fireEvent.change(origin, { target: { value } })
            fireEvent.keyDown(origin, { key: "Enter" })
        }
        fireEvent.click(screen.getByRole("switch", { name: label("onlineRestore") }))
        fireEvent.click(screen.getByRole("switch", { name: label("preMigrationBackup") }))
        fireEvent.click(screen.getByRole("button", { name: "common.save" }))

        await waitFor(() => expect(updateSettingMock).toHaveBeenCalledWith({ key: "system.online_restore", value: "false" }))
        for (const [key, value] of Object.entries({
            "system.mutation_concurrency": "2",
            "system.worker_poll_seconds": "30",
            "system.readiness_pull_grace_seconds": "900",
            "system.runtime_health_interval_seconds": "600",
            "system.pre_migration_backup": "false",
            "system.forwarded_allow_ips": "10.0.0.0/8\n192.168.0.0/16",
            "system.cors_origins": "https://app.example.com\nhttps://admin.example.com",
        })) {
            expect(updateSettingMock).toHaveBeenCalledWith({ key, value })
        }
    })

    it("blocks saving out-of-range values and partially disabled inspections", () => {
        renderPage()

        fireEvent.change(screen.getByLabelText(label("queue.mutationConcurrency")), { target: { value: "4" } })
        fireEvent.click(screen.getByRole("button", { name: "common.save" }))
        expect(updateSettingMock).not.toHaveBeenCalled()
        expect(screen.getByRole("alert")).toHaveTextContent("systemSettings.global.policies.rangeInvalid")

        fireEvent.change(screen.getByLabelText(label("queue.mutationConcurrency")), { target: { value: "1" } })
        fireEvent.change(screen.getByLabelText(label("runtimeHealth")), { target: { value: "120" } })
        fireEvent.click(screen.getByRole("button", { name: "common.save" }))
        expect(updateSettingMock).not.toHaveBeenCalled()
        expect(screen.getByRole("alert")).toHaveTextContent("systemSettings.global.runtimeHealth.invalid")
    })

    it("surfaces a rejected proxy list and stops before the remaining saves", async () => {
        updateSettingMock.mockImplementation(async ({ key }: { key: string }) => {
            if (key === "system.forwarded_allow_ips") {
                throw Object.assign(new Error("invalid"), { response: { status: 400 } })
            }
            return {}
        })
        renderPage()

        const input = screen.getByLabelText(label("inbound.forwardedAllowIps"))
        fireEvent.change(input, { target: { value: "not-a-proxy" } })
        fireEvent.keyDown(input, { key: "Enter" })
        fireEvent.click(screen.getByRole("button", { name: "common.save" }))

        await waitFor(() =>
            expect(screen.getByRole("alert")).toHaveTextContent("systemSettings.global.inbound.forwardedAllowIps.invalid"),
        )
        expect(updateSettingMock).toHaveBeenCalledTimes(1)
    })

    it("shows a token save error without logging the submitted credential", async () => {
        setMetricsTokenMock.mockImplementationOnce(async (token: string) => { throw { config: { data: JSON.stringify({ token }) } } })
        renderPage()
        const logError = vi.spyOn(console, "error").mockImplementation(() => {})
        try {
            fireEvent.click(screen.getByRole("button", { name: `${metricsToken}.generate` }))
            fireEvent.click(screen.getByRole("button", { name: "common.save" }))
            expect(await screen.findByRole("alert")).toHaveTextContent(`${metricsToken}.saveFailed`)
            expect(logError).not.toHaveBeenCalled()
        } finally {
            logError.mockRestore()
        }
    })

    it("shows the saved token after reopening settings and stages revocation until saved", async () => {
        setMetricsTokenMock.mockImplementation(async (token: string) => {
            Object.assign(metricsTokenState, { configured: Boolean(token), token })
            return { configured: Boolean(token) }
        })
        const page = renderPage()
        expect(screen.queryByRole("button", { name: `${metricsToken}.revoke` })).not.toBeInTheDocument()

        const save = () => screen.getByRole("button", { name: "common.save" })
        const generate = () => screen.getByRole("button", { name: `${metricsToken}.generate` })
        fireEvent.click(generate())
        const input = screen.getByLabelText<HTMLInputElement>(label("inbound.metricsToken"))
        expect(input).toHaveAttribute("readonly")
        const firstToken = input.value
        fireEvent.click(generate())
        const token = input.value
        expect(token).toMatch(/^[A-Za-z0-9_-]{16}$/)
        expect(token).not.toBe(firstToken)
        expect(setMetricsTokenMock).not.toHaveBeenCalled()
        fireEvent.click(save())

        await waitFor(() => expect(setMetricsTokenMock).toHaveBeenCalledWith(token))
        expect(input).toHaveValue(token)
        page.unmount()
        renderPage()
        expect(screen.getByLabelText(label("inbound.metricsToken"))).toHaveValue(token)

        const revoke = screen.getByRole("button", { name: `${metricsToken}.revoke` })
        fireEvent.click(revoke)
        expect(generate()).toBeDisabled()
        fireEvent.click(screen.getByRole("button", { name: `${metricsToken}.undoRevoke` }))
        fireEvent.click(save())
        await waitFor(() => expect(save()).toBeEnabled())
        expect(setMetricsTokenMock).toHaveBeenCalledTimes(1)

        fireEvent.click(revoke)
        expect(setMetricsTokenMock).toHaveBeenCalledTimes(1)
        fireEvent.click(save())
        await waitFor(() => expect(setMetricsTokenMock).toHaveBeenLastCalledWith(""))
    })
})
