import { MoreHorizontal, Play, Edit, Trash2, CheckCircle2, XCircle } from "lucide-react"
import { formatDistanceToNow } from "date-fns"
import { zhCN, enUS } from "date-fns/locale"
import { useTranslation } from "react-i18next"

import { Button } from "@/components/ui/button"
import {
    DropdownMenu,
    DropdownMenuContent,
    DropdownMenuItem,
    DropdownMenuLabel,
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
import { Badge } from "@/components/ui/badge"
import type { TrackerStatus } from "@/api/types"

interface TrackerListProps {
    trackers: TrackerStatus[]
    loading: boolean
    onEdit: (name: string) => void
    onDelete: (name: string) => void
    onCheck: (name: string) => void
}

export function TrackerList({ trackers, loading, onEdit, onDelete, onCheck }: TrackerListProps) {
    const { t, i18n } = useTranslation()

    return (
        <div className="rounded-md border overflow-auto max-h-[calc(100vh-16rem)]">
            <table className="w-full caption-bottom text-sm">
                <TableHeader className="sticky top-0 bg-background z-10">
                    <TableRow>
                        <TableHead>{t('trackers.table.name')}</TableHead>
                        <TableHead>{t('trackers.table.type')}</TableHead>
                        <TableHead>{t('trackers.table.status')}</TableHead>
                        <TableHead>Channels</TableHead>
                        <TableHead>{t('trackers.table.lastVersion')}</TableHead>
                        <TableHead>{t('trackers.table.lastCheck')}</TableHead>
                        <TableHead className="text-right">{t('trackers.table.actions')}</TableHead>
                    </TableRow>
                </TableHeader>
                <TableBody>
                    {loading ? (
                        <TableRow>
                            <TableCell colSpan={7} className="h-24 text-center">
                                {t('common.loading')}
                            </TableCell>
                        </TableRow>
                    ) : trackers.length === 0 ? (
                        <TableRow>
                            <TableCell colSpan={7} className="h-24 text-center">
                                {t('common.noData')}
                            </TableCell>
                        </TableRow>
                    ) : (
                        trackers.map((tracker) => (
                            <TableRow key={tracker.name} className="hover:bg-muted/50 transition-colors">
                                <TableCell className="font-medium">{tracker.name}</TableCell>
                                <TableCell>
                                    <Badge variant="outline" className="uppercase text-[10px]">
                                        {tracker.type}
                                    </Badge>
                                </TableCell>
                                <TableCell>
                                    <div className="flex items-center gap-2">
                                        {tracker.error ? (
                                            <XCircle className="h-4 w-4 text-destructive" />
                                        ) : (
                                            <CheckCircle2 className={`h-4 w-4 ${tracker.enabled ? 'text-green-500' : 'text-muted-foreground'}`} />
                                        )}
                                        <span className="text-xs text-muted-foreground">
                                            {tracker.error ? t('trackers.status.error') : (tracker.enabled ? t('trackers.status.enabled') : t('trackers.status.disabled'))}
                                        </span>
                                    </div>
                                </TableCell>
                                <TableCell>{tracker.channel_count ?? "-"}</TableCell>
                                <TableCell className="font-mono text-sm">{tracker.last_version || "-"}</TableCell>
                                <TableCell className="text-muted-foreground text-sm">
                                    {tracker.last_check ? formatDistanceToNow(new Date(tracker.last_check), { addSuffix: true, locale: i18n.language === 'zh' ? zhCN : enUS }) : "Never"}
                                </TableCell>
                                <TableCell className="text-right">
                                    <DropdownMenu>
                                        <DropdownMenuTrigger asChild>
                                            <Button variant="ghost" className="h-8 w-8 p-0">
                                                <span className="sr-only">Open menu</span>
                                                <MoreHorizontal className="h-4 w-4" />
                                            </Button>
                                        </DropdownMenuTrigger>
                                        <DropdownMenuContent align="end">
                                            <DropdownMenuLabel>{t('trackers.table.actions')}</DropdownMenuLabel>
                                            <DropdownMenuItem onClick={() => onCheck(tracker.name)}>
                                                <Play className="mr-2 h-4 w-4" /> Check Now
                                            </DropdownMenuItem>
                                            <DropdownMenuItem onClick={() => onEdit(tracker.name)}>
                                                <Edit className="mr-2 h-4 w-4" /> {t('common.edit')}
                                            </DropdownMenuItem>
                                            <DropdownMenuSeparator />
                                            <DropdownMenuItem onClick={() => onDelete(tracker.name)} className="text-destructive focus:text-destructive">
                                                <Trash2 className="mr-2 h-4 w-4" /> {t('common.delete')}
                                            </DropdownMenuItem>
                                        </DropdownMenuContent>
                                    </DropdownMenu>
                                </TableCell>
                            </TableRow>
                        ))
                    )}
                </TableBody>
            </table>
        </div>
    )
}
