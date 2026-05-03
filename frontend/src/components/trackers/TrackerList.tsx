import { CheckCircle2, Edit, MoreHorizontal, Play, Trash2, XCircle } from "lucide-react"
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
    TooltipProvider,
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

    return (
        <div className="min-h-0 flex-1 overflow-auto rounded-md border">
            <Table containerClassName="overflow-visible">
                <TableHeader className="sticky top-0 z-10 bg-background">
                    <TableRow>
                        <TableHead>{t('trackers.table.name')}</TableHead>
                        <TableHead>{t('trackers.aggregate.table.sources')}</TableHead>
                        <TableHead>{t('trackers.table.status')}</TableHead>
                        <TableHead>{t('trackers.table.channelCount')}</TableHead>
                        <TableHead>{t('trackers.table.lastVersion')}</TableHead>
                        <TableHead>{t('trackers.table.lastCheck')}</TableHead>
                        <TableHead className="text-right">{t('trackers.table.actions')}</TableHead>
                    </TableRow>
                </TableHeader>
                <TableBody>
                    {loading ? (
                        <TableRow>
                            <TableCell colSpan={7} className="h-24 text-center">{t('common.loading')}</TableCell>
                        </TableRow>
                    ) : trackers.length === 0 ? (
                        <TableRow>
                            <TableCell colSpan={7} className="h-24 text-center">{t('common.noData')}</TableCell>
                        </TableRow>
                    ) : (
                        trackers.map((tracker) => {
                            const channelTypes = formatChannelSummary(tracker)
                            const trackerError = getTrackerError(tracker)
                            const trackerLastVersion = getTrackerLastVersion(tracker)
                            const trackerLastCheck = getTrackerLastCheck(tracker)

                            return (
                                <TableRow
                                    key={tracker.name}
                                    className={cn(
                                        "cursor-pointer transition-colors hover:bg-muted/40",
                                        selectedTrackerName === tracker.name && "bg-accent/40",
                                    )}
                                    onClick={() => onSelect(tracker.name)}
                                >
                                    <TableCell className="py-3 align-middle">
                                        <div className="space-y-1">
                                            <div className="font-medium">{tracker.name}</div>
                                            {tracker.description ? (
                                                <div className="line-clamp-2 text-xs text-muted-foreground">{tracker.description}</div>
                                            ) : null}
                                        </div>
                                    </TableCell>

                                    <TableCell className="py-3 align-middle">
                                        {channelTypes.length > 0 ? (
                                            <div className="flex flex-wrap gap-1.5">
                                                {channelTypes.map((channelType) => (
                                                    <Badge key={channelType} variant="outline" className="text-[10px]">
                                                        {t(`trackers.aggregate.detail.channelType.${channelType}`)}
                                                    </Badge>
                                                ))}
                                            </div>
                                        ) : (
                                            <span className="text-muted-foreground">-</span>
                                        )}
                                    </TableCell>

                                    <TableCell className="py-3 align-middle">
                                        {trackerError ? (
                                            <TooltipProvider>
                                                <Tooltip>
                                                    <TooltipTrigger asChild>
                                                        <div className="flex items-center gap-2 text-destructive">
                                                            <XCircle className="h-4 w-4" />
                                                            <span className="text-xs font-medium">{t('trackers.status.error')}</span>
                                                        </div>
                                                    </TooltipTrigger>
                                                    <TooltipContent>
                                                        <p className="max-w-[320px] break-words text-xs">{trackerError}</p>
                                                    </TooltipContent>
                                                </Tooltip>
                                            </TooltipProvider>
                                        ) : (
                                            <div className="flex items-center gap-2">
                                                <CheckCircle2 className={cn("h-4 w-4", tracker.enabled ? "text-green-500" : "text-muted-foreground")} />
                                                <span className="text-xs text-muted-foreground">
                                                    {tracker.enabled ? t('trackers.status.enabled') : t('trackers.status.disabled')}
                                                </span>
                                            </div>
                                        )}
                                    </TableCell>

                                    <TableCell className="py-3 align-middle">{tracker.status.source_count ?? tracker.sources?.length ?? 0}</TableCell>
                                    <TableCell className="py-3 align-middle font-mono text-sm">{trackerLastVersion || "-"}</TableCell>
                                    <TableCell className="py-3 align-middle text-sm text-muted-foreground">
                                        {trackerLastCheck
                                            ? formatDistanceToNow(new Date(trackerLastCheck), {
                                                addSuffix: true,
                                                locale: i18n.language === 'zh' ? zhCN : enUS,
                                            })
                                            : t('common.never')}
                                    </TableCell>
                                    <TableCell className="py-3 text-right align-middle">
                                        <DropdownMenu>
                                            <DropdownMenuTrigger asChild onClick={(event) => event.stopPropagation()}>
                                                <Button variant="ghost" className="h-8 w-8 p-0">
                                                    <span className="sr-only">Open menu</span>
                                                    <MoreHorizontal className="h-4 w-4" />
                                                </Button>
                                            </DropdownMenuTrigger>
                                            <DropdownMenuContent align="end">
                                                <DropdownMenuItem onClick={() => onCheck(tracker.name)}>
                                                    <Play className="mr-2 h-4 w-4" /> {t('common.check')}
                                                </DropdownMenuItem>
                                                <DropdownMenuItem onClick={() => onEdit(tracker.name)}>
                                                    <Edit className="mr-2 h-4 w-4" /> {t('common.edit')}
                                                </DropdownMenuItem>
                                                <DropdownMenuSeparator />
                                                <DropdownMenuItem className="text-destructive focus:text-destructive" onClick={() => onDelete(tracker.name)}>
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
