import { expect, test } from "@playwright/test"

for (const language of ["zh", "en"]) for (const width of [320, 390, 1280]) {
    test(`selection problem tags ${language} ${width}px`, async ({ page }, info) => {
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
        const items = Array.from({ length: 25 }, (_, i) => ({ tag: `${i ? 1200 + i : "12".repeat(100)}-r2`, reason: "invalid_numeric_revision", family: null, fields: { version: String(1200 + i), revision: "r2" } }))
        const conflictTags = [
            { tag: "0.7.18", family: "version" }, { tag: "0.7.19", family: "version" },
            { tag: "0.5.4-fix1", family: "variant:fix1" }, { tag: "0.5.6-fix1", family: "variant:fix1" },
            { tag: "0.5.4-fix2", family: "variant:fix2" }, { tag: "0.5.6-fix2", family: "variant:fix2" },
            { tag: "0.5.4-fix3", family: "variant:fix3" },
        ]
        const familyGroups = Array.from(new Set(conflictTags.map(row => row.family))).map(family => ({ family, count: conflictTags.filter(row => row.family === family).length, examples: conflictTags.filter(row => row.family === family).slice(0, 3).map(row => row.tag) }))
        let diagnosticRequests = 0
        let familyConflictOnly = false
        let ambiguousFamilies = false
        await page.route(url => url.pathname.startsWith("/api/"), async route => {
            const url = new URL(route.request().url())
            let data: unknown = []
            if (url.pathname === "/api/auth/me") data = { id: 1, username: "fixture", is_admin: true }
            else if (url.pathname.endsWith("/selection-diagnostics")) {
                diagnosticRequests++
                const query = (url.searchParams.get("search") || "").toLowerCase()
                const matching = items.filter(item => item.tag.toLowerCase().includes(query))
                const offset = Number(url.searchParams.get("offset") || 0)
                data = { source_label: "image", channel_name: "stable", include_pattern: channel.include_pattern, exclude_pattern: null, candidate_count: 25, affected_count: 25, reason: "unorderable_version", ordering_basis: "extracted", conflicting_families: [], total: matching.length, items: matching.slice(offset, offset + Number(url.searchParams.get("limit") || 10)) }
                if (familyConflictOnly) {
                    const problems = conflictTags.filter(row => row.family !== "version")
                    const matching = problems.filter(row => row.tag.includes(query))
                    data = { source_label: "image", channel_name: "stable", include_pattern: null, exclude_pattern: null, candidate_count: 30, affected_count: problems.length, reason: "incomparable_versions", ordering_basis: "version", conflicting_families: familyGroups, total: matching.length, items: matching.slice(offset, offset + Number(url.searchParams.get("limit") || 10)).map(row => ({ ...row, reason: "incomparable_family", fields: {} })) }
                }
                if (ambiguousFamilies) data = { source_label: "image", channel_name: "stable", include_pattern: null, exclude_pattern: null, candidate_count: 60, affected_count: 0, reason: "incomparable_versions", ordering_basis: null, conflicting_families: [{ family: "natural:build-#", count: 50, examples: ["build-1", "build-2", "build-3"] }, { family: "natural:snapshot-#", count: 10, examples: ["snapshot-1", "snapshot-2", "snapshot-3"] }], total: 0, items: [] }
            } else if (url.pathname === "/api/trackers") data = { items: [tracker], total: 1 }
            else if (url.pathname.endsWith("/current")) data = { tracker: { name: tracker.name }, status: { last_check: tracker.status.last_check, last_version: null, error: null }, latest_release: null, channel_selections: [{ tracker_source_id: 1, channel_key: "stable", channel_name: "stable", state: "empty", reason: "unorderable_version", revision: "r1" }], matrix: { columns: [], rows: [] }, projected_at: null }
            else if (/\/(releases|history)$/.test(url.pathname)) data = { items: [], total: 0 }
            else if (url.pathname === "/api/trackers/diagnostic" || url.pathname.endsWith("/config")) data = tracker
            await route.fulfill({ contentType: "application/json", body: JSON.stringify(data) })
        })
        await page.goto("/trackers?tracker=diagnostic")
        const show = page.getByRole("button", { name: language === "zh" ? "查看问题标签" : "Show problem tags" })
        await expect(show).toBeVisible()
        expect(diagnosticRequests).toBe(0)
        await show.click()
        const region = page.getByRole("dialog", { name: language === "zh" ? "标签排序诊断" : "Tag ordering diagnostics" })
        await expect(region.getByText(items[0].tag, { exact: true })).toBeVisible()
        await expect(region.getByText(language === "zh" ? /提取的 revision 不是非负整数/ : /Extracted revision is not a nonnegative integer/).first()).toBeVisible()
        expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
        expect(await region.evaluate(element => element.scrollWidth <= element.clientWidth + 1)).toBe(true)
        await region.getByRole("listitem").first().scrollIntoViewIfNeeded()
        await page.screenshot({ path: info.outputPath(`diagnostics-${language}-${width}.png`), fullPage: true })
        await region.getByRole("button", { name: language === "zh" ? /下一页/ : /next page/i }).click()
        await expect(region.getByText(items[10].tag, { exact: true })).toBeVisible()
        await region.getByRole("textbox", { name: language === "zh" ? "搜索问题标签" : "Search problem tags" }).fill("1203")
        await expect(region.getByRole("listitem")).toHaveCount(1)
        await expect(region.getByText(items[3].tag, { exact: true })).toBeVisible()
        await region.getByRole("button", { name: language === "zh" ? "关闭" : "Close" }).click()
        await expect(region).toHaveCount(0)
        await expect(show).toBeFocused()
        familyConflictOnly = true
        await show.click()
        const problems = region.getByRole("list", { name: language === "zh" ? "需要检查的标签" : "Tags to review" })
        await expect(problems.getByRole("listitem")).toHaveCount(5)
        await expect(problems.getByText("0.5.4-fix1", { exact: true })).toBeVisible()
        await expect(problems.getByText("0.7.19", { exact: true })).toHaveCount(0)
        await expect(problems.getByText("361952a", { exact: true })).toHaveCount(0)
        expect(await region.evaluate(element => element.scrollWidth <= element.clientWidth + 1)).toBe(true)
        await region.scrollIntoViewIfNeeded()
        await page.screenshot({ path: info.outputPath(`families-${language}-${width}.png`), fullPage: true })
        await expect(region.getByText(language === "zh" ? "按版本号排序" : "Version ordering", { exact: true })).toBeVisible()
        await expect(region.getByRole("combobox", { name: language === "zh" ? "诊断参照系列" : "Reference family" })).toHaveCount(0)
        await expect(region.getByText(language === "zh" ? /这些标签与数字版本系列无法比较/ : /These tags cannot be compared/)).toHaveCount(1)
        const bounds = await region.boundingBox()
        expect(bounds).not.toBeNull()
        expect(bounds!.y).toBeGreaterThanOrEqual(0)
        expect(bounds!.y + bounds!.height).toBeLessThanOrEqual(height)
        await region.press("Escape")
        await expect(region).toHaveCount(0)
        await expect(show).toBeFocused()
        ambiguousFamilies = true
        await show.click()
        await expect(region.getByText("build-1", { exact: true })).toBeVisible()
        await expect(region.getByText("snapshot-1", { exact: true })).toBeVisible()
        await expect(region.getByRole("combobox")).toHaveCount(0)
        await expect(region.getByRole("textbox")).toHaveCount(0)
        await page.screenshot({ path: info.outputPath(`ambiguous-${language}-${width}.png`), fullPage: true })
        await region.press("Escape")
        await expect(region).toHaveCount(0)
        expect(errors).toEqual([])
    })
}
