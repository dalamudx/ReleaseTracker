import { expect, test } from "@playwright/test"

for (const width of [1280, 390]) {
test(`creates an SSH Compose executor from analyzed services (${width}px)`, async ({ page }) => {
    await page.setViewportSize({width, height: 900})
    const errors: string[] = []
    page.on("pageerror", error => errors.push(error.message))
    await page.addInitScript(() => {
        localStorage.setItem("token", "e2e-token")
        localStorage.setItem("language", "en")
    })
    let saved: Record<string, unknown> | null = null
    const channel = {release_channel_key: "stable", name: "stable", enabled: true, type: "release", last_version: "2", digest: "sha256:" + "a".repeat(64)}
    const source = {id: 11, source_key: "image", source_type: "container", source_rank: 0, enabled: true, source_config: {image: "app", registry: "docker.io"}, release_channels: [channel]}
    await page.route(url => url.pathname.startsWith("/api/"), async route => {
        const path = new URL(route.request().url()).pathname
        const send = (body: unknown) => route.fulfill({status: 200, contentType: "application/json", body: JSON.stringify(body)})
        if (path === "/api/auth/me") return send({id: 1, username: "admin", is_admin: true})
        if (path === "/api/runtime-connections") return send({items: [{id: 1, name: "SSH host", type: "ssh", enabled: true, credential_id: 1, config: {host: "example.test", username: "deploy"}}], total: 1})
        if (path === "/api/trackers") return send({items: [{name: "image-tracker", type: "container", enabled: true, sources: [source], release_channels: [channel], status: {name: "image-tracker", type: "container", enabled: true}}], total: 1})
        if (path === "/api/executors" && route.request().method() === "POST") {
            saved = route.request().postDataJSON()
            return send({id: 1, ...saved})
        }
        if (path === "/api/executors") return send({items: [], total: 0})
        if (path === "/api/tasks") return send([])
        if (path === "/api/executors/ssh/compose/discover") return send({items: [{id: "0123456789abcdefabcd", engine: "docker", project: "app", working_dir: "/app", config_files: ["/app/compose.yml"], env_files: [], profiles: [], tool: "docker_compose", tool_choices: ["docker_compose"], write_strategy: "source", services: ["web"], warnings: []}], tools: [], warnings: [], truncated: false, read_only: true})
        if (path === "/api/executors/ssh/compose/analyze") return send({read_only: true, selected_tool: "docker_compose", tools: [], requires_tool_selection: false, services: [{service: "web", image: "app:1", expression: "app:1", source: "compose_file", write_file: "/app/compose.yml", safe_to_edit: true, warnings: []}]})
        return send({})
    })
    await page.goto("/executors")
    await page.getByRole("button", {name: "Add Executor", exact: true}).click()
    const dialog = page.getByRole("dialog")
    await dialog.getByLabel("Executor name", {exact: true}).fill("remote-web")
    await dialog.getByRole("combobox", {name: "Runtime connection", exact: true}).click()
    await page.getByRole("option", {name: "SSH host (ssh)", exact: true}).click()
    await dialog.getByRole("combobox", {name: "Remote Compose project", exact: true}).click()
    await page.getByRole("option", {name: "app · docker · /app", exact: true}).click()
    const workingDirectory = dialog.getByLabel("Remote project directory")
    await expect(workingDirectory).toHaveText("/app")
    const composeFiles = dialog.getByLabel("Compose files")
    await expect(composeFiles).toContainText("/app/compose.yml")
    await expect(dialog.locator('[data-slot="card"]')).toHaveCount(3)
    await expect(dialog.getByRole("textbox", {name: "Remote project directory"})).toHaveCount(0)
    await dialog.getByRole("combobox", {name: "Version write strategy", exact: true}).click()
    await page.getByRole("option", {name: "Separate image override file", exact: true}).click()
    await expect(dialog.getByRole("cell", {name: "web"}).first()).toBeVisible()
    expect(await dialog.evaluate(element => element.scrollWidth <= element.clientWidth + 1)).toBe(true)
    await dialog.getByRole("button", {name: "Continue", exact: true}).click()
    await dialog.getByRole("button", {name: "Add service binding", exact: true}).click()
    const combos = dialog.getByRole("combobox")
    await combos.nth(1).click()
    await page.getByRole("option", {name: "image-tracker", exact: true}).click()
    await combos.nth(3).click()
    await page.getByRole("option", {name: /stable/i}).click()
    await dialog.getByRole("button", {name: "Continue", exact: true}).click()
    await dialog.getByRole("button", {name: "Continue", exact: true}).click()
    await dialog.getByRole("button", {name: "Create Executor", exact: true}).click()
    await expect.poll(() => saved).not.toBeNull()
    expect(saved).toMatchObject({runtime_type: "ssh", runtime_connection_id: 1, target_ref: {mode: "ssh_compose", discovery_id: "0123456789abcdefabcd", working_dir: "/app", project: "app", write_strategy: "override"}, service_bindings: [{service: "web", tracker_source_id: 11, channel_name: "stable"}], health_check: {strategy: "none"}})
    expect((saved as unknown as {target_ref: Record<string, unknown>}).target_ref.services).toBeUndefined()
    expect(errors).toEqual([])
})
}
