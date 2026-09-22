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

    test(`review and approve a deployment plan at ${width}px`, async ({ page }) => {
        await page.setViewportSize({ width, height: 900 })
        await page.addInitScript(() => {
            localStorage.setItem("token", "e2e-access-token")
            localStorage.setItem("language", "zh")
        })
        const errors: string[] = []
        page.on("pageerror", error => errors.push(error.message))
        const task = {
            id: 42,
            kind: "deploy",
            state: "queued",
            approval_pending: true,
            target_label: "sample-api deployment",
            attempts: 0,
            max_retries: 3,
            due_at: 1789705000,
            created_at: 1789704900,
            updated_at: 1789705000,
            error_code: null,
            message: null,
            result: {},
            target: { executor_id: 7 },
            attempt_history: [],
            triggers: [{ trigger_mode: "manual", created_at: 1789704900 }],
        }
        const plan = {
            id: 77,
            task_id: 42,
            fingerprint: "sample-plan-fingerprint",
            state: "awaiting_approval",
            reason: "marker_missing",
            expires_at: 1789706800,
            summary: {
                target_label: "sample-api deployment",
                identity_key: "sample-runtime/sample-api",
                configuration_fingerprint: "sample-config-fingerprint",
                recovery_scope: "container_config",
                includes_application_data: false,
                automatic_rollback: false,
                source_count: 1,
            },
        }
        let approvalRequest: Record<string, unknown> | null = null
        await page.route(url => new URL(url).pathname.startsWith("/api/"), async route => {
            const path = new URL(route.request().url()).pathname
            let data: unknown = {}
            if (path === "/api/auth/me") data = { id: 1, username: "e2e-admin", is_admin: true }
            if (path === "/api/tasks") data = [task]
            if (path === "/api/tasks/42") data = task
            if (path === "/api/tasks/42/deployment-plan") data = plan
            if (path === "/api/tasks/42/approve") {
                approvalRequest = route.request().postDataJSON() as Record<string, unknown>
                task.approval_pending = false
                data = { task_id: 42, plan_id: 77, status: "queued" }
            }
            await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(data) })
        })
        await page.goto("/tasks")
        await expect(page.getByText("sample-api deployment", { exact: true })).toBeVisible()
        await page.getByRole("button", { name: "任务 #42 详情", exact: true }).click()
        await expect(page.getByText("需要确认部署计划", { exact: true })).toBeVisible()
        await expect(page.getByText("运行时身份：sample-runtime/sample-api", { exact: true })).toBeVisible()
        await page.getByRole("button", { name: "确认并继续部署", exact: true }).click()
        await expect.poll(() => approvalRequest).toMatchObject({
            plan_id: 77,
            fingerprint: "sample-plan-fingerprint",
            plan_reviewed: true,
        })
        await expect(page.getByText("需要确认部署计划", { exact: true })).toHaveCount(0)
        expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
        expect(errors).toEqual([])
    })

    test(`surfaces stale and ownership-conflict approval responses at ${width}px`, async ({ page }) => {
        await page.setViewportSize({ width, height: 900 })
        await page.addInitScript(() => {
            localStorage.setItem("token", "e2e-access-token")
            localStorage.setItem("language", "zh")
        })
        const tasks = [
            { id: 43, kind: "deploy", state: "queued", approval_pending: true, target_label: "conflict-target", attempts: 0, max_retries: 3, due_at: 1789705000, created_at: 1789704900, updated_at: 1789705000, error_code: null, message: null, result: {}, target: {}, attempt_history: [], triggers: [] },
            { id: 44, kind: "deploy", state: "queued", approval_pending: true, target_label: "expired-target", attempts: 0, max_retries: 3, due_at: 1789705000, created_at: 1789704900, updated_at: 1789705000, error_code: null, message: null, result: {}, target: {}, attempt_history: [], triggers: [] },
        ]
        const plans = {
            43: { id: 78, task_id: 43, fingerprint: "conflict-fingerprint", state: "awaiting_approval", reason: "marker_missing", expires_at: 1789706800, summary: { target_label: "conflict-target", identity_key: "runtime/conflict", recovery_scope: "container_config" } },
            44: { id: 79, task_id: 44, fingerprint: "expired-fingerprint", state: "awaiting_approval", reason: "marker_missing", expires_at: 1789706800, summary: { target_label: "expired-target", identity_key: "runtime/expired", recovery_scope: "container_config" } },
        }
        const errors: string[] = []
        page.on("pageerror", error => errors.push(error.message))
        await page.route(url => new URL(url).pathname.startsWith("/api/"), async route => {
            const path = new URL(route.request().url()).pathname
            let data: unknown = {}
            let status = 200
            if (path === "/api/auth/me") data = { id: 1, username: "e2e-admin", is_admin: true }
            if (path === "/api/tasks") data = tasks
            if (path === "/api/tasks/43") data = tasks[0]
            if (path === "/api/tasks/44") data = tasks[1]
            if (path === "/api/tasks/43/deployment-plan") data = plans[43]
            if (path === "/api/tasks/44/deployment-plan") data = plans[44]
            if (path === "/api/tasks/43/approve") { status = 409; data = { detail: "target_already_owned" } }
            if (path === "/api/tasks/44/approve") { status = 409; data = { detail: "approval_stale_or_blocked" } }
            await route.fulfill({ status, contentType: "application/json", body: JSON.stringify(data) })
        })
        await page.goto("/tasks")
        await page.getByRole("button", { name: "任务 #43 详情", exact: true }).click()
        await page.getByRole("button", { name: "确认并继续部署", exact: true }).click()
        await expect(page.getByRole("alert").filter({ hasText: "目标已被其他配置占用，无法确认此计划。" })).toBeVisible()
        await page.getByRole("button", { name: "任务 #43 详情", exact: true }).click()
        await page.getByRole("button", { name: "任务 #44 详情", exact: true }).click()
        await page.getByRole("button", { name: "确认并继续部署", exact: true }).click()
        await expect(page.getByRole("alert").filter({ hasText: "该计划已失效，请刷新计划后重新确认。" })).toBeVisible()
        expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
        expect(errors).toEqual([])
    })
}
