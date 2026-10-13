import { fireEvent, render, screen, waitFor } from "@testing-library/react"
import { beforeEach, describe, expect, it, vi } from "vitest"

import type { SettingItem } from "@/api/types"

const {
  cleanupReleaseHistoryMock,
  cleanupSnapshotHistoryMock,
  rotateEncryptionKeyMock,
  rotateJwtSecretMock,
  settingsState,
  updateSettingMock,
} = vi.hoisted(() => ({
  cleanupReleaseHistoryMock: vi.fn(),
  cleanupSnapshotHistoryMock: vi.fn(),
  rotateEncryptionKeyMock: vi.fn(),
  rotateJwtSecretMock: vi.fn(),
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
  useCleanupReleaseHistory: () => ({
    isPending: false,
    mutateAsync: cleanupReleaseHistoryMock,
  }),
  useCleanupSnapshotHistory: () => ({
    isPending: false,
    mutateAsync: cleanupSnapshotHistoryMock,
  }),
  useRotateEncryptionKey: () => ({
    isPending: false,
    mutateAsync: rotateEncryptionKeyMock,
  }),
  useRotateJwtSecret: () => ({
    isPending: false,
    mutateAsync: rotateJwtSecretMock,
  }),
  useSecurityKeys: () => ({
    data: {
      encryption_key: {
        configured: true,
        fingerprint: "enc-fp",
        inventory: {
          credentials_token: 0,
          credentials_secrets: 0,
          oauth_provider_client_secret: 0,
          runtime_connection_secrets: 0,
        },
        undecryptable_count: 0,
      },
      jwt_secret: {
        active_sessions: 0,
        configured: true,
        fingerprint: "jwt-fp",
      },
    },
  }),
  useSettings: () => ({ data: settingsState.items }),
  useUpdateSetting: () => ({
    isPending: false,
    mutateAsync: updateSettingMock,
  }),
}))

import {
  SYSTEM_OCI_REGISTRY_REDIRECTS_ENABLED_SETTING_KEY,
  SystemSettingsPage,
} from "@/pages/SystemSettings"

function renderPage(settings: SettingItem[] = []) {
  settingsState.items = settings
  return render(<SystemSettingsPage />)
}

describe("SystemSettingsPage OCI registry redirects", () => {
  beforeEach(() => {
    settingsState.items = []
    updateSettingMock.mockReset()
    updateSettingMock.mockResolvedValue({})
    cleanupReleaseHistoryMock.mockReset()
    cleanupSnapshotHistoryMock.mockReset()
    rotateEncryptionKeyMock.mockReset()
    rotateJwtSecretMock.mockReset()
  })

  it("loads persisted backup options, rejects invalid input and saves dirty keys sequentially", async () => {
    renderPage([{key:"system.backup_retention",value:"9"},{key:"system.backup_interval_hours",value:"24"}])
    expect(screen.getByLabelText("systemSettings.global.backupRetention.label")).toHaveValue(9)
    fireEvent.change(screen.getByLabelText("systemSettings.global.backupInterval.label"),{target:{value:"8761"}})
    fireEvent.click(screen.getByRole("button",{name:"common.save"}))
    expect(updateSettingMock).not.toHaveBeenCalled()
    expect(screen.getByRole("alert")).toHaveTextContent("systemSettings.global.backupInterval.invalid")
    fireEvent.change(screen.getByLabelText("systemSettings.global.backupInterval.label"),{target:{value:"0"}})
    fireEvent.change(screen.getByLabelText("systemSettings.global.backupRetention.label"),{target:{value:"2"}})
    fireEvent.click(screen.getByRole("button",{name:"common.save"}))
    await waitFor(()=>expect(updateSettingMock).toHaveBeenCalledWith({key:"system.backup_interval_hours",value:"0"}))
    expect(updateSettingMock).toHaveBeenNthCalledWith(1,{key:"system.backup_retention",value:"2"})
    expect(updateSettingMock).toHaveBeenNthCalledWith(2,{key:"system.backup_interval_hours",value:"0"})
    await waitFor(()=>expect(updateSettingMock).toHaveBeenCalledWith({key:"system.timezone",value:expect.any(String)}))
  })

  it("loads, validates and saves system readiness defaults", async () => {
    renderPage([{key: "system.readiness_timeout_seconds", value: "240"}])
    const timeout = screen.getByLabelText("readiness.readiness_timeout_seconds")
    expect(timeout).toHaveValue(240)
    expect(screen.getByLabelText("readiness.readiness_interval_seconds")).toHaveValue(5)
    fireEvent.change(timeout, {target: {value: "0"}})
    fireEvent.click(screen.getByRole("button", {name: "common.save"}))
    expect(updateSettingMock).not.toHaveBeenCalled()
    expect(screen.getByRole("alert")).toHaveTextContent("readiness.invalid")
    fireEvent.change(timeout, {target: {value: "300"}})
    fireEvent.click(screen.getByRole("button", {name: "common.save"}))
    await waitFor(() => expect(updateSettingMock).toHaveBeenCalledWith({key: "system.readiness_timeout_seconds", value: "300"}))
    expect(updateSettingMock).toHaveBeenCalledWith({key: "system.readiness_stable_seconds", value: "10"})
  })
  it("defaults the global redirect switch to disabled when the setting is absent", async () => {
    renderPage()

    const toggle = screen.getByRole("switch", {
      name: "systemSettings.global.ociRegistryRedirects.label",
    })
    expect(toggle).not.toBeChecked()
    expect(screen.getByText("systemSettings.global.ociRegistryRedirects.description")).toBeInTheDocument()

    fireEvent.click(screen.getByRole("button", { name: "common.save" }))

    await waitFor(() => {
      expect(updateSettingMock).toHaveBeenCalledWith({
        key: SYSTEM_OCI_REGISTRY_REDIRECTS_ENABLED_SETTING_KEY,
        value: "false",
      })
    })
  })

  it("saves the canonical true value when the admin enables redirect compatibility", async () => {
    renderPage([
      {
        key: SYSTEM_OCI_REGISTRY_REDIRECTS_ENABLED_SETTING_KEY,
        value: "false",
      },
    ])

    fireEvent.click(
      screen.getByRole("switch", {
        name: "systemSettings.global.ociRegistryRedirects.label",
      }),
    )
    fireEvent.click(screen.getByRole("button", { name: "common.save" }))

    await waitFor(() => {
      expect(updateSettingMock).toHaveBeenCalledWith({
        key: SYSTEM_OCI_REGISTRY_REDIRECTS_ENABLED_SETTING_KEY,
        value: "true",
      })
    })
  })
})
