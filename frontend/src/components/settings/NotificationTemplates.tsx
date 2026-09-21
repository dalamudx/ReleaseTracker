import { useRef, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { Plus, Save, Trash2, Eye, RotateCcw, Copy, Check, FileCode, Sliders, Languages, HelpCircle, AlertCircle, Info } from 'lucide-react'
import { toast } from 'sonner'
import { notificationTemplates, type NotificationTemplate, type TemplatePreview } from '@/api/notification-templates'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Textarea } from '@/components/ui/textarea'
import { Label } from '@/components/ui/label'
import { Badge } from '@/components/ui/badge'
import { Card, CardHeader, CardTitle, CardDescription, CardContent } from '@/components/ui/card'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle } from '@/components/ui/alert-dialog'
import { QueryErrorState } from '@/components/common/QueryErrorState'

const QUICK_VARIABLES = [
    { label: '{{ subject.name }}', desc: '对象名称 (Tracker/Executor)' },
    { label: '{{ release.version }}', desc: '新发布版本号' },
    { label: '{{ release.channel }}', desc: '发布渠道 (如 stable/canary)' },
    { label: '{{ result.status }}', desc: '执行终态编码' },
    { label: '{{ links.detail }}', desc: '详情跳转 URL' },
    { label: '{{ labels.events[event] }}', desc: '本地化事件标题' },
]

