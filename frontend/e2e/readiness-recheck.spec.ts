import { expect, test } from "@playwright/test"

for (const width of [1280, 390]) test(`explicit read-only recheck keeps original outcome at ${width}px`, async ({ page }) => {
  await page.setViewportSize({ width, height: 900 })
  await page.addInitScript(() => { localStorage.setItem("token", "fixture-token"); localStorage.setItem("language", "en") })
  const errors: string[] = []
  page.on("pageerror", error => errors.push(error.message))
  const writes: string[] = []
  let latest: { outcome: string } | undefined
  const task = () => ({ id: 71, kind: "deploy", state: "failed", target_label: "service-a", attempts: 1, max_retries: 0, due_at: 1789705000, created_at: 1789704900, updated_at: 1789705000, result: { phase: "completed", health_check: { outcome: "timeout" } }, target: {}, attempt_history: [], triggers: [] })
  const executor = { id: 1, name: "service-a executor", runtime_type: "docker", enabled: true, update_mode: "manual", target_ref: { mode: "container", container_name: "service-a" }, status: { last_result: "failed" } }
  await page.route(url => url.pathname.startsWith("/api/"), async route => {
    const path = new URL(route.request().url()).pathname
    let data: unknown = { items: [], total: 0 }
    let status = 200
    if (route.request().method() !== "GET") writes.push(`${route.request().method()} ${path}`)
    if (path === "/api/auth/me") data = { id: 1, username: "fixture-admin", is_admin: true }
    if (path === "/api/tasks") data = latest ? [task(), { ...task(), id: 72, kind: "recover", state: "running", result: { phase: "health_checking", readiness_recheck: true, health_check: latest } }] : [task()]
    if (path === "/api/tasks/71") data = task()
    if (path === "/api/tasks/71/recheck") { latest = { outcome: "pending" }; status = 202; data = { task_id: 72, status: "queued" } }
    if (path === "/api/settings") data = []
    if (path === "/api/executors") data = { items: [executor], total: 1 }
    if (path === "/api/executors/1") data = executor
    if (path.endsWith("/history")) data = { items: [{ id: 101, executor_id: 1, status: "failed", started_at: "2026-09-19T12:00:00Z", finished_at: "2026-09-19T12:00:12Z", from_version: "registry.example.test/team/service-a:1.2.0", to_version: "registry.example.test/team/service-a:1.2.0", diagnostics: { health_check: { outcome: "timeout" }, health_recheck: latest, health_rechecks: [latest] } }], total: 1 }
    await route.fulfill({ status, contentType: "application/json", body: JSON.stringify(data) })
  })
  await page.goto("/tasks")
  await page.getByRole("button", { name: /71/ }).click()
  expect(writes).toEqual([])
  await expect(page.getByText("Read-only check; does not redeploy or change the original result.")).toBeVisible()
  await page.getByRole("button", { name: "Recheck readiness", exact: true }).click()
  await expect(page.getByText("Readiness recheck for “service-a” queued.", { exact: true })).toBeVisible()
  expect(writes).toEqual(["POST /api/tasks/71/recheck"])
  await expect(page.getByText("Latest readiness recheck: Waiting for readiness")).toBeVisible()
  await expect(page.getByTestId("task-summary-state")).toHaveText(["Failed", "Waiting for readiness"])
  await expect(page.getByText("Readiness: Timed out", { exact: true })).toBeVisible()
  // The original task remains unchanged; the backend deduplicates repeat requests.
  await expect(page.getByRole("button", { name: "Recheck readiness", exact: true })).toHaveCount(1)
  latest = { outcome: "healthy" }
  await page.goto("/executors")
  await page.getByRole("button", { name: "View execution history", exact: true }).click()
  const dialog = page.getByRole("dialog")
  const row = dialog.getByTestId("executor-history-item")
  await expect(row.getByText("Failed", { exact: true })).toBeVisible()
  await expect(row.getByText("Readiness: Timed out", { exact: true })).toBeVisible()
  await expect(row.getByText("Latest readiness recheck: Healthy", { exact: true })).toBeVisible()
  await expect(row.getByText("Read-only check; does not redeploy or change the original result.")).toBeVisible()
  expect(await row.evaluate(el => el.scrollWidth <= el.clientWidth + 1)).toBe(true)
  expect(await dialog.evaluate(el => el.scrollWidth <= el.clientWidth + 1)).toBe(true)
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
  await page.screenshot({ path: `/tmp/rt-readiness-recheck-${width}.png` })
  expect(writes).toEqual(["POST /api/tasks/71/recheck"])
  expect(errors).toEqual([])
})
