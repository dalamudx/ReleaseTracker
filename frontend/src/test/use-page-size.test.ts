import { act, renderHook } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it } from "vitest"
import { SHARED_PAGE_SIZE_KEY, usePageSize } from "@/hooks/use-page-size"

describe("usePageSize shared localStorage pagination", () => {
    beforeEach(() => {
        localStorage.clear()
    })

    afterEach(() => {
        localStorage.clear()
    })

    it("defaults to 15 when no key is in localStorage", () => {
        const { result } = renderHook(() => usePageSize())
        expect(result.current[0]).toBe(15)
    })

    it("shares a single localStorage key across different call sites", () => {
        const { result: pageA } = renderHook(() => usePageSize())
        const { result: pageB } = renderHook(() => usePageSize())

        expect(pageA.current[0]).toBe(15)
        expect(pageB.current[0]).toBe(15)

        act(() => {
            pageA.current[1](30)
        })

        expect(localStorage.getItem(SHARED_PAGE_SIZE_KEY)).toBe("30")
        expect(pageA.current[0]).toBe(30)
        expect(pageB.current[0]).toBe(30)
    })

    it("falls back to legacy page-specific key if shared key is not set", () => {
        localStorage.setItem("settings.trackers.pageSize", "20")

        const { result } = renderHook(() => usePageSize("settings.trackers.pageSize"))
        expect(result.current[0]).toBe(20)

        // Setting it writes to the shared key
        act(() => {
            result.current[1](40)
        })

        expect(localStorage.getItem(SHARED_PAGE_SIZE_KEY)).toBe("40")
    })
})