export function NotificationTemplates() {
    const { t } = useTranslation()
    const client = useQueryClient()
    const query = useQuery({ queryKey: ['notification-templates'], queryFn: notificationTemplates.list })

    const [draft, setDraft] = useState<NotificationTemplate | null>(null)
    const [translations, setTranslations] = useState('{}')
    const [activeTab, setActiveTab] = useState<'editor' | 'translations' | 'variables'>('editor')

    // Preview controls
    const [event, setEvent] = useState('new_release')
    const [language, setLanguage] = useState('zh')
    const [channel, setChannel] = useState('wecom')
    const [scenario, setScenario] = useState('normal')
    const [preview, setPreview] = useState<TemplatePreview | null>(null)
    const [copied, setCopied] = useState(false)

    const [error, setError] = useState('')
    const [busy, setBusy] = useState(false)
    const [pending, setPending] = useState<'delete' | 'reset' | 'switch' | null>(null)
    const [next, setNext] = useState<NotificationTemplate | null>(null)
    const [dirty, setDirty] = useState(false)

    const revision = useRef(0)
    const current = draft || query.data?.builtin
    const references = query.data?.items.find(item => item.id === current?.id)?.references_count || 0

    const changed = () => {
        revision.current++
        setPreview(null)
        setError('')
    }

    const load = (value: NotificationTemplate) => {
        changed()
        setDraft(value)
        setTranslations(JSON.stringify(value.translations, null, 2))
        setDirty(false)
    }

    const select = (value: NotificationTemplate) => {
        if (dirty) {
            setNext(value)
            setPending('switch')
        } else {
            load(value)
        }
    }

    const edit = (key: 'name' | 'title' | 'body', value: string) => {
        if (!current) return
        changed()
        setDraft({ ...current, [key]: value })
        setDirty(true)
    }

    const input = () => {
        const parsed: unknown = JSON.parse(translations)
        if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
            throw new Error('invalidTranslations')
        }
        return { ...current!, translations: parsed as NotificationTemplate['translations'] }
    }

    const report = (err: unknown) => {
        const detail = (err as { response?: { data?: { detail?: unknown } } }).response?.data?.detail
        setError(typeof detail === 'string' ? `${t('notificationTemplates.failed')} ${detail}` : t('notificationTemplates.invalidTranslations'))
    }

    const renderPreview = async () => {
        const token = revision.current
        setBusy(true)
        setError('')
        try {
            const result = await notificationTemplates.preview({ ...input(), event, language, channel, scenario })
            if (token === revision.current) {
                setPreview(result)
            }
        } catch (err) {
            if (token === revision.current) {
                report(err)
            }
        } finally {
            setBusy(false)
        }
    }

    const save = async () => {
        setBusy(true)
        setError('')
        try {
            const saved = await notificationTemplates.save(input())
            load(saved)
            await client.invalidateQueries({ queryKey: ['notification-templates'] })
            toast.success(t('notificationTemplates.saved'))
        } catch (err) {
            report(err)
        } finally {
            setBusy(false)
        }
    }

    const confirm = async () => {
        const action = pending
        setPending(null)
        if (action === 'switch' && next) {
            load(next)
            return
        }
        if (action === 'reset' && current && query.data) {
            changed()
            setDraft({
                ...current,
                title: query.data.builtin.title,
                body: query.data.builtin.body,
                translations: {},
            })
            setTranslations('{}')
            setDirty(true)
            return
        }
        if (action === 'delete' && current?.id) {
            setBusy(true)
            try {
                await notificationTemplates.remove(current.id)
                load(query.data!.builtin)
                await client.invalidateQueries({ queryKey: ['notification-templates'] })
                toast.success(t('common.delete'))
            } catch (err) {
                report(err)
            } finally {
                setBusy(false)
            }
        }
    }

    const handleCopyVariable = (variable: string) => {
        void navigator.clipboard.writeText(variable)
        toast.success(`${t('notificationTemplates.copySuccess')}: ${variable}`)
    }

    const handleCopyRendered = () => {
        if (!preview?.content) return
        void navigator.clipboard.writeText(preview.content)
        setCopied(true)
        setTimeout(() => setCopied(false), 2000)
        toast.success(t('common.copied'))
    }

    // Render preview on demand by user click to prevent cascading effect updates

    if (query.isError) return <QueryErrorState onRetry={() => void query.refetch()} />
    if (!current || !query.data) return <p role="status" className="p-4 text-sm text-muted-foreground">{t('common.loading')}</p>

    return (
        <section className="min-h-0 flex-1 overflow-y-auto" aria-label={t('notificationTemplates.title')}>
            <div className="space-y-6 pb-6">
                {/* 1. Header Toolbar */}
                <div className="flex flex-wrap items-center justify-between gap-4 border-b pb-4">
                    <div className="min-w-0">
                        <div className="flex items-center gap-2">
                            <h2 className="text-base font-semibold tracking-tight">{t('notificationTemplates.title')}</h2>
                            {current.id === null ? (
                                <Badge variant="secondary" className="text-xs">{t('notificationTemplates.builtin')}</Badge>
                            ) : (
                                <Badge variant="outline" className="text-xs">ID #{current.id} · v{current.revision}</Badge>
                            )}
                        </div>
                        <p className="mt-1 max-w-2xl text-xs text-muted-foreground leading-relaxed">
                            {t('notificationTemplates.description')}
                        </p>
                    </div>
                    <div className="flex flex-wrap items-center gap-2">
                        <Button
                            variant="outline"
                            size="sm"
                            disabled={busy}
                            onClick={() => select({ ...query.data!.builtin, name: t('notificationTemplates.newName') })}
                        >
                            <Plus className="mr-1.5 size-3.5" />
                            {t('notificationTemplates.create')}
                        </Button>
                    </div>
                </div>

                {/* 2. Template Selector Bar */}
                <div className="flex flex-wrap items-center justify-between gap-3 rounded-lg border bg-muted/20 p-3">
                    <div className="flex min-w-0 flex-1 items-center gap-3">
                        <Label htmlFor="template-selection" className="text-xs font-medium whitespace-nowrap text-muted-foreground">
                            {t('notificationTemplates.select')}
                        </Label>
                        <Select
                            value={String(current.id ?? 'builtin')}
                            onValueChange={(id) =>
                                select(id === 'builtin' ? query.data!.builtin : query.data!.items.find((item) => String(item.id) === id)!)
                            }
                            disabled={busy}
                        >
                            <SelectTrigger id="template-selection" className="h-8 max-w-xs text-xs">
                                <SelectValue />
                            </SelectTrigger>
                            <SelectContent>
                                <SelectItem value="builtin" className="text-xs">
                                    {t('notificationTemplates.builtin')}
                                </SelectItem>
                                {query.data.items.map((item) => (
                                    <SelectItem key={item.id} value={String(item.id)} className="text-xs">
                                        {item.name}
                                    </SelectItem>
                                ))}
                            </SelectContent>
                        </Select>
                        {Boolean(references) && (
                            <span className="text-xs text-muted-foreground">
                                {t('notificationTemplates.inUse', { count: references })}
                            </span>
                        )}
                    </div>
                    <div className="flex items-center gap-2">
                        <Button variant="ghost" size="sm" className="h-8 text-xs" disabled={busy} onClick={() => setPending('reset')}>
                            <RotateCcw className="mr-1.5 size-3.5" />
                            {t('notificationTemplates.reset')}
                        </Button>
                        {current.id !== null && (
                            <Button
                                variant="ghost"
                                size="sm"
                                className="h-8 text-xs text-destructive hover:bg-destructive/10"
                                disabled={busy || Boolean(references)}
                                onClick={() => setPending('delete')}
                            >
                                <Trash2 className="mr-1.5 size-3.5" />
                                {t('common.delete')}
                            </Button>
                        )}
                    </div>
                </div>

                {/* 3. Main Split Workbench: Editor vs Live Preview */}
                <div className="grid min-w-0 gap-6 xl:grid-cols-12">
                    {/* Left Column (7 cols): Template Definition */}
                    <div className="min-w-0 xl:col-span-7">
                        <Card className="flex flex-col gap-0 border shadow-xs">
                            <CardHeader className="border-b px-5 py-3.5">
                                <div className="flex items-center justify-between">
                                    <div className="flex items-center gap-2">
                                        <FileCode className="size-4 text-primary" />
                                        <CardTitle className="text-sm font-semibold">{t('notificationTemplates.editorCardTitle')}</CardTitle>
                                    </div>
                                    <div className="flex items-center gap-2">
                                        <Input
                                            id="template-name"
                                            maxLength={80}
                                            value={current.name}
                                            onChange={(e) => edit('name', e.target.value)}
                                            disabled={busy}
                                            placeholder={t('notificationTemplates.name')}
                                            className="h-7 w-44 text-xs font-medium"
                                        />
                                    </div>
                                </div>
                                <CardDescription className="text-xs">{t('notificationTemplates.editorCardDescription')}</CardDescription>
                            </CardHeader>

                            <CardContent className="space-y-4 px-5 pt-4 pb-5">
                                {/* Navigation Tabs for Editor Panels */}
                                <Tabs value={activeTab} onValueChange={(v) => setActiveTab(v as typeof activeTab)} className="w-full">
                                    <TabsList className="h-8 w-full justify-start rounded-md bg-muted/60 p-0.5">
                                        <TabsTrigger value="editor" className="h-7 gap-1.5 px-3 text-xs">
                                            <FileCode className="size-3.5" />
                                            {t('notificationTemplates.tabEditor')}
                                        </TabsTrigger>
                                        <TabsTrigger value="translations" className="h-7 gap-1.5 px-3 text-xs">
                                            <Languages className="size-3.5" />
                                            {t('notificationTemplates.tabTranslations')}
                                        </TabsTrigger>
                                        <TabsTrigger value="variables" className="h-7 gap-1.5 px-3 text-xs">
                                            <HelpCircle className="size-3.5" />
                                            {t('notificationTemplates.tabVariables')}
                                        </TabsTrigger>
                                    </TabsList>

                                    {/* Sub-tab 1: Code Editor */}
                                    <TabsContent value="editor" className="mt-4 space-y-4">
                                        <div className="space-y-1.5">
                                            <div className="flex items-center justify-between">
                                                <Label htmlFor="template-title" className="text-xs font-semibold">
                                                    {t('notificationTemplates.titleTemplate')}
                                                </Label>
                                                <span className="text-[10px] text-muted-foreground">Jinja Expression</span>
                                            </div>
                                            <Textarea
                                                id="template-title"
                                                value={current.title}
                                                onChange={(e) => edit('title', e.target.value)}
                                                maxLength={2048}
                                                className="min-h-14 font-mono text-xs leading-relaxed"
                                                disabled={busy}
                                            />
                                        </div>

                                        <div className="space-y-1.5">
                                            <div className="flex items-center justify-between">
                                                <Label htmlFor="template-body" className="text-xs font-semibold">
                                                    {t('notificationTemplates.bodyTemplate')}
                                                </Label>
                                                <span className="text-[10px] text-muted-foreground">Jinja Template · UTF-8</span>
                                            </div>
                                            <Textarea
                                                id="template-body"
                                                value={current.body}
                                                onChange={(e) => edit('body', e.target.value)}
                                                maxLength={12000}
                                                className="min-h-[22rem] font-mono text-xs leading-relaxed"
                                                disabled={busy}
                                            />
                                        </div>

                                        {/* Quick Insert Variables Toolbar */}
                                        <div className="rounded-md border bg-muted/20 p-2.5">
                                            <div className="mb-2 flex items-center gap-1.5 text-xs font-medium text-muted-foreground">
                                                <Info className="size-3.5 text-primary" />
                                                <span>{t('notificationTemplates.quickVariables')}</span>
                                            </div>
                                            <div className="flex flex-wrap gap-1.5">
                                                {QUICK_VARIABLES.map((v) => (
                                                    <button
                                                        key={v.label}
                                                        type="button"
                                                        onClick={() => handleCopyVariable(v.label)}
                                                        className="group inline-flex items-center gap-1 rounded bg-background px-2 py-1 font-mono text-[11px] text-foreground border shadow-2xs hover:border-primary/50 hover:bg-accent transition-colors"
                                                        title={v.desc}
                                                    >
                                                        <span>{v.label}</span>
                                                        <Copy className="size-2.5 opacity-40 group-hover:opacity-100" />
                                                    </button>
                                                ))}
                                            </div>
                                        </div>
                                    </TabsContent>

                                    {/* Sub-tab 2: Translations Dictionary */}
                                    <TabsContent value="translations" className="mt-4 space-y-3">
                                        <div className="space-y-1.5">
                                            <div className="flex items-center justify-between">
                                                <Label htmlFor="template-translations" className="text-xs font-semibold">
                                                    {t('notificationTemplates.translations')}
                                                </Label>
                                                <Badge variant="outline" className="text-[10px]">JSON</Badge>
                                            </div>
                                            <Textarea
                                                id="template-translations"
                                                value={translations}
                                                onChange={(e) => {
                                                    changed()
                                                    setTranslations(e.target.value)
                                                    setDirty(true)
                                                }}
                                                className="min-h-[20rem] font-mono text-xs leading-relaxed"
                                                disabled={busy}
                                            />
                                        </div>
                                        <p className="text-xs text-muted-foreground leading-relaxed">
                                            {t('notificationTemplates.translationsHint')}
                                        </p>
                                    </TabsContent>

                                    {/* Sub-tab 3: Variables & Syntax Documentation */}
                                    <TabsContent value="variables" className="mt-4 space-y-3">
                                        <div className="rounded-lg border bg-muted/10 p-4 space-y-3">
                                            <p className="whitespace-pre-line text-xs text-muted-foreground leading-relaxed">
                                                {t('notificationTemplates.variablesHint')}
                                            </p>
                                            <div className="border-t pt-3">
                                                <span className="block text-xs font-medium text-foreground mb-1.5">上下文内置顶级字段：</span>
                                                <code className="block rounded bg-background p-2.5 font-mono text-xs text-foreground/90 border break-all">
                                                    event · category · subject.name · result.status · release · services · health · labels · locale · links.detail
                                                </code>
                                            </div>
                                        </div>
                                    </TabsContent>
                                </Tabs>
                            </CardContent>
                        </Card>
                    </div>

                    {/* Right Column (5 cols): Preview Workbench */}
                    <div className="min-w-0 xl:col-span-5">
                        <Card className="flex flex-col gap-0 border shadow-xs h-full">
                            <CardHeader className="border-b px-5 py-3.5">
                                <div className="flex items-center justify-between">
                                    <div className="flex items-center gap-2">
                                        <Eye className="size-4 text-primary" />
                                        <CardTitle className="text-sm font-semibold">{t('notificationTemplates.previewCardTitle')}</CardTitle>
                                    </div>
                                    <Button
                                        variant="outline"
                                        size="sm"
                                        className="h-7 gap-1 px-2.5 text-xs"
                                        onClick={() => void renderPreview()}
                                        disabled={busy}
                                    >
                                        <Sliders className="size-3.5" />
                                        {busy ? t('common.loading') : t('notificationTemplates.render')}
                                    </Button>
                                </div>
                                <CardDescription className="text-xs">{t('notificationTemplates.previewCardDescription')}</CardDescription>
                            </CardHeader>

                            <CardContent className="flex flex-col flex-1 space-y-4 px-5 pt-4 pb-5">
                                {/* Preview Parameters Grid (2x2) */}
                                <div className="grid grid-cols-1 gap-2.5 sm:grid-cols-2">
                                    {([
                                        ['event', event, query.data.events, setEvent],
                                        ['language', language, ['zh', 'en'], setLanguage],
                                        ['channel', channel, ['wecom', 'webhook'], setChannel],
                                        ['scenario', scenario, ['normal', 'timeout', 'no_healthcheck', 'unchecked', 'many', 'container'], setScenario],
                                    ] as const).map(([key, value, options, update]) => (
                                        <div className="space-y-1" key={key}>
                                            <Label htmlFor={`preview-${key}`} className="text-[11px] font-medium text-muted-foreground">
                                                {t(`notificationTemplates.${key}`)}
                                            </Label>
                                            <Select
                                                value={value}
                                                onValueChange={(v) => {
                                                    changed()
                                                    update(v)
                                                }}
                                                disabled={busy}
                                            >
                                                <SelectTrigger id={`preview-${key}`} className="h-7 text-xs w-full">
                                                    <SelectValue />
                                                </SelectTrigger>
                                                <SelectContent>
                                                    {options.map((option) => (
                                                        <SelectItem key={option} value={option} className="text-xs">
                                                            {t(`notificationTemplates.options.${option}`)}
                                                        </SelectItem>
                                                    ))}
                                                </SelectContent>
                                            </Select>
                                        </div>
                                    ))}
                                </div>

                                {/* Preview Output Canvas */}
                                <div className="relative flex min-h-[22rem] flex-1 flex-col rounded-lg border bg-muted/25">
                                    <div className="flex items-center justify-between border-b bg-muted/40 px-3 py-1.5">
                                        <div className="flex items-center gap-1.5">
                                            <span className="text-[11px] font-medium text-muted-foreground">
                                                {channel === 'wecom' ? 'WeCom Markdown' : 'Webhook Text'}
                                            </span>
                                            {preview && (
                                                <Badge variant="outline" className="h-4 px-1 text-[9px] font-normal">
                                                    {preview.locale.toUpperCase()}
                                                </Badge>
                                            )}
                                        </div>
                                        {preview?.content && (
                                            <Button
                                                variant="ghost"
                                                size="icon"
                                                className="h-5 w-5 text-muted-foreground hover:text-foreground"
                                                onClick={handleCopyRendered}
                                                title={t('notificationTemplates.copyRendered')}
                                            >
                                                {copied ? <Check className="size-3 text-success" /> : <Copy className="size-3" />}
                                            </Button>
                                        )}
                                    </div>
                                    <div className="flex-1 overflow-auto p-3.5" aria-live="polite" aria-label={t('notificationTemplates.output')}>
                                        {preview ? (
                                            <pre className="whitespace-pre-wrap break-words font-sans text-xs leading-relaxed text-foreground select-text">
                                                {preview.content}
                                            </pre>
                                        ) : (
                                            <div className="flex h-full flex-col items-center justify-center gap-2 text-center text-xs text-muted-foreground">
                                                <AlertCircle className="size-5 opacity-40" />
                                                <p>{t('notificationTemplates.previewEmpty')}</p>
                                            </div>
                                        )}
                                    </div>
                                </div>

                                <p className="text-[11px] leading-relaxed text-muted-foreground">
                                    {t('notificationTemplates.safety')}
                                </p>
                            </CardContent>
                        </Card>
                    </div>
                </div>

                {/* Error Banner */}
                {error && (
                    <div role="alert" className="flex items-start gap-2.5 rounded-lg border border-destructive/30 bg-destructive/5 p-3.5 text-xs text-destructive">
                        <AlertCircle className="size-4 shrink-0 mt-0.5" />
                        <span className="break-words font-mono leading-relaxed">{error}</span>
                    </div>
                )}

                {/* Sticky Action Footer */}
                <div className="sticky bottom-0 z-10 flex flex-wrap items-center justify-between gap-3 border-t bg-background/95 py-3 backdrop-blur-xs">
                    <p className="text-xs text-muted-foreground">{t('notificationTemplates.saveHint')}</p>
                    <Button
                        onClick={() => void save()}
                        disabled={busy || !current.name.trim() || !current.title.trim() || !current.body.trim()}
                        className="gap-1.5"
                    >
                        <Save className="size-4" />
                        {t(current.id === null ? 'notificationTemplates.saveCopy' : 'common.save')}
                    </Button>
                </div>
            </div>

            {/* Confirmation Dialogs */}
            <AlertDialog open={pending !== null} onOpenChange={(open) => { if (!open) setPending(null) }}>
                <AlertDialogContent>
                    <AlertDialogHeader>
                        <AlertDialogTitle>{t('common.confirm')}</AlertDialogTitle>
                        <AlertDialogDescription>
                            {t(pending === 'delete' ? 'notificationTemplates.deleteConfirm' : pending === 'reset' ? 'notificationTemplates.resetConfirm' : 'notificationTemplates.discardConfirm')}
                        </AlertDialogDescription>
                    </AlertDialogHeader>
                    <AlertDialogFooter>
                        <AlertDialogCancel>{t('common.cancel')}</AlertDialogCancel>
                        <AlertDialogAction onClick={() => void confirm()}>{t('common.confirm')}</AlertDialogAction>
                    </AlertDialogFooter>
                </AlertDialogContent>
            </AlertDialog>
        </section>
    )
}
