
import { beforeEach, describe, it, expect, vi } from "vitest"
import { fireEvent, render, screen } from "@testing-library/react"
import i18n from "@/i18n/config"
import { SystemSettingsPage } from "@/pages/SystemSettings"

vi.mock("@/components/admin/OIDCProvidersManagement", () => ({
  OIDCProvidersManagement: () => null,
}))

vi.mock("@/hooks/queries", () => ({
  useSettings: () => ({ data: [] }),
  useSecurityKeys: () => ({
    data: {
      jwt_secret: { fingerprint: "sha256:1111", active_sessions: 1 },
      encryption_key: {
        fingerprint: "sha256:2222",
        inventory: { credentials_tokens: 0, credentials_secrets: 0, oidc_secrets: 0, runtime_connection_secrets: 0 },
        undecryptable_count: 0,
      },
    },
  }),
  useUpdateSetting: () => ({ isPending: false, mutateAsync: vi.fn() }),
  useCleanupReleaseHistory: () => ({ isPending: false, mutateAsync: vi.fn() }),
  useCleanupSnapshotHistory: () => ({ isPending: false, mutateAsync: vi.fn() }),
  useRotateJwtSecret: () => ({ isPending: false, mutateAsync: vi.fn() }),
  useRotateEncryptionKey: () => ({ isPending: false, mutateAsync: vi.fn() }),
}))

describe("SystemSettingsPage Layout and Sections", () => {
  beforeEach(async () => {
    await i18n.changeLanguage("zh")
  })
  it("renders grouped configuration controls and the save action", () => {
    render(<SystemSettingsPage />)

    for (const section of ["basic", "fetchAndReadiness", "storageAndRetention"]) {
      expect(screen.getAllByText(i18n.t(`systemSettings.global.sections.${section}.title`))).toHaveLength(2)
    }
    for (const field of [
      "baseUrl", "timezone", "logLevel", "ociRegistryRedirects",
      "releaseHistoryCount", "snapshotHistoryCount", "backupRetention", "backupInterval",
    ]) {
      expect(screen.getByLabelText(i18n.t(`systemSettings.global.${field}.label`))).toBeInTheDocument()
    }
    expect(screen.getByRole("button", { name: i18n.t("common.save") })).toBeInTheDocument()
  })

  it("navigates to the selected configuration card", () => {
    render(<SystemSettingsPage />)
    const target = document.getElementById("section-security")!
    const scrollIntoView = vi.fn()
    // Mock only the target element, leaving HTMLElement.prototype untouched.
    Object.defineProperty(target, "scrollIntoView", { value: scrollIntoView })

    fireEvent.click(screen.getByRole("button", { name: i18n.t("systemSettings.securityKeys.title") }))

    expect(scrollIntoView).toHaveBeenCalledWith({ behavior: "smooth", block: "start" })
    expect(screen.getByRole("button", { name: i18n.t("systemSettings.securityKeys.title") }))
      .toHaveAttribute("aria-current", "location")
  })
})
