import { CircleCheck, CircleSlash, Edit, MoreHorizontal, Trash2 } from "lucide-react"
import { useTranslation } from "react-i18next"

import type { RuntimeConnection } from "@/api/types"
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
import { cn } from "@/lib/utils"
import { buildConnectionSummary } from "./runtimeConnectionHelpers"

interface RuntimeConnectionListProps {
    runtimeConnections: RuntimeConnection[]
    loading: boolean
    onEdit: (runtimeConnection: RuntimeConnection) => void
    onDelete: (id: number) => void
}

export function RuntimeConnectionList({
    runtimeConnections,
    loading,
    onEdit,
    onDelete,
}: RuntimeConnectionListProps) {
    const { t } = useTranslation()

    return (
        <div className="min-h-0 flex-1 overflow-auto rounded-md border">
            <Table containerClassName="overflow-visible">
                <TableHeader className="sticky top-0 z-10 bg-background">
                    <TableRow>
                        <TableHead className="min-w-[14rem]">{t("runtimeConnections.table.name")}</TableHead>
                        <TableHead>{t("runtimeConnections.table.type")}</TableHead>
                        <TableHead className="min-w-[16rem]">{t("runtimeConnections.table.endpoint")}</TableHead>
                        <TableHead className="hidden md:table-cell">
                            {t("runtimeConnections.table.secrets")}
                        </TableHead>
                        <TableHead>{t("runtimeConnections.table.status")}</TableHead>
                        <TableHead className="w-[1%] text-right">
                            {t("runtimeConnections.table.actions")}
                        </TableHead>
                    </TableRow>
                </TableHeader>
                <TableBody>
                    {loading ? (
                        <TableRow>
                            <TableCell colSpan={6} className="h-24 text-center text-sm text-muted-foreground">
                                {t("common.loading")}
                            </TableCell>
                        </TableRow>
                    ) : runtimeConnections.length === 0 ? (
                        <TableRow>
                            <TableCell colSpan={6} className="h-24 text-center text-sm text-muted-foreground">
                                {t("common.noData")}
                            </TableCell>
                        </TableRow>
                    ) : (
                        runtimeConnections.map((runtimeConnection) => {
                            const connectionSummary = buildConnectionSummary(runtimeConnection)
                            const credentialName = runtimeConnection.credential_name

                            return (
                                <TableRow
                                    key={runtimeConnection.id}
                                    className="transition-colors hover:bg-muted/40"
                                >
                                    <TableCell className="py-3 align-middle">
                                        <div className="min-w-0 space-y-1">
                                            <div
                                                className="truncate font-medium text-foreground"
                                                title={runtimeConnection.name}
                                            >
                                                {runtimeConnection.name}
                                            </div>
                                            {runtimeConnection.description ? (
                                                <div
                                                    className="line-clamp-1 text-xs text-muted-foreground"
                                                    title={runtimeConnection.description}
                                                >
                                                    {runtimeConnection.description}
                                                </div>
                                            ) : null}
                                        </div>
                                    </TableCell>

                                    {/* Type — uppercase badge. */}
                                    <TableCell className="py-3 align-middle">
                                        <Badge
                                            variant="outline"
                                            className="border-border/60 bg-muted/30 px-1.5 text-[10px] font-medium uppercase"
                                        >
                                            {runtimeConnection.type}
                                        </Badge>
                                    </TableCell>

                                    {/* Endpoint summary. */}
                                    <TableCell className="py-3 align-middle">
                                        <div className="min-w-0 space-y-0.5">
                                            <div
                                                className="truncate text-sm text-foreground"
                                                title={connectionSummary.primary}
                                            >
                                                {connectionSummary.primary}
                                            </div>
                                            {connectionSummary.secondary ? (
                                                <div
                                                    className="truncate text-xs text-muted-foreground"
                                                    title={connectionSummary.secondary}
                                                >
                                                    {connectionSummary.secondary}
                                                </div>
                                            ) : null}
                                        </div>
                                    </TableCell>

                                    {/* Credential reference. */}
                                    <TableCell className="hidden py-3 align-middle md:table-cell">
                                        {credentialName ? (
                                            <Badge
                                                variant="secondary"
                                                className="gap-1 px-1.5 font-normal"
                                            >
                                                <span className="text-[10px] uppercase tracking-wide text-muted-foreground">
                                                    credential
                                                </span>
                                                <span className="max-w-[12rem] truncate font-mono text-xs text-foreground/90">
                                                    {credentialName}
                                                </span>
                                            </Badge>
                                        ) : (
                                            <span className="text-xs text-muted-foreground">—</span>
                                        )}
                                    </TableCell>

                                    {/* Enabled state. */}
                                    <TableCell className="py-3 align-middle">
                                        <div
                                            className={cn(
                                                "flex items-center gap-1.5 text-xs",
                                                runtimeConnection.enabled
                                                    ? "text-foreground"
                                                    : "text-muted-foreground",
                                            )}
                                        >
                                            {runtimeConnection.enabled ? (
                                                <CircleCheck className="h-4 w-4 text-primary" />
                                            ) : (
                                                <CircleSlash className="h-4 w-4 text-muted-foreground/60" />
                                            )}
                                            <span>
                                                {runtimeConnection.enabled
                                                    ? t("common.enabled")
                                                    : t("common.disabled")}
                                            </span>
                                        </div>
                                    </TableCell>

                                    {/* Actions. */}
                                    <TableCell className="w-[1%] whitespace-nowrap py-3 text-right align-middle">
                                        <DropdownMenu>
                                            <DropdownMenuTrigger asChild>
                                                <Button variant="ghost" size="icon" className="h-7 w-7">
                                                    <MoreHorizontal className="h-3.5 w-3.5" />
                                                    <span className="sr-only">{t("common.actions")}</span>
                                                </Button>
                                            </DropdownMenuTrigger>
                                            <DropdownMenuContent align="end">
                                                <DropdownMenuItem onClick={() => onEdit(runtimeConnection)}>
                                                    <Edit className="mr-2 h-4 w-4" />
                                                    {t("common.edit")}
                                                </DropdownMenuItem>
                                                <DropdownMenuSeparator />
                                                <DropdownMenuItem
                                                    className="text-destructive focus:text-destructive"
                                                    onClick={() => onDelete(runtimeConnection.id)}
                                                >
                                                    <Trash2 className="mr-2 h-4 w-4" />
                                                    {t("common.delete")}
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
