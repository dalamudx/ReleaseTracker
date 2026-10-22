import { fireEvent, render, screen, waitFor } from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { beforeEach, describe, expect, it, vi } from "vitest"
import { ChannelSelectionDiagnostics } from "@/components/trackers/ChannelSelectionDiagnostics"
import { api } from "@/api/client"
import i18n from "@/i18n/config"

vi.mock("@/api/client", () => ({ api: { getSelectionDiagnostics: vi.fn() } }))
const result = { source_label: "image", channel_name: "stable", include_pattern: "^(?P<version>[^-]+)-(?P<revision>[^-]+)$", exclude_pattern: null, candidate_count: 21, affected_count: 21, reason: "unorderable_version", ordering_basis: "extracted", conflicting_families: [], total: 21, items: [{ tag: "12-r2", reason: "invalid_numeric_revision", family: null, fields: { version: "12", revision: "r2" } }] }
function mount() {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(<QueryClientProvider client={client}><ChannelSelectionDiagnostics trackerName="diagnostic" sourceId={1} channelKey="stable" revision="v1" /></QueryClientProvider>)
    return client
}

beforeEach(async () => {
    vi.clearAllMocks()
    localStorage.clear()
    localStorage.setItem("settings.pagination.pageSize", "10")
    await i18n.changeLanguage("zh")
    vi.mocked(api.getSelectionDiagnostics).mockResolvedValue(result)
})

describe("channel selection diagnostics", () => {
    it("opens diagnostics on demand, reveals rules only when requested and closes cleanly", async () => {
        mount()
        expect(api.getSelectionDiagnostics).not.toHaveBeenCalled()
        fireEvent.click(screen.getByRole("button", { name: "查看问题标签" }))
        expect(await screen.findByText("12-r2")).toBeVisible()
        expect(screen.getByText(/提取的 revision 不是非负整数/)).toBeVisible()
        expect(screen.getByRole("dialog", { name: "标签排序诊断" })).toBeVisible()
        expect(screen.getByText(result.include_pattern)).not.toBeVisible()
        fireEvent.click(screen.getByText("查看渠道规则"))
        expect(screen.getByText(result.include_pattern)).toBeVisible()
        expect(screen.getByText("r2")).toBeVisible()
        expect(api.getSelectionDiagnostics).toHaveBeenCalledWith("diagnostic", expect.objectContaining({ tracker_source_id: 1, channel_key: "stable", limit: 10, offset: 0 }), expect.any(AbortSignal))
        fireEvent.click(screen.getByRole("button", { name: "关闭" }))
        expect(screen.queryByText("12-r2")).not.toBeInTheDocument()
    })

    it("uses shared pagination and resets the page for a debounced tag search", async () => {
        mount()
        fireEvent.click(screen.getByRole("button", { name: "查看问题标签" }))
        await screen.findByText("12-r2")
        fireEvent.click(screen.getByRole("button", { name: /下一页/ }))
        await waitFor(() => expect(api.getSelectionDiagnostics).toHaveBeenCalledWith("diagnostic", expect.objectContaining({ offset: 10, limit: 10 }), expect.any(AbortSignal)))
        fireEvent.change(screen.getByRole("textbox", { name: "搜索问题标签" }), { target: { value: "r2" } })
        await waitFor(() => expect(api.getSelectionDiagnostics).toHaveBeenLastCalledWith("diagnostic", expect.objectContaining({ offset: 0, search: "r2" }), expect.any(AbortSignal)))
    })

    it("shows an actionable error with retry", async () => {
        vi.mocked(api.getSelectionDiagnostics).mockRejectedValueOnce(new Error("offline"))
        mount()
        fireEvent.click(screen.getByRole("button", { name: "查看问题标签" }))
        expect(await screen.findByRole("alert")).toBeVisible()
        fireEvent.click(screen.getByRole("button", { name: "重试" }))
        expect(await screen.findByText("12-r2")).toBeVisible()
    })

    it("explains that no current labels block ordering when evidence changed", async () => {
        vi.mocked(api.getSelectionDiagnostics).mockResolvedValue({ ...result, total: 0, affected_count: 0, items: [] })
        mount()
        fireEvent.click(screen.getByRole("button", { name: "查看问题标签" }))
        expect(await screen.findByText(/当前没有阻碍排序的标签/)).toBeVisible()
    })
    it("summarizes ambiguous families without offering a reference selector or listing unrelated tags", async () => {
        vi.mocked(api.getSelectionDiagnostics).mockResolvedValue({ ...result, ordering_basis: null, reason: "incomparable_versions", total: 0, affected_count: 0, items: [], conflicting_families: [{ family: "natural:build-#", count: 50, examples: ["build-1", "build-2"] }, { family: "natural:snapshot-#", count: 10, examples: ["snapshot-1"] }] })
        mount()
        fireEvent.click(screen.getByRole("button", { name: "查看问题标签" }))
        expect(await screen.findByText("build-1")).toBeVisible()
        expect(screen.getByText("snapshot-1")).toBeVisible()
        expect(screen.getByRole("list", { name: "存在冲突的排序系列" })).toBeVisible()
        expect(screen.queryByRole("combobox")).not.toBeInTheDocument()
        expect(screen.queryByRole("textbox", { name: "搜索问题标签" })).not.toBeInTheDocument()
        expect(screen.queryByText("12-r2")).not.toBeInTheDocument()
    })
    it("keeps the search field mounted and hides stale rows while new results load", async () => {
        let finish: ((value: typeof result) => void) | undefined
        const pending = new Promise<typeof result>(resolve => { finish = resolve })
        vi.mocked(api.getSelectionDiagnostics).mockResolvedValueOnce(result).mockReturnValueOnce(pending)
        mount()
        fireEvent.click(screen.getByRole("button", { name: "查看问题标签" }))
        await screen.findByText("12-r2")
        const input = screen.getByRole("textbox", { name: "搜索问题标签" })
        input.focus()
        fireEvent.change(input, { target: { value: "r2" } })
        await waitFor(() => expect(api.getSelectionDiagnostics).toHaveBeenCalledTimes(2))
        expect(screen.getByRole("textbox", { name: "搜索问题标签" })).toBe(input)
        expect(input).toHaveFocus()
        expect(screen.queryByText("12-r2")).not.toBeInTheDocument()
        finish?.(result)
        expect(await screen.findByText("12-r2")).toBeVisible()
    })
})
