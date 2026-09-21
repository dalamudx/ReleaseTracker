import {expect, test} from "@playwright/test"

for (const width of [1280, 1920, 390]) {
    for (const language of ["zh", "en"]) {
        test(`compact task summaries ${width}px ${language}`, async ({page}) => {
            await page.setViewportSize({width, height: 900})
            await page.addInitScript(language => {
                localStorage.setItem("token", "test-token")
                localStorage.setItem("language", language)
            }, language)
            const tasks = ["retry_wait", "needs_attention", "succeeded"].map((state, i) => ({
                id: 17 + i, state, kind: i === 1 ? "deploy" : "fetch",
                target_label: i === 0 ? "affine-production-container-with-a-very-long-target-name" : `project-${i}`,
                attempts: 2, max_retries: 3, due_at: 1789705000, created_at: 1789704900, updated_at: 1789705000,
                error_code: i === 0 ? "upstream_timeout" : null, message: i === 0 ? "Registry request timed out" : null,
                target: {}, result: {}, triggers: [], attempt_history: [],
            }))
            const errors: string[] = []
            page.on("pageerror", error => errors.push(error.message))
            await page.route(url => new URL(url).pathname.startsWith("/api/"), async route => {
                const path = new URL(route.request().url()).pathname
                const data = path === "/api/auth/me" ? {id: 1, username: "admin", is_admin: true} : path === "/api/tasks" ? tasks : tasks.find(task => path === `/api/tasks/${task.id}`) ?? {}
                await route.fulfill({status: 200, contentType: "application/json", body: JSON.stringify(data)})
            })
            await page.goto("/tasks")
            const rows = page.getByTestId("task-summary")
            await expect(rows).toHaveCount(3)
            for (const row of await rows.all()) {
                await expect(row.getByRole("link")).toBeVisible()
                await expect(row.getByRole("button")).toBeVisible()
                const metrics = await row.evaluate(element => {
                    const selectors = ['a', '[data-testid="task-summary-state"]', '[data-testid="task-summary-attempts"]', '[data-testid="task-summary-time"]', 'button']
                    const boxes = selectors.map(selector => element.querySelector(selector)!.getBoundingClientRect())
                    return {height: element.getBoundingClientRect().height, centers: boxes.map(box => box.y + box.height / 2), boxes: boxes.map(box => ({left: box.left, right: box.right})), overflow: element.scrollWidth > element.clientWidth + 1}
                })
                expect(metrics.overflow).toBe(false)
                if (width >= 1280) {
                    expect(metrics.height).toBeLessThanOrEqual(36)
                    expect(Math.max(...metrics.centers) - Math.min(...metrics.centers)).toBeLessThan(2)
                    for (let i = 1; i < metrics.boxes.length; i++) expect(metrics.boxes[i].left).toBeGreaterThanOrEqual(metrics.boxes[i-1].right)
                }
            }
            expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
            await page.screenshot({path: `/tmp/rt-tasks-${width}-${language}.png`})
            await expect(page.getByText("Registry request timed out", {exact: true})).toHaveCount(0)
            await rows.first().getByRole("button").click()
            await expect(page.getByText("Registry request timed out", {exact: true})).toBeVisible()
            expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
            expect(errors).toEqual([])
        })
    }
}
