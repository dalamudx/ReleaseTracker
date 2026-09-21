import { expect, test } from "@playwright/test"

const cases = [
    { mode: "light", color: "orange", width: 1280 },
    { mode: "light", color: "yellow", width: 390 },
    { mode: "dark", color: "blue", width: 1280 },
    { mode: "dark", color: "rose", width: 390 },
] as const

for (const palette of cases) {
    test(`semantic palette ${palette.mode} ${palette.color} ${palette.width}`, async ({ page }) => {
        await page.setViewportSize({ width: palette.width, height: 900 })
        await page.addInitScript(config => {
            localStorage.setItem("token", "example-token")
            localStorage.setItem("language", "en")
            localStorage.setItem("vite-ui-theme-config", JSON.stringify({ ...config, radius: 0.5, zoom: "default" }))
        }, palette)

        const base = {
            kind: "fetch",
            attempts: 1,
            max_retries: 3,
            due_at: 1789705000,
            created_at: 1789704900,
            updated_at: 1789705000,
            error_code: null,
            result: {},
            target: {},
            triggers: [],
            attempt_history: [],
        }
        const tasks = [
            { ...base, id: 31, state: "running", target_label: "example-running-task" },
            { ...base, id: 32, state: "retry_wait", target_label: "example-retry-task", attempts: 2 },
            { ...base, id: 33, state: "succeeded", target_label: "example-success-task" },
            { ...base, id: 34, state: "needs_attention", kind: "deploy", target_label: "example-attention-task" },
        ]
        const errors: string[] = []
        page.on("pageerror", error => errors.push(error.message))
        await page.route(url => new URL(url).pathname.startsWith("/api/"), async route => {
            const path = new URL(route.request().url()).pathname
            const data = path === "/api/auth/me"
                ? { id: 1, username: "example-admin", is_admin: true }
                : path === "/api/tasks"
                  ? tasks
                  : {}
            await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(data) })
        })

        await page.goto("/tasks")
        await expect(page.locator("html")).toHaveClass(new RegExp(palette.mode))
        await expect(page.locator("html")).toHaveAttribute("data-theme", palette.color)
        for (const label of ["Running", "Waiting to retry", "Succeeded", "Needs verification"]) {
            await expect(page.getByText(label, { exact: true }).first()).toBeVisible()
        }
        expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
        await page.screenshot({ path: `/tmp/rt-palette-${palette.mode}-${palette.color}-${palette.width}.png`, fullPage: true })
        expect(errors).toEqual([])
    })
}
