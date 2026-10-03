import { act, fireEvent, render, screen, waitFor } from "@testing-library/react"
import { beforeEach, describe, expect, it, vi } from "vitest"
import { api } from "@/api/client"
import { ExecutorConfigurationPreview } from "@/components/executors/ExecutorConfigurationPreview"
import type { ExecutorConfigurationPreview as Preview } from "@/api/types"

vi.mock("@/api/client", () => ({ api: { previewExecutorConfiguration: vi.fn() } }))
vi.mock("react-i18next", () => ({ useTranslation: () => ({ t: (key: string) => key }) }))
const result = (image="nginx:new"): Preview => ({ mutation_performed:false,checked_at:"2026-10-03T10:00:00Z",comparison_error:null,configuration_diff:{scope:"container_configuration",truncated:false,lines:[{operation:"-",path:"/image",value:'"nginx:old"',redacted:false},{operation:"+",path:"/image",value:JSON.stringify(image),redacted:false}]}})

beforeEach(()=>{vi.mocked(api.previewExecutorConfiguration).mockReset()})
describe("unsaved executor live diff",()=>{
    it("reads the exact draft, shows signs inline and refreshes without saving",async()=>{
        vi.mocked(api.previewExecutorConfiguration).mockResolvedValue(result())
        render(<ExecutorConfigurationPreview executorId={7} payloadJson={JSON.stringify({image_reference_mode:"tag",target_ref:{container_name:"isolated"}})} />)
        const diff=await screen.findByTestId("executor-draft-configuration-diff")
        expect(diff).toHaveTextContent("nginx:new")
        expect(diff.querySelectorAll("li")).toHaveLength(2)
        expect(api.previewExecutorConfiguration).toHaveBeenCalledWith({image_reference_mode:"tag",target_ref:{container_name:"isolated"}},7,expect.any(AbortSignal))
        fireEvent.click(screen.getByRole("button",{name:"executors.review.refreshDiff"}))
        await waitFor(()=>expect(api.previewExecutorConfiguration).toHaveBeenCalledTimes(2))
    })
    it("immediately drops old content and ignores a late response after draft change",async()=>{
        let late!: (value:Preview)=>void
        vi.mocked(api.previewExecutorConfiguration).mockImplementationOnce(()=>new Promise(resolve=>{late=resolve})).mockResolvedValue(result("nginx:fresh"))
        const view=render(<ExecutorConfigurationPreview executorId={7} payloadJson='{"target_ref":{"container_name":"old"}}' />)
        const signal=vi.mocked(api.previewExecutorConfiguration).mock.calls[0][2]
        view.rerender(<ExecutorConfigurationPreview executorId={7} payloadJson='{"target_ref":{"container_name":"new"}}' />)
        expect(signal?.aborted).toBe(true)
        expect(await screen.findByText(/nginx:fresh/)).toBeVisible()
        await act(async()=>{late(result("nginx:stale"))})
        expect(screen.queryByText(/nginx:stale/)).not.toBeInTheDocument()
    })
    it("shows failed/no-release states as unavailable rather than no changes",async()=>{
        vi.mocked(api.previewExecutorConfiguration).mockRejectedValue(new Error("secret"))
        render(<ExecutorConfigurationPreview executorId={7} payloadJson='{}' />)
        expect(await screen.findByRole("alert")).toHaveTextContent("executors.review.failedDiff")
        expect(screen.queryByTestId("executor-draft-configuration-diff")).not.toBeInTheDocument()
        vi.mocked(api.previewExecutorConfiguration).mockResolvedValue({...result(),comparison_error:"no_deployable_version",configuration_diff:null})
        fireEvent.click(screen.getByRole("button",{name:"executors.review.refreshDiff"}))
        await waitFor(()=>expect(screen.getByRole("alert")).toHaveTextContent("executors.review.noVersionDiff"))
    })
    it("masks secret values and clearly marks incomplete results",async()=>{
        const data=result();data.configuration_diff!.lines=[{operation:"+",path:"/password",value:"never-show-this",redacted:true}];data.configuration_diff!.truncated=true
        vi.mocked(api.previewExecutorConfiguration).mockResolvedValue(data)
        render(<ExecutorConfigurationPreview executorId={null} payloadJson='{}' />)
        expect(await screen.findByRole("alert")).toHaveTextContent("executors.review.truncatedDiff")
        expect(screen.queryByText(/never-show-this/)).not.toBeInTheDocument()
        expect(screen.getByTestId("executor-draft-configuration-diff")).toHaveTextContent("executors.rollback.dialog.hiddenValue")
    })
})
