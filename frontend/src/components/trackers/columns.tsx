import type { ColumnDef } from "@tanstack/react-table"
import { formatDistanceToNow } from "date-fns"
import { zhCN, enUS } from "date-fns/locale"
import { CheckCircle2, XCircle } from "lucide-react"

import { Badge } from "@/components/ui/badge"
import { Checkbox } from "@/components/ui/checkbox"
import {
    Tooltip,
    TooltipContent,
    TooltipProvider,
    TooltipTrigger,
} from "@/components/ui/tooltip"
import { DataTableColumnHeader } from "@/components/ui/data-table/data-table-column-header"
import type { TrackerStatus } from "@/api/types"
import { DataTableRowActions } from "./data-table-row-actions"
import { getTrackerError, getTrackerLastCheck, getTrackerLastVersion } from "./trackerListHelpers"

type TrackerTableTranslator = (key: string) => string

interface TrackerTableI18n {
    language: string
}

export const getColumns = (
    t: TrackerTableTranslator,
    i18n: TrackerTableI18n,
    onEdit: (name: string) => void,
    onDelete: (name: string) => void,
    onCheck: (name: string) => void
): ColumnDef<TrackerStatus>[] => [
        {
            id: "select",
            header: ({ table }) => (
                <Checkbox
                    checked={
                        table.getIsAllPageRowsSelected() ||
                        (table.getIsSomePageRowsSelected() && "indeterminate")
                    }
                    onCheckedChange={(value) => table.toggleAllPageRowsSelected(!!value)}
                    aria-label="Select all"
                    className="translate-y-[2px]"
                />
            ),
            cell: ({ row }) => (
                <Checkbox
                    checked={row.getIsSelected()}
                    onCheckedChange={(value) => row.toggleSelected(!!value)}
                    aria-label="Select row"
                    className="translate-y-[2px]"
                />
            ),
            enableSorting: false,
            enableHiding: false,
        },
        {
            accessorKey: "name",
            header: ({ column }) => (
                <DataTableColumnHeader column={column} title={t('trackers.table.name')} />
            ),
            cell: ({ row }) => <div className="font-medium">{row.getValue("name")}</div>,
        },
        {
            accessorKey: "type",
            header: ({ column }) => (
                <DataTableColumnHeader column={column} title={t('trackers.table.type')} />
            ),
            cell: ({ row }) => (
                <Badge variant="outline" className="uppercase text-[10px]">
                    {row.getValue("type")}
                </Badge>
            ),
        },
        {
            accessorKey: "status",
            header: ({ column }) => (
                <DataTableColumnHeader column={column} title={t('trackers.table.status')} />
            ),
            cell: ({ row }) => {
                const tracker = row.original
                const trackerError = getTrackerError(tracker)
                return (
                    <div className="flex items-center">
                        {trackerError ? (
                            <TooltipProvider>
                                <Tooltip>
                                    <TooltipTrigger asChild>
                                        <div className="flex items-center gap-2 cursor-help text-destructive">
                                            <XCircle className="h-4 w-4" />
                                            <span className="text-xs font-medium">
                                                {t('trackers.status.error')}
                                            </span>
                                        </div>
                                    </TooltipTrigger>
                                    <TooltipContent>
                                        <p className="max-w-[300px] break-words text-xs">{trackerError}</p>
                                    </TooltipContent>
                                </Tooltip>
                            </TooltipProvider>
                        ) : (
                            <div className="flex items-center gap-2">
                                <CheckCircle2 className={`h-4 w-4 ${tracker.enabled ? 'text-green-500' : 'text-muted-foreground'}`} />
                                <span className="text-xs text-muted-foreground">
                                    {tracker.enabled ? t('trackers.status.enabled') : t('trackers.status.disabled')}
                                </span>
                            </div>
                        )}
                    </div>
                )
            },
        },
        {
            accessorKey: "channel_count",
            header: ({ column }) => (
                <DataTableColumnHeader column={column} title={t('trackers.table.channelCount')} />
            ),
            cell: ({ row }) => <div>{row.getValue("channel_count") ?? "-"}</div>,
        },
        {
            accessorKey: "last_version",
            header: ({ column }) => (
                <DataTableColumnHeader column={column} title={t('trackers.table.lastVersion')} />
            ),
            cell: ({ row }) => <div className="font-mono text-xs">{getTrackerLastVersion(row.original) || "-"}</div>,
        },
        {
            accessorKey: "last_check",
            header: ({ column }) => (
                <DataTableColumnHeader column={column} title={t('trackers.table.lastCheck')} />
            ),
            cell: ({ row }) => {
                const lastCheck = getTrackerLastCheck(row.original)
                return (
                    <div className="text-muted-foreground text-sm">
                        {lastCheck ? formatDistanceToNow(new Date(lastCheck), {
                            addSuffix: true,
                            locale: i18n.language === 'zh' ? zhCN : enUS
                        }) : t('common.never')}
                    </div>
                )
            },
        },
        {
            id: "actions",
            cell: ({ row }) => (
                <DataTableRowActions
                    row={row}
                    onEdit={onEdit}
                    onDelete={onDelete}
                    onCheck={onCheck}
                    t={t}
                />
            ),
        },
    ]
