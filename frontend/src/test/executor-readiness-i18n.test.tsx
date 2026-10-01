import { render, screen, within } from "@testing-library/react"
import { afterEach, describe, expect, it } from "vitest"
import i18n from "@/i18n/config"
import { ExecutorList } from "@/components/executors/ExecutorList"
import { ReadinessSummary } from "@/components/executors/ReadinessSummary"
import type { ExecutorListItem } from "@/api/types"

const executor = {
    id: 1, name: "probe", enabled: true, runtime_type: "kubernetes",
    runtime_connection_id: 1, tracker_name: "probe", tracker_source_id: 1,
    channel_name: "stable", update_mode: "manual", target_ref: {mode: "kubernetes_workload", namespace: "test", kind: "Deployment", name: "probe"},
    status: {last_result: "health_checking"},
} as ExecutorListItem

afterEach(async () => { await i18n.changeLanguage("en") })

describe.each([
    ["zh", "等待部署就绪", "镜像拉取暂时失败，正在只读复检（不会再次部署）"],
    ["en", "Waiting for readiness", "Image pull temporarily failed; verifying readiness without redeploying"],
])("real executor translations (%s)", (language, status, reason) => {
    it("falls back to localized labels for future status keys", async () => {
        await i18n.changeLanguage(language)
        render(<ReadinessSummary result={{outcome:"future_outcome", services:[{service:"probe", status:"future_status", method:"future_method", message:"future_reason"}]}} />)
        expect(document.body.textContent).not.toMatch(/future_(outcome|status|method|reason)/)
        expect(document.body.textContent).toContain(i18n.t("readiness.messages.other"))
    })

    it("never shows untranslated status keys or Pod messages", async () => {
        await i18n.changeLanguage(language)
        render(<>
            <ExecutorList executors={[executor]} loading={false} onEdit={() => {}} onDelete={() => {}} onRun={() => {}} onViewExecutionHistory={() => {}} onSelect={() => {}} selectedExecutorId={null} />
            <ReadinessSummary result={{outcome:"pending", message:"image_pull_retrying", service_interruption_risk:true, services:[{service:"probe", status:"pending", method:"kubernetes_rollout", message:"image_pull_retrying"}]}} />
        </>)
        expect(within(screen.getByTestId("executor-row")).getAllByText(status).length).toBeGreaterThan(0)
        expect(document.body.textContent).toContain(reason)
        expect(screen.getByRole("alert")).toHaveTextContent(i18n.t("readiness.messages.recreate_interruption"))
        expect(document.body.textContent).not.toContain("executors.results.health_checking")
        expect(document.body.textContent).not.toContain("readiness.messages.image_pull_retrying")
    })
})
