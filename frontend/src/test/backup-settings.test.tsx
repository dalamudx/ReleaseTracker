import { beforeEach, describe, expect, it, vi } from "vitest"
import { fireEvent, render, screen, waitFor } from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { toast } from "sonner"
import { BackupSettings } from "@/components/settings/BackupSettings"
import { backupApi } from "@/api/backups"
import { saveReceipt } from "@/hooks/restore-receipt"
import i18n from "@/i18n/config"

vi.mock("@/api/backups", () => ({ backupApi: { list: vi.fn(), create: vi.fn(), download: vi.fn(), delete: vi.fn(), restorePlan: vi.fn(), restore: vi.fn(), restoreStatus: vi.fn(), cancelPlan: vi.fn(), reviewRestore: vi.fn(), downloadSafety: vi.fn() } }))
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }))

function view() {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
    return render(<QueryClientProvider client={client}><BackupSettings /></QueryClientProvider>)
}
beforeEach(async () => {
    saveReceipt(null)
    sessionStorage.clear()
    vi.resetAllMocks()
    await i18n.changeLanguage("zh")
    vi.mocked(backupApi.list).mockResolvedValue({ items: [], interval_hours: 0, retention: 7, running: false })
})

describe("instance backup controls", () => {
    it("shows actual retention tiers and storage usage and requires explicit deletion", async () => {
        const first={name:"releasetracker-1-deadbeef.zip",size:1048576,created_at:1}
        const second={name:"releasetracker-2-deadbeef.zip",size:1048576,created_at:2}
        vi.mocked(backupApi.list).mockResolvedValue({items:[first,second],interval_hours:24,retention:7,daily_retention:14,weekly_retention:4,total_size:2097152,running:false})
        vi.mocked(backupApi.delete).mockResolvedValue({deleted:first.name})
        view()
        expect(await screen.findByText("额外保留最近 14 天每天的最新备份（UTC）")).toBeInTheDocument()
        expect(screen.getByText("当前 2 份归档，占用 2.0 MB")).toBeInTheDocument()
        fireEvent.click(screen.getByRole("button",{name:`删除 ${first.name}`}))
        expect(backupApi.delete).not.toHaveBeenCalled()
        expect(screen.getByRole("alertdialog")).toHaveTextContent(first.name)
        fireEvent.click(screen.getByRole("button",{name:"取消"}))
        expect(backupApi.delete).not.toHaveBeenCalled()
        fireEvent.click(screen.getByRole("button",{name:`删除 ${first.name}`}))
        vi.mocked(backupApi.list).mockResolvedValue({items:[second],interval_hours:24,retention:7,running:false})
        fireEvent.click(screen.getByRole("button",{name:"确认删除备份"}))
        await waitFor(()=>expect(backupApi.delete).toHaveBeenCalledWith(first.name,expect.anything()))
        await waitFor(()=>expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument())
        await waitFor(()=>expect(screen.queryByText(first.name)).not.toBeInTheDocument())
        expect(screen.getByRole("button",{name:`删除 ${second.name}`})).toBeDisabled()
    })
    it("keeps deletion failure visible and permits retry", async () => {
        const name="releasetracker-1-deadbeef.zip"
        vi.mocked(backupApi.list).mockResolvedValue({items:[{name,size:1,created_at:1},{name:"releasetracker-2-deadbeef.zip",size:1,created_at:2}],interval_hours:0,retention:7,running:false})
        vi.mocked(backupApi.delete).mockRejectedValue({response:{data:{detail:"backup_in_use"}}})
        view();fireEvent.click(await screen.findByRole("button",{name:`删除 ${name}`}))
        fireEvent.click(screen.getByRole("button",{name:"确认删除备份"}))
        expect(await screen.findByRole("alert")).toHaveTextContent("该备份正在下载")
        expect(screen.getByRole("alertdialog")).toBeInTheDocument()
        expect(screen.getByRole("button",{name:"确认删除备份"})).toBeEnabled()
    })
    it("refreshes a busy archive inside the confirmation instead of forcing repeated deletion", async () => {
        const first={name:"releasetracker-1-deadbeef.zip",size:1,created_at:1}
        const second={name:"releasetracker-2-deadbeef.zip",size:1,created_at:2}
        const inventory={items:[first,second],retention:7,interval_hours:0,running:false}
        vi.mocked(backupApi.list).mockResolvedValue(inventory)
        vi.mocked(backupApi.delete).mockRejectedValue({response:{data:{detail:"backup_in_use"}}})
        view();fireEvent.click(await screen.findByRole("button",{name:`删除 ${first.name}`}))
        vi.mocked(backupApi.list).mockResolvedValue({...inventory,items:[{...first,in_use:true},second]})
        fireEvent.click(screen.getByRole("button",{name:"确认删除备份"}))
        expect(await screen.findByRole("alert")).toHaveTextContent("正在下载")
        await waitFor(()=>expect(screen.getByRole("button",{name:"确认删除备份"})).toBeDisabled())
        vi.mocked(backupApi.list).mockResolvedValue(inventory)
        fireEvent.click(screen.getByRole("button",{name:"刷新列表"}))
        await waitFor(()=>expect(screen.queryByRole("alert")).not.toBeInTheDocument())
        expect(screen.getByRole("button",{name:"确认删除备份"})).toBeEnabled()
        expect(backupApi.delete).toHaveBeenCalledOnce()
    })

    it("protects in-use and last archives without blocking downloads of a last archive", async () => {
        const name="releasetracker-1-deadbeef.zip"
        vi.mocked(backupApi.list).mockResolvedValue({items:[{name,size:1,created_at:1}],interval_hours:0,retention:7,running:false})
        view();expect(await screen.findByRole("button",{name:`删除 ${name}`})).toBeDisabled()
        expect(screen.getByRole("button",{name:`下载 ${name}`})).toBeEnabled()
        expect(backupApi.delete).not.toHaveBeenCalled()
    })

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
    it("surfaces failing and overdue automatic backups", async () => {
        vi.mocked(backupApi.list).mockResolvedValue({
            items: [{ name: "old.zip", size: 1000, created_at: 100 }], interval_hours: 24, retention: 7, running: false,
            last_success_at: 100, consecutive_failures: 3, last_error_code: "storage_error", overdue: true,
        })
        view()
        const alert = await screen.findByRole("alert")
        expect(alert).toHaveTextContent("最近 3 次备份失败：存储错误")
        expect(alert).toHaveTextContent("自动备份已过期")
        expect(alert).toHaveTextContent("最近一次成功")
    })

    it("hides the health banner when backups are healthy", async () => {
        vi.mocked(backupApi.list).mockResolvedValue({
            items: [], interval_hours: 24, retention: 7, running: false, consecutive_failures: 0, overdue: false,
        })
        view()
        await screen.findByText("每 24 小时自动备份")
        expect(screen.queryByRole("alert")).not.toBeInTheDocument()
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
