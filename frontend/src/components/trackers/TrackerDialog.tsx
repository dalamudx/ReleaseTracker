import { useState, useEffect, useRef } from "react"
import { useForm, useFieldArray } from "react-hook-form"
import { Plus, Trash2, Save, ChevronDown, ChevronRight } from "lucide-react"
import { useTranslation } from "react-i18next"

import { Button } from "@/components/ui/button"
import {
    Dialog,
    DialogContent,
    DialogDescription,
    DialogFooter,
    DialogHeader,
    DialogTitle,
} from "@/components/ui/dialog"
import { cn } from "@/lib/utils"
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
import {
    Select,
    SelectContent,
    SelectItem,
    SelectTrigger,
    SelectValue,
} from "@/components/ui/select"
import { Switch } from "@/components/ui/switch"
import type { TrackerConfig, ApiCredential } from "@/api/types"
import { api } from "@/api/client"
import { toast } from "sonner"

interface TrackerDialogProps {
    open: boolean
    onOpenChange: (open: boolean) => void
    onSuccess: () => void
    trackerName?: string | null // If present, edit mode
}

const defaultChannels = [
    { name: "stable", type: "release", enabled: true },
]

const parseIntervalToMinutes = (interval: string | undefined): number => {
    if (!interval) return 360
    if (interval.endsWith('h')) return parseInt(interval) * 60
    if (interval.endsWith('m')) return parseInt(interval)
    if (interval.endsWith('s')) return Math.ceil(parseInt(interval) / 60)
    return parseInt(interval) || 360
}

