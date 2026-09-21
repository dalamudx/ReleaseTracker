import { expect, test } from "@playwright/test"

for (const theme of ["light", "dark"]) for (const width of [1280, 390]) {
  test(`executor selection stays aligned ${theme} ${width}`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 })
    await page.addInitScript(theme => {
      localStorage.setItem("token", "example-token")
      localStorage.setItem("language", "zh")
      localStorage.setItem("vite-ui-theme-config", JSON.stringify({ mode: theme, color: "neutral", radius: 0.5, zoom: "default" }))
    }, theme)
    const executors = [1, 2, 3].map(id => ({
      id, name: `Example executor ${id}`, runtime_type: "docker", runtime_connection_id: 1,
      runtime_connection_name: "Example connection", tracker_name: "example-tracker",
      tracker_source_id: 1, channel_name: "stable", enabled: true, update_mode: "manual",
      image_selection_mode: "replace_tag_on_current_image", image_reference_mode: "tag",
      target_ref: { mode: "container", container_name: `service-${id}`, container_id: `example-${id}` },
      status: { last_result: "success", last_run_at: "2026-09-19T12:00:00Z" },
    }))
    const errors: string[] = []
    page.on("pageerror", error => errors.push(error.message))
    await page.route(url => url.pathname.startsWith("/api/"), async route => {
      const path = new URL(route.request().url()).pathname
      const send = (data: unknown) => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(data) })
      if (path === "/api/auth/me") return send({ id: 1, username: "example", is_admin: true })
      if (path === "/api/settings" || path === "/api/tasks") return send([])
      if (path === "/api/executors") return send({ items: executors, total: executors.length })
      if (path === "/api/runtime-connections") return send({ items: [], total: 0 })
      if (path === "/api/trackers") return send({ items: [], total: 0 })
      const match = path.match(/^\/api\/executors\/(\d+)$/)
      if (match) return send({ ...executors[Number(match[1]) - 1], latest_run: null })
      return send({ items: [], total: 0 })
    })
    await page.goto("/executors")
    const rows = page.getByTestId("executor-row")
    await expect(rows).toHaveCount(3)
    await expect(page.locator("html")).toHaveClass(new RegExp(theme))
    const cells = rows.nth(1).locator('[data-slot="table-cell"]:visible')
    const geometry = () => cells.evaluateAll(items => items.map(el => {
      const { x, y, width, height } = el.getBoundingClientRect()
      return { x, y, width, height }
    }))
    // Cell backgrounds must not mask the table row's hover/selection layer.
    for (const target of [cells.first(), cells.last(), cells.last().getByRole("button", { name: "立即执行", exact: true })]) {
      await target.hover()
      await expect.poll(() => rows.nth(1).evaluate(el => el.matches(":hover"))).toBe(true)
      await expect.poll(() => rows.nth(1).evaluate(el => getComputedStyle(el).backgroundColor)).not.toBe("rgba(0, 0, 0, 0)")
      for (const cell of await cells.all()) {
        await expect(cell).toHaveCSS("background-color", "rgba(0, 0, 0, 0)")
      }
    }
    await rows.nth(1).screenshot({ path: `/tmp/rt-executor-hover-${theme}-${width}.png` })
    const before = await geometry()
    await rows.nth(1).getByText("Example executor 2", { exact: true }).click()
    await cells.first().click({ position: { x: 4, y: 4 } })
    await expect(page.getByRole("dialog")).toHaveCount(0)
    await expect(rows.nth(1)).toHaveAttribute("aria-selected", "true")
    await expect(rows.nth(1).getByTestId("executor-selection-marker")).toBeVisible()
    await rows.nth(1).screenshot({ path: `/tmp/rt-executor-selected-row-${theme}-${width}.png` })
    await rows.nth(1).getByRole("button", { name: "查看执行历史", exact: true }).focus()
    await page.keyboard.press("Enter")
    await expect(page.getByRole("dialog")).toBeVisible()
    await expect(rows.nth(1)).toHaveAttribute("data-selected", "true")
    await expect(rows.nth(1)).toHaveAttribute("data-state", "selected")
    await expect(rows.nth(1)).toHaveAttribute("aria-selected", "true")
    await expect(rows.first()).toHaveAttribute("aria-selected", "false")
    await expect.poll(async () => {
      const box = (await page.getByRole("dialog").boundingBox())!
      return Math.abs(box.x + box.width - width)
    }).toBeLessThan(1)
    await expect(cells.first()).toHaveCSS("overflow", "visible")
    const selectedColor = await rows.nth(1).evaluate(el => getComputedStyle(el).backgroundColor)
    expect(selectedColor).not.toBe("rgba(0, 0, 0, 0)")
    expect(selectedColor).not.toBe(await rows.first().evaluate(el => getComputedStyle(el).backgroundColor))
    const marker = rows.nth(1).getByTestId("executor-selection-marker")
    await expect(marker).toHaveCount(1)
    await expect(page.getByTestId("executor-selection-marker")).toHaveCount(1)
    await expect(marker).toHaveCSS("pointer-events", "none")
    const markerBox = (await marker.boundingBox())!
    const cellBox = (await cells.first().boundingBox())!
    expect(Math.abs(markerBox.x - cellBox.x)).toBeLessThan(1)
    expect(Math.abs(markerBox.y + markerBox.height / 2 - cellBox.y - cellBox.height / 2)).toBeLessThan(1)
    expect(markerBox.height).toBe(28)
    expect(markerBox.width).toBe(4)
    const after = await geometry()
    await page.screenshot({ path: `/tmp/rt-executor-selected-${theme}-${width}.png` })
    expect(after.map(cell => cell.width)).toEqual(before.map(cell => cell.width))
    expect(after.map(cell => cell.height)).toEqual(before.map(cell => cell.height))
    expect(Math.max(...after.map(cell => cell.y)) - Math.min(...after.map(cell => cell.y))).toBeLessThan(1)
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
    await page.keyboard.press("Escape")
    await expect(page.getByRole("dialog")).toHaveCount(0)
    await expect(rows.nth(1)).toHaveAttribute("data-selected", "true")
    await expect(rows.nth(1).getByTestId("executor-selection-marker")).toBeVisible()
    await page.getByRole("button", {name: "刷新执行器", exact: true}).click()
    await expect(rows.nth(1)).toHaveAttribute("data-state", "selected")
    await rows.nth(2).focus()
    await page.keyboard.press("Space")
    await expect(rows.nth(2)).toHaveAttribute("aria-selected", "true")
    await expect(rows.nth(1)).toHaveAttribute("aria-selected", "false")
    await expect(page.getByRole("dialog")).toHaveCount(0)
    await rows.first().getByRole("button", { name: "查看执行历史", exact: true }).click()
    await expect(rows.first()).toHaveAttribute("data-state", "selected")
    await expect(rows.nth(1)).toHaveAttribute("aria-selected", "false")
    await page.keyboard.press("Escape")
    expect(errors).toEqual([])
  })
}
