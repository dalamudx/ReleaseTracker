import { expect, test } from "@playwright/test"

for (const width of [1280, 390]) {
    test(`clear finished tasks and consistent navigation at ${width}px`, async ({ page }) => {
        await page.setViewportSize({width, height: 900})
        await page.addInitScript(() => {
            localStorage.setItem("token", "e2e-access-token")
            localStorage.setItem("language", "zh")
        })
        const errors: string[] = []
        page.on("pageerror", error => errors.push(error.message))
        const base = {kind: "fetch", attempts: 1, max_retries: 3, due_at: 1789705000, created_at: 1789704900, updated_at: 1789705000, error_code: null, result: {}, target: {}}
        const active = {...base, id: 21, state: "running", target_label: "active-job"}
        const attention = {...base, id: 22, state: "needs_attention", kind: "deploy", target_label: "protected-job"}
        const done = {...base, id: 20, state: "failed", target_label: "finished-job"}
        let cleared = false
        await page.route(url => new URL(url).pathname.startsWith("/api/"), async route => {
            const path = new URL(route.request().url()).pathname
            let data: unknown = {}
            if (path === "/api/auth/me") data = {id: 1, username: "e2e-admin", is_admin: true}
            if (path === "/api/tasks/clear") {cleared = true; data = {cleared: 1}}
            if (path === "/api/tasks") data = cleared ? [active, attention] : [done, active, attention]
            await route.fulfill({status: 200, contentType: "application/json", body: JSON.stringify(data)})
        })
        await page.goto("/tasks")
        await expect(page.locator('header [aria-current="page"]')).toHaveText("任务队列")
        if (width < 768) await page.locator('[data-sidebar="trigger"]').click()
        const sidebar = page.locator('[data-slot="sidebar"]')
        const taskLink = sidebar.getByRole("link", {name: "任务队列", exact: true})
        await expect(taskLink).toHaveAttribute("aria-current", "page")
        await expect(sidebar.getByRole("link", {name: "Webhook", exact: true})).toBeVisible()
        await taskLink.click()
        if (width < 768) await expect(page.locator('[data-mobile="true"]')).not.toBeVisible()
        await page.getByRole("button", {name: "清除已结束任务", exact: true}).click()
        await expect(page.getByRole("alertdialog")).toBeVisible()
        expect(cleared).toBe(false)
        await page.getByRole("button", {name: "取消", exact: true}).click()
        expect(cleared).toBe(false)
        await page.getByRole("button", {name: "清除已结束任务", exact: true}).click()
        await page.getByRole("button", {name: "确认清除", exact: true}).click()
        await expect(page.getByText("finished-job", {exact: true})).toHaveCount(0)
        await expect(page.getByText("active-job", {exact: true})).toBeVisible()
        await expect(page.getByText("protected-job", {exact: true})).toBeVisible()
        await page.getByRole("button", {name: "任务动态", exact: true}).click()
        const popover = page.locator('[data-slot="popover-content"]')
        await expect(popover.getByText("finished-job", {exact: true})).toHaveCount(0)
        await expect(popover.getByText("active-job", {exact: true})).toBeVisible()
        expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
        expect(errors).toEqual([])
    })

    test(`header task popover interaction at ${width}px`, async ({ page }) => {
        await page.setViewportSize({ width, height: 900 })
        await page.addInitScript(() => localStorage.setItem("token", "e2e-access-token"))
        const errors: string[] = []
        page.on("pageerror", error => errors.push(error.message))
        const task = { id: 18, kind: "fetch", state: "running", target_label: "header-smoke", attempts: 1, max_retries: 3, due_at: 1789705000, created_at: 1789704900, updated_at: 1789705000, error_code: null, result: {}, target: { tracker_name: "header-smoke" }, attempt_history: [], triggers: [{ trigger_mode: "webhook", created_at: 1789704900 }] }
        await page.route(url => new URL(url).pathname.startsWith("/api/"), async route => {
            const path = new URL(route.request().url()).pathname
            let data: unknown = {}
            if (path === "/api/auth/me") data = { id: 1, username: "e2e-admin", is_admin: true }
            if (path === "/api/tasks") data = [task]
            if (path === "/api/trackers") data = { items: [], total: 0 }
            if (path === "/api/releases/latest") data = []
            if (path === "/api/stats") data = { total_trackers: 0, total_releases: 0, recent_releases: 0, latest_update: null }
            await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(data) })
        })
        await page.goto("/")
        const bellBtn = page.getByRole("button", { name: /^(任务动态|Task notifications)$/ })
        await expect(bellBtn).toBeVisible()
        await bellBtn.click()
        await expect(page.getByText("header-smoke")).toBeVisible()
        await page.getByRole("link", { name: /^(查看全部任务|View all tasks)/ }).click()
        await expect(page).toHaveURL(/\/tasks$/)
        expect(errors).toEqual([])
    })

    test(`task queue receipt and cancellation at ${width}px`, async ({ page }) => {
        await page.setViewportSize({ width, height: 900 })
        await page.addInitScript(() => localStorage.setItem("token", "e2e-access-token"))
        const errors: string[] = []
        page.on("pageerror", error => errors.push(error.message))
        const task = { id: 17, kind: "fetch", state: "retry_wait", target_label: "queue-smoke", attempts: 1, max_retries: 3, due_at: 1789705000, created_at: 1789704900, updated_at: 1789705000, error_code: "upstream_timeout", result: { source_fetch_run_ids: { "2": 88 } }, target: { tracker_name: "queue-smoke" }, attempt_history: [], triggers: [{ trigger_mode: "webhook", created_at: 1789704900 }] }
        let cancelled = false
        await page.route(url => new URL(url).pathname.startsWith("/api/"), async route => {
            const path = new URL(route.request().url()).pathname
            let data: unknown = {}
            if (path === "/api/auth/me") data = { id: 1, username: "e2e-admin", is_admin: true }
            if (path === "/api/tasks") data = [task]
            if (path === "/api/tasks/17") data = task
            if (path === "/api/tasks/17/cancel") { task.state = "cancelled"; cancelled = true; data = { status: "cancelled" } }
            await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(data) })
        })
        await page.goto("/tasks")
        await expect(page.getByText("queue-smoke")).toBeVisible()
        await page.getByRole("button", { name: /17/ }).click()
        await expect(page.getByText(/88/)).toBeVisible()
        await page.getByRole("button", { name: /^(Cancel|取消)$/ }).click()
        await expect.poll(() => cancelled).toBe(true)
        await expect(page.getByRole("button", { name: /^(Cancel|取消)$/ })).toHaveCount(0)
        expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
        expect(errors).toEqual([])
    })
}
