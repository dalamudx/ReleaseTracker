import { Edit, MoreHorizontal, Trash2 } from "lucide-react"
import { useTranslation } from "react-i18next"

import type { RuntimeConnection } from "@/api/types"
import { Badge } from "@/components/ui/badge"
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
import { buildConnectionSummary } from "./runtimeConnectionHelpers"

interface RuntimeConnectionListProps {
    runtimeConnections: RuntimeConnection[]
    loading: boolean
    onEdit: (runtimeConnection: RuntimeConnection) => void
    onDelete: (id: number) => void
}

export function RuntimeConnectionList({ runtimeConnections, loading, onEdit, onDelete }: RuntimeConnectionListProps) {
    const { t } = useTranslation()

    return (
        <div className="min-h-0 flex-1 overflow-auto rounded-md border">
            <Table containerClassName="overflow-visible">
                <TableHeader className="sticky top-0 z-10 bg-background">
                    <TableRow>
                        <TableHead>{t('runtimeConnections.table.name')}</TableHead>
                        <TableHead>{t('runtimeConnections.table.type')}</TableHead>
                        <TableHead>{t('runtimeConnections.table.endpoint')}</TableHead>
                        <TableHead>{t('runtimeConnections.table.secrets')}</TableHead>
                        <TableHead>{t('runtimeConnections.table.status')}</TableHead>
                        <TableHead className="text-right">{t('runtimeConnections.table.actions')}</TableHead>
                    </TableRow>
                </TableHeader>
                <TableBody>
                    {loading ? (
                        <TableRow>
                            <TableCell colSpan={6} className="h-24 text-center">
                                {t('common.loading')}
                            </TableCell>
                        </TableRow>
                    ) : runtimeConnections.length === 0 ? (
                        <TableRow>
                            <TableCell colSpan={6} className="h-24 text-center">
                                {t('common.noData')}
                            </TableCell>
                        </TableRow>
                    ) : (
                        runtimeConnections.map((runtimeConnection) => {
                            const secretEntries = buildSecretEntries(runtimeConnection)
                            const connectionSummary = buildConnectionSummary(runtimeConnection)

                            return (
                                <TableRow key={runtimeConnection.id} className="hover:bg-muted/50 transition-colors">
                                    <TableCell className="py-2.5 align-middle">
                                        <div className="space-y-1">
                                            <div className="font-medium">{runtimeConnection.name}</div>
                                            {runtimeConnection.description ? (
                                                <div className="max-w-[260px] truncate text-xs text-muted-foreground">
                                                    {runtimeConnection.description}
                                                </div>
                                            ) : null}
                                        </div>
                                    </TableCell>
                                    <TableCell className="py-2.5 align-middle">
                                        <Badge variant="outline" className="uppercase text-[10px]">
                                            {runtimeConnection.type}
                                        </Badge>
                                    </TableCell>
                                    <TableCell className="py-2.5 align-middle">
                                        <div className="space-y-1">
                                            <div className="text-sm text-foreground">{connectionSummary.primary}</div>
                                            {connectionSummary.secondary ? (
                                                <div className="text-xs text-muted-foreground">{connectionSummary.secondary}</div>
                                            ) : null}
                                        </div>
                                    </TableCell>
                                    <TableCell className="py-2.5 align-middle">
                                        <div className="flex max-w-[260px] flex-wrap gap-1.5">
                                            {secretEntries.length > 0 ? secretEntries.map(([key, value]) => (
                                                <Badge key={key} variant="secondary" className="gap-1 font-normal">
                                                    <span className="text-[10px] uppercase tracking-[0.12em]">{key}</span>
                                                    <span className="font-mono text-xs">{value}</span>
                                                </Badge>
                                            )) : (
                                                <span className="text-sm text-muted-foreground">—</span>
                                            )}
                                        </div>
                                    </TableCell>
                                    <TableCell className="py-2.5 align-middle">
                                        <Badge variant={runtimeConnection.enabled ? 'default' : 'secondary'}>
                                            {runtimeConnection.enabled ? t('common.enabled') : t('common.disabled')}
                                        </Badge>
                                    </TableCell>
                                    <TableCell className="py-2.5 text-right align-middle">
                                        <DropdownMenu>
                                            <DropdownMenuTrigger asChild>
                                                <Button variant="ghost" className="h-8 w-8 p-0">
                                                    <span className="sr-only">Open menu</span>
                                                    <MoreHorizontal className="h-4 w-4" />
                                                </Button>
                                            </DropdownMenuTrigger>
                                            <DropdownMenuContent align="end">
                                                <DropdownMenuLabel>{t('common.actions')}</DropdownMenuLabel>
                                                <DropdownMenuItem onClick={() => onEdit(runtimeConnection)}>
                                                    <Edit className="mr-2 h-4 w-4" /> {t('common.edit')}
                                                </DropdownMenuItem>
                                                <DropdownMenuSeparator />
                                                <DropdownMenuItem
                                                    className="text-destructive focus:text-destructive"
                                                    onClick={() => onDelete(runtimeConnection.id)}
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

function buildSecretEntries(runtimeConnection: RuntimeConnection): Array<[string, string]> {
    return runtimeConnection.credential_name ? [["credential", runtimeConnection.credential_name]] : []
}
