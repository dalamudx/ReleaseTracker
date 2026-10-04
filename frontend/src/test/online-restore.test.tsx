import { beforeEach, describe, expect, it, vi } from "vitest"
import { fireEvent, render, screen, waitFor } from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { BackupRestoreDialog, OnlineRestoreProgress } from "@/components/settings/OnlineRestoreControls"
import { BackupSettings } from "@/components/settings/BackupSettings"
import { backupApi } from "@/api/backups"
import { saveReceipt } from "@/hooks/restore-receipt"
import i18n from "@/i18n/config"
vi.mock("@/api/backups",()=>({backupApi:{list:vi.fn(),create:vi.fn(),delete:vi.fn(),download:vi.fn(),restorePlan:vi.fn(),restore:vi.fn(),restoreStatus:vi.fn(),cancelPlan:vi.fn(),reviewRestore:vi.fn(),downloadSafety:vi.fn()}}))
vi.mock("sonner",()=>({toast:{success:vi.fn(),error:vi.fn()}}))
const name="releasetracker-1791000000000000000-deadbeef.zip"
const plan={id:"a".repeat(32),name,fingerprint:"c".repeat(64),expires_at:9999999999,created_at:1791000000,app_version:"1.1.14",mutation_performed:false as const}
const receipt={id:"a".repeat(32),token:"b".repeat(43)}
function view(element:React.ReactNode){const client=new QueryClient({defaultOptions:{queries:{retry:false},mutations:{retry:false}}});return render(<QueryClientProvider client={client}>{element}</QueryClientProvider>)}
beforeEach(async()=>{saveReceipt(null);sessionStorage.clear();vi.resetAllMocks();await i18n.changeLanguage("zh");vi.mocked(backupApi.restorePlan).mockResolvedValue(plan);vi.mocked(backupApi.cancelPlan).mockResolvedValue(undefined as never);vi.mocked(backupApi.restore).mockResolvedValue(receipt)})

describe("online restore review",()=>{
 it("requires actual preflight, complete filename and loss acknowledgement",async()=>{
  const close=vi.fn();view(<BackupRestoreDialog name={name} onClose={close}/>)
  const confirm=screen.getByRole("button",{name:"进入维护并恢复"});expect(confirm).toBeDisabled()
  await screen.findByLabelText("输入完整备份文件名以确认")
  fireEvent.change(screen.getByLabelText("输入完整备份文件名以确认"),{target:{value:name}});expect(confirm).toBeDisabled()
  fireEvent.click(screen.getByRole("checkbox"));expect(confirm).toBeEnabled();fireEvent.click(confirm)
  await waitFor(()=>expect(close).toHaveBeenCalledOnce());expect(backupApi.restore).toHaveBeenCalledWith(name,plan)
  expect(JSON.parse(sessionStorage.getItem("instance-restore-receipt")!)).toMatchObject(receipt)
  expect(backupApi.cancelPlan).not.toHaveBeenCalled()
 })
 it("does not perform recovery when cancelled and releases the prepared plan",async()=>{
  const close=vi.fn();view(<BackupRestoreDialog name={name} onClose={close}/>)
  await screen.findByLabelText("输入完整备份文件名以确认");fireEvent.click(screen.getByRole("button",{name:"取消"}))
  expect(backupApi.restore).not.toHaveBeenCalled();expect(backupApi.cancelPlan).toHaveBeenCalledWith(name);expect(close).toHaveBeenCalledOnce()
 })
 it("failed preflight cannot be confirmed and supports fresh validation",async()=>{
  vi.mocked(backupApi.restorePlan).mockRejectedValueOnce({response:{data:{detail:"restore_validation_failed"}}})
  view(<BackupRestoreDialog name={name} onClose={vi.fn()}/>)
  expect(await screen.findByRole("alert")).toHaveTextContent("版本不匹配")
  expect(screen.getByRole("button",{name:"进入维护并恢复"})).toBeDisabled()
  fireEvent.click(screen.getByRole("button",{name:"重新校验备份"}))
  await screen.findByLabelText("输入完整备份文件名以确认");expect(backupApi.restorePlan).toHaveBeenCalledTimes(2)
 })
 it("refuses stale approval with actionable error rather than dismissing dialog",async()=>{
  vi.mocked(backupApi.restore).mockRejectedValue({response:{data:{detail:"restore_plan_expired"}}})
  view(<BackupRestoreDialog name={name} onClose={vi.fn()}/>)
  await screen.findByLabelText("输入完整备份文件名以确认");fireEvent.change(screen.getByLabelText("输入完整备份文件名以确认"),{target:{value:name}});fireEvent.click(screen.getByRole("checkbox"));fireEvent.click(screen.getByRole("button",{name:"进入维护并恢复"}))
  expect(await screen.findByRole("alert")).toHaveTextContent("重新校验备份")
  expect(screen.getByRole("alertdialog")).toBeInTheDocument()
 })
 it("shows recovery result via private capability without relying on old session",async()=>{
  vi.mocked(backupApi.restoreStatus).mockResolvedValue({id:receipt.id,state:"succeeded",phase:"finished",error_code:null,rolled_back:false,review_required:true})
  saveReceipt(receipt);view(<OnlineRestoreProgress/>);await waitFor(()=>expect(screen.getByRole("status")).toHaveTextContent("恢复完成"))
  expect(screen.getByRole("button",{name:"重新登录"})).toBeEnabled()
  expect(backupApi.restoreStatus).toHaveBeenCalledWith(receipt,expect.any(AbortSignal))
 })
 it("review gate requires explicit confirmation and does not replay old approvals",async()=>{
  vi.mocked(backupApi.list).mockResolvedValue({items:[],retention:7,interval_hours:0,running:false,restore_review_required:true,online_restore_available:true})
  vi.mocked(backupApi.reviewRestore).mockResolvedValue(undefined as never)
  view(<BackupSettings/>);fireEvent.click(await screen.findByRole("button",{name:"复核恢复状态"}))
  expect(backupApi.reviewRestore).not.toHaveBeenCalled();expect(screen.getByRole("alertdialog")).toHaveTextContent("旧任务、审批和通知仍被撤销")
  fireEvent.click(screen.getByRole("button",{name:"已核对现场，恢复新操作"}));await waitFor(()=>expect(backupApi.reviewRestore).toHaveBeenCalledOnce())
 })
 it("expired receipt is not polled and does not hide normal settings",()=>{
  sessionStorage.setItem("instance-restore-receipt",JSON.stringify({...receipt,at:Date.now()-3600001}))
  const result=view(<OnlineRestoreProgress/>);expect(result.container).toBeEmptyDOMElement();expect(backupApi.restoreStatus).not.toHaveBeenCalled()
 })
})
