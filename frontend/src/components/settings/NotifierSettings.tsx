import { useState, useEffect } from "react"
import { useTranslation } from "react-i18next"
import { Plus, MoreHorizontal, Edit, Trash2, Send, ChevronLeft, ChevronRight } from "lucide-react"
import { useForm } from "react-hook-form"

import { Button } from "@/components/ui/button"
import {
    Table,
    TableBody,
    TableCell,
    TableHead,
    TableHeader,
    TableRow,
} from "@/components/ui/table"
import {
    DropdownMenu,
    DropdownMenuContent,
    DropdownMenuItem,
    DropdownMenuLabel,
    DropdownMenuSeparator,
    DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import {
    Dialog,
    DialogContent,
    DialogDescription,
    DialogFooter,
    DialogHeader,
    DialogTitle,
} from "@/components/ui/dialog"
import {
    Form,
    FormControl,
    FormDescription,
    FormField,
    FormItem,
    FormLabel,
    FormMessage,
} from "@/components/ui/form"
import {
    Select,
    SelectContent,
    SelectItem,
    SelectTrigger,
    SelectValue,
} from "@/components/ui/select"
import { Input } from "@/components/ui/input"
import { Switch } from "@/components/ui/switch"
import { Badge } from "@/components/ui/badge"
import { Checkbox } from "@/components/ui/checkbox"

import { api } from "@/api/client"
import type { Notifier } from "@/api/types"
import { toast } from "sonner"

export function NotifierSettings() {
    const { t } = useTranslation()
    const [notifiers, setNotifiers] = useState<Notifier[]>([])
    const [loading, setLoading] = useState(false)
    const [dialogOpen, setDialogOpen] = useState(false)
    const [editingNotifier, setEditingNotifier] = useState<Notifier | null>(null)

    // Pagination state
    const [total, setTotal] = useState(0)
    const [page, setPage] = useState(1)
    const [pageSize, setPageSize] = useState(() => {
        const saved = localStorage.getItem('settings.notifiers.pageSize')
        return saved ? Number(saved) : 15
    })

    const loadNotifiers = async () => {
        setLoading(true)
        try {
            const skip = (page - 1) * pageSize
            const data = await api.getNotifiers({ skip, limit: pageSize })
            setNotifiers(data.items)
            setTotal(data.total)
        } catch (error) {
            console.error(error)
            toast.error(t('common.unexpectedError'))
        } finally {
            setLoading(false)
        }
    }

    useEffect(() => {
        loadNotifiers()
    }, [page, pageSize])

    const handleDelete = async (id: number) => {
        if (!window.confirm(t('common.confirm'))) return

        try {
            await api.deleteNotifier(id)
            setNotifiers(notifiers.filter(n => n.id !== id))
            setTotal(total - 1)
            toast.success(t('common.deleted'))
        } catch (error) {
            toast.error(t('common.deleteFailed'))
        }
    }

    const handleTest = async (id: number) => {
        try {
            const res = await api.testNotifier(id)
            toast.success(res ? t('settings.notifications.dialog.testSuccess') : "Test failed")
        } catch (error: any) {
            toast.error(error.response?.data?.detail || t('settings.notifications.dialog.testFailed'))
        }
    }

    const totalPages = Math.ceil(total / pageSize)

    return (
        <div className="flex flex-col h-full space-y-6">
            <div className="flex items-center justify-end space-y-2 flex-shrink-0">
                <Button onClick={() => {
                    setEditingNotifier(null)
                    setDialogOpen(true)
                }}>
                    <Plus className="mr-2 h-4 w-4" />
                    {t('settings.notifications.add')}
                </Button>
            </div>

            <div className="rounded-md border overflow-auto max-h-[calc(100vh-16rem)]">
                <Table>
                    <TableHeader className="sticky top-0 bg-background z-10">
                        <TableRow>
                            <TableHead>{t('settings.notifications.table.name')}</TableHead>
                            <TableHead>{t('settings.notifications.table.url')}</TableHead>
                            <TableHead>{t('settings.notifications.table.events')}</TableHead>
                            <TableHead>{t('settings.notifications.table.status')}</TableHead>
                            <TableHead className="text-right">{t('settings.notifications.table.actions')}</TableHead>
                        </TableRow>
                    </TableHeader>
                    <TableBody>
                        {loading ? (
                            <TableRow>
                                <TableCell colSpan={5} className="h-24 text-center">
                                    {t('common.loading')}
                                </TableCell>
                            </TableRow>
                        ) : notifiers.length === 0 ? (
                            <TableRow>
                                <TableCell colSpan={5} className="text-center h-24">
                                    {t('common.noData')}
                                </TableCell>
                            </TableRow>
                        ) : (
                            notifiers.map((notifier) => (
                                <TableRow key={notifier.id} className="hover:bg-muted/50 transition-colors">
                                    <TableCell className="font-medium py-2.5">{notifier.name}</TableCell>
                                    <TableCell className="max-w-[200px] truncate py-2.5" title={notifier.url}>
                                        {notifier.url}
                                    </TableCell>
                                    <TableCell className="py-2.5">
                                        <div className="flex gap-1 flex-wrap">
                                            {notifier.events.map(e => (
                                                <Badge key={e} variant="secondary" className="text-xs">
                                                    {t(`settings.notifications.eventTypes.${e}`, { defaultValue: e })}
                                                </Badge>
                                            ))}
                                        </div>
                                    </TableCell>
                                    <TableCell className="py-2.5">
                                        <Badge variant={notifier.enabled ? "default" : "secondary"}>
                                            {notifier.enabled ? t('settings.notifications.table.enabled') : t('settings.notifications.table.disabled')}
                                        </Badge>
                                    </TableCell>
                                    <TableCell className="text-right py-2.5">
                                        <DropdownMenu>
                                            <DropdownMenuTrigger asChild>
                                                <Button variant="ghost" className="h-8 w-8 p-0">
                                                    <span className="sr-only">Open menu</span>
                                                    <MoreHorizontal className="h-4 w-4" />
                                                </Button>
                                            </DropdownMenuTrigger>
                                            <DropdownMenuContent align="end">
                                                <DropdownMenuLabel>{t('common.actions')}</DropdownMenuLabel>
                                                <DropdownMenuItem onClick={() => {
                                                    setEditingNotifier(notifier)
                                                    setDialogOpen(true)
                                                }}>
                                                    <Edit className="mr-2 h-4 w-4" /> {t('common.edit')}
                                                </DropdownMenuItem>
                                                <DropdownMenuItem onClick={() => handleTest(notifier.id)}>
                                                    <Send className="mr-2 h-4 w-4" /> {t('settings.notifications.dialog.test')}
                                                </DropdownMenuItem>
                                                <DropdownMenuSeparator />
                                                <DropdownMenuItem
                                                    className="text-destructive focus:text-destructive"
                                                    onClick={() => handleDelete(notifier.id)}
                                                >
                                                    <Trash2 className="mr-2 h-4 w-4" /> {t('common.delete')}
                                                </DropdownMenuItem>
                                            </DropdownMenuContent>
                                        </DropdownMenu>
                                    </TableCell>
                                </TableRow>
                            ))
                        )}
                    </TableBody>
                </Table>
            </div>

            {/* Pagination Controls */}
            <div className="flex items-center justify-between mt-3 flex-shrink-0">
                <div className="flex-1 text-sm text-muted-foreground">
                    {t('pagination.totalItems', { count: total })}
                </div>

                <div className="flex items-center space-x-6 lg:space-x-8">
                    {/* Rows per page */}
                    <div className="flex items-center space-x-2">
                        <p className="text-sm font-medium">{t('pagination.rowsPerPage')}</p>
                        <Select
                            value={`${pageSize}`}
                            onValueChange={(value) => {
                                const newSize = Number(value)
                                setPageSize(newSize)
                                setPage(1)
                                localStorage.setItem('settings.notifiers.pageSize', String(newSize))
                            }}
                        >
                            <SelectTrigger className="h-8 w-[70px]">
                                <SelectValue placeholder={pageSize} />
                            </SelectTrigger>
                            <SelectContent side="top">
                                {[10, 15, 20, 30, 40, 50].map((size) => (
                                    <SelectItem key={size} value={`${size}`}>
                                        {size}
                                    </SelectItem>
                                ))}
                            </SelectContent>
                        </Select>
                    </div>

                    {/* Page X of Y */}
                    <div className="flex w-[100px] items-center justify-center text-sm font-medium">
                        {t('pagination.pageOf', { page, total: totalPages || 1 })}
                    </div>

                    {/* Navigation Buttons */}
                    <div className="flex items-center space-x-2">
                        <Button
                            variant="outline"
                            className="h-8 w-8 p-0"
                            onClick={() => setPage(page - 1)}
                            disabled={page <= 1}
                        >
                            <span className="sr-only">Go to previous page</span>
                            <ChevronLeft className="h-4 w-4" />
                        </Button>
                        <Button
                            variant="outline"
                            className="h-8 w-8 p-0"
                            onClick={() => setPage(page + 1)}
                            disabled={page >= totalPages}
                        >
                            <span className="sr-only">Go to next page</span>
                            <ChevronRight className="h-4 w-4" />
                        </Button>
                    </div>
                </div>
            </div>

            <NotifierDialog
                open={dialogOpen}
                onOpenChange={setDialogOpen}
                notifier={editingNotifier}
                onSuccess={loadNotifiers}
            />
        </div>
    )
}

interface NotifierDialogProps {
    open: boolean
    onOpenChange: (open: boolean) => void
    notifier: Notifier | null
    onSuccess: () => void
}

function NotifierDialog({ open, onOpenChange, notifier, onSuccess }: NotifierDialogProps) {
    const { t } = useTranslation()

    const form = useForm<Partial<Notifier>>({
        defaultValues: {
            name: "",
            url: "",
            events: ["new_release"],
            enabled: true,
            description: "",
        }
    })

    useEffect(() => {
        if (open) {
            form.reset(notifier || {
                name: "",
                url: "",
                events: ["new_release"],
                enabled: true,
                description: ""
            })
        }
    }, [open, notifier, form])

    const onSubmit = async (data: Partial<Notifier>) => {
        try {
            if (notifier) {
                await api.updateNotifier(notifier.id, data)
                toast.success(t('settings.notifications.dialog.updateSuccess'))
            } else {
                await api.createNotifier(data)
                toast.success(t('settings.notifications.dialog.createSuccess'))
            }
            onSuccess()
            onOpenChange(false)
        } catch (error: any) {
            toast.error(error.response?.data?.detail || t('common.unexpectedError'))
        }
    }

    return (
        <Dialog open={open} onOpenChange={onOpenChange}>
            <DialogContent className="sm:max-w-[525px]">
                <DialogHeader>
                    <DialogTitle>{notifier ? t('settings.notifications.dialog.editTitle') : t('settings.notifications.dialog.addTitle')}</DialogTitle>
                    <DialogDescription>
                        {t('settings.notifications.dialog.description')}
                    </DialogDescription>
                </DialogHeader>

                <Form {...form}>
                    <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-4">
                        <FormField
                            control={form.control}
                            name="name"
                            render={({ field }) => (
                                <FormItem>
                                    <FormLabel>{t('settings.notifications.dialog.name')}</FormLabel>
                                    <FormControl>
                                        <Input placeholder="Discord Webhook" {...field} />
                                    </FormControl>
                                    <FormMessage />
                                </FormItem>
                            )}
                        />
                        <FormField
                            control={form.control}
                            name="url"
                            render={({ field }) => (
                                <FormItem>
                                    <FormLabel>{t('settings.notifications.dialog.url')}</FormLabel>
                                    <FormControl>
                                        <Input placeholder="https://..." {...field} />
                                    </FormControl>
                                    <FormMessage />
                                </FormItem>
                            )}
                        />
                        <FormField
                            control={form.control}
                            name="description"
                            render={({ field }) => (
                                <FormItem>
                                    <FormLabel>{t('common.description')}</FormLabel>
                                    <FormControl>
                                        <Input {...field} />
                                    </FormControl>
                                    <FormMessage />
                                </FormItem>
                            )}
                        />
                        <div className="grid grid-cols-2 gap-4">
                            <FormField
                                control={form.control}
                                name="events"
                                render={({ field }) => (
                                    <FormItem className="flex flex-row items-start space-x-3 space-y-0 rounded-md p-4 shadow-sm border">
                                        <FormControl>
                                            <Checkbox
                                                checked={field.value?.includes("Release")}
                                                onCheckedChange={(checked) => {
                                                    return checked
                                                        ? field.onChange([...(field.value || []), "Release"])
                                                        : field.onChange(field.value?.filter((value) => value !== "Release"))
                                                }}
                                            />
                                        </FormControl>
                                        <div className="space-y-1 leading-none">
                                            <FormLabel>
                                                {t('settings.notifications.eventTypes.Release')}
                                            </FormLabel>
                                            <FormDescription>
                                                New release published
                                            </FormDescription>
                                        </div>
                                    </FormItem>
                                )}
                            />
                            <FormField
                                control={form.control}
                                name="enabled"
                                render={({ field }) => (
                                    <FormItem className="flex flex-row items-center justify-between rounded-lg border p-4 shadow-sm">
                                        <div className="space-y-0.5">
                                            <FormLabel className="text-base">
                                                {t('common.enabled')}
                                            </FormLabel>
                                        </div>
                                        <FormControl>
                                            <Switch
                                                checked={field.value}
                                                onCheckedChange={field.onChange}
                                            />
                                        </FormControl>
                                    </FormItem>
                                )}
                            />
                        </div>

                        <DialogFooter>
                            <Button type="submit">{t('common.save')}</Button>
                        </DialogFooter>
                    </form>
                </Form>
            </DialogContent>
        </Dialog>
    )
}
