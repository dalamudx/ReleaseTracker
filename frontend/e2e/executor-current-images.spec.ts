import { expect, test } from "@playwright/test"

for (const runtime of ["kubernetes", "portainer"] as const) {
  for (const language of ["zh", "en"]) for (const width of [1280, 390]) {
    test(`live ${runtime} images stay display-only ${language} ${width}`, async ({ page }) => {
      await page.setViewportSize({ width, height: 900 })
      await page.addInitScript(language => {
        document.cookie = "releasetracker-csrf=fixture-csrf; path=/"
        localStorage.setItem("language", language)
      }, language)
      const target = {
        ...(runtime === "kubernetes"
          ? { mode: "kubernetes_workload", namespace: "apps", kind: "Deployment", name: "app" }
          : { mode: "portainer_stack", endpoint_id: 1, stack_id: 1, stack_name: "app", stack_type: "standalone" }),
        services: [{ service: "api", image: "registry.example.test/api:stale" }],
        service_count: 1,
      }
      const liveImage = "registry.example.test/api@sha256:" + "a".repeat(64)
      const executor = {
        id: 1, name: "live-image-regression", runtime_type: runtime, runtime_connection_id: 1,
        enabled: true, update_mode: "manual", image_selection_mode: "use_tracker_image_and_tag",
        image_reference_mode: "tag", target_ref: target, tracker_name: "image-tracker",
        tracker_source_id: 9, channel_name: "stable",
        service_bindings: [{ service: "api", tracker_name: "image-tracker", tracker_source_id: 9, channel_name: "stable" }],
        status: null,
      }
      const source = {
        id: 9, source_key: "image", source_type: "container", enabled: true,
        source_config: { registry: "registry.example.test", image: "api" }, source_rank: 0,
        release_channels: [{ release_channel_key: "stable", name: "stable", type: "release", enabled: true, last_version: "1.2.3" }],
      }
      const tracker = { id: 1, name: "image-tracker", enabled: true, sources: [source], status: {} }
      let saved: Record<string, unknown> | null = null
      const errors: string[] = []
      page.on("pageerror", error => errors.push(error.message))
      await page.route(url => url.pathname.startsWith("/api/"), async route => {
        const request = route.request()
        const path = new URL(request.url()).pathname
        const send = (data: unknown) => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(data) })
        if (path === "/api/auth/me") return send({ id: 1, username: "fixture-admin", is_admin: true })
        if (path === "/api/executors/configuration-preview") {
          expect(request.method()).toBe("POST")
          const draft = request.postDataJSON()
          expect(draft.executor_id).toBe(1)
          expect(draft.configuration.target_ref).toEqual(target)
          expect(draft.configuration.service_bindings[0].service).toBe("api")
          expect(draft.configuration).not.toHaveProperty("current_images")
          return send({ mutation_performed: false, checked_at: "2026-10-03T14:00:00Z", comparison_error: null, configuration_diff: {
            scope: runtime === "kubernetes" ? "workload_spec" : "stack_configuration", truncated: false,
            lines: [{ operation: "-", path: "/services/api/image", value: JSON.stringify(liveImage), redacted: false },
              { operation: "+", path: "/services/api/image", value: '"registry.example.test/api:1.2.3"', redacted: false }],
          } })
        }

        if (path === "/api/executors/1" && request.method() === "PUT") {
          saved = request.postDataJSON()
          return send(executor)
        }
        if (path === "/api/executors") return send({ items: [executor], total: 1 })
        if (path === "/api/executors/1/config") return send({ ...executor, current_image: liveImage, current_images: { api: liveImage } })
        if (path === "/api/executors/1") return send({ ...executor, latest_run: null })
        if (path === "/api/runtime-connections") return send({ items: [{ id: 1, name: "runtime", type: runtime, enabled: true, config: runtime === "kubernetes" ? { namespaces: ["apps"] } : { endpoint_id: 1 }, secrets: {} }], total: 1 })
        if (path === "/api/trackers") return send({ items: [tracker], total: 1 })
        if (path === "/api/settings" || path === "/api/tasks") return send([])
        return send({ items: [], total: 0 })
      })
      await page.goto("/executors")
      await page.getByTestId("executor-row").getByRole("button", { name: language === "zh" ? "查看执行历史" : "View execution history", exact: true }).click()
      await page.getByRole("dialog").getByRole("button", { name: language === "zh" ? "编辑" : "Edit", exact: true }).click()
      const dialog = page.getByRole("dialog")
      await expect(dialog).toHaveAccessibleDescription(language === "zh"
        ? "选择运行时目标、绑定版本来源并审核部署策略。"
        : "Choose a runtime target, bind release sources, and review deployment policies.")
      const descriptionStyle = await dialog.locator('[data-slot="sheet-description"]').evaluate(node => {
        const style = getComputedStyle(node)
        return { position: style.position, width: style.width, height: style.height }
      })
      expect(descriptionStyle).toEqual({ position: "absolute", width: "1px", height: "1px" })
      await expect(dialog.getByRole("textbox", { name: language === "zh" ? "执行器名称" : "Executor name", exact: true })).toHaveValue(executor.name)
      async function checkStepAlignment() {
        const steps = dialog.getByTestId("executor-step-tabs").getByRole("button")
        await expect(steps).toHaveCount(4)
        for (const button of await steps.all()) {
          // Read all rectangles in one frame: the sheet itself animates on entry.
          await expect.poll(() => button.evaluate((node, desktop) => {
            const number = node.querySelector('[data-testid="executor-step-number"]')!.getBoundingClientRect()
            const label = node.querySelector('[data-testid="executor-step-label"]')!.getBoundingClientRect()
            const bounds = node.getBoundingClientRect()
            const aligned = desktop
              ? Math.abs(number.y + number.height / 2 - label.y - label.height / 2) < 1
              : Math.abs(number.x + number.width / 2 - label.x - label.width / 2) < 1 && label.y >= number.y + number.height
            return aligned && label.x >= bounds.x && label.right <= bounds.right && node.scrollWidth <= node.clientWidth
          }, width >= 640)).toBe(true)
        }
      }
      await checkStepAlignment()
      const next = dialog.getByRole("button", { name: language === "zh" ? "继续" : "Continue", exact: true })
      await next.click()
      await expect(dialog.locator('[data-slot="select-value"]').filter({ hasText: /^image-tracker$/ })).toBeVisible()
      await next.click()
      await next.click()
      await checkStepAlignment()
      await dialog.getByTestId("executor-step-tabs").screenshot({ path: `/tmp/rt-executor-step-alignment-${runtime}-${language}-${width}.png` })
      const diff = dialog.getByTestId("executor-draft-configuration-diff")
      await diff.scrollIntoViewIfNeeded()
      await expect(diff).toContainText(liveImage)
      await expect(diff.locator("li")).toHaveCount(2)
      expect(await diff.evaluate(node => node.scrollWidth <= node.clientWidth)).toBe(true)
      await dialog.getByRole("button", { name: language === "zh" ? "刷新对比" : "Refresh comparison", exact: true }).click()
      await expect(diff).toContainText(liveImage)
      await expect(dialog.getByText("registry.example.test/api:stale", { exact: true })).toHaveCount(0)
      await dialog.getByRole("button", { name: language === "zh" ? "保存" : "Save", exact: true }).click()
      await expect.poll(() => saved).not.toBeNull()
      expect(saved!.target_ref).toEqual(target)
      expect(saved).not.toHaveProperty("current_images")
      expect(saved).not.toHaveProperty("current_image")
      expect(errors).toEqual([])
    })
  }
}
