import { expect, test } from "@playwright/test"

for (const language of ["zh", "en"]) for (const width of [1280, 390]) {
  test(`snapshot recovery configuration validation ${language} ${width}`, async ({ page }) => {
    await page.setViewportSize({ width, height: 1100 })
    await page.addInitScript(language => {
      document.cookie = "releasetracker-csrf=fixture-csrf; path=/"
      localStorage.setItem("language", language)
    }, language)
    const executor = {
      id: 1, name: "isolated-nginx-fixture", runtime_type: "docker", runtime_connection_id: 1,
      tracker_name: "isolated-source", enabled: true, update_mode: "manual",
      image_selection_mode: "replace_tag_on_current_image", image_reference_mode: "tag",
      target_ref: { mode: "container", container_name: "isolated-nginx" },
      status: { last_result: "success", last_version: "nginx:1.27" },
    }
    let previewValid = false
    let previewRequests = 0
    const mutations: string[] = []
    const errors: string[] = []
    page.on("pageerror", error => errors.push(error.message))
    await page.route(url => url.pathname.startsWith("/api/"), route => {
      const path = new URL(route.request().url()).pathname
      const send = (data: unknown) => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(data) })
      if (path === "/api/auth/me") return send({ id: 1, username: "fixture-admin", is_admin: true })
      if (path === "/api/tasks" || path === "/api/settings") return send([])
      if (path === "/api/executors") return send({ items: [executor], total: 1 })
      if (path === "/api/executors/1") return send({ ...executor, latest_run: null })
      if (path === "/api/executors/1/snapshots") return send({ items: [{
        id: 42, executor_id: 1, run_id: null, runtime_type: "docker", trigger: "manual",
        image_at_capture: "nginx:1.26", created_at: "2026-09-19T00:00:00Z",
        unredacted_persisted: true, integrity_status: "verified", locked: false, schema_version: 1,
      }], total: 1, page: 1, page_size: 10, snapshot_retention_cap: 10, latest_snapshot_id: 42 })
      if (path === "/api/executors/1/rollback/preview") {
        previewRequests++
        expect(route.request().method()).toBe("POST")
        expect(route.request().postDataJSON()).toEqual({ snapshot_id: 42, include_diff: true })
        return send({ snapshot_id: 42, image_at_capture: "nginx:1.26", mutation_performed: false,
          integrity_status: previewValid ? "verified" : "invalid", snapshot_valid: previewValid,
          validation_error: previewValid ? null : "snapshot.create_config must be a non-empty dict",
          configuration_diff: previewValid ? { scope: "container_configuration", truncated: false, current_missing: false, review_fingerprint: "reviewed-runtime-state", lines: [
            { operation: "-", path: "/create_config/image", value: '"nginx:1.27"', redacted: false },
            { operation: "+", path: "/create_config/image", value: '"nginx:1.26"', redacted: false },
            { operation: "-", path: "/create_config/environment/PASSWORD", value: "***REDACTED***", redacted: true },
            { operation: "+", path: "/create_config/environment/PASSWORD", value: "***REDACTED***", redacted: true },
          ] } : null })
      }
      if (route.request().method() !== "GET") mutations.push(path)
      return send({ items: [], total: 0 })
    })
    await page.goto("/executors")
    const row = page.getByTestId("executor-row")
    await row.getByRole("button", { name: language === "zh" ? "查看执行历史" : "View execution history", exact: true }).click()
    await page.getByRole("tab", { name: language === "zh" ? "快照" : "Snapshots", exact: true }).click()
    const item = page.getByTestId("executor-snapshot-item")
    await expect(item.getByText(language === "zh" ? "存储数据校验通过" : "Stored data verified", { exact: true })).toBeVisible()
    await expect(page.getByText(/分享前|before sharing/i)).toHaveCount(0)
    await item.getByRole("button", { name: language === "zh" ? "回滚" : "Rollback", exact: true }).click()
    const dialog = page.getByRole("alertdialog")
    await expect(dialog.getByRole("alert")).toContainText("snapshot.create_config")
    await dialog.getByLabel(language === "zh" ? "输入执行器名称以确认" : "Type the executor name to confirm").fill(executor.name)
    const confirm = page.getByTestId("executor-rollback-confirm")
    await expect(confirm).toBeDisabled()
    await expect(dialog).toContainText(language === "zh" ? "可能覆盖当前修改" : "may overwrite current changes")
    previewValid = true
    await dialog.getByRole("button", { name: language === "zh" ? "重新校验" : "Recheck snapshot", exact: true }).click()
    await expect(confirm).toBeEnabled()
    const diff = page.getByTestId("recovery-configuration-diff")
    await expect(diff).toContainText("nginx:1.27")
    await expect(diff).toContainText("nginx:1.26")
    await expect(diff).toContainText(language === "zh" ? "敏感值已隐藏" : "Sensitive value hidden")
    await expect(diff.locator("li")).toHaveCount(4)
    await expect(diff.locator("li").first()).toContainText("-")
    await expect(diff.locator("li").nth(1)).toContainText("+")
    await expect(dialog).toContainText(language === "zh" ? "以上配置差异" : "configuration changes above")
    expect(await dialog.evaluate(element => element.scrollWidth <= element.clientWidth)).toBe(true)
    expect(previewRequests).toBe(2)
    expect(mutations).toEqual([])
    expect(errors).toEqual([])
    await dialog.getByRole("button", { name: language === "zh" ? "取消" : "Cancel", exact: true }).click()
    await expect(dialog).not.toBeVisible()
  })
}
