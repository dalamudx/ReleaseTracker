import { describe, expect, it } from "vitest"
import { render, screen } from "@testing-library/react"
import { MemoryRouter } from "react-router"
import { I18nextProvider } from "react-i18next"
import i18n from "@/i18n/config"
import { DashboardHeaderCards } from "@/components/dashboard/DashboardHeaderCards"
import type { ReleaseStats, ExecutorListItem } from "@/api/types"

const mockStats: ReleaseStats = {
    total_trackers: 12,
    total_releases: 158,
    recent_releases: 3,
    latest_update: "2026-09-17T18:00:00Z",
    daily_stats: [],
    channel_stats: { stable: 120, prerelease: 38 },
    release_type_stats: { release: 120, prerelease: 38 },
}

const mockExecutors: ExecutorListItem[] = [
    {
        id: 1,
        name: "Web Executor",
        runtime_type: "docker",
        runtime_connection_id: 1,
        tracker_name: "frontend",
        enabled: true,
        update_mode: "immediate",
        target_ref: { mode: "container" },
        status: {
            executor_id: 1,
            last_result: "success",
            last_run_at: "2026-09-17T17:00:00Z",
        },
    },
    {
        id: 2,
        name: "Worker Executor",
        runtime_type: "podman",
        runtime_connection_id: 2,
        tracker_name: "backend",
        enabled: false,
        update_mode: "manual",
        target_ref: { mode: "container" },
        status: {
            executor_id: 2,
            last_result: "failed",
            last_run_at: "2026-09-17T16:00:00Z",
        },
    },
]

describe("DashboardHeaderCards", () => {
    it("renders all 6 top cards correctly when stats and executors provided", () => {
        render(
            <I18nextProvider i18n={i18n}>
                <MemoryRouter>
                    <DashboardHeaderCards
                        stats={mockStats}
                        executors={mockExecutors}
                        statsLoading={false}
                        executorsLoading={false}
                    />
                </MemoryRouter>
            </I18nextProvider>
        )

        expect(screen.getByTestId("kpi-total-trackers")).toHaveTextContent("12")
        expect(screen.getByTestId("kpi-total-releases")).toHaveTextContent("158")
        expect(screen.getByTestId("kpi-recent-releases")).toHaveTextContent("3")
        expect(screen.getByTestId("executors-total-count")).toHaveTextContent("2")
        expect(screen.getByTestId("executors-enabled-count")).toHaveTextContent("1")
    })

    it("renders loading skeletons when loading is true", () => {
        const { container } = render(
            <I18nextProvider i18n={i18n}>
                <MemoryRouter>
                    <DashboardHeaderCards
                        stats={null}
                        executors={undefined}
                        statsLoading={true}
                        executorsLoading={true}
                    />
                </MemoryRouter>
            </I18nextProvider>
        )

        const pulses = container.querySelectorAll(".animate-pulse")
        expect(pulses.length).toBeGreaterThan(0)
    })
})
