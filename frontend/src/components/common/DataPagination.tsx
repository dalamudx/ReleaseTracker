import { useState } from "react"
import {
    ChevronLeft,
    ChevronRight,
    ChevronsLeft,
    ChevronsRight,
} from "lucide-react"
import { useTranslation } from "react-i18next"

import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import {
    Select,
    SelectContent,
    SelectItem,
    SelectTrigger,
    SelectValue,
} from "@/components/ui/select"
import {
    Tooltip,
    TooltipContent,
    TooltipTrigger,
} from "@/components/ui/tooltip"
import { cn } from "@/lib/utils"

export interface DataPaginationProps {
    page: number
    pageSize: number
    total: number
    onPageChange: (page: number) => void
    onPageSizeChange: (pageSize: number) => void
    /**
     * Override the list of rows-per-page options. Defaults to [10, 15, 20, 30, 40, 50].
     */
    pageSizeOptions?: readonly number[]
    /**
     * Optional callback fired before navigation/page-size changes so callers
     * can close transient UI (e.g., side sheets) first.
     */
    onBeforeChange?: () => void
    /**
     * Additional class name applied to the outer wrapper.
     */
    className?: string
}

const DEFAULT_PAGE_SIZE_OPTIONS = [10, 15, 20, 30, 40, 50] as const

/**
 * Shared pagination bar used across list pages.
 *
 * Replaces five near-identical copies of the same JSX previously duplicated
 * in Trackers, Executors, History, Credentials, and RuntimeConnections.
 *
 * Layout:
 *   [ total items ] · · · [ rows-per-page ] [ ‹‹ ‹  page X of Y  › ›› ]
 *
 * Behaviour:
 *   - "Rows per page" label collapses below md so the select can still fit.
 *   - First/last page buttons only show on sm+ where there's room.
 *   - The current page is rendered as a tiny editable field so users can
 *     jump by typing a number and pressing Enter.
 *   - Changing page size always resets back to page 1 to avoid landing on
 *     an empty out-of-range page.
 */
export function DataPagination({
    page,
    pageSize,
    total,
    onPageChange,
    onPageSizeChange,
    pageSizeOptions = DEFAULT_PAGE_SIZE_OPTIONS,
    onBeforeChange,
    className,
}: DataPaginationProps) {
    const { t } = useTranslation()
    const totalPages = Math.max(1, Math.ceil(total / pageSize))
    // Guard against the caller forgetting to clamp after items get deleted,
    // so "Page 7 of 3" can never show up.
    const currentPage = Math.min(Math.max(1, page), totalPages)
    const canPrev = currentPage > 1
    const canNext = currentPage < totalPages

    const handlePageSizeChange = (value: string) => {
        const nextSize = Number(value)
        if (!Number.isFinite(nextSize) || nextSize <= 0) return
        onBeforeChange?.()
        onPageSizeChange(nextSize)
        // Reset to first page on size change to avoid showing an out-of-range
        // empty page.
        onPageChange(1)
    }

    const goToPage = (nextPage: number) => {
        const target = Math.min(Math.max(1, nextPage), totalPages)
        if (target === currentPage) return
        onBeforeChange?.()
        onPageChange(target)
    }

    return (
        <div className={cn("flex flex-shrink-0 items-center justify-between gap-3", className)}>
            <div className="hidden min-w-0 flex-1 truncate text-sm text-muted-foreground sm:block">
                {t("pagination.totalItems", { count: total })}
            </div>

            <div className="flex flex-wrap items-center justify-end gap-2 sm:gap-4 lg:gap-6">
                <PageSizeControl
                    value={pageSize}
                    options={pageSizeOptions}
                    onChange={handlePageSizeChange}
                />

                <div className="flex items-center gap-1">
                    <Tooltip>
                        <TooltipTrigger asChild>
                            <Button
                                variant="outline"
                                size="icon"
                                className="hidden h-8 w-8 sm:inline-flex"
                                onClick={() => goToPage(1)}
                                disabled={!canPrev}
                            >
                                <ChevronsLeft className="h-4 w-4" />
                                <span className="sr-only">{t("pagination.firstPage")}</span>
                            </Button>
                        </TooltipTrigger>
                        <TooltipContent>{t("pagination.firstPage")}</TooltipContent>
                    </Tooltip>

                    <Tooltip>
                        <TooltipTrigger asChild>
                            <Button
                                variant="outline"
                                size="icon"
                                className="h-8 w-8"
                                onClick={() => goToPage(currentPage - 1)}
                                disabled={!canPrev}
                            >
                                <ChevronLeft className="h-4 w-4" />
                                <span className="sr-only">{t("pagination.previousPage")}</span>
                            </Button>
                        </TooltipTrigger>
                        <TooltipContent>{t("pagination.previousPage")}</TooltipContent>
                    </Tooltip>

                    <PageInput
                        currentPage={currentPage}
                        totalPages={totalPages}
                        onJump={goToPage}
                    />

                    <Tooltip>
                        <TooltipTrigger asChild>
                            <Button
                                variant="outline"
                                size="icon"
                                className="h-8 w-8"
                                onClick={() => goToPage(currentPage + 1)}
                                disabled={!canNext}
                            >
                                <ChevronRight className="h-4 w-4" />
                                <span className="sr-only">{t("pagination.nextPage")}</span>
                            </Button>
                        </TooltipTrigger>
                        <TooltipContent>{t("pagination.nextPage")}</TooltipContent>
                    </Tooltip>

                    <Tooltip>
                        <TooltipTrigger asChild>
                            <Button
                                variant="outline"
                                size="icon"
                                className="hidden h-8 w-8 sm:inline-flex"
                                onClick={() => goToPage(totalPages)}
                                disabled={!canNext}
                            >
                                <ChevronsRight className="h-4 w-4" />
                                <span className="sr-only">{t("pagination.lastPage")}</span>
                            </Button>
                        </TooltipTrigger>
                        <TooltipContent>{t("pagination.lastPage")}</TooltipContent>
                    </Tooltip>
                </div>
            </div>
        </div>
    )
}

