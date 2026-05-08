import {
    CircleAlert,
    CircleCheck,
    CircleSlash,
    CircleX,
    Edit,
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
import { cn } from "@/lib/utils"
import { getChannelLabel } from "@/lib/channel"
import { buildExecutorTargetDisplay, isHelmReleaseTarget } from "./executorSheetHelpers"

interface ExecutorListProps {
    executors: ExecutorListItem[]
    loading: boolean
    onEdit: (executorId: number) => void
    onDelete: (executorId: number) => void
    onRun: (executorId: number) => void
    onViewExecutionHistory: (executorId: number) => void
    selectedExecutorId: number | null
}

const STATUS_VARIANT_MAP = {
    success: "default",
    failed: "destructive",
    skipped: "secondary",
} as const

const STATUS_ICON_MAP: Record<string, ReactNode> = {
    success: <CircleCheck className="h-3 w-3" />,
    failed: <CircleX className="h-3 w-3" />,
    skipped: <CircleSlash className="h-3 w-3" />,
}

export function ExecutorList({
    executors,
    loading,
    onEdit,
    onDelete,
    onRun,
    onViewExecutionHistory,
    selectedExecutorId,
}: ExecutorListProps) {
    const { t, i18n } = useTranslation()
    const dateLocale = i18n?.language === "zh" ? zhCN : enUS

    const stopRowClick = (event: MouseEvent) => event.stopPropagation()

    return (
        <div
            className="min-h-0 flex-1 overflow-auto rounded-md border"
            data-testid="executor-list"
            data-loading={String(loading)}
        >
            <Table containerClassName="overflow-visible">
                <TableHeader className="sticky top-0 z-10 bg-background">
                    <TableRow>
                        <TableHead className="min-w-[14rem]">{t("executors.table.name")}</TableHead>
                        <TableHead className="min-w-[18rem]">{t("executors.table.target")}</TableHead>
                        <TableHead className="hidden min-w-[12rem] md:table-cell">
                            {t("executors.table.tracker")}
                        </TableHead>
                        <TableHead className="min-w-[10rem]">{t("executors.table.status")}</TableHead>
                        <TableHead className="hidden lg:table-cell">{t("executors.table.lastRun")}</TableHead>
                        <TableHead className="w-[1%] text-right">{t("executors.table.actions")}</TableHead>
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
                            const lastResult = executor.status?.last_result ?? null
                            const lastRunAt = executor.status?.last_run_at ?? null
                            const serviceBindings = executor.service_bindings ?? []
                            const serviceBindingSummary = buildServiceBindingSummary(serviceBindings)
                            const isSelected = executor.id === selectedExecutorId
                            const hasError = Boolean(executor.invalid_config_error)
                            const referenceLabel = isHelmReleaseTarget(executor.target_ref)
                                ? t("executors.referenceModes.chart")
                                : executor.image_reference_mode?.toUpperCase()

                            return (
                                <TableRow
                                    key={executor.id ?? executor.name}
                                    data-selected={isSelected || undefined}
                                    className={cn(
                                        "relative cursor-pointer transition-colors hover:bg-muted/40",
                                        isSelected && "bg-primary/5 hover:bg-primary/10",
                                    )}
                                    onClick={() => executor.id && onViewExecutionHistory(executor.id)}
                                >
                                    {/* Name column — tracker name + runtime type accent. */}
                                    <TableCell className="relative py-3 align-top">
                                        {isSelected ? (
                                            <span
                                                aria-hidden
                                                className="absolute left-0 top-1/2 h-8 w-[3px] -translate-y-1/2 rounded-r-full bg-primary"
                                            />
                                        ) : null}
                                        <div className="min-w-0 space-y-1 pl-1">
                                            <div className="flex items-center gap-1.5">
                                                <span
                                                    className="truncate text-sm font-semibold text-foreground"
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
                                            <div className="flex min-w-0 items-center gap-1.5 text-[11px] text-muted-foreground">
                                                <span className="shrink-0 uppercase tracking-wide">
                                                    {executor.runtime_type}
                                                </span>
                                                <span aria-hidden>·</span>
                                                <span className="truncate">
                                                    {executor.runtime_connection_name || "—"}
                                                </span>
                                            </div>
                                        </div>
                                    </TableCell>

                                    {/* Target column. */}
                                    <TableCell className="py-3 align-top">
                                        <div className="min-w-0 space-y-0.5">
                                            <div className="flex min-w-0 items-center gap-2">
                                                <span
                                                    className="truncate text-sm font-medium text-foreground"
                                                    title={targetDisplay.title}
                                                >
                                                    {targetDisplay.title}
                                                </span>
                                                {targetKindLabel ? (
                                                    <Badge
                                                        variant="outline"
                                                        className="h-5 shrink-0 border-border/60 px-1.5 text-[10px] font-normal"
                                                    >
                                                        {targetKindLabel}
                                                    </Badge>
                                                ) : null}
                                            </div>
                                            {targetDisplay.subtitle ? (
                                                <div
                                                    className="truncate font-mono text-[11px] text-muted-foreground"
                                                    title={targetDisplay.subtitle}
                                                >
                                                    {targetDisplay.subtitle}
                                                </div>
                                            ) : null}
                                            <div
                                                className="truncate text-xs text-muted-foreground"
                                                title={targetDisplay.summary}
                                            >
                                                {targetDisplay.summary}
                                            </div>
                                        </div>
                                    </TableCell>

                                    {/* Tracker column (hidden on narrow screens). */}
                                    <TableCell className="hidden py-3 align-top md:table-cell">
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
                                            {serviceBindingSummary ? (
                                                <div
                                                    className="truncate text-[11px] text-muted-foreground"
                                                    title={serviceBindingSummary}
                                                >
                                                    {t("executors.target.details.services")}: {serviceBindingSummary}
                                                </div>
                                            ) : null}
                                        </div>
                                    </TableCell>

                                    {/* Status column — compact badge + mode. */}
                                    <TableCell className="py-3 align-top">
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
                                        className="w-[1%] whitespace-nowrap py-3 text-right align-top"
                                        onClick={stopRowClick}
                                    >
                                        <div className="flex items-center justify-end gap-0.5">
                                            <Tooltip>
                                                <TooltipTrigger asChild>
                                                    <Button
                                                        variant="ghost"
                                                        size="icon"
                                                        className="h-7 w-7"
                                                        disabled={!executor.enabled || !executor.id}
                                                        onClick={(event) => {
                                                            stopRowClick(event)
                                                            if (executor.id) onRun(executor.id)
                                                        }}
                                                    >
                                                        <Play className="h-3.5 w-3.5" />
                                                        <span className="sr-only">
                                                            {t("executors.actions.runNow")}
                                                        </span>
                                                    </Button>
                                                </TooltipTrigger>
                                                <TooltipContent>
                                                    {t("executors.actions.runNow")}
                                                </TooltipContent>
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

function buildServiceBindingSummary(
    serviceBindings: NonNullable<ExecutorListItem["service_bindings"]>,
): string | null {
    if (serviceBindings.length === 0) return null
    const visible = serviceBindings.slice(0, 2).map((binding) => binding.service).filter(Boolean)
    const overflow = serviceBindings.length - visible.length
    const summary = visible.join(", ")
    if (!summary) return String(serviceBindings.length)
    return overflow > 0 ? `${summary} +${overflow}` : summary
}
