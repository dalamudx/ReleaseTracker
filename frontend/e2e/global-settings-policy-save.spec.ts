import { expect, test } from "@playwright/test"

// Backup retention policies share one server-side operation lock, so the form
// must never send two system.backup_* updates at the same time.
const BACKUP_KEYS = new Set(["system.backup_retention", "system.backup_interval_hours"])

const defaults = {
    "system.mutation_concurrency": "1",
    "system.worker_poll_seconds": "2",
    "system.readiness_pull_grace_seconds": "1800",
    "system.runtime_health_interval_seconds": "0",
    "system.backup_retention": "7",
    "system.backup_interval_hours": "0",
    "system.online_restore": "true",
    "system.pre_migration_backup": "true",
    "system.cors_origins": "",
    "system.forwarded_allow_ips": "127.0.0.1",
}

test("saves policy changes one at a time and reports invalid ranges", async ({ page }) => {
    await page.setViewportSize({ width: 1280, height: 1000 })
    await page.addInitScript(() => localStorage.setItem("language", "en"))

    const writes: { key: string; value: unknown }[] = []
    const errors: string[] = []
    let savedToken = ""
    let backupInFlight = 0
    let overlappingBackupWrites = false
    let resumeSave!: () => void
    const saveGate = new Promise<void>(resolve => { resumeSave = resolve })

    page.on("pageerror", error => errors.push(error.message))

    await page.route(url => url.pathname.startsWith("/api/"), async route => {
        const request = route.request()
        const path = new URL(request.url()).pathname
        const send = (data: unknown, status = 200) =>
            route.fulfill({ status, contentType: "application/json", body: JSON.stringify(data) })

        if (path === "/api/auth/browser/login") {
            return route.fulfill({
                status: 200,
                contentType: "application/json",
                headers: { "set-cookie": "releasetracker-csrf=fixture-csrf; Path=/; SameSite=Lax" },
                body: JSON.stringify({ user: { id: 1, username: "fixture-admin", is_admin: true } }),
            })
        }
        if (path === "/api/auth/me") return send({ id: 1, username: "fixture-admin", is_admin: true })
        if (path === "/api/settings/metrics-token") {
            if (request.method() === "PUT") {
                savedToken = request.postDataJSON().token
                return send({ configured: Boolean(savedToken) })
            }
            return send({ configured: Boolean(savedToken), token: savedToken || undefined })
        }
        if (path === "/api/settings/security-keys") {
            return send({
                jwt_secret: { configured: true, fingerprint: "fixture", active_sessions: 1 },
                encryption_key: {
                    configured: true,
                    fingerprint: "fixture",
                    undecryptable_count: 0,
                    inventory: {
                        credentials_token: 0,
                        credentials_secrets: 0,
                        oauth_provider_client_secret: 0,
                        runtime_connection_secrets: 0,
                    },
                },
            })
        }
        if (path === "/api/settings" && request.method() === "GET") {
            return send(Object.entries(defaults).map(([key, value]) => ({ key, value, updated_at: null })))
        }
        if (path === "/api/settings" && request.method() === "POST") {
            const body = request.postDataJSON() as { key: string; value: unknown }
            writes.push(body)
            if (BACKUP_KEYS.has(body.key)) {
                backupInFlight += 1
                if (backupInFlight > 1) overlappingBackupWrites = true
                // Holding the response makes any concurrent backup write observable.
                await saveGate
                await new Promise(resolve => setTimeout(resolve, 150))
                backupInFlight -= 1
            }
            return send({ ...body, updated_at: "2026-01-01T00:00:00" })
        }
        if (path === "/api/backups") {
            return send({ items: [], retention: 7, interval_hours: 0, total_size: 0, running: false, minimum_local_archives: 1 })
        }
        if (path === "/api/oidc-providers" || path === "/api/auth/oidc/providers" || path === "/api/tasks" || path === "/api/oidc-providers/admin-binding") {
            return send(path === "/api/tasks" ? [] : path === "/api/oidc-providers/admin-binding" ? { binding: null } : [])
        }
        return send({ items: [], total: 0 })
    })

    await page.goto("/settings")
    await expect(page).toHaveURL(/\/login$/)
    await page.locator("#username").fill("fixture-admin")
    await page.locator("#password").fill("fixture-password")
    await page.locator('button[type="submit"]').click()
    await expect(page).toHaveURL(/\/settings$/)

    await expect(page.locator("#system-mutation-concurrency")).toHaveValue("1")
    await expect(page.locator("#system-backup-retention")).toHaveValue("7")

    await page.setViewportSize({ width: 320, height: 900 })
    const range = page.getByLabel("IP range", { exact: true })
    await range.fill("198.18.0.0/24")
    await range.press("Enter")
    const chip = page.getByRole("listitem").filter({ has: page.getByRole("button", { name: "Delete 198.18.0.0/24" }) })
    expect((await chip.boundingBox())!.width).toBeLessThan((await chip.locator("..").boundingBox())!.width)
    await chip.getByRole("button").click()
    await expect(chip).toHaveCount(0)
    const origin = page.getByLabel("Origin", { exact: true })
    const longOrigin = `https://${"a".repeat(60)}.example.com`
    await origin.fill(longOrigin)
    await origin.press("Enter")
    const remove = page.getByRole("button", { name: `Delete ${longOrigin}` })
    const removeBounds = (await remove.boundingBox())!
    expect(removeBounds.x + removeBounds.width).toBeLessThanOrEqual(320)
    await page.setViewportSize({ width: 1280, height: 1000 })
    await page.locator("#system-mutation-concurrency").fill("2")
    await page.locator("#system-worker-poll-seconds").fill("5")
    await page.locator("#system-backup-retention").fill("3")
    await page.locator("#system-backup-interval").fill("2")
    await page.locator("#system-pre-migration-backup").click()
    await expect(page.locator("#system-pre-migration-backup")).toHaveAttribute("aria-checked", "false")

    const save = page.getByRole("button", { name: "Save", exact: true })
    for (const width of [320, 1280]) {
        await page.setViewportSize({ width, height: 1000 })
        const bounds = await save.evaluate(button => {
            const parent = document.getElementById("section-storage-retention")!.getBoundingClientRect()
            const panel = button.parentElement!
            const rect = panel.getBoundingClientRect()
            return {
                width: rect.width, available: parent.width, rightGap: parent.right - rect.right,
                inset: rect.right - button.getBoundingClientRect().right,
                background: getComputedStyle(panel).backgroundColor,
            }
        })
        expect(bounds.width).toBeLessThan(bounds.available / 2)
        expect(bounds.rightGap).toBeCloseTo(0, 0)
        expect(bounds.inset).toBeGreaterThan(0)
        expect(bounds.background).not.toBe("rgba(0, 0, 0, 0)")
    }
    await save.click()
    try {
        await expect(page.locator("#system-worker-poll-seconds")).toBeDisabled()
        await expect(page.locator("#system-pre-migration-backup")).toBeDisabled()
    } finally {
        resumeSave()
    }
    await expect(page.getByText("Saved successfully", { exact: true })).toBeVisible()
    await expect(page.locator("#system-worker-poll-seconds")).toBeEnabled()

    expect(writes).toEqual(
        expect.arrayContaining([
            { key: "system.mutation_concurrency", value: "2" },
            { key: "system.worker_poll_seconds", value: "5" },
            { key: "system.backup_retention", value: "3" },
            { key: "system.backup_interval_hours", value: "2" },
            { key: "system.cors_origins", value: longOrigin },
            { key: "system.pre_migration_backup", value: "false" },
        ]),
    )
    expect(overlappingBackupWrites).toBe(false)

    const revoke = page.getByRole("button", { name: "Revoke token", exact: true })
    const tokenField = page.getByLabel("Token", { exact: true })
    await page.getByRole("button", { name: "Generate token" }).click()
    await expect(revoke).toHaveCount(0)
    const generated = await tokenField.inputValue()
    expect(generated).toMatch(/^[A-Za-z0-9_-]{16}$/)
    await save.click()
    await expect(revoke).toBeVisible()
    await page.reload()
    await expect(tokenField).toHaveValue(generated)
    for (const width of [320, 1280]) {
        await page.setViewportSize({ width, height: 1000 })
        const generate = await page.getByRole("button", { name: "Generate token" }).boundingBox()
        const remove = await revoke.boundingBox()
        const token = await tokenField.boundingBox()
        expect(remove!.y).toBeCloseTo(generate!.y, 0)
        expect(remove!.x).toBeGreaterThanOrEqual(generate!.x + generate!.width)
        expect(remove!.x + remove!.width).toBeLessThanOrEqual(width)
        expect(token!.y).toBeGreaterThanOrEqual(remove!.y + remove!.height)
    }
    await revoke.click()
    await save.click()
    await expect(revoke).toHaveCount(0)
    await expect(tokenField).toHaveCount(0)
    // Out-of-range input is rejected before any request is made.
    const before = writes.length
    await page.locator("#system-runtime-health-interval").fill("120")
    await expect(page.getByRole("alert").filter({ hasText: "300 to 86400" })).toBeVisible()
    await page.getByRole("button", { name: "Save", exact: true }).click()
    await expect(page.getByText("Check the highlighted policy values.", { exact: true })).toBeVisible()
    expect(writes.length).toBe(before)
    expect(errors).toEqual([])
})
