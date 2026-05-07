import { MoreHorizontal, Edit, History, Play, Trash2 } from "lucide-react"
import { formatDistanceToNow } from "date-fns"
import { zhCN, enUS } from "date-fns/locale"
import { useTranslation } from "react-i18next"
import type { MouseEvent } from "react"

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
    selectedExecutorId: number | null
}

const statusVariantMap = {
    success: "default",
    failed: "destructive",
    skipped: "secondary",
} as const

export function ExecutorList({ executors, loading, onEdit, onDelete, onRun, onViewExecutionHistory, selectedExecutorId }: ExecutorListProps) {
    const { t, i18n } = useTranslation()

    const stopRowClickPropagation = (event: MouseEvent<HTMLElement>) => {
        event.stopPropagation()
    }

    return (
        <div className="min-h-0 flex-1 overflow-auto rounded-md border">
            <Table className="min-w-[1040px]" containerClassName="overflow-visible">
                <TableHeader className="sticky top-0 bg-background z-10">
                    <TableRow className="bg-muted/30 hover:bg-muted/30">
                        <TableHead className="w-[28%] py-3">{t('executors.table.name')}</TableHead>
                        <TableHead className="w-[30%] py-3">{t('executors.table.target')}</TableHead>
                        <TableHead className="w-[20%] py-3">{t('executors.table.tracker')}</TableHead>
                        <TableHead className="w-[17%] py-3">{t('executors.table.status')}</TableHead>
                        <TableHead className="w-[5%] py-3 text-right">{t('executors.table.actions')}</TableHead>
                    </TableRow>
                </TableHeader>
                <TableBody>
                    {loading ? (
                        <TableRow>
                            <TableCell colSpan={5} className="h-24 text-center">
                                {t('common.loading')}
                            </TableCell>
                        </TableRow>
                    ) : executors.length === 0 ? (
                        <TableRow>
                            <TableCell colSpan={5} className="h-24 text-center">
                                {t('common.noData')}
                            </TableCell>
                        </TableRow>
                    ) : (
                        executors.map((executor) => {
                            const targetDisplay = buildExecutorTargetDisplay(executor.runtime_type, executor.target_ref, t)
                            const lastResult = executor.status?.last_result ?? null
                            const lastRunAt = executor.status?.last_run_at ?? null
                            const serviceBindings = executor.service_bindings ?? []
                            const serviceBindingSummary = buildServiceBindingSummary(serviceBindings)
                            const targetKindLabel = targetDisplay.badges.find((badge) => badge !== executor.runtime_type) ?? targetDisplay.badges[0]
                            const referenceLabel = isHelmReleaseTarget(executor.target_ref)
                                ? t("executors.referenceModes.chart")
                                : executor.image_reference_mode?.toUpperCase()

                            return (
                                <TableRow
                                    key={executor.id ?? executor.name}
                                    className="cursor-pointer transition-colors hover:bg-muted/40 data-[selected=true]:bg-primary/5"
                                    data-selected={executor.id === selectedExecutorId}
                                    onClick={() => executor.id && onViewExecutionHistory(executor.id)}
                                >
                                    <TableCell className="py-3 align-top">
                                        <div className="min-w-0 space-y-2">
                                            <div className="min-w-0 space-y-1">
                                                <div className="truncate text-sm font-semibold text-foreground">{executor.name}</div>
                                                {executor.description ? (
                                                    <div className="max-w-[320px] truncate text-xs text-muted-foreground">
                                                        {executor.description}
                                                    </div>
                                                ) : null}
                                            </div>
                                            <div className="max-w-[300px] truncate text-xs text-muted-foreground">
                                                <span className="uppercase tracking-[0.12em]">{executor.runtime_type}</span>
                                                <span className="px-1.5">·</span>
                                                <span>{executor.runtime_connection_name || '-'}</span>
                                            </div>
                                        </div>
                                    </TableCell>
                                    <TableCell className="py-3 align-top">
                                        <div className="max-w-[380px] min-w-0 space-y-1">
                                            <div className="break-words text-sm font-semibold text-foreground">{targetDisplay.title}</div>
                                            {targetDisplay.subtitle ? (
                                                <div className="mt-0.5 break-words font-mono text-[11px] text-muted-foreground">
                                                    {targetDisplay.subtitle}
                                                </div>
                                            ) : null}
                                            <div className="break-words text-xs text-muted-foreground">{targetDisplay.summary}</div>
                                            {targetKindLabel ? (
                                                <div className="text-[11px] uppercase tracking-[0.12em] text-muted-foreground/80">
                                                    {targetKindLabel}
                                                </div>
                                            ) : null}
                                        </div>
                                    </TableCell>
                                    <TableCell className="py-3 align-top">
                                        <div className="min-w-0 space-y-2">
                                            <div className="min-w-0 space-y-1">
                                                <div className="truncate text-sm font-medium text-foreground">{executor.tracker_name}</div>
                                                <div className="text-xs text-muted-foreground">{getChannelLabel(executor.channel_name)}</div>
                                            </div>
                                            {serviceBindingSummary ? (
                                                <div className="max-w-[280px] truncate text-xs text-muted-foreground">
                                                    {t("executors.target.details.services")}: {serviceBindingSummary}
                                                </div>
                                            ) : null}
                                        </div>
                                    </TableCell>
                                    <TableCell className="py-3 align-top">
                                        <div className="space-y-1.5">
                                                <div className="flex flex-wrap items-center gap-1.5">
                                                {executor.invalid_config_error ? (
                                                    <Badge variant="destructive">
                                                        {t("executors.status.invalid")}
                                                    </Badge>
                                                ) : null}
                                                <Badge variant={lastResult ? statusVariantMap[lastResult] : 'outline'}>
                                                    {lastResult ? t(`executors.results.${lastResult}`) : t('executors.results.idle')}
                                                </Badge>
                                                <span className="text-xs text-muted-foreground">
                                                    {executor.enabled ? t('common.enabled') : t('common.disabled')}
                                                </span>
                                            </div>
                                            <div className="text-xs text-muted-foreground">
                                                <span className="capitalize">{t(`executors.modes.${executor.update_mode}`)}</span>
                                                {referenceLabel ? (
                                                    <>
                                                        <span className="px-1.5">·</span>
                                                        <span className="font-mono uppercase">{referenceLabel}</span>
                                                    </>
                                                ) : null}
                                            </div>
                                            <div className="text-xs text-muted-foreground">
                                                {lastRunAt
                                                    ? formatDistanceToNow(new Date(lastRunAt), {
                                                        addSuffix: true,
                                                        locale: i18n.language === 'zh' ? zhCN : enUS,
                                                    })
                                                    : t('common.never')}
                                            </div>
                                            {executor.invalid_config_error ? (
                                                <div className="max-w-[260px] break-words text-xs text-destructive">
                                                    {executor.invalid_config_error}
                                                </div>
                                            ) : null}
                                        </div>
                                    </TableCell>
                                    <TableCell className="py-3 text-right align-top" onClick={stopRowClickPropagation}>
                                        <DropdownMenu>
                                            <DropdownMenuTrigger asChild>
                                                <Button variant="ghost" className="h-8 w-8 p-0" onClick={stopRowClickPropagation}>
                                                    <span className="sr-only">Open menu</span>
                                                    <MoreHorizontal className="h-4 w-4" />
                                                </Button>
                                            </DropdownMenuTrigger>
                                            <DropdownMenuContent align="end">
                                                <DropdownMenuItem onClick={(event) => {
                                                    stopRowClickPropagation(event)
                                                    if (executor.id) {
                                                        onViewExecutionHistory(executor.id)
                                                    }
                                                }}>
                                                    <History className="mr-2 h-4 w-4" /> {t('executors.actions.viewExecutionHistory')}
                                                </DropdownMenuItem>
                                                <DropdownMenuItem onClick={(event) => {
                                                    stopRowClickPropagation(event)
                                                    if (executor.id) {
                                                        onRun(executor.id)
                                                    }
                                                }}>
                                                    <Play className="mr-2 h-4 w-4" /> {t('executors.actions.runNow')}
                                                </DropdownMenuItem>
                                                <DropdownMenuItem onClick={(event) => {
                                                    stopRowClickPropagation(event)
                                                    if (executor.id) {
                                                        onEdit(executor.id)
                                                    }
                                                }}>
                                                    <Edit className="mr-2 h-4 w-4" /> {t('common.edit')}
                                                </DropdownMenuItem>
                                                <DropdownMenuSeparator />
                                                <DropdownMenuItem
                                                    className="text-destructive focus:text-destructive"
                                                    onClick={(event) => {
                                                        stopRowClickPropagation(event)
                                                        if (executor.id) {
                                                            onDelete(executor.id)
                                                        }
                                                    }}
                                                >
                                                    <Trash2 className="mr-2 h-4 w-4" /> {t('common.delete')}
                                                </DropdownMenuItem>
                                            </DropdownMenuContent>
                                        </DropdownMenu>
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

function buildServiceBindingSummary(serviceBindings: NonNullable<ExecutorListItem["service_bindings"]>): string | null {
    if (serviceBindings.length === 0) {
        return null
    }

    const visibleServices = serviceBindings.slice(0, 2).map((binding) => binding.service).filter(Boolean)
    const overflowCount = serviceBindings.length - visibleServices.length
    const visibleSummary = visibleServices.join(", ")

    if (!visibleSummary) {
        return String(serviceBindings.length)
    }

    return overflowCount > 0 ? `${visibleSummary} +${overflowCount}` : visibleSummary
}
