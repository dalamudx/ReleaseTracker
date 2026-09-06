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

async function installApiFixture(page: Page) {
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
            return fulfill([])
        }

        return fulfill({})
    })

    return requests
}

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
