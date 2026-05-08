import { Edit, MoreHorizontal, Trash2 } from "lucide-react"
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

export function CredentialList({ credentials, loading, onEdit, onDelete }: CredentialListProps) {
    const { t, i18n } = useTranslation()
    const dateLocale = i18n?.language === "zh" ? zhCN : enUS

    return (
        <div className="min-h-0 flex-1 overflow-auto rounded-md border">
            <Table containerClassName="overflow-visible">
                <TableHeader className="sticky top-0 z-10 bg-background">
                    <TableRow>
                        <TableHead className="min-w-[12rem]">{t("credentials.table.name")}</TableHead>
                        <TableHead>{t("credentials.table.type")}</TableHead>
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
                            <TableCell colSpan={5} className="h-24 text-center text-sm text-muted-foreground">
                                {t("common.loading")}
                            </TableCell>
                        </TableRow>
                    ) : credentials.length === 0 ? (
                        <TableRow>
                            <TableCell colSpan={5} className="h-24 text-center text-sm text-muted-foreground">
                                {t("common.noData")}
                            </TableCell>
                        </TableRow>
                    ) : (
                        credentials.map((cred) => {
                            return (
                                <TableRow key={cred.id} className="transition-colors hover:bg-muted/40">
                                    <TableCell className="py-3 align-middle font-medium">
                                        <span className="truncate" title={cred.name}>
                                            {cred.name}
                                        </span>
                                    </TableCell>

                                    <TableCell className="py-3 align-middle">
                                        <Badge
                                            variant="outline"
                                            className="border-border/60 bg-muted/30 px-1.5 text-[10px] font-medium"
                                        >
                                            {getCredentialTypeLabel(t, cred.type)}
                                        </Badge>
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
