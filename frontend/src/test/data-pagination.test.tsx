import { render, screen } from "@testing-library/react"
import { describe, expect, it, vi } from "vitest"
import { DataPagination } from "@/components/common/DataPagination"
import i18n from "@/i18n/config"

function view(page: number, total: number, onPageChange = vi.fn()) {
    const props = { page, pageSize: 15, onPageChange, onPageSizeChange: vi.fn() }
    const result = render(<DataPagination {...props} total={total} />)
    return { ...result, onPageChange, rerenderTotal: (next: number) => result.rerender(<DataPagination {...props} total={next} />) }
}

describe("DataPagination", () => {
    it("shows the real page count and pulls an out-of-range page back after rows disappear", () => {
        const { onPageChange, rerenderTotal } = view(3, 31)
        expect(screen.getByLabelText(i18n.t("pagination.pageOf", { page: 3, total: 3 }))).toBeInTheDocument()
        expect(onPageChange).not.toHaveBeenCalled()

        rerenderTotal(30)
        expect(onPageChange).toHaveBeenCalledWith(2)
    })

    it("does not reset the page while the total is still unknown", () => {
        const { onPageChange } = view(3, 0)
        expect(onPageChange).not.toHaveBeenCalled()
    })
})
