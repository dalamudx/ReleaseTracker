import { Edit, KeyRound, MoreHorizontal, Server, ShieldCheck, Terminal, Trash2 } from "lucide-react"
import { useTranslation } from "react-i18next"
import { formatDistanceToNow } from "date-fns"
import { enUS, zhCN } from "date-fns/locale"

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
import type { ApiCredential } from "@/api/types"
import { getCredentialTypeLabel } from "./credentialTypeLabels"

interface CredentialListProps {
    credentials: ApiCredential[]
    loading: boolean
    onEdit: (cred: ApiCredential) => void
    onDelete: (id: number) => void
}

function getCredentialCategoryBadge(type: ApiCredential["type"], t: ReturnType<typeof useTranslation>["t"]) {
    if (type === "ssh") {
        return (
            <span className="inline-flex items-center gap-1 text-[10px] text-muted-foreground">
                <Terminal className="h-3 w-3 text-warning" />
                {t("credentials.categories.ssh")}
            </span>
        )
    }
    if (type.endsWith("_runtime")) {
        return (
            <span className="inline-flex items-center gap-1 text-[10px] text-muted-foreground">
                <Server className="h-3 w-3 text-info" />
                {t("credentials.categories.runtime")}
            </span>
        )
    }
    return (
        <span className="inline-flex items-center gap-1 text-[10px] text-muted-foreground">
            <ShieldCheck className="h-3 w-3 text-success" />
            {t("credentials.categories.tracker")}
        </span>
    )
}

export function CredentialList({ credentials, loading, onEdit, onDelete }: CredentialListProps) {
    const { t, i18n } = useTranslation()
    const dateLocale = i18n?.language === "zh" ? zhCN : enUS

    return (
        <div className="min-h-0 overflow-auto rounded-md border sm:flex-1">
            <Table containerClassName="overflow-visible">
                <TableHeader className="sticky top-0 z-10 bg-background">
                    <TableRow>
                        <TableHead className="min-w-[10rem]">{t("credentials.table.name")}</TableHead>
                        <TableHead className="hidden sm:table-cell">{t("credentials.table.type")}</TableHead>
                        <TableHead className="hidden text-center sm:table-cell">{t("credentials.table.runtimeConnections")}</TableHead>
                        <TableHead className="hidden md:table-cell">
                            {t("credentials.table.description")}
                        </TableHead>
                        <TableHead className="hidden lg:table-cell">
                            {t("credentials.table.createdAt")}
                        </TableHead>
                        <TableHead className="w-[1%] text-right">
                            {t("credentials.table.actions")}
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
                    ) : credentials.length === 0 ? (
                        <TableRow>
                            <TableCell colSpan={6} className="h-24 text-center text-sm text-muted-foreground">
                                {t("common.noData")}
                            </TableCell>
                        </TableRow>
                    ) : (
                        credentials.map((cred) => {
                            return (
                                <TableRow key={cred.id} className="transition-colors hover:bg-muted/40">
                                    <TableCell className="py-3 align-middle">
                                        <div className="flex items-center gap-2.5">
                                            <div className="flex h-7 w-7 shrink-0 items-center justify-center rounded-md border border-border/60 bg-muted/40 text-muted-foreground">
                                                <KeyRound className="h-3.5 w-3.5" />
                                            </div>
                                            <div className="min-w-0">
                                                <div className="truncate font-medium text-foreground" title={cred.name}>
                                                    {cred.name}
                                                </div>
                                                <div className="mt-0.5 flex flex-wrap items-center gap-1.5">
                                                    {getCredentialCategoryBadge(cred.type, t)}
                                                    <Badge variant="outline" className="h-4 px-1.5 text-[9px] sm:hidden">
                                                        {getCredentialTypeLabel(t, cred.type)}
                                                    </Badge>
                                                    {typeof cred.runtime_connections_count === "number" && cred.runtime_connections_count > 0 ? (
                                                        <span className="inline-flex items-center gap-1 text-[10px] text-info sm:hidden">
                                                            <Server className="size-3" aria-hidden="true" />
                                                            {t("credentials.runtimeConnectionsCount", { count: cred.runtime_connections_count })}
                                                        </span>
                                                    ) : null}
                                                </div>
                                            </div>
                                        </div>
                                    </TableCell>

                                    <TableCell className="hidden py-3 align-middle sm:table-cell">
                                        <Badge
                                            variant="outline"
                                            className="border-border/60 bg-muted/30 px-2 py-0.5 text-xs font-medium"
                                        >
                                            {getCredentialTypeLabel(t, cred.type)}
                                        </Badge>
                                    </TableCell>

                                    <TableCell className="hidden py-3 text-center align-middle sm:table-cell">
                                        {typeof cred.runtime_connections_count === "number" && cred.runtime_connections_count > 0 ? (
                                            <span className="inline-flex items-center gap-1 rounded-full border border-info/30 bg-info/10 px-2 py-0.5 font-mono text-xs font-medium text-info">
                                                <Server className="h-3 w-3" />
                                                {cred.runtime_connections_count}
                                            </span>
                                        ) : (
                                            <span className="text-xs text-muted-foreground tabular-nums">0</span>
                                        )}
                                    </TableCell>

                                    <TableCell className="hidden max-w-[360px] py-3 align-middle text-sm text-muted-foreground md:table-cell">
                                        {cred.description ? (
                                            <span className="line-clamp-1" title={cred.description}>
                                                {cred.description}
                                            </span>
                                        ) : (
                                            <span className="text-xs">—</span>
                                        )}
                                    </TableCell>

                                    <TableCell className="hidden py-3 align-middle text-xs text-muted-foreground lg:table-cell">
                                        {cred.created_at ? (
                                            <span
                                                className="whitespace-nowrap tabular-nums"
                                                title={new Date(cred.created_at).toLocaleString()}
                                            >
                                                {formatDistanceToNow(new Date(cred.created_at), {
                                                    addSuffix: true,
                                                    locale: dateLocale,
                                                })}
                                            </span>
                                        ) : (
                                            "—"
                                        )}
                                    </TableCell>

                                    <TableCell className="w-[1%] whitespace-nowrap py-3 text-right align-middle">
                                        <DropdownMenu>
                                            <DropdownMenuTrigger asChild>
                                                <Button variant="ghost" size="icon" className="h-7 w-7">
                                                    <MoreHorizontal className="h-3.5 w-3.5" />
                                                    <span className="sr-only">{t("common.actions")}</span>
                                                </Button>
                                            </DropdownMenuTrigger>
                                            <DropdownMenuContent align="end">
                                                <DropdownMenuItem onClick={() => onEdit(cred)}>
                                                    <Edit className="mr-2 h-4 w-4" />
                                                    {t("common.edit")}
                                                </DropdownMenuItem>
                                                <DropdownMenuSeparator />
                                                <DropdownMenuItem
                                                    onClick={() => onDelete(cred.id)}
                                                    className="text-destructive focus:text-destructive"
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
