import {
    CircleAlert,
    CircleCheck,
    CircleSlash,
    CircleX,
    Edit,
    History,
    MoreHorizontal,
    Play,
    Trash2,
} from "lucide-react"
import { formatDistanceToNow } from "date-fns"
import { zhCN, enUS } from "date-fns/locale"
import { useTranslation } from "react-i18next"
import type { MouseEvent, ReactNode } from "react"

import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { ActiveRowMarker } from "@/components/common/ActiveRowMarker"
import { Spinner } from "@/components/ui/spinner"
import {
    DropdownMenu,
    DropdownMenuContent,
    DropdownMenuItem,
    DropdownMenuSeparator,
    DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import {
    Table,
    TableBody,
    TableCell,
    TableHead,
    TableHeader,
    TableRow,
} from "@/components/ui/table"
import {
    Tooltip,
    TooltipContent,
    TooltipTrigger,
} from "@/components/ui/tooltip"
import type { ExecutorListItem } from "@/api/types"
import { getChannelLabel } from "@/lib/channel"
import { buildExecutorTargetDisplay, isHelmReleaseTarget } from "./executorSheetHelpers"

interface ExecutorListProps {
    executors: ExecutorListItem[]
    loading: boolean
    onEdit: (executorId: number) => void
    onDelete: (executorId: number) => void
    onRun: (executorId: number) => void
    onViewExecutionHistory: (executorId: number) => void
    onSelect: (executorId: number) => void
    selectedExecutorId: number | null
    submittingExecutorIds?: ReadonlySet<number>
}

const STATUS_VARIANT_MAP = {
    health_checking: "info",
    success: "success",
    failed: "destructive",
    skipped: "secondary",
} as const

const STATUS_ICON_MAP: Record<string, ReactNode> = {
    health_checking: <History className="h-3 w-3" />,
    success: <CircleCheck className="h-3 w-3" />,
    failed: <CircleX className="h-3 w-3" />,
    skipped: <CircleSlash className="h-3 w-3" />,
}

function getExecutorTargetServiceCount(executor: ExecutorListItem): number | null {
    const serviceBindingCount = executor.service_bindings?.length ?? 0
    if (serviceBindingCount > 0) {
        return serviceBindingCount
    }

    const targetRef = executor.target_ref
    const explicitCount = targetRef.service_count
    if (typeof explicitCount === "number" && Number.isInteger(explicitCount) && explicitCount >= 0) {
        return explicitCount
    }

    if (Array.isArray(targetRef.services)) {
        return targetRef.services.length
    }
    if (Array.isArray(targetRef.workloads)) {
        return targetRef.workloads.length
    }

    if (!targetRef.mode || targetRef.mode === "container") {
        return 1
    }

    return null
}

export function ExecutorList({
    executors,
    loading,
    onEdit,
    onDelete,
    onRun,
    onViewExecutionHistory,
    onSelect,
    selectedExecutorId,
    submittingExecutorIds,
}: ExecutorListProps) {
    const { t, i18n } = useTranslation()
    const dateLocale = i18n?.language === "zh" ? zhCN : enUS

    const stopRowClick = (event: MouseEvent) => event.stopPropagation()

    return (
        <div
            className="min-h-0 flex-1 overflow-auto rounded-md border"
            data-testid="executor-list"
            data-loading={String(loading)}
            aria-busy={loading}
        >
            <Table className="table-fixed" containerClassName="overflow-visible">
                <TableHeader className="sticky top-0 z-10 bg-background">
                    <TableRow>
                        <TableHead>{t("executors.table.name")}</TableHead>
                        <TableHead className="hidden w-[28%] md:table-cell">{t("executors.table.target")}</TableHead>
                        <TableHead className="hidden w-[17%] xl:table-cell">
                            {t("executors.table.tracker")}
                        </TableHead>
                        <TableHead className="hidden w-24 sm:table-cell">{t("executors.table.status")}</TableHead>
                        <TableHead className="hidden w-24 lg:table-cell">{t("executors.table.lastRun")}</TableHead>
                        <TableHead className="w-28 text-right">{t("executors.table.actions")}</TableHead>
                    </TableRow>
                </TableHeader>
                <TableBody>
                    {loading ? (
                        <TableRow>
                            <TableCell colSpan={6} className="h-24 text-center text-sm text-muted-foreground">
                                {t("common.loading")}
                            </TableCell>
                        </TableRow>
                    ) : executors.length === 0 ? (
                        <TableRow>
                            <TableCell colSpan={6} className="h-24 text-center text-sm text-muted-foreground">
                                {t("common.noData")}
                            </TableCell>
                        </TableRow>
                    ) : (
                        executors.map((executor) => {
                            const targetDisplay = buildExecutorTargetDisplay(executor.runtime_type, executor.target_ref, t)
                            const targetKindLabel = targetDisplay.badges.find((badge) => badge !== executor.runtime_type) ?? targetDisplay.badges[0]
                            const targetServiceCount = getExecutorTargetServiceCount(executor)
                            const lastResult = executor.status?.last_result ?? null
                            const lastRunAt = executor.status?.last_run_at ?? null
                            const isSelected = executor.id === selectedExecutorId
                            const hasError = Boolean(executor.invalid_config_error)
                            const isPending = Boolean(executor.id && submittingExecutorIds?.has(executor.id))
                            const referenceLabel = isHelmReleaseTarget(executor.target_ref)
                                ? t("executors.referenceModes.chart")
                                : executor.image_reference_mode?.toUpperCase()

                            return (
                                <TableRow
                                    key={executor.id ?? executor.name}
                                    data-testid="executor-row"
                                    data-selected={isSelected || undefined}
                                    data-state={isSelected ? "selected" : undefined}
                                    aria-selected={isSelected}
                                    tabIndex={0}
                                    className="cursor-pointer focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-ring"
                                    onClick={() => executor.id && onSelect(executor.id)}
                                    onKeyDown={(event) => {
                                        if (event.target !== event.currentTarget) return
                                        if (event.key === "Enter" || event.key === " ") {
                                            event.preventDefault()
                                            if (executor.id) onSelect(executor.id)
                                        }
                                    }}
                                >
                                    {/* Name column — tracker name + runtime type accent. */}
                                    <TableCell className="relative py-3 align-top">
                                        <ActiveRowMarker active={isSelected} testId="executor-selection-marker" />
                                        <div className="min-w-0 space-y-1 pl-1.5">
                                            <div className="flex items-center gap-1.5">
                                                <span
                                                    className="min-w-0 truncate text-sm font-semibold text-foreground"
                                                    title={executor.name}
                                                >
                                                    {executor.name}
                                                </span>
                                                {!executor.enabled ? (
                                                    <Badge variant="outline" className="h-5 shrink-0 text-[10px]">
                                                        {t("common.disabled")}
                                                    </Badge>
                                                ) : null}
                                            </div>
                                            {executor.description ? (
                                                <div
                                                    className="line-clamp-1 text-xs text-muted-foreground"
                                                    title={executor.description}
                                                >
                                                    {executor.description}
                                                </div>
                                            ) : null}
                                            <div className="truncate text-xs text-muted-foreground md:hidden" title={targetDisplay.title}>
                                                {targetDisplay.title}
                                            </div>
                                            <div className="flex min-w-0 items-center gap-1.5 text-[11px] text-muted-foreground">
                                                <span className="shrink-0 uppercase tracking-wide">
                                                    {executor.runtime_type}
                                                </span>
                                                <span aria-hidden>·</span>
                                                <span className="truncate">
                                                    {executor.runtime_connection_name || "—"}
                                                </span>
                                            </div>
                                            {executor.compose_ownership && executor.compose_ownership !== "verified" ? (
                                                <Badge variant="outline" className="max-w-full whitespace-normal break-words text-[10px] text-warning">
                                                    {t(`sshExecutor.ownership_${executor.compose_ownership}`)}
                                                </Badge>
                                            ) : null}
                                            <div className="sm:hidden">
                                                {hasError ? (
                                                    <Badge variant="destructive" className="h-5 gap-1 text-[10px]"><CircleAlert className="size-3" />{t("executors.status.invalid")}</Badge>
                                                ) : (
                                                    <Badge variant={lastResult ? STATUS_VARIANT_MAP[lastResult] : "outline"} className="h-5 gap-1 text-[10px]">
                                                        {lastResult ? STATUS_ICON_MAP[lastResult] : null}
                                                        {t(lastResult ? `executors.results.${lastResult}` : "executors.results.idle")}
                                                    </Badge>
                                                )}
                                            </div>
                                        </div>
                                    </TableCell>

                                    {/* Target column. */}
                                    <TableCell data-testid="executor-target-cell" className="hidden py-3 align-top md:table-cell">
                                        <div className="min-w-0 space-y-0.5">
                                            <div
                                                className="truncate text-sm font-medium text-foreground"
                                                title={targetDisplay.title}
                                            >
                                                {targetDisplay.title}
                                            </div>
                                            <div className="flex min-w-0 items-center gap-1.5">
                                                {targetKindLabel ? (
                                                    <Badge
                                                        variant="outline"
                                                        className="h-5 max-w-[65%] shrink truncate border-border/60 px-1.5 text-[10px] font-normal"
                                                        title={targetKindLabel}
                                                    >
                                                        {targetKindLabel}
                                                    </Badge>
                                                ) : null}
                                                {targetServiceCount !== null ? (
                                                    <span
                                                        data-testid="executor-target-service-count"
                                                        data-count={targetServiceCount}
                                                        className="shrink-0 text-[11px] text-muted-foreground"
                                                    >
                                                        {t("executors.target.serviceCountSummary", { count: targetServiceCount })}
                                                    </span>
                                                ) : null}
                                            </div>
                                        </div>
                                    </TableCell>

                                    {/* Tracker column (hidden on narrow screens). */}
                                    <TableCell className="hidden py-3 align-top xl:table-cell">
                                        <div className="min-w-0 space-y-0.5">
                                            <div
                                                className="truncate text-sm text-foreground"
                                                title={executor.tracker_name}
                                            >
                                                {executor.tracker_name}
                                            </div>
                                            <div className="text-[11px] text-muted-foreground">
                                                {getChannelLabel(executor.channel_name)}
                                                {referenceLabel ? (
                                                    <>
                                                        <span aria-hidden> · </span>
                                                        <span className="font-mono uppercase">{referenceLabel}</span>
                                                    </>
                                                ) : null}
                                            </div>
                                        </div>
                                    </TableCell>

                                    {/* Status column — compact badge + mode. */}
                                    <TableCell className="hidden py-3 align-top sm:table-cell">
                                        <div className="space-y-1">
                                            <div className="flex flex-wrap items-center gap-1.5">
                                                {hasError ? (
                                                    <Tooltip>
                                                        <TooltipTrigger asChild>
                                                            <Badge variant="destructive" className="h-5 gap-1 text-[10px]">
                                                                <CircleAlert className="h-3 w-3" />
                                                                {t("executors.status.invalid")}
                                                            </Badge>
                                                        </TooltipTrigger>
                                                        <TooltipContent>
                                                            <p className="max-w-[320px] break-words text-xs">
                                                                {executor.invalid_config_error}
                                                            </p>
                                                        </TooltipContent>
                                                    </Tooltip>
                                                ) : (
                                                    <Badge
                                                        variant={lastResult ? STATUS_VARIANT_MAP[lastResult] : "outline"}
                                                        className="h-5 gap-1 text-[10px]"
                                                    >
                                                        {lastResult ? STATUS_ICON_MAP[lastResult] : null}
                                                        {lastResult
                                                            ? t(`executors.results.${lastResult}`)
                                                            : t("executors.results.idle")}
                                                    </Badge>
                                                )}
                                            </div>
                                            <div className="text-[11px] uppercase tracking-wide text-muted-foreground">
                                                {t(`executors.modes.${executor.update_mode}`)}
                                            </div>
                                        </div>
                                    </TableCell>

                                    {/* Last-run timestamp. */}
                                    <TableCell className="hidden py-3 align-top text-xs text-muted-foreground lg:table-cell">
                                        {lastRunAt ? (
                                            <span
                                                className="whitespace-nowrap tabular-nums"
                                                title={new Date(lastRunAt).toLocaleString()}
                                            >
                                                {formatDistanceToNow(new Date(lastRunAt), {
                                                    addSuffix: true,
                                                    locale: dateLocale,
                                                })}
                                            </span>
                                        ) : (
                                            t("common.never")
                                        )}
                                    </TableCell>

                                    {/* Actions. */}
                                    <TableCell
                                        className="whitespace-nowrap py-3 text-right align-top"
                                        onClick={stopRowClick}
                                    >
                                        <div className="flex items-center justify-end gap-0.5">
                                            <Tooltip>
                                                <TooltipTrigger asChild>
                                                    <Button
                                                        variant="ghost"
                                                        size="icon"
                                                        className="h-7 w-7"
                                                        disabled={!executor.enabled || !executor.id || hasError || isPending}
                                                        aria-label={t("executors.actions.runNow")}
                                                        onClick={(event) => {
                                                            stopRowClick(event)
                                                            if (executor.id) onRun(executor.id)
                                                        }}
                                                    >
                                                        {isPending ? <Spinner className="size-3.5" /> : <Play className="size-3.5" />}
                                                        <span className="sr-only">{t("executors.actions.runNow")}</span>
                                                    </Button>
                                                </TooltipTrigger>
                                                <TooltipContent>
                                                    {t("executors.actions.runNow")}
                                                </TooltipContent>
                                            </Tooltip>
                                            <Tooltip>
                                                <TooltipTrigger asChild>
                                                    <Button
                                                        variant="ghost"
                                                        size="icon"
                                                        className="size-7"
                                                        aria-label={t("executors.actions.viewExecutionHistory")}
                                                        onClick={(event) => {
                                                            stopRowClick(event)
                                                            if (executor.id) onViewExecutionHistory(executor.id)
                                                        }}
                                                    >
                                                        <History className="size-3.5" />
                                                    </Button>
                                                </TooltipTrigger>
                                                <TooltipContent>{t("executors.actions.viewExecutionHistory")}</TooltipContent>
                                            </Tooltip>
                                            <DropdownMenu>
                                                <DropdownMenuTrigger asChild onClick={stopRowClick}>
                                                    <Button variant="ghost" size="icon" className="h-7 w-7">
                                                        <MoreHorizontal className="h-3.5 w-3.5" />
                                                        <span className="sr-only">{t("common.actions")}</span>
                                                    </Button>
                                                </DropdownMenuTrigger>
                                                <DropdownMenuContent align="end">
                                                    <DropdownMenuItem
                                                        disabled={isPending}
                                                        onClick={(event) => {
                                                            stopRowClick(event)
                                                            if (executor.id) onEdit(executor.id)
                                                        }}
                                                    >
                                                        <Edit className="mr-2 h-4 w-4" />
                                                        {t("common.edit")}
                                                    </DropdownMenuItem>
                                                    <DropdownMenuSeparator />
                                                    <DropdownMenuItem
                                                        disabled={isPending}
                                                        className="text-destructive focus:text-destructive"
                                                        onClick={(event) => {
                                                            stopRowClick(event)
                                                            if (executor.id) onDelete(executor.id)
                                                        }}
                                                    >
                                                        <Trash2 className="mr-2 h-4 w-4" />
                                                        {t("common.delete")}
                                                    </DropdownMenuItem>
                                                </DropdownMenuContent>
                                            </DropdownMenu>
                                        </div>
                                    </TableCell>
                                </TableRow>
                            )
                        })
                    )}
                </TableBody>
            </Table>
        </div>
    )
}
