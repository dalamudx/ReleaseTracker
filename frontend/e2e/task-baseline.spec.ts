import { expect, test } from "@playwright/test"

for (const width of [1280, 320]) for (const language of ["zh", "en"]) {
    test(`baseline review and blocker details ${width} ${language}`, async ({ page }, testInfo) => {
        await page.setViewportSize({ width, height: 800 })
        await page.addInitScript(language => {
            localStorage.setItem("language", language)
            document.cookie = "releasetracker-csrf=fixture-csrf; path=/"
        }, language)
        const errors: string[] = []
        const writes: string[] = []
        page.on("pageerror", error => errors.push(error.message))
        const base = { kind: "deploy", attempts: 1, max_retries: 0, due_at: 1789705000, created_at: 1789704900, updated_at: 1789705000, error_code: null, message: null, target: {}, triggers: [], attempt_history: [] }
        const done = { ...base, id: 17, state: "succeeded", target_label: "OpenBao-isolated-fixture", result: { baseline_status: "review_required", baseline_reason: "legacy_plan_missing_proof" } }
        const waiting = { ...base, id: 21, state: "queued", target_label: "waiting-isolated-fixture", result: {}, blockers: [{ task_id: 99, target_label: "isolated-runtime", reason: "readiness" }] }
        await page.route(url => new URL(url).pathname.startsWith("/api/"), async route => {
            const path = new URL(route.request().url()).pathname
            let data: unknown = {}
            if (route.request().method() === "POST") writes.push(path)
            if (path === "/api/auth/me") data = { id: 1, username: "fixture-admin", is_admin: true }
            if (path === "/api/tasks") data = [done, waiting]
            if (path === "/api/tasks/17") data = done
            if (path === "/api/tasks/21") data = waiting
            if (path === "/api/tasks/17/baseline/recheck") {
                done.result.baseline_status = "pending"
                data = { status: "pending" }
            }
            await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(data) })
        })
        await page.goto("/tasks")
        await expect(page.getByText(language === "zh" ? "基线待审核" : "Baseline needs review", { exact: true })).toBeVisible()
        await page.getByRole("button", { name: /17/ }).click()
        await expect(page.getByText(language === "zh" ? /旧计划缺少批准后的配置证明/ : /The old plan lacks approved configuration evidence/)).toBeVisible()
        await page.getByRole("button", { name: /21/ }).click()
        await expect(page.getByText(/isolated-runtime.*#99/)).toBeVisible()
        expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
        await page.screenshot({ path: testInfo.outputPath("baseline-review.png"), fullPage: true })
        await page.getByRole("button", { name: language === "zh" ? "重新核验基线" : "Recheck baseline", exact: true }).click()
        await expect(page.getByText(language === "zh" ? "基线核验中" : "Baseline verification pending", { exact: true })).toBeVisible()
        expect(writes).toEqual(["/api/tasks/17/baseline/recheck"])
        expect(errors).toEqual([])
    })
}
