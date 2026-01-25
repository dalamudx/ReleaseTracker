import { MoreHorizontal, Edit, Trash2 } from "lucide-react"
import { useTranslation } from "react-i18next"
import { useDateFormatter } from "@/hooks/use-date-formatter"

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
import type { ApiCredential } from "@/api/types"

interface CredentialListProps {
    credentials: ApiCredential[]
    loading: boolean
    onEdit: (cred: ApiCredential) => void
    onDelete: (id: number) => void
}

export function CredentialList({ credentials, loading, onEdit, onDelete }: CredentialListProps) {
    const { t } = useTranslation()
    const formatDate = useDateFormatter()

    return (
        <div className="rounded-md border overflow-auto max-h-[calc(100vh-16rem)]">
            <table className="w-full caption-bottom text-sm">
                <TableHeader className="sticky top-0 bg-background z-10">
                    <TableRow>
                        <TableHead>{t('credentials.table.name')}</TableHead>
                        <TableHead>{t('credentials.table.type')}</TableHead>
                        <TableHead>{t('credentials.table.description')}</TableHead>
                        <TableHead>{t('credentials.table.createdAt')}</TableHead>
                        <TableHead className="text-right">{t('credentials.table.actions')}</TableHead>
                    </TableRow>
                </TableHeader>
                <TableBody>
                    {loading ? (
                        <TableRow>
                            <TableCell colSpan={5} className="h-24 text-center">
                                {t('common.loading')}
                            </TableCell>
                        </TableRow>
                    ) : credentials.length === 0 ? (
                        <TableRow>
                            <TableCell colSpan={5} className="h-24 text-center">
                                {t('common.noData')}
                            </TableCell>
                        </TableRow>
                    ) : (
                        credentials.map((cred) => (
                            <TableRow key={cred.id} className="hover:bg-muted/50 transition-colors">
                                <TableCell className="font-medium">{cred.name}</TableCell>
                                <TableCell>
                                    <Badge variant="outline" className="uppercase text-[10px]">
                                        {cred.type}
                                    </Badge>
                                </TableCell>
                                <TableCell className="text-muted-foreground text-sm max-w-[300px] truncate">
                                    {cred.description || "-"}
                                </TableCell>
                                <TableCell className="text-muted-foreground text-sm">
                                    {formatDate(cred.created_at)}
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
                                            <DropdownMenuLabel>{t('credentials.table.actions')}</DropdownMenuLabel>
                                            <DropdownMenuItem onClick={() => onEdit(cred)}>
                                                <Edit className="mr-2 h-4 w-4" /> {t('common.edit')}
                                            </DropdownMenuItem>
                                            <DropdownMenuSeparator />
                                            <DropdownMenuItem onClick={() => onDelete(cred.id)} className="text-destructive focus:text-destructive">
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
