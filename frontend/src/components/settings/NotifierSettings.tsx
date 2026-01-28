import { useState, useEffect } from "react"
import { useTranslation } from "react-i18next"
import { Plus, MoreHorizontal, Edit, Trash2, Send } from "lucide-react"
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

    const loadNotifiers = async () => {
        setLoading(true)
        try {
            const data = await api.getNotifiers()
            setNotifiers(data)
        } catch (error) {
            console.error(error)
            toast.error(t('common.unexpectedError'))
        } finally {
            setLoading(false)
        }
    }

    useEffect(() => {
        loadNotifiers()
    }, [])

    const handleDelete = async (id: number) => {
        if (!window.confirm(t('common.confirm'))) return

        try {
            await api.deleteNotifier(id)
            setNotifiers(notifiers.filter(n => n.id !== id))
            toast.success(t('common.deleted'))
        } catch (error) {
            toast.error(t('common.deleteFailed'))
        }
    }

    const handleTest = async (id: number) => {
        try {
            const res = await api.testNotifier(id)
            toast.success(res.message || t('settings.notifications.dialog.testSuccess'))
        } catch (error: any) {
            toast.error(error.response?.data?.detail || t('settings.notifications.dialog.testFailed'))
        }
    }

    return (
        <div className="flex flex-col h-full space-y-6">
            <div className="flex items-center justify-between space-y-2 flex-shrink-0">
                <div>
                    <h2 className="text-3xl font-bold tracking-tight">{t('settings.notifications.title')}</h2>
                    <p className="text-muted-foreground">
                        {t('settings.notifications.description')}
                    </p>
                </div>
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
                                    <TableCell className="font-medium">{notifier.name}</TableCell>
                                    <TableCell className="max-w-[200px] truncate" title={notifier.url}>
                                        {notifier.url}
                                    </TableCell>
                                    <TableCell>
                                        <div className="flex gap-1 flex-wrap">
                                            {notifier.events.map(e => (
                                                <Badge key={e} variant="secondary" className="text-xs">
                                                    {t(`settings.notifications.eventTypes.${e}`, { defaultValue: e })}
                                                </Badge>
                                            ))}
                                        </div>
                                    </TableCell>
                                    <TableCell>
                                        <Badge variant={notifier.enabled ? "default" : "secondary"}>
                                            {notifier.enabled ? t('settings.notifications.table.enabled') : t('settings.notifications.table.disabled')}
                                        </Badge>
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
                                                <DropdownMenuLabel>{t('settings.notifications.table.actions')}</DropdownMenuLabel>
                                                <DropdownMenuItem onClick={() => handleTest(notifier.id)}>
                                                    <Send className="mr-2 h-4 w-4" /> {t('settings.notifications.dialog.test')}
                                                </DropdownMenuItem>
                                                <DropdownMenuItem onClick={() => {
                                                    setEditingNotifier(notifier)
                                                    setDialogOpen(true)
                                                }}>
                                                    <Edit className="mr-2 h-4 w-4" /> {t('common.edit')}
                                                </DropdownMenuItem>
                                                <DropdownMenuSeparator />
                                                <DropdownMenuItem onClick={() => handleDelete(notifier.id)} className="text-destructive focus:text-destructive">
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

            <NotifierDialog
                open={dialogOpen}
                onOpenChange={setDialogOpen}
                notifier={editingNotifier}
                onSuccess={() => {
                    setDialogOpen(false)
                    loadNotifiers()
                }}
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
    const [loading, setLoading] = useState(false)

    const form = useForm({
        defaultValues: {
            name: "",
            url: "",
            events: ["new_release", "republish"],
            enabled: true,
            description: "",
        }
    })

    useEffect(() => {
        if (open) {
            if (notifier) {
                form.reset({
                    name: notifier.name,
                    url: notifier.url,
                    events: notifier.events,
                    enabled: notifier.enabled,
                    description: notifier.description || "",
                })
            } else {
                form.reset({
                    name: "",
                    url: "",
                    events: ["new_release"],
                    enabled: true,
                    description: "",
                })
            }
        }
    }, [open, notifier, form])

    const onSubmit = async (data: any) => {
        setLoading(true)
        try {
            if (notifier) {
                await api.updateNotifier(notifier.id, data)
            } else {
                await api.createNotifier(data)
            }
            toast.success(t('common.saved'))
            onSuccess()
        } catch (error: any) {
            toast.error(error.response?.data?.detail || t('common.unexpectedError'))
        } finally {
            setLoading(false)
        }
    }

    const availableEvents = [
        { id: "new_release", label: t('settings.notifications.eventTypes.new_release') },
        { id: "republish", label: t('settings.notifications.eventTypes.republish') },
    ]

    return (
        <Dialog open={open} onOpenChange={onOpenChange}>
            <DialogContent>
                <DialogHeader>
                    <DialogTitle>{notifier ? t('settings.notifications.dialog.editTitle') : t('settings.notifications.dialog.addTitle')}</DialogTitle>
                    <DialogDescription>
                        {t('settings.notifications.description')}
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
                                        <Input placeholder={t('settings.notifications.dialog.placeholder.name')} {...field} />
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
                                        <Input placeholder={t('settings.notifications.dialog.placeholder.url')} {...field} />
                                    </FormControl>
                                    <FormMessage />
                                </FormItem>
                            )}
                        />
                        <FormField
                            control={form.control}
                            name="enabled"
                            render={({ field }) => (
                                <FormItem className="flex flex-row items-center justify-between rounded-lg border p-3">
                                    <div className="space-y-0.5">
                                        <FormLabel className="text-base">{t('settings.notifications.table.enabled')}</FormLabel>
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

                        <FormField
                            control={form.control}
                            name="events"
                            render={() => (
                                <FormItem>
                                    <div className="mb-4">
                                        <FormLabel className="text-base">{t('settings.notifications.dialog.events')}</FormLabel>
                                        <FormDescription>
                                            {t('settings.notifications.dialog.eventsDesc')}
                                        </FormDescription>
                                    </div>
                                    {availableEvents.map((item) => (
                                        <FormField
                                            key={item.id}
                                            control={form.control}
                                            name="events"
                                            render={({ field }) => {
                                                return (
                                                    <FormItem
                                                        key={item.id}
                                                        className="flex flex-row items-start space-x-3 space-y-0"
                                                    >
                                                        <FormControl>
                                                            <Checkbox
                                                                checked={field.value?.includes(item.id)}
                                                                onCheckedChange={(checked) => {
                                                                    return checked
                                                                        ? field.onChange([...field.value, item.id])
                                                                        : field.onChange(
                                                                            field.value?.filter(
                                                                                (value: string) => value !== item.id
                                                                            )
                                                                        )
                                                                }}
                                                            />
                                                        </FormControl>
                                                        <FormLabel className="font-normal">
                                                            {item.label}
                                                        </FormLabel>
                                                    </FormItem>
                                                )
                                            }}
                                        />
                                    ))}
                                    <FormMessage />
                                </FormItem>
                            )}
                        />

                        <DialogFooter>
                            <Button type="submit" disabled={loading}>
                                {t('common.save')}
                            </Button>
                        </DialogFooter>
                    </form>
                </Form>
            </DialogContent>
        </Dialog>
    )
}
