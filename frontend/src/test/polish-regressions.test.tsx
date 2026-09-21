import { fireEvent, render, screen, waitFor } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

import type { TrackerStatus } from "@/api/types"
import { QueryErrorState } from "@/components/common/QueryErrorState"
import { TrackerList } from "@/components/trackers/TrackerList"
import { TooltipProvider } from "@/components/ui/tooltip"
import i18n from "@/i18n/config"
import { ThemeProvider } from "@/providers/theme-provider"

const tracker: TrackerStatus = {
    name: "qa-tracker",
    enabled: true,
    description: "Sanitized tracker fixture",
    primary_changelog_source_key: null,
    sources: [],
    interval: 60,
    version_sort_mode: "published_at",
    fetch_limit: 20,
    fetch_timeout: 15,
    fallback_tags: false,
    status: {
        last_check: null,
        last_version: "1.2.3",
        error: null,
        source_count: 0,
        enabled_source_count: 0,
        source_types: [],
    },
}

describe("frontend polish regressions", () => {
    beforeEach(async () => {
        await i18n.changeLanguage("zh")
        document.documentElement.className = ""
        document.documentElement.removeAttribute("data-theme")
        document.documentElement.removeAttribute("data-scale")
        localStorage.clear()
        vi.stubGlobal("matchMedia", vi.fn().mockReturnValue({
            matches: false,
            addEventListener: vi.fn(),
            removeEventListener: vi.fn(),
        }))
    })

    afterEach(() => {
        vi.unstubAllGlobals()
    })

    it("offers an explicit retry for load failures", () => {
        const onRetry = vi.fn()
        render(<QueryErrorState onRetry={onRetry} />)

        expect(screen.getByRole("alert")).toHaveTextContent(i18n.t("common.loadFailedTitle"))
        fireEvent.click(screen.getByRole("button", { name: i18n.t("common.retry") }))
        expect(onRetry).toHaveBeenCalledOnce()
    })

    it("falls back safely when persisted theme configuration is malformed", async () => {
        localStorage.setItem("polish-theme-config", "{broken")
        render(
            <ThemeProvider storageKey="polish-theme" defaultTheme="light">
                <div>ready</div>
            </ThemeProvider>,
        )

        await waitFor(() => expect(document.documentElement).toHaveClass("light"))
        expect(screen.getByText("ready")).toBeVisible()
    })

    it("keeps the document language synchronized", async () => {
        await i18n.changeLanguage("en")
        expect(document.documentElement).toHaveAttribute("lang", "en")
        await i18n.changeLanguage("zh")
        expect(document.documentElement).toHaveAttribute("lang", "zh-CN")
    })

    it("selects tracker rows with Enter and Space without opening another action", () => {
        const onSelect = vi.fn()
        render(
            <TooltipProvider>
                <TrackerList
                    trackers={[tracker]}
                    loading={false}
                    selectedTrackerName={null}
                    onSelect={onSelect}
                    onEdit={vi.fn()}
                    onDelete={vi.fn()}
                    onCheck={vi.fn()}
                />
            </TooltipProvider>,
        )

        const row = screen.getByText("qa-tracker").closest('[role="button"]')
        expect(row).toHaveAttribute("tabindex", "0")
        fireEvent.keyDown(row!, { key: "Enter" })
        fireEvent.keyDown(row!, { key: " " })
        expect(onSelect).toHaveBeenNthCalledWith(1, "qa-tracker")
        expect(onSelect).toHaveBeenNthCalledWith(2, "qa-tracker")
    })
})
