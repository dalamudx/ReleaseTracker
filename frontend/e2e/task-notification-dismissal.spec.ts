import { expect, test } from "@playwright/test"

for (const [language, width] of [["zh", 390], ["en", 1280]] as const) {
    test(`clearing recent notifications never clears the queue ${language} ${width}`, async ({ page }) => {
        await page.setViewportSize({ width, height: 900 })
        await page.addInitScript(language => {
            document.cookie = "releasetracker-csrf=isolated-fixture; path=/"
            localStorage.setItem("language", language)
        }, language)
        const base = { kind: "fetch", attempts: 1, max_retries: 3, created_at: 1789704900, updated_at: 1789705000, due_at: 1789705000, result: {}, target: {}, error_code: null }
        const tasks = [
            { ...base, id: 7, state: "succeeded", target_label: "recent-result-7" },
            { ...base, id: 6, state: "failed", target_label: "recent-result-6" },
            { ...base, id: 5, state: "no_change", target_label: "recent-result-5" },
            { ...base, id: 4, state: "needs_attention", kind: "deploy", target_label: "protected-attention" },
            { ...base, id: 3, state: "running", target_label: "protected-running" },
            { ...base, id: 2, state: "succeeded", target_label: "older-unseen-2" },
            { ...base, id: 1, state: "succeeded", target_label: "older-unseen-1" },
        ]
        const queueWrites: string[] = []
        const errors: string[] = []
        page.on("pageerror", error => errors.push(error.message))
        await page.route(url => url.pathname.startsWith("/api/"), route => {
            const request = route.request()
            const path = new URL(request.url()).pathname
            if (path.startsWith("/api/tasks") && request.method() !== "GET") queueWrites.push(path)
            const data = path === "/api/auth/me" ? { id: 1, username: "fixture-admin", is_admin: true }
                : path === "/api/tasks" ? tasks : { items: [], total: 0 }
            return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(data) })
        })
        await page.goto("/tasks")
        const main = page.locator("main")
        for (const task of tasks) await expect(main.getByText(task.target_label, { exact: true })).toBeVisible()
        const bell = page.getByRole("button", { name: language === "zh" ? "任务动态" : "Task notifications", exact: true })
        await bell.click()
        const popup = page.locator('[data-slot="popover-content"]')
        await expect(popup.getByText("recent-result-7", { exact: true })).toBeVisible()
        await expect(popup.getByText("older-unseen-2", { exact: true })).toHaveCount(0)
        const clear = popup.getByRole("button", { name: language === "zh" ? "清理最近任务" : "Clear recent tasks", exact: true })
        await expect(clear).toBeEnabled()
        await clear.click()
        for (const label of ["recent-result-7", "recent-result-6", "recent-result-5"]) await expect(popup.getByText(label, { exact: true })).toHaveCount(0)
        for (const label of ["protected-attention", "protected-running", "older-unseen-2", "older-unseen-1"]) await expect(popup.getByText(label, { exact: true })).toBeVisible()
        const hidden = await page.evaluate(() => JSON.parse(localStorage.getItem("releasetracker:task-notifications:dismissed:v1")!).tasks)
        expect(hidden.map(([id]: [number, number]) => id).sort()).toEqual([5, 6, 7])
        expect(queueWrites).toEqual([])
        await popup.getByRole("button", { name: language === "zh" ? "刷新任务" : "Refresh tasks", exact: true }).click()
        await expect(popup.getByText("recent-result-7", { exact: true })).toHaveCount(0)
        await page.keyboard.press("Escape")
        for (const task of tasks) await expect(main.getByText(task.target_label, { exact: true })).toBeVisible()
        await page.reload()
        await bell.click()
        await expect(popup.getByText("recent-result-7", { exact: true })).toHaveCount(0)
        await expect(popup.getByText("protected-attention", { exact: true })).toBeVisible()
        await page.keyboard.press("Escape")
        for (const task of tasks) await expect(main.getByText(task.target_label, { exact: true })).toBeVisible()
        // A changed result is a new notification, not permanently hidden by id.
        tasks[0].updated_at++
        await bell.click()
        await popup.getByRole("button", { name: language === "zh" ? "刷新任务" : "Refresh tasks", exact: true }).click()
        await expect(popup.getByText("recent-result-7", { exact: true })).toBeVisible()
        expect(queueWrites).toEqual([])
        expect(errors).toEqual([])
    })
}
