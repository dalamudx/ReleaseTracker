import { useId, useState } from "react"
import { ChevronDown, Loader2 } from "lucide-react"
import { useTranslation } from "react-i18next"
import { apiClient } from "@/api/client"
import type { ReleaseChannelInput } from "@/api/types"
import { CopyableCode } from "@/components/common/CopyableCode"
import { Button } from "@/components/ui/button"
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible"
import { Textarea } from "@/components/ui/textarea"
import { cn } from "@/lib/utils"

type Preview = { selected: string | null; reason: string | null; groups: { tag: string; family: string | null }[] }

const COMPLEX_TAG_REGEX_EXAMPLE = "^(?<version>\\d+)\\.v[\\w_]+-(?<revision>\\d+)$"

export function ChannelOrderPreview({ channel }: { channel: ReleaseChannelInput }) {
    const { t } = useTranslation()
    const id = useId()
    const [open, setOpen] = useState(false)
    const [tags, setTags] = useState("")
    const [result, setResult] = useState<{ key: string; data: Preview } | null>(null)
    const [busy, setBusy] = useState(false)
    const [error, setError] = useState<{ key: string; message: string } | null>(null)
    const key = JSON.stringify([channel.name, channel.type, channel.include_pattern || null, channel.exclude_pattern || null, tags])

    async function preview() {
        setBusy(true)
        setError(null)
        try {
            const { data } = await apiClient.post<Preview>("/api/trackers/version-preview", {
                channel: {
                    name: channel.name,
                    type: channel.type,
                    include_pattern: channel.include_pattern || null,
                    exclude_pattern: channel.exclude_pattern || null,
                },
                tags: tags.split(/\r?\n/).map(s => s.trim()).filter(Boolean),
            })
            setResult({ key, data })
        } catch {
            setError({ key, message: t("trackers.orderPreview.error") })
        } finally {
            setBusy(false)
        }
    }

    const visible = result?.key === key ? result.data : null
    const canPreview = !busy && Boolean(tags.trim())

    const handleTextareaKeyDown = (event: React.KeyboardEvent<HTMLTextAreaElement>) => {
        if ((event.metaKey || event.ctrlKey) && event.key === "Enter") {
            event.preventDefault()
            if (canPreview) {
                void preview()
            }
        }
    }

    return (
        <Collapsible
            open={open}
            onOpenChange={setOpen}
            className="col-span-full w-full min-w-0 rounded-lg border border-border/60 bg-muted/20 text-sm"
        >
            <CollapsibleTrigger asChild>
                <Button
                    type="button"
                    variant="ghost"
                    className="h-auto w-full justify-between gap-3 px-4 py-2.5 text-left font-normal hover:bg-muted/40 [&[data-state=open]>svg]:rotate-180"
                >
                    <div className="flex min-w-0 flex-wrap items-baseline gap-x-2.5 gap-y-0.5">
                        <span className="text-sm font-medium text-foreground">{t("trackers.orderPreview.title")}</span>
                        <span className="text-xs text-muted-foreground">{t("trackers.orderPreview.hint")}</span>
                    </div>
                    <ChevronDown aria-hidden="true" className="h-4 w-4 shrink-0 text-muted-foreground transition-transform duration-150" />
                </Button>
            </CollapsibleTrigger>

            <CollapsibleContent className="border-t border-border/50 px-4 pb-4 pt-3.5">
                <div className="grid gap-5 lg:grid-cols-[minmax(0,0.95fr)_minmax(0,1.05fr)]">
                    <div className="flex min-w-0 flex-col justify-between gap-3">
                        <div className="space-y-2 rounded-md border border-border/60 bg-background/80 p-3">
                            <div className="text-xs font-medium text-foreground">{t("trackers.orderPreview.advanced")}</div>
                            <p className="text-xs leading-relaxed text-muted-foreground">{t("trackers.orderPreview.advancedHint")}</p>
                            <CopyableCode
                                value={COMPLEX_TAG_REGEX_EXAMPLE}
                                truncate={false}
                                className="w-full justify-between bg-muted/50 px-2.5 py-1.5"
                                codeClassName="overflow-x-auto whitespace-nowrap"
                            />
                        </div>

                        <p className="text-xs leading-relaxed text-muted-foreground">{t("trackers.orderPreview.note")}</p>
                    </div>

                    <div className="min-w-0 space-y-2.5">
                        <div className="flex items-center justify-between gap-2">
                            <label htmlFor={id} className="text-xs font-medium text-foreground">
                                {t("trackers.orderPreview.tags")}
                            </label>
                            <Button
                                type="button"
                                size="sm"
                                variant="outline"
                                className="h-7 px-2.5 text-xs"
                                disabled={!canPreview}
                                onClick={() => void preview()}
                            >
                                {busy ? <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" /> : null}
                                {t("trackers.orderPreview.run")}
                            </Button>
                        </div>

                        <Textarea
                            id={id}
                            value={tags}
                            onChange={e => setTags(e.target.value)}
                            onKeyDown={handleTextareaKeyDown}
                            rows={4}
                            maxLength={25700}
                            placeholder={"v1.26.0\nv1.25.4\nlatest"}
                            className="bg-background font-mono text-xs"
                        />

                        <div aria-live="polite" className="space-y-2 break-all">
                            {error?.key === key && (
                                <p role="alert" className="rounded-md border border-destructive/30 bg-destructive/10 px-3 py-2 text-xs text-destructive">
                                    {error.message}
                                </p>
                            )}
                            {visible && (
                                <div className="space-y-2 rounded-md border border-border/60 bg-background/80 p-3">
                                    <div className="flex items-center gap-2">
                                        <span
                                            className={cn(
                                                "inline-block h-2 w-2 shrink-0 rounded-full",
                                                visible.selected ? "bg-emerald-500" : "bg-amber-500"
                                            )}
                                            aria-hidden="true"
                                        />
                                        <p className="text-xs font-medium text-foreground">
                                            {visible.selected ? t("trackers.orderPreview.selected", { tag: visible.selected }) : t("trackers.orderPreview.unordered")}
                                        </p>
                                    </div>
                                    {visible.groups.length > 0 && (
                                        <ul className="max-h-40 divide-y divide-border/50 overflow-y-auto rounded border border-border/50 bg-muted/15 text-xs">
                                            {visible.groups.map((item, index) => {
                                                const isSelected = visible.selected === item.tag
                                                return (
                                                    <li
                                                        key={item.tag}
                                                        className={cn(
                                                            "flex items-center justify-between gap-2 px-2.5 py-1.5",
                                                            isSelected && "bg-primary/5 font-medium"
                                                        )}
                                                    >
                                                        <div className="flex min-w-0 flex-1 items-baseline gap-2">
                                                            <span className="shrink-0 select-none font-mono text-[11px] text-muted-foreground">
                                                                #{index + 1}
                                                            </span>
                                                            <span className="min-w-0 break-all font-mono">
                                                                {item.tag}{!item.family && ` — ${t("trackers.orderPreview.unknown")}`}
                                                            </span>
                                                        </div>
                                                        {item.family ? (
                                                            <span className="shrink-0 rounded border border-border/60 bg-background px-1.5 py-0.5 font-mono text-[11px] font-normal text-muted-foreground">
                                                                {item.family}
                                                            </span>
                                                        ) : null}
                                                    </li>
                                                )
                                            })}
                                        </ul>
                                    )}
                                </div>
                            )}
                        </div>
                    </div>
                </div>
            </CollapsibleContent>
        </Collapsible>
    )
}
