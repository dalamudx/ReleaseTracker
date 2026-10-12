import { Boxes, Edit, KeyRound, MoreHorizontal, Plug2, Trash2 } from "lucide-react"
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

// Runtime connections and trackers reuse the sidebar icons so the column reads at a glance.
function getCredentialReferenceGroups(cred: ApiCredential, t: ReturnType<typeof useTranslation>["t"]) {
    const counts = cred.reference_counts ?? { runtime_connections: cred.runtime_connections_count ?? 0 }
    return [
        { key: "runtime", Icon: Plug2, label: t("credentials.references.runtimeConnections"), count: counts.runtime_connections ?? 0 },
        {
            key: "trackers",
            Icon: Boxes,
            label: t("credentials.references.trackerSources"),
            count: (counts.aggregate_tracker_sources ?? 0) + (counts.trackers ?? 0),
        },
    ].filter((group) => group.count > 0)
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
                        <TableHead className="whitespace-nowrap">{t("credentials.table.references")}</TableHead>
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
                            const referenceGroups = getCredentialReferenceGroups(cred, t)
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
                                                <Badge variant="outline" className="mt-0.5 h-4 px-1.5 text-[9px] sm:hidden">
                                                    {getCredentialTypeLabel(t, cred.type)}
                                                </Badge>
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

                                    <TableCell className="py-3 align-middle">
                                        {referenceGroups.length > 0 ? (
                                            <div className="flex flex-wrap items-center gap-1.5">
                                                {referenceGroups.map(({ key, Icon, label, count }) => (
                                                    <span
                                                        key={key}
                                                        title={`${label}: ${count}`}
                                                        aria-label={`${label}: ${count}`}
                                                        className="inline-flex items-center gap-1 rounded-full border border-border/60 bg-muted/30 px-2 py-0.5 font-mono text-xs font-medium tabular-nums text-foreground"
                                                    >
                                                        <Icon className="h-3 w-3 text-muted-foreground" aria-hidden="true" />
                                                        {count}
                                                    </span>
                                                ))}
                                            </div>
                                        ) : (
                                            <span className="text-xs text-muted-foreground" title={t("credentials.table.noReferences")}>—</span>
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
