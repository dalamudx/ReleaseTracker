import { CircleCheck, CircleX, Edit, MoreHorizontal, Play, Trash2 } from "lucide-react"
import { formatDistanceToNow } from "date-fns"
import { enUS, zhCN } from "date-fns/locale"
import { useTranslation } from "react-i18next"

import type { TrackerStatus } from "@/api/types"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
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
    getTrackerLastCheck,
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
    const { t, i18n } = useTranslation()
    const dateLocale = i18n?.language === "zh" ? zhCN : enUS

    const stopRowClick = (event: React.MouseEvent) => event.stopPropagation()

    return (
        <div className="min-h-0 flex-1 overflow-auto rounded-md border">
            <Table containerClassName="overflow-visible">
                <TableHeader className="sticky top-0 z-10 bg-background">
                    <TableRow>
                        <TableHead className="min-w-[14rem]">{t("trackers.table.name")}</TableHead>
                        <TableHead>{t("trackers.aggregate.table.sources")}</TableHead>
                        <TableHead>{t("trackers.table.status")}</TableHead>
                        <TableHead className="hidden md:table-cell">{t("trackers.table.lastVersion")}</TableHead>
                        <TableHead className="hidden lg:table-cell">{t("trackers.table.lastCheck")}</TableHead>
                        <TableHead className="w-[1%] text-right">{t("trackers.table.actions")}</TableHead>
                    </TableRow>
                </TableHeader>
                <TableBody>
                    {loading ? (
                        <TableRow>
                            <TableCell colSpan={6} className="h-24 text-center text-sm text-muted-foreground">
                                {t("common.loading")}
                            </TableCell>
                        </TableRow>
                    ) : trackers.length === 0 ? (
                        <TableRow>
                            <TableCell colSpan={6} className="h-24 text-center text-sm text-muted-foreground">
                                {t("common.noData")}
                            </TableCell>
                        </TableRow>
                    ) : (
                        trackers.map((tracker) => {
                            const channelTypes = formatChannelSummary(tracker)
                            const trackerError = getTrackerError(tracker)
                            const trackerLastVersion = getTrackerLastVersion(tracker)
                            const trackerLastCheck = getTrackerLastCheck(tracker)
                            const isSelected = selectedTrackerName === tracker.name

                            return (
                                <TableRow
                                    key={tracker.name}
                                    data-selected={isSelected || undefined}
                                    className={cn(
                                        "relative cursor-pointer transition-colors hover:bg-muted/40",
                                        isSelected && "bg-primary/5 hover:bg-primary/10",
                                    )}
                                    onClick={() => onSelect(tracker.name)}
                                >
                                    <TableCell className="relative py-3 align-middle">
                                        {isSelected ? (
                                            <span
                                                aria-hidden
                                                className="absolute left-0 top-1/2 h-8 w-[3px] -translate-y-1/2 rounded-r-full bg-primary"
                                            />
                                        ) : null}
                                        <div className="space-y-1 pl-1">
                                            <div className="font-medium text-foreground">{tracker.name}</div>
                                            {tracker.description ? (
                                                <div className="line-clamp-2 text-xs text-muted-foreground">
                                                    {tracker.description}
                                                </div>
                                            ) : null}
                                        </div>
                                    </TableCell>

                                    <TableCell className="py-3 align-middle">
                                        {channelTypes.length > 0 ? (
                                            <div className="flex flex-wrap gap-1">
                                                {channelTypes.map((channelType) => (
                                                    <Badge
                                                        key={channelType}
                                                        variant="outline"
                                                        className="border-border/60 bg-muted/30 px-1.5 text-[10px] font-medium"
                                                    >
                                                        {t(`trackers.aggregate.detail.channelType.${channelType}`)}
                                                    </Badge>
                                                ))}
                                            </div>
                                        ) : (
                                            <span className="text-xs text-muted-foreground">—</span>
                                        )}
                                    </TableCell>

                                    <TableCell className="py-3 align-middle">
                                        {trackerError ? (
                                            <Tooltip>
                                                <TooltipTrigger asChild>
                                                    <div className="flex items-center gap-1.5 text-destructive">
                                                        <CircleX className="h-4 w-4" />
                                                        <span className="text-xs font-medium">{t("trackers.status.error")}</span>
                                                    </div>
                                                </TooltipTrigger>
                                                <TooltipContent>
                                                    <p className="max-w-[320px] break-words text-xs">{trackerError}</p>
                                                </TooltipContent>
                                            </Tooltip>
                                        ) : (
                                            <div className="flex items-center gap-1.5">
                                                <CircleCheck
                                                    className={cn(
                                                        "h-4 w-4",
                                                        tracker.enabled ? "text-primary" : "text-muted-foreground/60",
                                                    )}
                                                />
                                                <span className="text-xs text-muted-foreground">
                                                    {tracker.enabled ? t("trackers.status.enabled") : t("trackers.status.disabled")}
                                                </span>
                                            </div>
                                        )}
                                    </TableCell>

                                    <TableCell className="hidden py-3 align-middle font-mono text-sm md:table-cell">
                                        {trackerLastVersion ? (
                                            <span className="max-w-[10rem] truncate text-foreground/80" title={trackerLastVersion}>
                                                {trackerLastVersion}
                                            </span>
                                        ) : (
                                            <span className="text-xs text-muted-foreground">—</span>
                                        )}
                                    </TableCell>

                                    <TableCell className="hidden py-3 align-middle text-xs text-muted-foreground lg:table-cell">
                                        {trackerLastCheck ? (
                                            <span
                                                className="whitespace-nowrap tabular-nums"
                                                title={new Date(trackerLastCheck).toLocaleString()}
                                            >
                                                {formatDistanceToNow(new Date(trackerLastCheck), {
                                                    addSuffix: true,
                                                    locale: dateLocale,
                                                })}
                                            </span>
                                        ) : (
                                            t("common.never")
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
