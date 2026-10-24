import { expect, test } from "@playwright/test"

for (const width of [1280, 320]) for (const language of ["zh", "en"]) {
    test(`legacy Helm identity review ${width} ${language}`, async ({ page }, testInfo) => {
        await page.setViewportSize({ width, height: 800 })
        await page.addInitScript(language => {
            localStorage.setItem("language", language)
            document.cookie = "releasetracker-csrf=fixture; path=/"
        }, language)
        const errors: string[] = [], writes: string[] = []
        page.on("pageerror", error => errors.push(error.message))
        const saved = { id: 17, kind: "deploy", state: "queued", target_label: "authentik-isolated-fixture", approval_pending: true, attempts: 0, max_retries: 0, due_at: 1789705000, created_at: 1789704900, updated_at: 1789705000, error_code: "target_identity_changed", message: null, target: {}, result: { deployment_plan_state: "blocked" } }
        const plan = { id: 111, task_id: 17, fingerprint: "isolated-plan", state: "blocked", reason: "target_identity_changed", expires_at: 2000000000, summary: { recovery_scope: "helm_revision", configuration_diff: { scope: "helm_revision", truncated: false, lines: [{ operation: "+", path: "/chart_version", value: '"1.2.0"', redacted: false }] } } }
        await page.route(url => new URL(url).pathname.startsWith("/api/"), async route => {
            const url = new URL(route.request().url()), path = url.pathname
            if (route.request().method() === "POST") writes.push(path)
            let data: unknown = {}
            if (path === "/api/auth/me") data = { id: 1, username: "fixture-admin", is_admin: true }
            if (path === "/api/tasks") data = [saved]
            if (path === "/api/tasks/17") data = saved
            if (path === "/api/tasks/17/deployment-plan") {
                if (url.searchParams.get("refresh") === "true") {
                    plan.state = "pending"
                    plan.reason = "helm_identity_review_required"
                }
                data = plan
            }
            await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(data) })
        })
        await page.goto("/tasks")
        const blocked = language === "zh" ? "部署已阻断" : "Deployment blocked"
        await expect(page.getByText(blocked, { exact: true })).toBeVisible()
        await page.getByRole("button", { name: /17/ }).click()
        const approve = page.getByRole("button", { name: language === "zh" ? "确认并继续部署" : "Approve and continue", exact: true })
        await expect(approve).toHaveCount(0)
        await expect(page.getByText(language === "zh" ? /当前目标与纳管身份不一致/ : /The current target differs/)).toBeVisible()
        await page.screenshot({ path: testInfo.outputPath("blocked.png"), fullPage: true })
        await page.getByRole("button", { name: language === "zh" ? "刷新计划" : "Refresh plan", exact: true }).click()
        await expect(page.getByText(language === "zh" ? /已核对为同一 Helm 目标/ : /The Helm target is unchanged/)).toBeVisible()
        await expect(approve).toBeEnabled()
        await expect(page.getByText(blocked, { exact: true })).toHaveCount(0)
        expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
        await page.screenshot({ path: testInfo.outputPath("review.png"), fullPage: true })
        expect(writes).toEqual([])
        expect(errors).toEqual([])
    })
}
