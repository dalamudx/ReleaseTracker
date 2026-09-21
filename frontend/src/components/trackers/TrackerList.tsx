import { CircleCheck, CircleX, Edit, MoreHorizontal, Play, Trash2 } from "lucide-react"
import { useTranslation } from "react-i18next"

import type { TrackerStatus } from "@/api/types"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { ActiveRowMarker } from "@/components/common/ActiveRowMarker"
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
import { cn } from "@/lib/utils"
import {
    formatChannelSummary,
    getTrackerError,
    getTrackerLastVersion,
} from "./trackerListHelpers"

interface TrackerListProps {
    trackers: TrackerStatus[]
    loading: boolean
    selectedTrackerName: string | null
    onSelect: (name: string) => void
    onEdit: (name: string) => void
    onDelete: (name: string) => void
    onCheck: (name: string) => void
}

export function TrackerList({
    trackers,
    loading,
    selectedTrackerName,
    onSelect,
    onEdit,
    onDelete,
    onCheck,
}: TrackerListProps) {
    const { t } = useTranslation()

    const stopRowClick = (event: React.MouseEvent) => event.stopPropagation()

    return (
        <div className="min-h-0 flex-1 overflow-auto rounded-lg border border-border/60 bg-card/40 shadow-xs">
            <Table containerClassName="overflow-visible">
                <TableHeader className="sticky top-0 z-10 bg-background/95 backdrop-blur-sm">
                    <TableRow className="hover:bg-transparent">
                        <TableHead className="min-w-[10rem]">{t("trackers.table.name")}</TableHead>
                        <TableHead className="w-20">{t("trackers.table.status")}</TableHead>
                        <TableHead className="hidden sm:table-cell">{t("trackers.table.lastVersion")}</TableHead>
                        <TableHead className="w-[1%] text-right">{t("trackers.table.actions")}</TableHead>
                    </TableRow>
                </TableHeader>
                <TableBody>
                    {loading ? (
                        <TableRow>
                            <TableCell colSpan={4} className="h-24 text-center text-sm text-muted-foreground">
                                {t("common.loading")}
                            </TableCell>
                        </TableRow>
                    ) : trackers.length === 0 ? (
                        <TableRow>
                            <TableCell colSpan={4} className="h-24 text-center text-sm text-muted-foreground">
                                {t("common.noData")}
                            </TableCell>
                        </TableRow>
                    ) : (
                        trackers.map((tracker) => {
                            const channelTypes = formatChannelSummary(tracker)
                            const trackerError = getTrackerError(tracker)
                            const trackerLastVersion = getTrackerLastVersion(tracker)
                            const isSelected = selectedTrackerName === tracker.name

                            return (
                                <TableRow
                                    key={tracker.name}
                                    data-selected={isSelected || undefined}
                                    className={cn(
                                        "relative cursor-pointer transition-colors hover:bg-muted/40 focus-visible:z-10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring",
                                        isSelected && "bg-primary/[0.08] hover:bg-primary/[0.12] dark:bg-primary/15 dark:hover:bg-primary/20",
                                    )}
                                    onClick={() => onSelect(tracker.name)}
                                    onKeyDown={(event) => {
                                        if (event.currentTarget !== event.target) return
                                        if (event.key === "Enter" || event.key === " ") {
                                            event.preventDefault()
                                            onSelect(tracker.name)
                                        }
                                    }}
                                    role="button"
                                    tabIndex={0}
                                    aria-pressed={isSelected}
                                >
                                    <TableCell className="relative py-2.5 align-middle">
                                        <ActiveRowMarker active={isSelected} />
                                        <div className="space-y-0.5 pl-1.5 min-w-0">
                                            <div className="flex items-center gap-1.5">
                                                <span className="truncate font-semibold text-sm text-foreground">{tracker.name}</span>
                                                {channelTypes.length > 0 && (
                                                    <div className="flex items-center gap-1 shrink-0">
                                                        {channelTypes.map((channelType) => (
                                                            <Badge
                                                                key={channelType}
                                                                variant="secondary"
                                                                className="h-4 rounded px-1 text-[9px] font-medium leading-none"
                                                            >
                                                                {t(`trackers.aggregate.detail.channelType.${channelType}`)}
                                                            </Badge>
                                                        ))}
                                                    </div>
                                                )}
                                            </div>
                                            {tracker.description ? (
                                                <div className="line-clamp-1 text-[11px] text-muted-foreground">
                                                    {tracker.description}
                                                </div>
                                            ) : null}
                                        </div>
                                    </TableCell>

                                    <TableCell className="py-2.5 align-middle">
                                        {trackerError ? (
                                            <Tooltip>
                                                <TooltipTrigger asChild>
                                                    <div className="flex items-center gap-1 text-destructive">
                                                        <CircleX className="h-3.5 w-3.5 shrink-0" />
                                                        <span className="text-[11px] font-medium">{t("trackers.status.error")}</span>
                                                    </div>
                                                </TooltipTrigger>
                                                <TooltipContent>
                                                    <p className="max-w-[320px] break-words text-xs">{trackerError}</p>
                                                </TooltipContent>
                                            </Tooltip>
                                        ) : (
                                            <div className="flex items-center gap-1">
                                                <CircleCheck
                                                    className={cn(
                                                        "h-3.5 w-3.5 shrink-0",
                                                        tracker.enabled ? "text-success" : "text-muted-foreground/60",
                                                    )}
                                                />
                                                <span className="text-[11px] text-muted-foreground">
                                                    {tracker.enabled ? t("trackers.status.enabled") : t("trackers.status.disabled")}
                                                </span>
                                            </div>
                                        )}
                                    </TableCell>

                                    <TableCell className="hidden py-2.5 align-middle font-mono text-xs sm:table-cell">
                                        {trackerLastVersion ? (
                                            <span className="max-w-[12rem] truncate text-foreground/90 font-medium inline-block rounded bg-muted/40 px-1.5 py-0.5 border border-border/50" title={trackerLastVersion}>
                                                {trackerLastVersion}
                                            </span>
                                        ) : (
                                            <span className="text-xs text-muted-foreground">—</span>
                                        )}
                                    </TableCell>

                                    <TableCell
                                        className="w-[1%] whitespace-nowrap py-3 text-right align-middle"
                                        onClick={stopRowClick}
                                    >
                                        <div className="flex items-center justify-end gap-0.5">
                                            <Tooltip>
                                                <TooltipTrigger asChild>
                                                    <Button
                                                        variant="ghost"
                                                        size="icon"
                                                        className="h-7 w-7"
                                                        onClick={(event) => {
                                                            stopRowClick(event)
                                                            onCheck(tracker.name)
                                                        }}
                                                    >
                                                        <Play className="h-3.5 w-3.5" />
                                                        <span className="sr-only">{t("common.check")}</span>
                                                    </Button>
                                                </TooltipTrigger>
                                                <TooltipContent>{t("common.check")}</TooltipContent>
                                            </Tooltip>
                                            <DropdownMenu>
                                                <DropdownMenuTrigger asChild onClick={stopRowClick}>
                                                    <Button variant="ghost" size="icon" className="h-7 w-7">
                                                        <MoreHorizontal className="h-3.5 w-3.5" />
                                                        <span className="sr-only">{t("common.actions")}</span>
                                                    </Button>
                                                </DropdownMenuTrigger>
                                                <DropdownMenuContent align="end">
                                                    <DropdownMenuItem onClick={() => onEdit(tracker.name)}>
                                                        <Edit className="mr-2 h-4 w-4" /> {t("common.edit")}
                                                    </DropdownMenuItem>
                                                    <DropdownMenuSeparator />
                                                    <DropdownMenuItem
                                                        className="text-destructive focus:text-destructive"
                                                        onClick={() => onDelete(tracker.name)}
                                                    >
                                                        <Trash2 className="mr-2 h-4 w-4" /> {t("common.delete")}
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
