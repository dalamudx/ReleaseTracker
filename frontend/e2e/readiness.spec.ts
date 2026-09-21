import { expect, test, type Locator, type Page } from "@playwright/test"

const image = "registry.example.test/team/service-a:1.2.0"
const serviceName = "service-a-" + "long-name-".repeat(12)
const health = (outcome: string) => ({ outcome, elapsed_seconds: 12, services: [{ service: serviceName, status: outcome, method: "runtime_state", message: "Running; no healthcheck configured" }] })

async function fits(page: Page, region?: Locator) {
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
  if (region) {
    expect(await region.evaluate(el => el.scrollWidth <= el.clientWidth + 1)).toBe(true)
    const box = (await region.boundingBox())!
    expect(box.x).toBeGreaterThanOrEqual(0)
    expect(box.x + box.width).toBeLessThanOrEqual(page.viewportSize()!.width + 1)
  }
}

for (const width of [1280, 390]) {
  test(`readiness pending tasks and service evidence at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 })
    await page.addInitScript(() => { localStorage.setItem("token", "fixture-token"); localStorage.setItem("language", "en") })
    const errors: string[] = []
    page.on("pageerror", error => errors.push(error.message))
    const task = { id: 71, kind: "deploy", state: "running", target_label: "service-a deployment", attempts: 1, max_retries: 0, due_at: 1789705000, created_at: 1789704900, updated_at: 1789705000, result: { phase: "health_checking", health_check: health("pending") }, target: {}, attempt_history: [], triggers: [] }
    const executor = { id: 1, name: "service-a executor", runtime_type: "docker", runtime_connection_id: 1, tracker_name: "sample-tracker", tracker_source_id: 9, channel_name: "stable", enabled: true, update_mode: "manual", target_ref: { mode: "container", container_name: "service-a" }, status: { last_result: "health_checking" } }
    await page.route(url => url.pathname.startsWith("/api/"), async route => {
      const path = new URL(route.request().url()).pathname
      let data: unknown = { items: [], total: 0 }
      if (path === "/api/auth/me") data = { id: 1, username: "fixture-admin", is_admin: true }
      if (path === "/api/tasks") data = [task]
      if (path === "/api/tasks/71") data = task
      if (path === "/api/settings") data = []
      if (path === "/api/executors") data = { items: [executor], total: 1 }
      if (path === "/api/executors/1") data = executor
      if (path.endsWith("/history")) data = { items: ["pending", "healthy", "timeout", "unknown"].map((outcome, index) => ({ id: 101 + index, executor_id: 1, status: outcome === "pending" ? "health_checking" : outcome === "healthy" ? "success" : "failed", started_at: "2026-09-19T12:00:00Z", finished_at: outcome === "pending" ? null : "2026-09-19T12:00:12Z", from_version: image, to_version: image, diagnostics: { health_check: health(outcome) } })), total: 4 }
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(data) })
    })
    await page.goto("/tasks")
    await expect(page.getByTestId("task-summary-state")).toHaveText("Waiting for readiness")
    await expect(page.getByRole("status").filter({ hasText: "Readiness:" })).toContainText("Readiness 12s")
    await expect(page.getByText("1 active", { exact: true })).toBeVisible()
    await fits(page)
    await page.getByRole("button", { name: "Task notifications", exact: true }).click()
    const popover = page.locator('[data-slot="popover-content"]')
    await expect(popover.getByText("Waiting for readiness", { exact: true })).toBeVisible()
    await expect(popover.getByRole("status")).toContainText("runtime_state")
    await expect(popover.getByText("Succeeded", { exact: true })).toHaveCount(0)
    await fits(page, popover)
    await page.screenshot({ path: `/tmp/rt-readiness-tasks-${width}.png` })
    await page.keyboard.press("Escape")
    await page.goto("/executors")
    await page.getByRole("button", { name: "View execution history", exact: true }).click()
    const dialog = page.getByRole("dialog")
    const rows = dialog.getByTestId("executor-history-item")
    await expect(rows).toHaveCount(4)
    await expect(rows.nth(0).getByText("Waiting for readiness", { exact: true })).toBeVisible()
    for (const [index, label] of ["Waiting for readiness", "Healthy", "Timed out", "Unknown"].entries()) {
      const row = rows.nth(index)
      await expect(row).toContainText(`Readiness: ${label}`)
      await expect(row.getByRole("listitem").filter({ hasText: serviceName })).toContainText(`${serviceName}: ${label} · runtime_state — Running; no healthcheck configured`)
      await expect(row).not.toContainText(/business.*healthy|application.*healthy/i)
      await row.scrollIntoViewIfNeeded()
      expect(await row.evaluate(el => el.scrollWidth <= el.clientWidth + 1)).toBe(true)
    }
    await fits(page, dialog)
    await page.screenshot({ path: `/tmp/rt-readiness-history-${width}.png` })
    expect(errors).toEqual([])
  })

  test(`health notification defaults off and persists opt-in at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 })
    await page.addInitScript(() => { localStorage.setItem("token", "fixture-token"); localStorage.setItem("language", "en") })
    const errors: string[] = []
    page.on("pageerror", error => errors.push(error.message))
    let config: Record<string, unknown> = { id: 1, name: "service-a executor", runtime_type: "docker", runtime_connection_id: 1, tracker_name: "sample-tracker", tracker_source_id: 9, channel_name: "stable", enabled: true, update_mode: "manual", image_selection_mode: "replace_tag_on_current_image", image_reference_mode: "digest", current_image: image, target_ref: { mode: "container", container_id: "fixture-container", container_name: "service-a" }, service_bindings: [] }
    const payloads: Record<string, unknown>[] = []
    await page.route(url => url.pathname.startsWith("/api/"), async route => {
      const path = new URL(route.request().url()).pathname
      let data: unknown = { items: [], total: 0 }
      if (path === "/api/auth/me") data = { id: 1, username: "fixture-admin", is_admin: true }
      if (path === "/api/tasks" || path === "/api/settings") data = []
      if (path === "/api/runtime-connections") data = { items: [{ id: 1, name: "fixture-runtime", type: "docker", enabled: true, config: {}, secrets: {} }], total: 1 }
      if (path === "/api/trackers") data = { items: [{ id: 1, name: "sample-tracker", enabled: true, sources: [{ id: 9, source_key: "image", source_type: "container", channel_key: "image", channel_type: "container", enabled: true, source_config: { image }, channel_config: { image }, release_channels: [{ release_channel_key: "stable", name: "stable", type: "release", enabled: true }] }] }], total: 1 }
      if (path === "/api/executors") data = { items: [config], total: 1 }
      if (path === "/api/executors/1" && route.request().method() === "PUT") { const payload = route.request().postDataJSON(); payloads.push(payload); config = { ...config, ...payload }; data = config }
      if (path === "/api/executors/1" || path === "/api/executors/1/config") data = config
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(data) })
    })
    const openPolicy = async () => {
      await page.getByTestId("executor-row").getByRole("button", { name: "Actions", exact: true }).click()
      await page.getByRole("menuitem", { name: "Edit", exact: true }).click()
      const dialog = page.getByRole("dialog")
      await expect(dialog.getByRole("textbox", { name: "Executor name", exact: true })).toHaveValue("service-a executor")
      await dialog.getByRole("button", { name: "Continue", exact: true }).click()
      await dialog.getByRole("button", { name: "Continue", exact: true }).click()
      return dialog
    }
    await page.goto("/executors")
    let dialog = await openPolicy()
    const notification = dialog.getByRole("switch", { name: "Standalone health-result notification", exact: true })
    await expect(notification).not.toBeChecked()
    await notification.click()
    await expect(notification).toBeChecked()
    await fits(page, dialog)
    await page.screenshot({ path: `/tmp/rt-readiness-notification-${width}.png` })
    await dialog.getByRole("button", { name: "Continue", exact: true }).click()
    await dialog.getByRole("button", { name: "Save", exact: true }).click()
    await expect.poll(() => payloads.length).toBe(1)
    expect(payloads[0].health_check).toMatchObject({ notify_result: true, readiness_enabled: true, strategy: "none" })
    await expect(dialog).not.toBeVisible()
    await page.reload()
    dialog = await openPolicy()
    await expect(dialog.getByRole("switch", { name: "Standalone health-result notification", exact: true })).toBeChecked()
    await fits(page, dialog)
    expect(errors).toEqual([])
  })
}
