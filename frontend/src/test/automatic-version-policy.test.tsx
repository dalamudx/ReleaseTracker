import { render, screen, fireEvent, waitFor } from "@testing-library/react"
import { afterEach, describe, expect, it } from "vitest"
import { useForm, useWatch } from "react-hook-form"
import i18n from "@/i18n/config"
import { Form } from "@/components/ui/form"
import { AutomaticVersionPolicyField } from "@/components/executors/AutomaticVersionPolicyField"
import type { ExecutorFormValues } from "@/components/executors/executorSheetHelpers"

afterEach(() => Reflect.deleteProperty(HTMLElement.prototype, "scrollIntoView"))

function View() {
    const form = useForm<ExecutorFormValues>({defaultValues:{auto_update_policy:"minor"}})
    const policy = useWatch({control:form.control, name:"auto_update_policy"})
    return <Form {...form}><AutomaticVersionPolicyField form={form} /><output>{policy}</output></Form>
}

describe("automatic version policy", () => {
    it("shows saved limits and allows changing to patch-only", async () => {
        Object.defineProperty(HTMLElement.prototype, "scrollIntoView", { configurable: true, value: () => {} })
        await i18n.changeLanguage("zh")
        render(<View />)
        const select = screen.getByRole("combobox", {name:"自动升级版本限制"})
        expect(select).toHaveTextContent("仅次版本和补丁")
        expect(screen.getByText(/超出范围时需要审批/)).toBeInTheDocument()
        fireEvent.click(select)
        fireEvent.click(await screen.findByRole("option", {name:"仅补丁"}))
        await waitFor(()=>expect(screen.getByRole("status")).toHaveTextContent("patch"))
    })
})
