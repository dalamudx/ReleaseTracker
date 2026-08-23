import { fireEvent, render, screen, waitFor } from "@testing-library/react"
import { beforeEach, describe, expect, it, vi } from "vitest"

import type { SettingItem } from "@/api/types"
import enLocale from "@/i18n/locales/en.json"
import zhLocale from "@/i18n/locales/zh.json"

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

vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string) => key,
  }),
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

  it("defaults the global redirect switch to disabled when the setting is absent", async () => {
    renderPage()

    const toggle = screen.getByRole("switch", {
      name: "systemSettings.global.ociRegistryRedirects.label",
    })
    expect(toggle).not.toBeChecked()
    expect(screen.getByText("systemSettings.global.ociRegistryRedirects.description")).toBeInTheDocument()
    expect(zhLocale.systemSettings.global.ociRegistryRedirects.description).toBe(
      "仅在你的镜像仓库需要通过跳转访问标签、manifest、token 或 blob 时开启。",
    )
    expect(enLocale.systemSettings.global.ociRegistryRedirects.description).toBe(
      "Keep this disabled unless your registry requires redirects for tag, manifest, token, or blob access.",
    )
    expect(zhLocale.systemSettings.global.ociRegistryRedirects.description).not.toContain(
      "允许 Docker/OCI 镜像仓库请求使用项目内置的有界手动跳转策略。",
    )
    expect(screen.queryByText("允许 Docker/OCI 镜像仓库请求使用项目内置的有界手动跳转策略。")).not.toBeInTheDocument()

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
