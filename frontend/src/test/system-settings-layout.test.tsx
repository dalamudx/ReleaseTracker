
import { describe, it, expect, vi } from "vitest"
import { fireEvent, render, screen } from "@testing-library/react"
import { SystemSettingsPage } from "@/pages/SystemSettings"
import zhLocale from "@/i18n/locales/zh.json"
import zhSettings from "@/i18n/locales/system-settings.zh.json"
import enSettings from "@/i18n/locales/system-settings.en.json"
import securityChinese from "@/i18n/locales/security-keys.zh.json"
import backupChinese from "@/i18n/locales/backups.zh.json"

vi.mock("react-i18next", async (importOriginal) => ({
  ...await importOriginal<typeof import("react-i18next")>(),
  useTranslation: () => ({
    t: (key: string, options?: { ns?: string; defaultValue?: string }) => {
      if (options?.ns === "backups") {
        return (backupChinese as unknown as Record<string, string>)[key] || options?.defaultValue || key
      }
      const parts = key.split(".")
      let cur: unknown = { ...zhLocale, systemSettings: { ...zhSettings, securityKeys: securityChinese }, backups: backupChinese }
      for (const p of parts) {
        if (!cur) return options?.defaultValue || key
        cur = (cur as Record<string, unknown>)[p]
      }
      return typeof cur === "string" ? cur : options?.defaultValue || key
    },
    i18n: { language: "zh" },
  }),
}))

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
  it("renders the 3 grouped cards with clear headers and descriptions", () => {
    render(<SystemSettingsPage />)

    // Card 1: 基础运行环境
    expect(screen.getAllByText("基础运行环境")[0]).toBeInTheDocument()
    expect(screen.getByText("配置系统访问地址、时区及后端日志输出级别。")).toBeInTheDocument()
    expect(screen.getByLabelText("应用公开访问地址")).toBeInTheDocument()
    expect(screen.getByLabelText("系统时区")).toBeInTheDocument()
    expect(screen.getByLabelText("后端日志级别")).toBeInTheDocument()

    // Card 2: 版本抓取与就绪策略
    expect(screen.getAllByText("版本抓取与就绪策略")[0]).toBeInTheDocument()
    expect(screen.getByText("控制镜像拉取容错、网络策略及执行器更新后的就绪观察默认参数。")).toBeInTheDocument()
    expect(screen.getByLabelText("启用镜像仓库跳转兼容")).toBeInTheDocument()
    expect(screen.getByLabelText("版本拉取失败重试次数")).toBeInTheDocument()
    expect(screen.getByText("部署就绪默认配置")).toBeInTheDocument()

    // Card 3: 存储与历史保留
    expect(screen.getAllByText("存储与历史保留")[0]).toBeInTheDocument()
    expect(screen.getByText("配置发布版本与执行器快照的保留策略，并支持手动清理历史数据。")).toBeInTheDocument()
    expect(screen.getByLabelText("每个发布渠道保留版本数")).toBeInTheDocument()
    expect(screen.getByLabelText("每个执行器保留快照数")).toBeInTheDocument()
    expect(screen.getByLabelText("保留最近备份数量")).toBeInTheDocument()
    expect(screen.getByLabelText("自动备份周期（小时）")).toBeInTheDocument()
    // 底部保存条
    expect(screen.getByRole("button", { name: /保存/ })).toBeInTheDocument()
  })

  it("ensures section translations are complete in both zh and en locales", () => {
    expect(zhSettings.global.sections.basic.title).toBe("基础运行环境")
    expect(enSettings.global.sections.basic.title).toBe("Basic Environment")

    expect(zhSettings.global.sections.fetchAndReadiness.title).toBe("版本抓取与就绪策略")
    expect(enSettings.global.sections.fetchAndReadiness.title).toBe("Fetch & Readiness Strategy")

    expect(zhSettings.global.sections.storageAndRetention.title).toBe("存储与历史保留")
    expect(enSettings.global.sections.storageAndRetention.title).toBe("Storage & Retention")
  })

  it("renders vertical timeline navigator and scrolls to anchored sections when clicked", () => {
    const scrollIntoViewMock = vi.fn()
    window.HTMLElement.prototype.scrollIntoView = scrollIntoViewMock

    render(<SystemSettingsPage />)

    expect(screen.getByText("配置导览")).toBeInTheDocument()
    expect(screen.getByRole("button", { name: /基础运行环境/ })).toBeInTheDocument()
    expect(screen.getByRole("button", { name: /版本抓取与就绪策略/ })).toBeInTheDocument()
    expect(screen.getByRole("button", { name: /存储与历史保留/ })).toBeInTheDocument()
    expect(screen.getByRole("button", { name: /安全密钥/ })).toBeInTheDocument()
    expect(screen.getByRole("button", { name: /OIDC/ })).toBeInTheDocument()
    expect(screen.getByRole("button", { name: /实例备份/ })).toBeInTheDocument()

    const securityNavBtn = screen.getByRole("button", { name: /安全密钥/ })
    fireEvent.click(securityNavBtn)

    expect(scrollIntoViewMock).toHaveBeenCalledWith({ behavior: "smooth", block: "start" })
  })
})
