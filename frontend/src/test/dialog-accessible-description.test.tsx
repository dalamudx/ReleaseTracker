import { act, cleanup, render, screen } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi, type MockInstance } from "vitest"
import { api } from "@/api/client"
import i18n from "@/i18n/config"
import { ExecutorSheet } from "@/components/executors/ExecutorSheet"
import { RuntimeConnectionDialog } from "@/components/runtime-connections/RuntimeConnectionDialog"
import type { ExecutorConfig, RuntimeConnection } from "@/api/types"
import { assertNoActWarnings } from "./assert-no-act-warnings"

vi.mock("@/api/client", () => ({ api: { getExecutorConfig: vi.fn(), getCredentials: vi.fn(), getRuntimeConnections: vi.fn() } }))
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }))
assertNoActWarnings()

let warningSpy: MockInstance<typeof console.warn>
beforeEach(() => {
  warningSpy = vi.spyOn(console, "warn")
  vi.stubGlobal("ResizeObserver", class { observe() {} unobserve() {} disconnect() {} })
  Element.prototype.scrollIntoView = vi.fn()
  vi.mocked(api.getCredentials).mockResolvedValue({ items: [], total: 0 })
  vi.mocked(api.getRuntimeConnections).mockResolvedValue({ items: [], total: 0 })
  vi.mocked(api.getExecutorConfig).mockResolvedValue({
    id: 1, name: "test-executor", runtime_type: "docker", runtime_connection_id: 1,
    tracker_name: "", tracker_source_id: null, channel_name: "stable", enabled: true,
    update_mode: "manual", image_selection_mode: "replace_tag_on_current_image", image_reference_mode: "digest",
    target_ref: { mode: "container", container_id: "synthetic", container_name: "test" },
    service_bindings: [], maintenance_window: null, description: null,
  } as ExecutorConfig)
})
afterEach(async () => {
  cleanup()
  try {
    expect(warningSpy.mock.calls.filter(([message]) => /Missing.*Description/.test(String(message)))).toEqual([])
  } finally {
    warningSpy.mockRestore()
    vi.unstubAllGlobals()
    await i18n.changeLanguage("en")
  }
})

const connection: RuntimeConnection = { id: 1, name: "test-runtime", type: "portainer", enabled: true, config: { base_url: "https://runtime.test", endpoint_id: 1 }, secrets: {} }

function assertDescription(text: string, title: string) {
  const dialog = screen.getByRole("dialog")
  expect(dialog).toHaveAccessibleName(title)
  expect(dialog).toHaveAccessibleDescription(text)
  const descriptionId = dialog.getAttribute("aria-describedby")
  expect(descriptionId).toBeTruthy()
  const description = document.getElementById(descriptionId!)
  expect(description).toHaveTextContent(text)
  expect(description).toHaveClass("sr-only")
  expect(dialog).toContainElement(description)
  expect(description).not.toHaveAttribute("aria-hidden", "true")
}

describe.each(["zh", "en"])("localized dialog descriptions (%s)", language => {
  it.each([false, true])("executor create/edit description survives reopen (edit=%s)", async edit => {
    await i18n.changeLanguage(language)
    const title = i18n.t(edit ? "executors.sheet.editTitle" : "executors.sheet.addTitle")
    const description = language === "zh" ? "选择运行时目标、绑定版本来源并审核部署策略。" : "Choose a runtime target, bind release sources, and review deployment policies."
    const props = { onOpenChange: vi.fn(), executorId: edit ? 1 : null, runtimeConnections: [], trackers: [], systemTimezone: "UTC", onSuccess: vi.fn() }
    let view!: ReturnType<typeof render>
    await act(async () => { view = render(<ExecutorSheet {...props} open />) })
    assertDescription(description, title)
    view.rerender(<ExecutorSheet {...props} open={false} />)
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument()
    await act(async () => { view.rerender(<ExecutorSheet {...props} open />) })
    assertDescription(description, title)
  })

  it.each([false, true])("runtime create/edit description survives reopen (edit=%s)", async edit => {
    await i18n.changeLanguage(language)
    const title = i18n.t(edit ? "runtimeConnections.dialog.editTitle" : "runtimeConnections.dialog.addTitle")
    const description = i18n.t("runtimeConnections.dialog.description")
    const props = { onOpenChange: vi.fn(), runtimeConnection: edit ? connection : null, onSuccess: vi.fn() }
    let view!: ReturnType<typeof render>
    await act(async () => { view = render(<RuntimeConnectionDialog {...props} open />) })
    assertDescription(description, title)
    view.rerender(<RuntimeConnectionDialog {...props} open={false} />)
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument()
    await act(async () => { view.rerender(<RuntimeConnectionDialog {...props} open />) })
    assertDescription(description, title)
  })
})
