import { expect, test } from "@playwright/test"

for (const language of ["zh", "en"]) for (const width of [320, 390, 1280]) {
    test(`channel regex notice ${language} ${width}px`, async ({ page }) => {
        const height = width === 320 ? 568 : 900
        await page.setViewportSize({ width, height })
        await page.addInitScript(language => {
            localStorage.setItem("language", language)
            localStorage.setItem("settings.pagination.pageSize", "10")
            document.cookie = "releasetracker-csrf=fixture; path=/"
        }, language)
        const errors: string[] = []
        page.on("pageerror", error => errors.push(error.message))
        const channel = { release_channel_key: "stable", name: "stable", enabled: true, type: "release", include_pattern: "^(?P<version>\\d+)-(?P<revision>.+)$" }
        const source = { id: 1, source_key: "image", source_type: "container", enabled: true, source_config: { registry: "docker.io", image: "example/app" }, release_channels: [channel] }
        const tracker = { id: 1, name: "diagnostic", enabled: true, description: "Tag ordering example", sources: [source], channels: [channel], status: { error: null, last_check: "2026-10-22T10:00:00Z", last_version: null, source_count: 1, enabled_source_count: 1 } }
        let diagnosticRequests = 0
        await page.route(url => url.pathname.startsWith("/api/"), async route => {
            const url = new URL(route.request().url())
            let data: unknown = []
            if (url.pathname === "/api/auth/me") data = { id: 1, username: "fixture", is_admin: true }
            else if (url.pathname.endsWith("/selection-diagnostics")) diagnosticRequests++
            else if (url.pathname === "/api/trackers") data = { items: [tracker], total: 1 }
            else if (url.pathname.endsWith("/current")) data = { tracker: { name: tracker.name }, status: tracker.status, latest_release: null, channel_selections: [
                { tracker_source_id: 1, channel_key: "stable", channel_name: "stable", state: "empty", reason: "unorderable_version", revision: "r1" },
                { tracker_source_id: 1, channel_key: "beta", channel_name: "beta", state: "empty", reason: "no_matching_tag", revision: "r2" },
            ], matrix: { columns: [], rows: [] }, projected_at: null }
            else if (/\/(releases|history)$/.test(url.pathname)) data = { items: [], total: 0 }
            else if (url.pathname === "/api/trackers/diagnostic" || url.pathname.endsWith("/config")) data = tracker
            await route.fulfill({ contentType: "application/json", body: JSON.stringify(data) })
        })
        await page.goto("/trackers?tracker=diagnostic")
        await expect(page.getByText(language === "zh" ? "beta 渠道的标签均未通过正则筛选。" : "No tags pass the regex filter for beta.")).toBeVisible()
        await expect(page.getByRole("button", { name: language === "zh" ? "查看问题标签" : "Show problem tags" })).toHaveCount(0)
        expect(diagnosticRequests).toBe(0)
        expect(errors).toEqual([])
    })
}
