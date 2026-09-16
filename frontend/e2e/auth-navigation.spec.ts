import { expect, test, type Page } from "@playwright/test"

type ApiRequest = {
    method: string
    path: string
    body: string | null
}

const e2eUser = {
    id: 1,
    username: "e2e-admin",
    email: "e2e@example.test",
    is_admin: true,
}

async function installApiFixture(page: Page, latestReleases: unknown[] = []) {
    const requests: ApiRequest[] = []

    await page.route(requestUrl => new URL(requestUrl).pathname.startsWith("/api/"), async route => {
        const request = route.request()
        const url = new URL(request.url())
        const path = url.pathname
        requests.push({
            method: request.method(),
            path,
            body: request.postData(),
        })

        const fulfill = (json: unknown) => route.fulfill({
            status: 200,
            contentType: "application/json",
            body: JSON.stringify(json),
        })

        if (path === "/api/auth/login") {
            return fulfill({
                user: e2eUser,
                token: {
                    access_token: "e2e-access-token",
                    refresh_token: "e2e-refresh-token",
                },
            })
        }

        if (path === "/api/auth/me") {
            return fulfill(e2eUser)
        }

        if (path === "/api/auth/oidc/config") {
            return fulfill({ enabled: false })
        }

        if (path === "/api/trackers") {
            return fulfill({ items: [], total: 0 })
        }

        if (path === "/api/releases/latest") {
            return fulfill(latestReleases)
        }

        return fulfill({})
    })

    return requests
}

test("renders lazy release notes from the production bundle", async ({ page }) => {
    const pageErrors: string[] = []
    page.on("pageerror", error => pageErrors.push(error.message))
    await page.addInitScript(() => {
        localStorage.setItem("token", "e2e-access-token")
    })
    await installApiFixture(page, [{
        tracker_release_history_id: 101,
        identity_key: "release:1.2.3",
        digest: "sha256:e2e",
        tracker_name: "production-markdown",
        tracker_type: "gitea",
        name: "Release 1.2.3",
        tag_name: "v1.2.3",
        version: "1.2.3",
        published_at: "2026-09-16T12:00:00Z",
        projected_at: "2026-09-16T12:00:01Z",
        url: "https://git.example.test/acme/project/releases/tag/v1.2.3",
        prerelease: false,
        body: "## Production Markdown\n\n- lazy chunk loaded\n- **rendered successfully**",
        primary_source: {
            source_key: "repo",
            source_type: "gitea",
            source_release_history_id: 202,
        },
    }])

    await page.goto("/")
    await page.getByRole("button", { name: /View Release Notes|查看发布说明/ }).click()

    await expect(page.getByRole("dialog")).toBeVisible()
    await expect(page.getByRole("heading", { name: "Production Markdown" })).toBeVisible()
    await expect(page.getByText("rendered successfully")).toBeVisible()
    expect(pageErrors).toEqual([])
})

test("redirects anonymous tracker visits to the login form", async ({ page }) => {
    await installApiFixture(page)

    await page.goto("/trackers")

    await expect(page).toHaveURL(/\/login$/)
    await expect(page.locator("#username")).toBeVisible()
    await expect(page.locator("#password")).toBeVisible()
})

test("logs in through the form and restores the protected tracker route", async ({ page }) => {
    const requests = await installApiFixture(page)

    await page.goto("/trackers")
    await expect(page).toHaveURL(/\/login$/)

    await page.locator("#username").fill(e2eUser.username)
    await page.locator("#password").fill("e2e-password")
    await page.locator('button[type="submit"]').click()

    await expect(page).toHaveURL(/\/trackers$/)
    await expect(page.locator("#username")).toHaveCount(0)
    await expect(page.getByRole("main").last()).toBeVisible()
    await expect.poll(() => requests.some(request => request.path === "/api/trackers")).toBe(true)

    await expect(page.evaluate(() => localStorage.getItem("token"))).resolves.toBe("e2e-access-token")

    const loginRequest = requests.find(request => request.path === "/api/auth/login")
    expect(loginRequest?.method).toBe("POST")
    expect(JSON.parse(loginRequest?.body ?? "{}"))
        .toEqual({ username: e2eUser.username, password: "e2e-password" })
})
