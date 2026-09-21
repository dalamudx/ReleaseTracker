import { act, renderHook } from "@testing-library/react"
import { MemoryRouter } from "react-router"
import { describe, expect, it } from "vitest"
import { useBreadcrumb } from "@/hooks/useBreadcrumb"
import { navigationItems, isNavigationActive } from "@/lib/navigation"
import i18n from "@/i18n/config"

function crumbs(path: string) {
    return renderHook(() => useBreadcrumb(), { wrapper: ({ children }) => <MemoryRouter initialEntries={[path]}>{children}</MemoryRouter> })
}

describe("shared navigation", () => {
    for (const language of ["zh", "en"]) {
        it.each(navigationItems)(`${language} sidebar and breadcrumb: $url`, async item => {
            await i18n.changeLanguage(language)
            const { result } = crumbs(item.url)
            expect(result.current).toEqual([{ label: i18n.t(item.titleKey), href: undefined }])
            expect(i18n.t(item.titleKey)).not.toBe(item.titleKey)
            expect(navigationItems.filter(nav => isNavigationActive(item.url, nav.url))).toEqual([item])
        })
    }
    it("updates the current title when language changes and resolves the legacy Webhook route", async () => {
        await i18n.changeLanguage("zh")
        const { result } = crumbs("/notifications")
        expect(result.current[0].label).toBe(i18n.t("sidebar.webhooks"))
        await act(async () => { await i18n.changeLanguage("en") })
        expect(result.current[0].label).toBe(i18n.t("sidebar.webhooks"))
        expect(isNavigationActive("/notifications", "/webhooks")).toBe(true)
    })
    it("matches nested paths, not similarly named routes", () => {
        expect(isNavigationActive("/tasks/17", "/tasks")).toBe(true)
        expect(isNavigationActive("/tasks-other", "/tasks")).toBe(false)
        expect(isNavigationActive("/tasks", "/")).toBe(false)
    })
})
