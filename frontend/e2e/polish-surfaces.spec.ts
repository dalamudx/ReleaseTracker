import { expect, test, type Page } from "@playwright/test"

type Surface = {
    route: string
    mode: "light" | "dark"
    width: number
    screenshot: string
}

const surfaces: Surface[] = [
    { route: "/credentials", mode: "light", width: 390, screenshot: "/tmp/rt-polish-credentials-mobile-light.png" },
    { route: "/runtime-connections", mode: "dark", width: 390, screenshot: "/tmp/rt-polish-runtime-mobile-dark.png" },
]

const credentials = [
    { id: 1, name: "example-source-token", type: "github", description: "Source repository access", created_at: "2026-09-20T10:00:00Z", runtime_connections_count: 0 },
    { id: 2, name: "example-cluster-token", type: "kubernetes_runtime", description: "Example cluster access", created_at: "2026-09-19T10:00:00Z", runtime_connections_count: 3 },
    { id: 3, name: "example-host-key", type: "ssh", description: "Example SSH host", created_at: "2026-09-18T10:00:00Z", runtime_connections_count: 1 },
]

const runtimeConnections = [
    { id: 1, name: "example-container-runtime", type: "docker", enabled: true, endpoint: "unix:///var/run/docker.sock", description: "Local example runtime", config: { endpoint: "unix:///var/run/docker.sock" }, secrets: {}, credential_id: 1, credential_name: "example-source-token" },
    { id: 2, name: "example-remote-host", type: "ssh", enabled: false, endpoint: "ssh://host.example.test:22", description: "Example remote host", config: { host: "host.example.test", port: 22 }, secrets: {}, credential_id: 3, credential_name: "example-host-key" },
]

async function prepare(page: Page, mode: "light" | "dark", width: number) {
    await page.setViewportSize({ width, height: 900 })
    await page.addInitScript(config => {
        localStorage.setItem("token", "example-token")
        localStorage.setItem("language", "en")
        localStorage.setItem("vite-ui-theme-config", JSON.stringify({ mode: config.mode, color: "blue", radius: 0.5, zoom: "default" }))
    }, { mode })
    await page.route(url => new URL(url).pathname.startsWith("/api/"), async route => {
        const path = new URL(route.request().url()).pathname
        let body: unknown = {}
        if (path === "/api/auth/me") body = { id: 1, username: "example-admin", is_admin: true }
        else if (path === "/api/tasks") body = []
        else if (path === "/api/credentials") body = { items: credentials, total: credentials.length }
        else if (path === "/api/runtime-connections") body = { items: runtimeConnections, total: runtimeConnections.length }
        else if (path === "/api/settings") body = []
        await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(body) })
    })
}

for (const surface of surfaces) {
    test(`polished ${surface.route} ${surface.mode} ${surface.width}`, async ({ page }) => {
        const errors: string[] = []
        page.on("pageerror", error => errors.push(error.message))
        await prepare(page, surface.mode, surface.width)
        await page.goto(surface.route)

        await expect(page.locator("main#main-content")).toBeVisible()
        await expect(page.locator("html")).toHaveClass(new RegExp(surface.mode))
        await expect(page.getByText(surface.route === "/credentials" ? "example-cluster-token" : "example-remote-host")).toBeVisible()
        expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
        await page.screenshot({ path: surface.screenshot, fullPage: true })
        expect(errors).toEqual([])
    })
}

test("keyboard recovery and not-found route", async ({ page }) => {
    const errors: string[] = []
    page.on("pageerror", error => errors.push(error.message))
    await prepare(page, "light", 1280)
    await page.goto("/example-missing-page")

    await expect(page.getByRole("heading", { name: "Page not found" })).toBeVisible()
    await page.keyboard.press("Tab")
    const skipLink = page.getByRole("link", { name: "Skip to main content" })
    await expect(skipLink).toBeFocused()
    await page.keyboard.press("Enter")
    await expect(page.locator("main#main-content")).toBeFocused()
    await page.screenshot({ path: "/tmp/rt-polish-not-found-desktop-light.png", fullPage: true })
    expect(errors).toEqual([])
})
