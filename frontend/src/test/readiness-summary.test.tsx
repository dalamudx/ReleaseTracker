import { render, screen } from "@testing-library/react"
import { beforeEach, describe, expect, it } from "vitest"
import i18n from "@/i18n/config"
import { ReadinessSummary } from "@/components/executors/ReadinessSummary"

beforeEach(async () => { await i18n.changeLanguage("en") })

describe("readiness diagnostic compatibility", () => {
  it.each([undefined, null, false, "healthy", {}, { services: [] }])("does not invent readiness for legacy or missing data: %j", result => {
    const { container } = render(<ReadinessSummary result={result} />)
    expect(container).toBeEmptyDOMElement()
  })

  it("keeps per-service runtime evidence separate from aggregate health", () => {
    render(<ReadinessSummary result={{ outcome: "healthy", elapsed_seconds: 12.4, services: [
      { service: "service-a", status: "healthy", method: "runtime_state", message: "running_no_healthcheck" },
      { service: "service-b", status: "healthy", method: "runtime_native", message: "Native healthcheck passed" },
    ] }} />)
    expect(screen.getAllByRole("listitem")).toHaveLength(2)
    const first = screen.getByText(/service-a:/)
    expect(first).toHaveTextContent(i18n.t("readiness.method.runtime_state"))
    expect(first).toHaveTextContent(i18n.t("readiness.messages.running_no_healthcheck"))
    // Free-form legacy messages remain readable as stored.
    expect(screen.getByText(/service-b:/)).toHaveTextContent("Native healthcheck passed")
    expect(document.body.textContent).not.toMatch(/runtime_state|runtime_native|running_no_healthcheck/)
    expect(screen.queryByRole("status")).not.toBeInTheDocument()
  })

  it("announces pending without a success claim and tolerates incomplete observations", () => {
    render(<ReadinessSummary result={{ outcome: "pending", elapsed_seconds: Infinity, services: [null, false, "bad", {}] }} />)
    expect(screen.getByRole("status")).toHaveTextContent(i18n.t("readiness.outcome.pending"))
    expect(screen.getByRole("status")).not.toHaveTextContent(/Infinity/)
    expect(screen.getAllByRole("listitem")).toHaveLength(1)
  })

  it("localizes unknown outcomes and codes instead of showing raw keys", () => {
    render(<ReadinessSummary result={{ outcome: "future_outcome", services: [{ service: "service-a", status: "unknown", message: "Runtime observation unavailable" }] }} />)
    expect(document.body.textContent).not.toContain("future_outcome")
    expect(document.body.textContent).toContain(i18n.t("readiness.outcome.unknown"))
    expect(screen.getByRole("listitem")).toHaveTextContent("Runtime observation unavailable")
  })

  it("shows localized application probe reasons with HTTP status", () => {
    render(<ReadinessSummary result={{ outcome: "pending", services: [{ service: "Application probe", status: "pending", method: "manual_http", message: "probe_status_mismatch", status_code: 503 }] }} />)
    const item = screen.getByRole("listitem")
    expect(item).toHaveTextContent(i18n.t("readiness.messages.application_probe"))
    expect(item).toHaveTextContent(i18n.t("readiness.messages.probe_status_mismatch"))
    expect(item).toHaveTextContent("HTTP 503")
  })
})
