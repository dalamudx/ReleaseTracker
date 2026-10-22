import { expect, test } from "@playwright/test"

for (const width of [390, 1280]) {
    test(`three task categories and processing default at ${width}px`, async ({ page }, testInfo) => {
        await page.setViewportSize({ width, height: 900 })
        await page.addInitScript(() => { localStorage.setItem("language", "zh"); document.cookie = "releasetracker-csrf=fixture; path=/" })
        const states = ["queued", "awaiting_approval", "running", "retry_wait", "succeeded", "no_change", "skipped", "cancelled", "superseded", "failed", "needs_attention"]
        const base = { kind: "fetch", attempts: 0, max_retries: 3, due_at: 1789705000, created_at: 1789704900, updated_at: 1789705000, error_code: null, result: {}, target: {} }
        const rows = states.map((state, i) => ({ ...base, id: 100 + i, state, target_label: `sample-${state}` }))
        const groups: Record<string, string[]> = { processing: states.slice(0, 4), completed: states.slice(4, 9), failed: states.slice(9) }
        const requests: string[] = []
        await page.route(url => url.pathname.startsWith("/api/"), async route => {
            const url = new URL(route.request().url())
            let data: unknown = []
            const headers: Record<string, string> = {}
            if (url.pathname === "/api/auth/me") data = { id: 1, username: "fixture", is_admin: true }
            if (url.pathname === "/api/tasks") {
                const category = url.searchParams.get("category")
                if (category) requests.push(category)
                const filtered = rows.filter(row => !category || groups[category].includes(row.state))
                const offset = Number(url.searchParams.get("offset") ?? 0)
                data = filtered.slice(offset, offset + Number(url.searchParams.get("limit") ?? 50))
                headers["X-Total-Count"] = String(filtered.length)
            }
            await route.fulfill({ contentType: "application/json", headers, body: JSON.stringify(data) })
        })
        await page.goto("/tasks")
        const filter = page.getByRole("combobox", { name: "筛选任务", exact: true })
        await expect(filter).toHaveText("处理中")
        await expect(page.getByText("sample-awaiting_approval", { exact: true })).toBeVisible()
        await expect(page.getByText("sample-failed", { exact: true })).toHaveCount(0)
        await filter.click()
        await expect(page.getByRole("option")).toHaveText(["全部状态", "处理中", "已完成", "失败"])
        await page.getByRole("option", { name: "失败", exact: true }).click()
        await expect(page.getByText("sample-needs_attention", { exact: true })).toBeVisible()
        await expect(page.getByText("sample-queued", { exact: true })).toHaveCount(0)
        await filter.click()
        await page.getByRole("option", { name: "已完成", exact: true }).click()
        await expect(page.getByText("sample-cancelled", { exact: true })).toBeVisible()
        await expect(page.getByText("sample-superseded", { exact: true })).toBeVisible()
        expect(requests).toEqual(expect.arrayContaining(["processing", "failed", "completed"]))
        await filter.click()
        const allRequest = page.waitForRequest(request => {
            const url = new URL(request.url())
            return url.pathname === "/api/tasks" && url.searchParams.get("include_total") === "true" && !url.searchParams.has("category")
        })
        await page.getByRole("option", { name: "全部状态", exact: true }).click()
        await allRequest
        await expect(page.getByText("sample-queued", { exact: true })).toBeVisible()
        await expect(page.getByText("sample-succeeded", { exact: true })).toBeVisible()
        await expect(page.getByText("sample-failed", { exact: true })).toBeVisible()
        await expect(page.getByTestId("task-summary")).toHaveCount(states.length)
        expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
        await page.screenshot({ path: testInfo.outputPath("task-categories.png") })
        await page.reload()
        await expect(filter).toHaveText("处理中")
    })
}

for (const language of ["zh", "en"]) for (const width of [320, 1280]) {
    test(`backup import and retry ${language} at ${width}px`, async ({ page }, testInfo) => {
        await page.setViewportSize({ width, height: 900 })
        await page.addInitScript(language => { localStorage.setItem("language", language); document.cookie = "releasetracker-csrf=fixture; path=/" }, language)
        const name = "releasetracker-1791000000000000000-deadbeef.zip"
        const items: Array<{ name: string; size: number; created_at: number }> = []
        let uploads = 0, restores = 0
        const errors: string[] = []
        page.on("pageerror", error => errors.push(error.message))
        await page.route(url => url.pathname.startsWith("/api/"), async route => {
            const req = route.request(), path = new URL(req.url()).pathname
            const send = (data: unknown, status = 200) => route.fulfill({ status, contentType: "application/json", body: JSON.stringify(data) })
            if (path === "/api/auth/me") return send({ id: 1, username: "fixture", is_admin: true })
            if (path === "/api/settings/security-keys") return send({ jwt_secret: { configured: true, fingerprint: "fixture", active_sessions: 1 }, encryption_key: { configured: true, fingerprint: "fixture", undecryptable_count: 0, inventory: { credentials_token: 0, credentials_secrets: 0, oauth_provider_client_secret: 0, runtime_connection_secrets: 0 } } })
            if (path === "/api/backups") return send({ items, interval_hours: 0, retention: 7, running: false, online_restore_available: true })
            if (path === "/api/backups/import") {
                expect(req.headers()["content-type"]).toBe("application/zip")
                expect(req.postDataBuffer()?.toString()).toBe("isolated-upload")
                uploads++
                if (uploads === 1) return send({ detail: "backup_import_invalid" }, 400)
                const item = { name, size: 15, created_at: 1791000000 }
                items.push(item)
                return send(item, 201)
            }
            if (path.endsWith("/restore-plan")) return send({ id: "plan", name, fingerprint: "fixture", expires_at: Date.now() / 1000 + 600, created_at: 1791000000, app_version: "1.1.16", mutation_performed: false })
            if (path.endsWith("/restore")) restores++
            return send([])
        })
        await page.goto("/settings")
        await page.getByRole("main").getByRole("navigation").getByRole("button", { name: language === "zh" ? "实例备份" : "Instance backups", exact: true }).click()
        const label = language === "zh" ? "在线导入" : "Online import"
        const fileLabel = language === "zh" ? "备份 ZIP 文件" : "Backup ZIP file"
        const upload = page.getByRole("button", { name: label, exact: true })
        await expect(upload).toBeDisabled()
        await page.getByLabel(fileLabel, { exact: true }).setInputFiles({ name: "local.zip", mimeType: "application/zip", buffer: Buffer.from("isolated-upload") })
        await upload.click()
        await expect(page.getByRole("alert")).toContainText(language === "zh" ? "归档损坏" : "damaged")
        await upload.click()
        await expect(page.getByText(name, { exact: true })).toBeVisible()
        await expect(upload).toBeDisabled()
        expect(uploads).toBe(2)
        expect(restores).toBe(0)
        const restoreLabel = language === "zh" ? "在线恢复" : "Restore online"
        await expect(page.getByRole("button", { name: `${restoreLabel} ${name}`, exact: true })).toBeEnabled()
        expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
        expect(errors).toEqual([])
        await page.getByLabel(fileLabel, { exact: true }).scrollIntoViewIfNeeded()
        await page.screenshot({ path: testInfo.outputPath("backup-import.png") })
    })
}
