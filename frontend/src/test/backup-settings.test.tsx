import { beforeEach, describe, expect, it, vi } from "vitest"
import { fireEvent, render, screen, waitFor } from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { toast } from "sonner"
import { BackupSettings } from "@/components/settings/BackupSettings"
import { backupApi } from "@/api/backups"
import i18n from "@/i18n/config"

vi.mock("@/api/backups", () => ({ backupApi: { list: vi.fn(), create: vi.fn(), download: vi.fn() } }))
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }))

function view() {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
    return render(<QueryClientProvider client={client}><BackupSettings /></QueryClientProvider>)
}
beforeEach(async () => {
    vi.resetAllMocks()
    await i18n.changeLanguage("zh")
    vi.mocked(backupApi.list).mockResolvedValue({ items: [], interval_hours: 0, retention: 7, running: false })
})

describe("instance backup controls", () => {
    it("requires confirmation before creating sensitive archive", async () => {
        vi.mocked(backupApi.create).mockResolvedValue({ name: "backup.zip", size: 12, created_at: 100 })
        view()
        await screen.findByText("自动备份未启用")
        fireEvent.click(screen.getByRole("button", { name: "创建备份" }))
        expect(backupApi.create).not.toHaveBeenCalled()
        expect(screen.getByRole("alertdialog")).toHaveTextContent("ZIP 本身不加密")
        fireEvent.click(screen.getByRole("button", { name: "确认并创建" }))
        await waitFor(() => expect(backupApi.create).toHaveBeenCalledOnce())
        await waitFor(() => expect(toast.success).toHaveBeenCalled())
        expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument()
    })
    it("keeps confirmation open on failure and allows retry", async () => {
        vi.mocked(backupApi.create).mockRejectedValue(new Error("disk full"))
        view()
        await screen.findByText("自动备份未启用")
        fireEvent.click(screen.getByRole("button", { name: "创建备份" }))
        fireEvent.click(screen.getByRole("button", { name: "确认并创建" }))
        await waitFor(() => expect(toast.error).toHaveBeenCalled())
        expect(screen.getByRole("alertdialog")).toBeInTheDocument()
        expect(screen.getByRole("button", { name: "确认并创建" })).toBeEnabled()
    })
    it("shows fetch errors without a misleading empty state", async () => {
        vi.mocked(backupApi.list).mockRejectedValue(new Error("offline"))
        view()
        await screen.findByRole("alert")
        expect(screen.getByRole("button", { name: "创建备份" })).toBeDisabled()
        expect(screen.queryByText("暂无备份，建议在升级或轮换密钥前创建。")).not.toBeInTheDocument()
    })
    it("uses the authenticated download API and reports failure", async () => {
        vi.mocked(backupApi.list).mockResolvedValue({ items: [{ name: "test.zip", size: 1000, created_at: 100 }], interval_hours: 24, retention: 7, running: false })
        vi.mocked(backupApi.download).mockRejectedValue(new Error("expired"))
        view()
        const download = await screen.findByRole("button", { name: "下载 test.zip" })
        fireEvent.click(download)
        await waitFor(() => expect(backupApi.download).toHaveBeenCalledWith("test.zip"))
        await waitFor(() => expect(toast.error).toHaveBeenCalled())
        expect(screen.getByText("test.zip")).toBeInTheDocument()
    })
})
