
import { describe, it, expect, vi } from "vitest"
import { render, screen } from "@testing-library/react"
import { SystemSettingsPage } from "@/pages/SystemSettings"
import zhLocale from "@/i18n/locales/zh.json"
import enLocale from "@/i18n/locales/en.json"

vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string) => {
      const parts = key.split(".")
      let cur: unknown = zhLocale
      for (const p of parts) {
        if (!cur) return key
        cur = (cur as Record<string, unknown>)[p]
      }
      return typeof cur === "string" ? cur : key
    },
    i18n: { language: "zh" },
  }),
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
    expect(screen.getByText("基础运行环境")).toBeInTheDocument()
    expect(screen.getByText("配置系统访问地址、时区及后端日志输出级别。")).toBeInTheDocument()
    expect(screen.getByLabelText("应用公开访问地址")).toBeInTheDocument()
    expect(screen.getByLabelText("系统时区")).toBeInTheDocument()
    expect(screen.getByLabelText("后端日志级别")).toBeInTheDocument()

    // Card 2: 版本抓取与就绪策略
    expect(screen.getByText("版本抓取与就绪策略")).toBeInTheDocument()
    expect(screen.getByText("控制镜像拉取容错、网络策略及执行器更新后的就绪观察默认参数。")).toBeInTheDocument()
    expect(screen.getByLabelText("启用镜像仓库跳转兼容")).toBeInTheDocument()
    expect(screen.getByLabelText("版本拉取失败重试次数")).toBeInTheDocument()
    expect(screen.getByText("部署就绪默认配置")).toBeInTheDocument()

    // Card 3: 存储与历史保留
    expect(screen.getByText("存储与历史保留")).toBeInTheDocument()
    expect(screen.getByText("配置发布版本与执行器快照的保留策略，并支持手动清理历史数据。")).toBeInTheDocument()
    expect(screen.getByLabelText("每个发布渠道保留版本数")).toBeInTheDocument()
    expect(screen.getByLabelText("每个执行器保留快照数")).toBeInTheDocument()

    // 底部保存条
    expect(screen.getByRole("button", { name: /保存/ })).toBeInTheDocument()
  })

  it("ensures section translations are complete in both zh and en locales", () => {
    expect(zhLocale.systemSettings.global.sections.basic.title).toBe("基础运行环境")
    expect(enLocale.systemSettings.global.sections.basic.title).toBe("Basic Environment")

    expect(zhLocale.systemSettings.global.sections.fetchAndReadiness.title).toBe("版本抓取与就绪策略")
    expect(enLocale.systemSettings.global.sections.fetchAndReadiness.title).toBe("Fetch & Readiness Strategy")

    expect(zhLocale.systemSettings.global.sections.storageAndRetention.title).toBe("存储与历史保留")
    expect(enLocale.systemSettings.global.sections.storageAndRetention.title).toBe("Storage & Retention")
  })
})
