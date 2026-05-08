import { useEffect, useMemo, useState } from "react"
import { useTranslation } from "react-i18next"
import { Edit, MoreHorizontal, Plus, Search, Send, Trash2, X } from "lucide-react"
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
    AlertDialog,
    AlertDialogAction,
    AlertDialogCancel,
    AlertDialogContent,
    AlertDialogDescription,
    AlertDialogFooter,
    AlertDialogHeader,
    AlertDialogTitle,
} from "@/components/ui/alert-dialog"
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
import {
    InputGroup,
    InputGroupAddon,
    InputGroupInput,
    InputGroupText,
} from "@/components/ui/input-group"
import { DataPagination } from "@/components/common/DataPagination"
import { usePageSize } from "@/hooks/use-page-size"
import {
    useCreateNotifier,
    useDeleteNotifier,
    useNotifiers,
    useTestNotifier,
    useUpdateNotifier,
} from "@/hooks/queries"

import type { Notifier } from "@/api/types"
import { toast } from "sonner"

export function NotifierSettings() {
    const { t } = useTranslation()
    const [dialogOpen, setDialogOpen] = useState(false)
    const [editingNotifier, setEditingNotifier] = useState<Notifier | null>(null)
    const [pendingDeleteNotifierId, setPendingDeleteNotifierId] = useState<number | null>(null)
    const [search, setSearch] = useState("")
    const [page, setPage] = useState(1)
    const [pageSize, setPageSize] = usePageSize("settings.notifiers.pageSize")

    const skip = (page - 1) * pageSize
    const { data, isLoading: loading } = useNotifiers({ skip, limit: pageSize })
    const rawNotifiers = data?.items ?? []
    const total = data?.total ?? 0

    // Client-side filter.
    const notifiers = useMemo(() => {
        const term = search.trim().toLowerCase()
        if (!term) return rawNotifiers
        return rawNotifiers.filter((notifier) => {
            if (notifier.name.toLowerCase().includes(term)) return true
            if (notifier.url.toLowerCase().includes(term)) return true
            if (notifier.description?.toLowerCase().includes(term)) return true
            return false
        })
    }, [rawNotifiers, search])

    const deleteNotifier = useDeleteNotifier()
    const testNotifier = useTestNotifier()

    const handleDelete = async () => {
        if (pendingDeleteNotifierId === null) return
        try {
            await deleteNotifier.mutateAsync(pendingDeleteNotifierId)
            toast.success(t("common.deleted"))
        } catch {
            toast.error(t("common.deleteFailed"))
        } finally {
            setPendingDeleteNotifierId(null)
        }
    }

    const handleTest = async (id: number) => {
        try {
            const ok = await testNotifier.mutateAsync(id)
            toast.success(
                ok
                    ? t("settings.notifications.dialog.testSuccess")
                    : t("settings.notifications.dialog.testFailed"),
            )
        } catch (error: unknown) {
            const detail = (error as { response?: { data?: { detail?: string } } })?.response?.data?.detail
            toast.error(detail || t("settings.notifications.dialog.testFailed"))
        }
    }

    return (
        <div className="flex min-h-0 flex-1 flex-col gap-4">
            {/* Toolbar — search + primary action. */}
            <div className="flex flex-none flex-wrap items-center justify-between gap-3">
                <div className="w-full max-w-sm">
                    <InputGroup>
                        <InputGroupAddon align="inline-start">
                            <InputGroupText>
                                <Search className="h-4 w-4" />
                            </InputGroupText>
                        </InputGroupAddon>
                        <InputGroupInput
                            placeholder={t("settings.notifications.searchPlaceholder")}
                            value={search}
                            onChange={(event) => setSearch(event.target.value)}
                        />
                        {search ? (
                            <InputGroupAddon align="inline-end">
                                <Button
                                    variant="ghost"
                                    size="icon"
                                    className="h-6 w-6"
                                    onClick={() => setSearch("")}
                                    title={t("common.clear")}
                                >
                                    <X className="h-3.5 w-3.5" />
                                </Button>
                            </InputGroupAddon>
                        ) : null}
                    </InputGroup>
                </div>
                <Button
                    onClick={() => {
                        setEditingNotifier(null)
                        setDialogOpen(true)
                    }}
                >
                    <Plus className="mr-2 h-4 w-4" />
                    {t("settings.notifications.add")}
                </Button>
            </div>

            <div className="flex min-h-0 flex-1 flex-col gap-3">
                <div className="min-h-0 flex-1 overflow-auto rounded-md border">
                    <Table containerClassName="overflow-visible">
                        <TableHeader className="sticky top-0 z-10 bg-background">
                            <TableRow>
                                <TableHead className="min-w-[12rem]">
                                    {t("settings.notifications.table.name")}
                                </TableHead>
                                <TableHead className="min-w-[18rem]">
                                    {t("settings.notifications.table.url")}
                                </TableHead>
                                <TableHead className="hidden md:table-cell">
                                    {t("settings.notifications.table.events")}
                                </TableHead>
                                <TableHead>{t("settings.notifications.table.status")}</TableHead>
                                <TableHead className="w-[1%] text-right">
                                    {t("settings.notifications.table.actions")}
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
                            ) : notifiers.length === 0 ? (
                                <TableRow>
                                    <TableCell colSpan={5} className="h-24 text-center text-sm text-muted-foreground">
                                        {t("common.noData")}
                                    </TableCell>
                                </TableRow>
                            ) : (
                                notifiers.map((notifier) => (
                                    <TableRow key={notifier.id} className="transition-colors hover:bg-muted/40">
                                        <TableCell className="py-3 align-middle font-medium">
                                            <div className="min-w-0 space-y-0.5">
                                                <span className="truncate">{notifier.name}</span>
                                                {notifier.description ? (
                                                    <div
                                                        className="line-clamp-1 text-xs font-normal text-muted-foreground"
                                                        title={notifier.description}
                                                    >
                                                        {notifier.description}
                                                    </div>
                                                ) : null}
                                            </div>
                                        </TableCell>
                                        <TableCell className="py-3 align-middle">
                                            <code
                                                className="max-w-[22rem] truncate rounded bg-muted/40 px-1.5 py-0.5 font-mono text-xs text-foreground/80"
                                                title={notifier.url}
                                            >
                                                {notifier.url}
                                            </code>
                                        </TableCell>
                                        <TableCell className="hidden py-3 align-middle md:table-cell">
                                            <div className="flex flex-wrap gap-1">
                                                {notifier.events.map((eventKey) => (
                                                    <Badge
                                                        key={eventKey}
                                                        variant="outline"
                                                        className="border-border/60 bg-muted/30 text-[10px]"
                                                    >
                                                        {t(`settings.notifications.eventTypes.${eventKey}`, {
                                                            defaultValue: eventKey,
                                                        })}
                                                    </Badge>
                                                ))}
                                            </div>
                                        </TableCell>
                                        <TableCell className="py-3 align-middle">
                                            <Badge
                                                variant={notifier.enabled ? "secondary" : "outline"}
                                                className="h-5 text-[10px]"
                                            >
                                                {notifier.enabled
                                                    ? t("settings.notifications.table.enabled")
                                                    : t("settings.notifications.table.disabled")}
                                            </Badge>
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
                                                    <DropdownMenuItem
                                                        onClick={() => {
                                                            setEditingNotifier(notifier)
                                                            setDialogOpen(true)
                                                        }}
                                                    >
                                                        <Edit className="mr-2 h-4 w-4" />
                                                        {t("common.edit")}
                                                    </DropdownMenuItem>
                                                    <DropdownMenuItem onClick={() => handleTest(notifier.id)}>
                                                        <Send className="mr-2 h-4 w-4" />
                                                        {t("settings.notifications.dialog.test")}
                                                    </DropdownMenuItem>
                                                    <DropdownMenuSeparator />
                                                    <DropdownMenuItem
                                                        className="text-destructive focus:text-destructive"
                                                        onClick={() => setPendingDeleteNotifierId(notifier.id)}
                                                    >
                                                        <Trash2 className="mr-2 h-4 w-4" />
                                                        {t("common.delete")}
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

                <DataPagination
                    page={page}
                    pageSize={pageSize}
                    total={total}
                    onPageChange={setPage}
                    onPageSizeChange={setPageSize}
                />
            </div>

            <NotifierDialog
                open={dialogOpen}
                onOpenChange={setDialogOpen}
                notifier={editingNotifier}
            />

            <AlertDialog
                open={pendingDeleteNotifierId !== null}
                onOpenChange={(open) => {
                    if (!open) setPendingDeleteNotifierId(null)
                }}
            >
                <AlertDialogContent>
                    <AlertDialogHeader>
                        <AlertDialogTitle>{t("common.confirm")}</AlertDialogTitle>
                        <AlertDialogDescription>
                            {t("settings.notifications.dialog.deleteConfirm")}
                        </AlertDialogDescription>
                    </AlertDialogHeader>
                    <AlertDialogFooter>
                        <AlertDialogCancel>{t("common.cancel")}</AlertDialogCancel>
                        <AlertDialogAction onClick={handleDelete}>
                            {t("common.delete")}
                        </AlertDialogAction>
                    </AlertDialogFooter>
                </AlertDialogContent>
            </AlertDialog>
        </div>
    )
}

interface NotifierDialogProps {
    open: boolean
    onOpenChange: (open: boolean) => void
    notifier: Notifier | null
}

function NotifierDialog({ open, onOpenChange, notifier }: NotifierDialogProps) {
    const { t } = useTranslation()
    const createNotifier = useCreateNotifier()
    const updateNotifier = useUpdateNotifier()

    const form = useForm<Partial<Notifier>>({
        defaultValues: {
            name: "",
            url: "",
            events: ["new_release"],
            enabled: true,
            language: "en",
            description: "",
        },
    })

    useEffect(() => {
        if (open) {
            form.reset(
                notifier || {
                    name: "",
                    url: "",
                    events: ["new_release"],
                    enabled: true,
                    language: "en",
                    description: "",
                },
            )
        }
    }, [open, notifier, form])

    const onSubmit = async (data: Partial<Notifier>) => {
        try {
            const payload = {
                ...data,
                language: data.language || "en",
                description: data.description || "",
            }

            if (notifier) {
                await updateNotifier.mutateAsync({ id: notifier.id, data: payload })
                toast.success(t("settings.notifications.dialog.updateSuccess"))
            } else {
                await createNotifier.mutateAsync(payload)
                toast.success(t("settings.notifications.dialog.createSuccess"))
            }
            onOpenChange(false)
        } catch (error: unknown) {
            const detail = (error as { response?: { data?: { detail?: string } } })?.response?.data?.detail
            toast.error(detail || t("common.unexpectedError"))
        }
    }

    const availableEvents = [
        { id: "new_release", label: t("settings.notifications.eventTypes.new_release") },
        { id: "republish", label: t("settings.notifications.eventTypes.republish") },
        { id: "executor_run_success", label: t("settings.notifications.eventTypes.executor_run_success") },
        { id: "executor_run_failed", label: t("settings.notifications.eventTypes.executor_run_failed") },
        { id: "executor_run_skipped", label: t("settings.notifications.eventTypes.executor_run_skipped") },
    ]

    return (
        <Dialog open={open} onOpenChange={onOpenChange}>
            <DialogContent className="sm:max-w-[525px]">
                <DialogHeader>
                    <DialogTitle>
                        {notifier
                            ? t("settings.notifications.dialog.editTitle")
                            : t("settings.notifications.dialog.addTitle")}
                    </DialogTitle>
                    <DialogDescription>
                        {t("settings.notifications.dialog.description")}
                    </DialogDescription>
                </DialogHeader>

                <Form {...form}>
                    <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-4">
                        <FormField
                            control={form.control}
                            name="name"
                            render={({ field }) => (
                                <FormItem>
                                    <FormLabel>{t("settings.notifications.dialog.name")}</FormLabel>
                                    <FormControl>
                                        <Input
                                            placeholder={t("settings.notifications.dialog.placeholder.name")}
                                            {...field}
                                        />
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
                                    <FormLabel>{t("settings.notifications.dialog.url")}</FormLabel>
                                    <FormControl>
                                        <Input
                                            placeholder={t("settings.notifications.dialog.placeholder.url")}
                                            {...field}
                                        />
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
                                    <FormLabel>{t("common.description")}</FormLabel>
                                    <FormControl>
                                        <Input {...field} />
                                    </FormControl>
                                    <FormMessage />
                                </FormItem>
                            )}
                        />
                        <FormField
                            control={form.control}
                            name="language"
                            render={({ field }) => (
                                <FormItem>
                                    <FormLabel>{t("settings.notifications.dialog.language")}</FormLabel>
                                    <Select onValueChange={field.onChange} value={field.value || "en"}>
                                        <FormControl>
                                            <SelectTrigger>
                                                <SelectValue />
                                            </SelectTrigger>
                                        </FormControl>
                                        <SelectContent>
                                            <SelectItem value="en">
                                                {t("settings.notifications.dialog.languages.en")}
                                            </SelectItem>
                                            <SelectItem value="zh">
                                                {t("settings.notifications.dialog.languages.zh")}
                                            </SelectItem>
                                        </SelectContent>
                                    </Select>
                                    <FormDescription>
                                        {t("settings.notifications.dialog.languageDesc")}
                                    </FormDescription>
                                    <FormMessage />
                                </FormItem>
                            )}
                        />

                        <FormField
                            control={form.control}
                            name="events"
                            render={() => (
                                <FormItem>
                                    <div className="mb-4">
                                        <FormLabel className="text-base">
                                            {t("settings.notifications.dialog.events")}
                                        </FormLabel>
                                        <FormDescription>
                                            {t("settings.notifications.dialog.eventsDesc")}
                                        </FormDescription>
                                    </div>
                                    <div className="flex flex-row flex-wrap gap-4">
                                        {availableEvents.map((item) => (
                                            <FormField
                                                key={item.id}
                                                control={form.control}
                                                name="events"
                                                render={({ field }) => (
                                                    <FormItem className="flex flex-row items-start space-x-3 space-y-0">
                                                        <FormControl>
                                                            <Checkbox
                                                                checked={field.value?.includes(item.id)}
                                                                onCheckedChange={(checked) =>
                                                                    checked
                                                                        ? field.onChange([...(field.value || []), item.id])
                                                                        : field.onChange(
                                                                            field.value?.filter(
                                                                                (value: string) => value !== item.id,
                                                                            ),
                                                                        )
                                                                }
                                                            />
                                                        </FormControl>
                                                        <FormLabel className="cursor-pointer font-normal">
                                                            {item.label}
                                                        </FormLabel>
                                                    </FormItem>
                                                )}
                                            />
                                        ))}
                                    </div>
                                    <FormMessage />
                                </FormItem>
                            )}
                        />

                        <DialogFooter className="gap-4 sm:justify-end">
                            <FormField
                                control={form.control}
                                name="enabled"
                                render={({ field }) => (
                                    <FormItem className="flex flex-row items-center space-x-2 space-y-0">
                                        <FormControl>
                                            <Switch
                                                checked={field.value}
                                                onCheckedChange={field.onChange}
                                            />
                                        </FormControl>
                                        <FormLabel className="cursor-pointer text-sm font-normal text-muted-foreground">
                                            {t("common.enabled")}
                                        </FormLabel>
                                    </FormItem>
                                )}
                            />
                            <Button type="submit">{t("common.save")}</Button>
                        </DialogFooter>
                    </form>
                </Form>
            </DialogContent>
        </Dialog>
    )
}
