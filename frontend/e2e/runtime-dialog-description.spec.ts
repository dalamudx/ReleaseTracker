import { expect, test } from "@playwright/test"

for (const language of ["zh", "en"]) for (const width of [1280, 390]) {
  test(`runtime dialog description create/edit ${language} ${width}`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 })
    await page.addInitScript(language => {
      document.cookie = "releasetracker-csrf=fixture-csrf; path=/"
      localStorage.setItem("language", language)
    }, language)
    const warnings: string[] = []
    const errors: string[] = []
    page.on("pageerror", error => errors.push(error.message))
    page.on("console", message => {
      if (message.type() === "warning" && /Missing.*Description/.test(message.text())) warnings.push(message.text())
    })
    await page.route(url => url.pathname.startsWith("/api/"), route => {
      const path = new URL(route.request().url()).pathname
      const send = (data: unknown) => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(data) })
      if (path === "/api/auth/me") return send({ id: 1, username: "fixture-admin", is_admin: true })
      if (path === "/api/runtime-connections") return send({ items: [{ id: 1, name: "description-probe-runtime", type: "portainer", enabled: true, config: { base_url: "https://runtime.test", endpoint_id: 1 }, secrets: {} }], total: 1 })
      if (path === "/api/tasks" || path === "/api/settings") return send([])
      return send({ items: [], total: 0 })
    })
    await page.goto("/runtime-connections")
    const description = language === "zh" ? "为运行时添加或更新连接。" : "Add or update a connection for your runtime."
    async function checkDescription(title: string) {
      const dialog = page.getByRole("dialog")
      await expect(dialog).toHaveAccessibleName(title)
      await expect(dialog).toHaveAccessibleDescription(description)
      const style = await dialog.locator('[data-slot="dialog-description"]').evaluate(node => {
        const computed = getComputedStyle(node)
        return { position: computed.position, width: computed.width, height: computed.height }
      })
      expect(style).toEqual({ position: "absolute", width: "1px", height: "1px" })
      await page.keyboard.press("Escape")
      await expect(dialog).not.toBeVisible()
    }
    await page.getByRole("button", { name: language === "zh" ? "添加运行时连接" : "Add Runtime Connection", exact: true }).click()
    await checkDescription(language === "zh" ? "添加运行时连接" : "Add Runtime Connection")
    await page.getByRole("row").filter({ hasText: "description-probe-runtime" }).getByRole("button").click()
    await page.getByRole("menuitem", { name: language === "zh" ? "编辑" : "Edit", exact: true }).click()
    await checkDescription(language === "zh" ? "编辑运行时连接" : "Edit Runtime Connection")
    expect(warnings).toEqual([])
    expect(errors).toEqual([])
  })
}