interface PageSizeControlProps {
    value: number
    options: readonly number[]
    onChange: (value: string) => void
}

function PageSizeControl({ value, options, onChange }: PageSizeControlProps) {
    const { t } = useTranslation()
    return (
        <div className="flex items-center gap-2">
            <span className="hidden text-sm font-medium text-muted-foreground md:inline">
                {t("pagination.rowsPerPage")}
            </span>
            <Select value={`${value}`} onValueChange={onChange}>
                <SelectTrigger
                    className="h-8 w-[72px]"
                    aria-label={t("pagination.rowsPerPage")}
                >
                    <SelectValue placeholder={value} />
                </SelectTrigger>
                <SelectContent side="top">
                    {options.map((size) => (
                        <SelectItem key={size} value={`${size}`}>
                            {size}
                        </SelectItem>
                    ))}
                </SelectContent>
            </Select>
        </div>
    )
}

interface PageInputProps {
    currentPage: number
    totalPages: number
    onJump: (page: number) => void
}

/**
 * Inline editable page number — renders as a plain number that accepts
 * focus and keyboard input. Pressing Enter or blurring commits the jump,
 * Escape reverts.
 */
function PageInput({ currentPage, totalPages, onJump }: PageInputProps) {
    const { t } = useTranslation()
    const [editDraft, setEditDraft] = useState<string | null>(null)
    const draft = editDraft ?? String(currentPage)

    const commit = () => {
        const parsed = Number.parseInt(draft, 10)
        if (Number.isFinite(parsed) && parsed >= 1 && parsed <= totalPages) {
            onJump(parsed)
        }
        setEditDraft(null)
    }

    const cancel = () => {
        setEditDraft(null)
    }

    return (
        <div
            className="flex h-8 items-center gap-1 whitespace-nowrap rounded-md border border-transparent px-2 text-sm tabular-nums"
            aria-label={t("pagination.pageOf", { page: currentPage, total: totalPages })}
        >
            <Input
                value={draft}
                type="number"
                min={1}
                max={totalPages}
                onFocus={(event) => {
                    setEditDraft(String(currentPage))
                    event.currentTarget.select()
                }}
                onChange={(event) => setEditDraft(event.target.value)}
                onBlur={commit}
                onKeyDown={(event) => {
                    if (event.key === "Enter") {
                        event.preventDefault()
                        ;(event.currentTarget as HTMLInputElement).blur()
                    } else if (event.key === "Escape") {
                        event.preventDefault()
                        cancel()
                        ;(event.currentTarget as HTMLInputElement).blur()
                    }
                }}
                className={cn(
                    "h-6 w-12 rounded border-transparent bg-transparent px-1 text-center font-medium shadow-none",
                    "focus-visible:border-border focus-visible:bg-background focus-visible:ring-[3px] focus-visible:ring-ring/50",
                    "[-moz-appearance:textfield] [&::-webkit-inner-spin-button]:m-0 [&::-webkit-inner-spin-button]:appearance-none [&::-webkit-outer-spin-button]:m-0 [&::-webkit-outer-spin-button]:appearance-none",
                )}
            />
            <span className="text-muted-foreground">/</span>
            <span className="font-medium">{totalPages}</span>
        </div>
    )
}
