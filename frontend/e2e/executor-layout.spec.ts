import { expect, test } from "@playwright/test"

for (const language of ["zh", "en"]) for (const width of [1280, 390]) {
  test(`executor layout and safe actions ${width}px ${language}`, async ({ page }) => {
    await page.setViewportSize({width, height: 900})
    await page.addInitScript(language => {
      localStorage.setItem("token", "e2e-token")
      localStorage.setItem("language", language)
    }, language)
    const errors: string[] = []
    page.on("pageerror", error => errors.push(error.message))
    const common = {runtime_connection_id: 1, tracker_name: "release-tracker", tracker_source_id: 1, channel_name: "stable", enabled: true, update_mode: "manual", image_selection_mode: "replace_tag_on_current_image", image_reference_mode: "digest", runtime_connection_name: "prod-connection-" + "x".repeat(60), status: {last_result: "success", last_run_at: "2026-09-19T12:00:00Z"}}
    const executors = [
      {...common, id: 1, name: "release-api-" + "long-name-".repeat(12), runtime_type: "docker", description: "Production API", target_ref: {mode: "container", container_id: "abc123", container_name: "release-api"}},
      {...common, id: 2, name: "SSH project", runtime_type: "ssh", compose_ownership: "verification_required", invalid_config_error: "Ownership must be confirmed", target_ref: {mode: "ssh_compose", project: "app", working_dir: "/data/" + "long-path/".repeat(20), config_files: ["compose.yml"], env_files: [], profiles: [], services: [{service: "api"}, {service: "web"}], tool: "podman-compose", write_strategy: "source"}},
      {...common, id: 3, name: "Helm disabled", runtime_type: "kubernetes", enabled: false, target_ref: {mode: "helm_release", namespace: "apps", release_name: "certd", chart_name: "certd-chart", service_count: 2}},
      {...common, id: 4, name: "Portainer stack", runtime_type: "portainer", status: {last_result: "failed", last_run_at: "2026-09-19T12:00:00Z"}, target_ref: {mode: "portainer_stack", endpoint_id: 2, stack_id: 11, stack_name: "release-stack", stack_type: "standalone", service_count: 3}},
    ]
    let runCount = 0
    let deleteCount = 0
    let releaseRun!: () => void
    const runReady = new Promise<void>(resolve => { releaseRun = resolve })
    await page.route(url => url.pathname.startsWith("/api/"), async route => {
      const url = new URL(route.request().url())
      const path = url.pathname
      const send = (data: unknown) => route.fulfill({status: 200, contentType: "application/json", body: JSON.stringify(data)})
      if (path === "/api/auth/me") return send({id: 1, username: "admin", is_admin: true})
      if (path === "/api/settings") return send([])
      if (path === "/api/tasks") return send([])
      if (path === "/api/tasks/7") return send({id: 7, state: "succeeded"})
      if (path === "/api/runtime-connections") return send({items: [{id: 1, name: "prod", type: "docker", enabled: true, config: {}, secrets: {}}], total: 1})
      if (path === "/api/trackers") return send({items: [{id: 1, name: "release-tracker", sources: [], enabled: true}], total: 1})
      if (path === "/api/executors") {
        const query = url.searchParams.get("search")
        const items = query ? executors.filter(item => item.name.includes(query)) : executors
        return send({items, total: items.length})
      }
      if (path === "/api/executors/1/run") { runCount++; await runReady; return send({task_id: 7, status: "queued"}) }
      if (path === "/api/executors/1" && route.request().method() === "DELETE") { deleteCount++; return send({}) }
      if (path.endsWith("/history")) return send({items: [
        {id: 101, executor_id: 1, status: "success", started_at: "2026-09-19T12:00:00Z", finished_at: "2026-09-19T12:00:23Z", from_version: "registry.example/project/api:1.0.0", to_version: "registry.example/project/api:1.1.0", message: "Deployment completed"},
        {id: 102, executor_id: 1, status: "failed", started_at: "2026-09-19T11:00:00Z", finished_at: "2026-09-19T11:01:00Z", from_version: "registry.example/project/api@sha256:" + "a".repeat(64), to_version: "registry.example/project/api@sha256:" + "b".repeat(64), message: "Registry timeout: " + "long-error-message-".repeat(35)},
        {id: 103, executor_id: 1, status: "success", started_at: "2026-09-19T10:00:00Z", finished_at: "2026-09-19T10:00:30Z", from_version: null, to_version: null, message: "Services updated", diagnostics: {kind: "docker_compose", summary: {updated_count: 1, skipped_count: 1, failed_count: 0, group_message: null}, services: [{service: "service-a", status: "success", from_version: "registry.example.test/team/service-a:1.2.0", to_version: "registry.example.test/team/service-a:1.2.0"}, {service: "service-b", status: "skipped", from_version: "registry.example.test/team/service-b:2.3.0", to_version: "registry.example.test/team/service-b:2.3.0"}]}},
      ], total: 3})
      if (path.endsWith("/snapshots")) return send({items: [], total: 0})
      if (path === "/api/executors/1/config") return send(executors[0])
      if (path === "/api/executors/1") return send({...executors[0], latest_run: null})
      return send({items: [], total: 0})
    })
    await page.goto("/executors")
    const rows = page.getByTestId("executor-row")
    await expect(rows).toHaveCount(4)
    // No extra title/count block; the shared breadcrumb remains the page heading.
    await expect(page.getByRole("heading")).toHaveCount(0)
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
    for (const row of await rows.all()) {
      expect(await row.evaluate(el => el.scrollWidth <= el.clientWidth + 1)).toBe(true)
    }
    if (width >= 1280) {
      const geometry = await rows.first().locator('[data-slot="table-cell"]').evaluateAll(cells => cells.map(cell => cell.getBoundingClientRect().top))
      expect(Math.max(...geometry) - Math.min(...geometry)).toBeLessThan(2)
      expect((await rows.first().boundingBox())!.height).toBeLessThan(110)
      const targetCells = page.getByTestId("executor-target-cell")
      await expect(targetCells).toHaveCount(4)
      await expect(targetCells.first()).toBeVisible()
      await expect(targetCells.first()).toContainText("release-api")
      await expect(targetCells.first().getByTestId("executor-target-service-count")).toHaveAttribute("data-count", "1")
      await expect(targetCells.nth(1)).toContainText("app")
      await expect(targetCells.nth(1).getByTestId("executor-target-service-count")).toHaveAttribute("data-count", "2")
      await expect(targetCells.nth(2).getByTestId("executor-target-service-count")).toHaveAttribute("data-count", "2")
      await expect(targetCells.nth(3).getByTestId("executor-target-service-count")).toHaveAttribute("data-count", "3")
    }
    await expect(page.getByText("sshExecutor.ownership_verification_required", {exact: true})).toHaveCount(0)
    await page.getByRole("button", {name: language === "zh" ? "刷新执行器" : "Refresh executors", exact: true}).click()
    await expect(rows).toHaveCount(4)
    await page.screenshot({path: `/tmp/rt-executors-${width}-${language}.png`})
    const runLabel = language === "zh" ? "立即执行" : "Run Now"
    const historyLabel = language === "zh" ? "查看执行历史" : "View execution history"
    await expect(rows.nth(1).getByRole("button", {name: runLabel, exact: true})).toBeDisabled()
    await expect(rows.nth(2).getByRole("button", {name: runLabel, exact: true})).toBeDisabled()
    const historyButton = rows.first().getByRole("button", {name: historyLabel, exact: true})
    await historyButton.focus()
    await page.keyboard.press("Enter")
    const dialog = page.getByRole("dialog")
    await expect(dialog.getByRole("heading", {name: executors[0].name})).toBeVisible()
    await expect.poll(async () => Math.round((await dialog.boundingBox())!.x + (await dialog.boundingBox())!.width)).toBeLessThanOrEqual(width)
    await expect(dialog.getByRole("button", {name: runLabel, exact: true})).toBeEnabled()
    expect(runCount).toBe(0)
    const historyRows = dialog.getByTestId("executor-history-item")
    await expect(historyRows).toHaveCount(3)
    // Original history exposes changes and messages directly, without an extra toggle.
    await expect(dialog.getByTestId("executor-history-toggle")).toHaveCount(0)
    await expect(historyRows.first().getByTestId("executor-history-from-image")).toHaveText("registry.example/project/api:1.0.0")
    await expect(historyRows.first().getByTestId("executor-history-to-image")).toHaveText("registry.example/project/api:1.1.0")
    await expect(historyRows.nth(1).getByTestId("executor-history-message")).toContainText("Registry timeout")
    await expect(historyRows.nth(1).getByTestId("executor-history-to-image")).toHaveText("registry.example/project/api@sha256:" + "b".repeat(64))
    await expect(historyRows.nth(2).getByTestId("executor-history-to-image")).toHaveCount(2)
    const changes = historyRows.nth(2).getByTestId("executor-history-image-change-list")
    await changes.scrollIntoViewIfNeeded()
    await expect(changes.getByRole("table")).toHaveCount(0)
    const serviceRows = changes.getByTestId("executor-history-service-change")
    await expect(serviceRows).toHaveCount(2)
    await expect(serviceRows.nth(0)).toHaveAttribute("aria-label", "service-a")
    await expect(serviceRows.nth(1)).toHaveAttribute("aria-label", "service-b")
    for (const row of await serviceRows.all()) {
      const from = row.getByTestId("executor-history-from-image")
      const to = row.getByTestId("executor-history-to-image")
      const before = (await from.boundingBox())!
      const after = (await to.boundingBox())!
      if (width >= 1280) {
        expect(Math.abs(before.y - after.y)).toBeLessThan(2)
        expect(after.x).toBeGreaterThan(before.x + before.width)
      } else {
        expect(after.y).toBeGreaterThanOrEqual(before.y + before.height)
        expect(before.width).toBeGreaterThan((await row.boundingBox())!.width * 0.8)
      }
      expect(await row.evaluate(el => el.scrollWidth <= el.clientWidth + 1)).toBe(true)
    }
    await changes.screenshot({path: `/tmp/rt-multi-service-changes-${width}-${language}.png`})
    for (const row of await historyRows.all()) {
      expect(await row.evaluate(el => el.scrollWidth <= el.clientWidth + 1)).toBe(true)
    }
    await page.screenshot({path: `/tmp/rt-executor-history-restored-${width}-${language}.png`})
    expect(await dialog.evaluate(el => el.scrollWidth <= el.clientWidth + 1)).toBe(true)
    await dialog.getByRole("button", {name: language === "zh" ? "编辑" : "Edit", exact: true}).click()
    await expect(page.getByRole("dialog").getByRole("textbox", {name: language === "zh" ? "执行器名称" : "Executor name", exact: true})).toHaveValue(executors[0].name)
    expect(runCount).toBe(0)
    await page.keyboard.press("Escape")
    await rows.first().getByRole("button", {name: historyLabel, exact: true}).click()
    await expect(dialog).toBeVisible()
    await page.keyboard.press("Escape")
    const run = rows.first().getByRole("button", {name: runLabel, exact: true})
    await run.click()
    await expect(run).toBeDisabled()
    await expect.poll(() => runCount).toBe(1)
    releaseRun()
    await expect(run).toBeEnabled()
    await rows.first().getByRole("button", {name: language === "zh" ? "操作" : "Actions", exact: true}).click()
    await page.getByRole("menuitem", {name: language === "zh" ? "删除" : "Delete", exact: true}).click()
    await expect(page.getByRole("alertdialog")).toBeVisible()
    await page.getByRole("alertdialog").getByRole("button", {name: language === "zh" ? "取消" : "Cancel", exact: true}).click()
    expect(deleteCount).toBe(0)
    const search = page.getByRole("textbox", {name: language === "zh" ? "按名称、描述、追踪器或运行时搜索" : "Search by name, description, tracker, or runtime", exact: true})
    await search.fill("nothing-matches")
    await expect(rows).toHaveCount(0)
    await search.fill("")
    await expect(rows).toHaveCount(4)
    expect(errors).toEqual([])
  })
}
