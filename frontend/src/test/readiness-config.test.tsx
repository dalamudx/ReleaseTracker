import { describe, expect, it, vi } from "vitest"
import { fireEvent, render, screen } from "@testing-library/react"
import { useForm } from "react-hook-form"
import { Form } from "@/components/ui/form"
import { ExecutorSheetHealthCheckFields } from "@/components/executors/ExecutorSheetHealthCheckFields"
import { ReadinessSummary } from "@/components/executors/ReadinessSummary"
import { buildExecutorFormValues, buildExecutorPayload, createDefaultExecutorValues } from "@/components/executors/executorSheetHelpers"
vi.mock("react-i18next", () => ({ useTranslation: () => ({ t: (key: string) => key }) }))

const target = { mode: "container" as const, container_name: "service-a" }
function serialize(values = createDefaultExecutorValues()) {
    return buildExecutorPayload({ values, effectiveTrackerSourceId: "1", selectedTargetRef: target })
}
function Fields({ targetRef = target }: { targetRef?: Record<string, unknown> }) {
    const form = useForm({ defaultValues: createDefaultExecutorValues() })
    return <Form {...form}><ExecutorSheetHealthCheckFields form={form} selectedTargetRef={targetRef} /></Form>
}
describe("readiness configuration", () => {
    it("defaults to native readiness on, standalone notification off", () => {
        expect(serialize().health_check).toMatchObject({ readiness_enabled: true, notify_result: false, use_system_readiness_defaults: true, readiness_timeout_seconds: 600, readiness_interval_seconds: 5, readiness_attempt_timeout_seconds: 10, readiness_stable_seconds: 10 })
    })
    it("drops a stale supplemental probe when the single readiness switch is disabled", () => {
        const profile = serialize({
            ...createDefaultExecutorValues(),
            health_check_readiness_enabled: false,
            health_check_notify_result: true,
            health_check_strategy: "manual_http",
            health_check_http_host: "probe.example.test",
            health_check_http_port: "8080",
        })
        expect(profile.health_check).toMatchObject({
            readiness_enabled: false,
            notify_result: false,
            strategy: "none",
            http: null,
        })
    })
    it.each([true, false])("round trips toggles %s independently of no supplemental probe", (enabled) => {
        const values = { ...createDefaultExecutorValues(), health_check_strategy: "none" as const, health_check_readiness_enabled: enabled, health_check_notify_result: enabled, health_check_use_system_readiness_defaults: enabled, health_check_readiness_timeout_seconds: "120", health_check_readiness_stable_seconds: "0" }
        const profile = serialize(values)
        expect(serialize(buildExecutorFormValues(profile)).health_check).toEqual(profile.health_check)
        expect(profile.health_check).toMatchObject({ strategy: "none", notify_result: enabled, readiness_enabled: enabled, readiness_timeout_seconds: 120, readiness_stable_seconds: 0 })
    })
    it("exposes labelled switches and custom timing only when defaults are off", () => {
        render(<Fields />)
        expect(screen.getByRole("switch", {name: "readiness.notify"})).not.toBeChecked()
        expect(screen.getByRole("switch", {name: "executors.healthCheck.title"})).toBeChecked()
        expect(screen.queryByLabelText("readiness.readiness_timeout_seconds")).not.toBeInTheDocument()
        fireEvent.click(screen.getByRole("switch", {name: "readiness.useDefaults"}))
        expect(screen.getByLabelText("readiness.readiness_timeout_seconds")).toHaveValue(600)
    })
    it("uses one operable readiness switch for SSH Compose", () => {
        render(<Fields targetRef={{ mode: "ssh_compose" }} />)
        expect(screen.queryByRole("combobox")).not.toBeInTheDocument()
        const readiness = screen.getByRole("switch", { name: "executors.healthCheck.title" })
        expect(readiness).toBeEnabled()
        expect(readiness).toBeChecked()
        fireEvent.click(readiness)
        expect(readiness).not.toBeChecked()
        expect(screen.queryByRole("switch", { name: "readiness.notify" })).not.toBeInTheDocument()
    })
    it.each(["pending", "healthy", "unhealthy", "timeout", "unknown", "unsupported", "superseded", "not_checked"])("shows %s service diagnostics", (outcome) => {
        render(<ReadinessSummary result={{ outcome, services: [{service: "service-a", status: outcome, method: "native", message: "sample observation"}] }} />)
        expect(screen.getByText(/service-a/)).toHaveTextContent(`readiness.outcome.${outcome}`)
        expect(screen.getByText(/service-a/)).toHaveTextContent("sample observation")
    })
})
