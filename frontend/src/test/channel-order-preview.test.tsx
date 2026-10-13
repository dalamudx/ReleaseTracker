import { fireEvent, render, screen, waitFor } from "@testing-library/react"
import { beforeEach, describe, expect, it, vi } from "vitest"
import { ChannelOrderPreview } from "@/components/trackers/ChannelOrderPreview"
import { validateRegexPattern } from "@/components/trackers/trackerDialogHelpers"
import { apiClient } from "@/api/client"

vi.mock("@/api/client", () => ({ apiClient: { post: vi.fn() } }))
vi.mock("react-i18next", () => ({ useTranslation: () => ({ t: (key: string, args?: { tag?: string }) => args?.tag ? `${key}: ${args.tag}` : key }) }))
const channel = { release_channel_key: "stable", name: "stable" as const, include_pattern: "^(?<version>\\d+)\\.v[\\w_]+-(?<revision>\\d+)$" }

beforeEach(() => vi.clearAllMocks())
describe("channel ordering preview", () => {
    it("is collapsed by default and reveals both complex rules and preview input in a single expand", () => {
        const { container } = render(<ChannelOrderPreview channel={channel} />)
        expect(container.querySelector("details")).toBeNull()
        expect(screen.getByText("trackers.orderPreview.title")).toBeVisible()
        expect(screen.getByText("trackers.orderPreview.hint")).toBeVisible()
        expect(screen.queryByLabelText("trackers.orderPreview.tags")).not.toBeInTheDocument()

        fireEvent.click(screen.getByText("trackers.orderPreview.title"))
        expect(screen.getByText("trackers.orderPreview.advanced")).toBeVisible()
        expect(screen.getByText("trackers.orderPreview.advancedHint")).toBeVisible()
        expect(screen.getByText("^(?<version>\\d+)\\.v[\\w_]+-(?<revision>\\d+)$")).toBeVisible()
        expect(screen.getByLabelText("trackers.orderPreview.tags")).toBeVisible()
    })

    it("uses the real backend contract without saving the tracker and shows family badges", async () => {
        vi.mocked(apiClient.post).mockResolvedValue({
            data: {
                selected: "3386.vabc_def-10",
                reason: null,
                groups: [
                    { tag: "3386.vabc_def-10", family: "extracted" },
                    { tag: "latest", family: null },
                ],
            },
        })
        render(<ChannelOrderPreview channel={channel} />)
        fireEvent.click(screen.getByText("trackers.orderPreview.title"))
        fireEvent.change(screen.getByLabelText("trackers.orderPreview.tags"), { target: { value: "3386.vabc_def-1\n3386.vabc_def-10\nlatest" } })
        fireEvent.click(screen.getByRole("button", { name: "trackers.orderPreview.run" }))
        expect(await screen.findByText("trackers.orderPreview.selected: 3386.vabc_def-10")).toBeInTheDocument()
        expect(screen.getByText("extracted")).toBeInTheDocument()
        expect(screen.getByText("latest — trackers.orderPreview.unknown")).toBeInTheDocument()
        expect(apiClient.post).toHaveBeenCalledOnce()
        expect(apiClient.post).toHaveBeenCalledWith("/api/trackers/version-preview", expect.objectContaining({ tags: ["3386.vabc_def-1", "3386.vabc_def-10", "latest"], channel: expect.objectContaining({ include_pattern: channel.include_pattern }) }))
    })

    it("supports triggering preview with Ctrl+Enter in the tags textarea", async () => {
        vi.mocked(apiClient.post).mockResolvedValue({ data: { selected: "1.6.0", reason: null, groups: [{ tag: "1.6.0", family: "semver" }] } })
        render(<ChannelOrderPreview channel={channel} />)
        fireEvent.click(screen.getByText("trackers.orderPreview.title"))
        const textarea = screen.getByLabelText("trackers.orderPreview.tags")
        fireEvent.change(textarea, { target: { value: "1.6.0\n1.5.0" } })
        fireEvent.keyDown(textarea, { key: "Enter", ctrlKey: true })
        expect(await screen.findByText("trackers.orderPreview.selected: 1.6.0")).toBeInTheDocument()
        expect(apiClient.post).toHaveBeenCalledOnce()
    })

    it("hides obsolete results when channel rules change", async () => {
        vi.mocked(apiClient.post).mockResolvedValue({ data: { selected: "build-10", reason: null, groups: [] } })
        const { rerender } = render(<ChannelOrderPreview channel={channel} />)
        fireEvent.click(screen.getByText("trackers.orderPreview.title"))
        fireEvent.change(screen.getByLabelText("trackers.orderPreview.tags"), { target: { value: "build-10" } })
        fireEvent.click(screen.getByRole("button", { name: "trackers.orderPreview.run" }))
        await screen.findByText("trackers.orderPreview.selected: build-10")
        rerender(<ChannelOrderPreview channel={{ ...channel, include_pattern: "^other" }} />)
        await waitFor(() => expect(screen.queryByText("trackers.orderPreview.selected: build-10")).not.toBeInTheDocument())
    })

    it("accepts both named capture syntaxes in form validation", () => {
        expect(validateRegexPattern(channel.include_pattern, "invalid", "error")).toBeNull()
        expect(validateRegexPattern("^(?P<version>\\d+)$", "invalid", "error")).toBeNull()
        expect(validateRegexPattern("[", "invalid", "error")).toContain("invalid")
    })
})