export function TrackerDialog({ open, onOpenChange, onSuccess, trackerName }: TrackerDialogProps) {
    const { t } = useTranslation()
    const [credentials, setCredentials] = useState<ApiCredential[]>([])
    const [loading, setLoading] = useState(false)
    const [expandedChannel, setExpandedChannel] = useState<number | null>(0)

    const form = useForm<TrackerConfig>({
        defaultValues: {
            name: "",
            type: "github",
            enabled: true,
            channels: defaultChannels as any,
            interval: "360m",
        },
    })

    const { fields, append, remove } = useFieldArray({
        control: form.control,
        name: "channels",
    });

    const type = form.watch("type")

    // Auto-select credential logic
    const prevTypeRef = useRef(type)

    useEffect(() => {
        const typeChanged = prevTypeRef.current !== type

        // Only run logic if we are NOT in edit mode (trackerName is null)
        // And either type changed OR it's the first load (typeChanged is false but maybe credentials just loaded)
        // We check credentials.length > 0 to ensure we have data to select from.
        if (!trackerName && (typeChanged || credentials.length > 0)) {
            // If type changed, OR if we have no value set yet
            const currentValue = form.getValues("credential_name")
            if (typeChanged || !currentValue) {
                const matches = credentials.filter(c => c.type === type)
                if (matches.length > 0) {
                    form.setValue("credential_name", matches[0].name)
                } else {
                    form.setValue("credential_name", "none")
                }
            }
        }
        prevTypeRef.current = type
    }, [type, credentials, trackerName, form])

    // Load credentials and tracker data (if editing)
    useEffect(() => {
        if (open) {
            setExpandedChannel(trackerName ? null : 0)
            api.getCredentials({ limit: 1000 }).then(data => setCredentials(data.items)).catch(console.error)

            if (trackerName) {
                api.getTrackerConfig(trackerName).then((data) => {
                    form.reset(data)
                }).catch(console.error)
            } else {
                form.reset({
                    name: "",
                    type: "github",
                    enabled: true,
                    repo: "",
                    project: "",
                    instance: "",
                    chart: "",
                    credential_name: "",
                    channels: defaultChannels as any,
                    interval: "360m",
                    description: ""
                })
                // Reset prevTypeRef on open to trigger auto-select if needed? 
                // Actually the effect above handles 'type' change or initial load.
                // When dialog opens, 'type' is 'github'. prevTypeRef might be stale?
                // It's safer to not reset here, but rely on the effect.
            }
        }
    }, [open, trackerName, form])

    const onSubmit = async (data: TrackerConfig) => {
        setLoading(true)
        try {
            if (trackerName) {
                await api.updateTracker(trackerName, data)
                toast.success(t('common.saved'))
            } else {
                await api.createTracker(data)
                toast.success(t('common.saved'))
            }
            onSuccess()
            onOpenChange(false)
        } catch (error: any) {
            console.error("Failed to save tracker", error)

            // Handle duplicate name error
            if (error.response?.status === 400) {
                const detail = error.response.data?.detail || "";
                if (detail.includes("already exists") || detail.includes("已存在") || detail.includes("exist") || detail.includes("重复")) {
                    form.setError("name", {
                        type: "manual",
                        message: t('tracker.errors.nameExists')
                    })
                    toast.error(t('tracker.errors.nameExists'))
                } else {
                    // Fallback to general alert for other errors
                    toast.error(detail || t('common.unexpectedError'));
                }
            } else {
                toast.error(t('common.unexpectedError'));
            }
        } finally {
            setLoading(false)
        }
    }

    return (
        <Dialog open={open} onOpenChange={onOpenChange}>
            <DialogContent className="sm:max-w-[50vw] max-h-[90vh] overflow-y-auto">
                <DialogHeader>
                    <DialogTitle>{trackerName ? t('tracker.editTitle') : t('tracker.addTitle')}</DialogTitle>
                    <DialogDescription>
                        {t('tracker.description')}
                    </DialogDescription>
                </DialogHeader>

                <Form {...form}>
                    <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-6">

                        <div className="grid grid-cols-6 gap-4">
                            <FormField
                                control={form.control}
                                name="name"
                                render={({ field }) => (
                                    <FormItem className="col-span-4">
                                        <FormLabel>{t('tracker.fields.name')}</FormLabel>
                                        <FormControl>
                                            <Input placeholder="my-app" {...field} disabled={!!trackerName} />
                                        </FormControl>
                                        <FormDescription>{t('tracker.fields.nameDesc')}</FormDescription>
                                        <FormMessage />
                                    </FormItem>
                                )}
                            />

                            <FormField
                                control={form.control}
                                name="type"
                                render={({ field }) => (
                                    <FormItem className="col-span-2">
                                        <FormLabel>{t('tracker.fields.type')}</FormLabel>
                                        <Select onValueChange={field.onChange} defaultValue={field.value} disabled={!!trackerName}>
                                            <FormControl>
                                                <SelectTrigger>
                                                    <SelectValue placeholder={t('tracker.fields.selectType')} />
                                                </SelectTrigger>
                                            </FormControl>
                                            <SelectContent>
                                                <SelectItem value="github">GitHub</SelectItem>
                                                <SelectItem value="gitlab">GitLab</SelectItem>
                                                <SelectItem value="helm">Helm Chart</SelectItem>
                                            </SelectContent>
                                        </Select>
                                        <FormMessage />
                                    </FormItem>
                                )}
                            />
                        </div>

                        {/* Type Specific Fields - Compact Grid */}
                        <div className="grid grid-cols-2 gap-4 p-4 border rounded-lg bg-muted/40">
                            {type === 'github' && (
                                <FormField
                                    control={form.control}
                                    name="repo"
                                    render={({ field }) => (
                                        <FormItem className="col-span-2">
                                            <FormLabel>{t('tracker.fields.repo')}</FormLabel>
                                            <FormControl>
                                                <Input placeholder="facebook/react" {...field} className="bg-background" />
                                            </FormControl>
                                            <FormMessage />
                                        </FormItem>
                                    )}
                                />
                            )}

                            {type === 'gitlab' && (
                                <>
                                    <FormField
                                        control={form.control}
                                        name="instance"
                                        render={({ field }) => (
                                            <FormItem>
                                                <FormLabel>{t('tracker.fields.instanceUrl')}</FormLabel>
                                                <FormControl>
                                                    <Input placeholder="https://gitlab.com" {...field} className="bg-background" />
                                                </FormControl>
                                                <FormMessage />
                                            </FormItem>
                                        )}
                                    />
                                    <FormField
                                        control={form.control}
                                        name="project"
                                        render={({ field }) => (
                                            <FormItem>
                                                <FormLabel>{t('tracker.fields.projectId')}</FormLabel>
                                                <FormControl>
                                                    <Input placeholder="group/project" {...field} className="bg-background" />
                                                </FormControl>
                                                <FormMessage />
                                            </FormItem>
                                        )}
                                    />
                                </>
                            )}

                            {type === 'helm' && (
                                <>
                                    <FormField
                                        control={form.control}
                                        name="repo"
                                        render={({ field }) => (
                                            <FormItem>
                                                <FormLabel>{t('tracker.fields.chartRepo')}</FormLabel>
                                                <FormControl>
                                                    <Input placeholder="https://charts.bitnami.com/bitnami" {...field} className="bg-background" />
                                                </FormControl>
                                                <FormMessage />
                                            </FormItem>
                                        )}
                                    />
                                    <FormField
                                        control={form.control}
                                        name="chart"
                                        render={({ field }) => (
                                            <FormItem>
                                                <FormLabel>{t('tracker.fields.chartName')}</FormLabel>
                                                <FormControl>
                                                    <Input placeholder="nginx" {...field} className="bg-background" />
                                                </FormControl>
                                                <FormMessage />
                                            </FormItem>
                                        )}
                                    />
                                </>
                            )}
                        </div>

                        <div className="grid grid-cols-2 gap-4">
                            <FormField
                                control={form.control}
                                name="credential_name"
                                render={({ field }) => (
                                    <FormItem>
                                        <FormLabel>{t('tracker.fields.credential')} <span className="text-muted-foreground font-normal">{t('tracker.fields.optional')}</span></FormLabel>
                                        <Select onValueChange={field.onChange} value={field.value || undefined}>
                                            <FormControl>
                                                <SelectTrigger>
                                                    <SelectValue placeholder={t('tracker.fields.none')} />
                                                </SelectTrigger>
                                            </FormControl>
                                            <SelectContent>
                                                <SelectItem value="none">{t('tracker.fields.none')}</SelectItem>
                                                {credentials.filter(c => c.type === type).map(c => (
                                                    <SelectItem key={c.id} value={c.name}>{c.name} ({c.type})</SelectItem>
                                                ))}
                                            </SelectContent>
                                        </Select>
                                        <FormMessage />
                                    </FormItem>
                                )}
                            />

                            <FormField
                                control={form.control}
                                name="interval"
                                render={({ field }) => (
                                    <FormItem>
                                        <FormLabel>{t('tracker.fields.interval')}</FormLabel>
                                        <FormControl>
                                            <Input
                                                type="number"
                                                min={1}
                                                placeholder="360"
                                                value={parseIntervalToMinutes(field.value)}
                                                onChange={e => field.onChange(`${e.target.value}m`)}
                                            />
                                        </FormControl>
                                        <FormMessage />
                                    </FormItem>
                                )}
                            />
                        </div>

                        <div className="space-y-3">
                            <div className="flex items-center justify-between border-b pb-2">
                                <h4 className="text-sm font-medium">{t('tracker.fields.channels')}</h4>
                                <Button type="button" variant="outline" size="sm" className="h-7 text-xs" onClick={() => {
                                    append({ name: "stable", type: "release", enabled: true })
                                    // Set expanded to the index of the newly added item (fields.length)
                                    // Need to wait for render cycle, but logic-wise it will be the next index
                                    // Or simply use setTimeout, but React setState batching usually handles this if logic is right.
                                    // Actually fields.length is the current length, so the new index is fields.length
                                    setExpandedChannel(fields.length)
                                }}>
                                    <Plus className="mr-1 h-3 w-3" /> {t('tracker.fields.addChannel')}
                                </Button>
                            </div>

                            <div className="grid gap-3 max-h-[400px] overflow-y-auto pr-2">
                                {fields.map((field, index) => {
                                    const isExpanded = expandedChannel === index
                                    return (
                                        <div key={field.id} className={cn("grid gap-3 border rounded-md bg-card transition-all", isExpanded ? "p-4 border-primary/20 bg-accent/5" : "px-3 py-2")}>
                                            <div className="flex items-center gap-3">
                                                <Button
                                                    type="button"
                                                    variant="ghost"
                                                    size="icon"
                                                    className="h-6 w-6 shrink-0"
                                                    onClick={() => setExpandedChannel(isExpanded ? null : index)}
                                                >
                                                    {isExpanded ? <ChevronDown className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />}
                                                </Button>

                                                <FormField
                                                    control={form.control}
                                                    name={`channels.${index}.enabled`}
                                                    render={({ field }) => (
                                                        <FormItem className="space-y-0">
                                                            <FormControl>
                                                                <Switch
                                                                    checked={field.value}
                                                                    onCheckedChange={field.onChange}
                                                                    className="scale-75 origin-left"
                                                                />
                                                            </FormControl>
                                                        </FormItem>
                                                    )}
                                                />

                                                {!isExpanded && (
                                                    <div className="flex-1 grid grid-cols-2 gap-3 items-center">
                                                        <div className="text-sm font-medium truncate">
                                                            {t(`channel.${form.getValues(`channels.${index}.name`)}`)}
                                                        </div>
                                                        <div className="text-xs text-muted-foreground truncate">
                                                            {form.getValues(`channels.${index}.type`) === 'prerelease' ? t('tracker.fields.preRelease') :
                                                                form.getValues(`channels.${index}.type`) === 'release' ? t('tracker.fields.release') :
                                                                    t('tracker.fields.all')}
                                                        </div>
                                                    </div>
                                                )}

                                                {isExpanded && <div className="flex-1 font-medium text-sm">{t(`channel.${form.watch(`channels.${index}.name`)}`)}</div>}

                                                <Button
                                                    type="button"
                                                    variant="ghost"
                                                    size="icon"
                                                    onClick={() => {
                                                        remove(index)
                                                        if (expandedChannel === index) setExpandedChannel(null)
                                                    }}
                                                    className="h-8 w-8 text-muted-foreground hover:text-destructive self-start mt-0"
                                                >
                                                    <Trash2 className="h-4 w-4" />
                                                </Button>
                                            </div>

                                            {isExpanded && (
                                                <div className="space-y-4 pl-11 pt-2">
                                                    <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                                                        <FormField
                                                            control={form.control}
                                                            name={`channels.${index}.name`}
                                                            render={({ field }) => (
                                                                <FormItem className="space-y-1">
                                                                    <FormLabel className="text-[10px] uppercase text-muted-foreground font-semibold">{t('tracker.fields.channelName')}</FormLabel>
                                                                    <Select onValueChange={field.onChange} value={field.value}>
                                                                        <FormControl>
                                                                            <SelectTrigger>
                                                                                <SelectValue />
                                                                            </SelectTrigger>
                                                                        </FormControl>
                                                                        <SelectContent>
                                                                            <SelectItem value="stable">{t('channel.stable')}</SelectItem>
                                                                            <SelectItem value="prerelease">{t('channel.prerelease')}</SelectItem>
                                                                            <SelectItem value="beta">{t('channel.beta')}</SelectItem>
                                                                            <SelectItem value="canary">{t('channel.canary')}</SelectItem>
                                                                        </SelectContent>
                                                                    </Select>
                                                                    <FormDescription className="text-xs">
                                                                        {t('tracker.fields.channelNameDesc')}
                                                                    </FormDescription>
                                                                </FormItem>
                                                            )}
                                                        />
                                                        <FormField
                                                            control={form.control}
                                                            name={`channels.${index}.type`}
                                                            render={({ field }) => (
                                                                <FormItem className="space-y-1">
                                                                    <FormLabel className="text-[10px] uppercase text-muted-foreground font-semibold">{t('tracker.fields.platformType')}</FormLabel>
                                                                    <Select onValueChange={(value) => field.onChange(value === "all" ? null : value)} value={field.value || "all"}>
                                                                        <FormControl>
                                                                            <SelectTrigger>
                                                                                <SelectValue />
                                                                            </SelectTrigger>
                                                                        </FormControl>
                                                                        <SelectContent>
                                                                            <SelectItem value="all">{t('tracker.fields.all')}</SelectItem>
                                                                            <SelectItem value="release">{t('tracker.fields.release')}</SelectItem>
                                                                            <SelectItem value="prerelease">{t('tracker.fields.preRelease')}</SelectItem>
                                                                        </SelectContent>
                                                                    </Select>
                                                                    <FormDescription className="text-xs">
                                                                        {t('tracker.fields.platformTypeDesc')}
                                                                    </FormDescription>
                                                                </FormItem>
                                                            )}
                                                        />
                                                    </div>

                                                    <div className="space-y-4 pt-2 border-t">
                                                        <div className="grid gap-4 items-start">
                                                            <FormField
                                                                control={form.control}
                                                                name={`channels.${index}.include_pattern`}
                                                                render={({ field }) => (
                                                                    <FormItem className="space-y-1">
                                                                        <div className="flex justify-between">
                                                                            <FormLabel className="text-[10px] uppercase text-muted-foreground font-semibold">{t('tracker.fields.includeRegex')}</FormLabel>
                                                                        </div>
                                                                        <FormControl>
                                                                            <Input {...field} className="font-mono text-xs" placeholder="e.g. ^v2\." />
                                                                        </FormControl>
                                                                        <FormDescription className="text-xs">
                                                                            {t('tracker.fields.includeRegexDesc')}
                                                                        </FormDescription>
                                                                    </FormItem>
                                                                )}
                                                            />
                                                            <FormField
                                                                control={form.control}
                                                                name={`channels.${index}.exclude_pattern`}
                                                                render={({ field }) => (
                                                                    <FormItem className="space-y-1">
                                                                        <div className="flex justify-between">
                                                                            <FormLabel className="text-[10px] uppercase text-muted-foreground font-semibold">{t('tracker.fields.excludeRegex')}</FormLabel>
                                                                        </div>
                                                                        <FormControl>
                                                                            <Input {...field} className="font-mono text-xs" placeholder="e.g. -rc\." />
                                                                        </FormControl>
                                                                        <FormDescription className="text-xs">
                                                                            {t('tracker.fields.excludeRegexDesc')}
                                                                        </FormDescription>
                                                                    </FormItem>
                                                                )}
                                                            />
                                                        </div>
                                                    </div>
                                                </div>
                                            )}
                                        </div>
                                    )
                                })}
                            </div>
                        </div>

                        <DialogFooter>
                            <Button type="submit" disabled={loading || fields.length === 0}>
                                {loading && <span className="mr-2 animate-spin">⚪</span>}
                                <Save className="mr-2 h-4 w-4" /> {t('tracker.save')}
                            </Button>
                        </DialogFooter>
                    </form>
                </Form>
            </DialogContent>
        </Dialog>
    )
}
