import { expect, test } from "@playwright/test"

for (const language of ["zh", "en"]) {
    for (const width of [320, 390, 480, 640, 1280]) {
        test(`Webhook tabs fit their content ${language} ${width}`, async ({ page }) => {
            await page.setViewportSize({ width, height: 900 })
            await page.addInitScript(language => {
                document.cookie = "releasetracker-csrf=fixture; path=/"
                localStorage.setItem("language", language)
            }, language)
            const errors: string[] = []
            page.on("pageerror", error => errors.push(error.message))
            await page.route(url => url.pathname.startsWith("/api/"), route => {
                const path = new URL(route.request().url()).pathname
                const data = path === "/api/auth/me" ? { id: 1, username: "layout-fixture", is_admin: true }
                    : path === "/api/notifiers" ? { items: [], total: 123 }
                        : path === "/api/webhooks/repositories" ? []
                            : path.includes("/templates") || path.includes("/settings") ? []
                                : { items: [], total: 0 }
                return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(data) })
            })
            await page.goto("/webhooks")
            const list = page.getByRole("tablist").first()
            await expect(list.getByRole("tab")).toHaveCount(3)
            await expect(list).toContainText("123")
            const measure = () => list.evaluate(element => {
                const rect = element.getBoundingClientRect()
                const tabs = Array.from(element.querySelectorAll<HTMLElement>('[role="tab"]'))
                const bounds = tabs.map(tab => tab.getBoundingClientRect())
                return {
                    rows: new Set(bounds.map(bound => Math.round(bound.top))).size,
                    splitLabelWord: tabs.slice(0, 2).some(tab => {
                        const text = tab.querySelector("span.min-w-0")?.firstChild
                        if (!text || text.nodeType !== Node.TEXT_NODE) return false
                        const value = text.textContent ?? ""
                        return Array.from(value.matchAll(/\S+/g)).some(match => {
                            const range = document.createRange()
                            range.setStart(text, match.index!); range.setEnd(text, match.index! + match[0].length)
                            return range.getClientRects().length > 1
                        })
                    }),
                    positions: bounds.map(bound => ({ top: bound.top, left: bound.left, width: bound.width, height: bound.height })),
                    contained: bounds.every(bound => bound.left >= rect.left - 1 && bound.right <= rect.right + 1 && bound.bottom <= rect.bottom + 1),
                    clipped: tabs.some(tab => tab.scrollWidth > tab.clientWidth + 1),
                    viewportContained: rect.left >= 0 && rect.right <= window.innerWidth,
                    horizontalOverflow: document.querySelector("main")!.scrollWidth > document.querySelector("main")!.clientWidth,
                }
            })
            await page.evaluate(() => document.fonts.ready)
            if (width >= 640 || (language === "zh" && width >= 480)) {
                await expect.poll(async () => (await measure()).rows).toBe(1)
            }
            const layout = await measure()
            expect(layout.contained).toBe(true)
            expect(layout.viewportContained).toBe(true)
            expect(layout.clipped).toBe(false)
            if (language === "en") expect(layout.splitLabelWord).toBe(false)
            expect(layout.horizontalOverflow).toBe(false)
            expect(layout.rows).toBeLessThanOrEqual(2)
            if (width >= 640 || (language === "zh" && width >= 480)) expect(layout.rows).toBe(1)
            if (width < 640) {
                for (const tab of await list.getByRole("tab").all()) expect((await tab.boundingBox())!.height).toBeGreaterThanOrEqual(44)
            }
            console.log("Webhook tab layout", JSON.stringify({ language, width, ...layout }))
            await page.screenshot({ path: `/tmp/rt-webhook-tabs-${language}-${width}.png` })
            const tabs = list.getByRole("tab")
            await tabs.nth(1).click()
            await expect(tabs.nth(1)).toHaveAttribute("aria-selected", "true")
            await tabs.nth(2).click()
            await expect(tabs.nth(2)).toHaveAttribute("aria-selected", "true")
            await tabs.nth(0).focus()
            await page.keyboard.press("ArrowRight")
            await expect(tabs.nth(1)).toBeFocused()
            expect(errors).toEqual([])
        })
    }
}
