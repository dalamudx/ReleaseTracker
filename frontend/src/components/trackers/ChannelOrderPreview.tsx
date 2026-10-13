import { useId, useState } from "react"
import { useTranslation } from "react-i18next"
import { apiClient } from "@/api/client"
import type { ReleaseChannelInput } from "@/api/types"
import { Button } from "@/components/ui/button"
import { Textarea } from "@/components/ui/textarea"

type Preview = { selected: string | null; reason: string | null; groups: { tag: string; family: string | null }[] }

export function ChannelOrderPreview({ channel }: { channel: ReleaseChannelInput }) {
    const { t } = useTranslation()
    const id = useId()
    const [tags, setTags] = useState("")
    const [result, setResult] = useState<{ key: string; data: Preview } | null>(null)
    const [busy, setBusy] = useState(false)
    const [error, setError] = useState<{ key: string; message: string } | null>(null)
    const key = JSON.stringify([channel, tags])
    async function preview() {
        setBusy(true)
        setError(null)
        try {
            const { data } = await apiClient.post<Preview>("/api/trackers/version-preview", {
                channel: { name: channel.name, type: channel.type, include_pattern: channel.include_pattern || null, exclude_pattern: channel.exclude_pattern || null },
                tags: tags.split(/\r?\n/).map(s => s.trim()).filter(Boolean),
            })
            setResult({ key, data })
        } catch {
            setError({ key, message: t("trackers.orderPreview.error") })
        } finally { setBusy(false) }
    }
    const visible = result?.key === key ? result.data : null
    return <details className="col-span-full w-full min-w-0 max-w-2xl text-sm">
        <summary className="cursor-pointer text-muted-foreground">{t("trackers.orderPreview.title")}</summary>
        <div className="mt-3 space-y-3">
            <p className="text-muted-foreground">{t("trackers.orderPreview.hint")}</p>
            <details className="text-muted-foreground">
                <summary className="cursor-pointer">{t("trackers.orderPreview.advanced")}</summary>
                <p className="mt-2">{t("trackers.orderPreview.advancedHint")}</p>
                <code className="mt-2 block overflow-x-auto text-xs">{'^(?<version>\\d+)\\.v[\\w_]+-(?<revision>\\d+)$'}</code>
            </details>
            <label htmlFor={id} className="block">{t("trackers.orderPreview.tags")}</label>
            <Textarea id={id} value={tags} onChange={e => setTags(e.target.value)} rows={4} maxLength={25700} />
            <Button type="button" variant="outline" disabled={busy || !tags.trim()} onClick={() => void preview()}>{t("trackers.orderPreview.run")}</Button>
            <div aria-live="polite" className="space-y-2 break-all">
                {error?.key === key && <p role="alert">{error.message}</p>}
                {visible && <>
                    <p>{visible.selected ? t("trackers.orderPreview.selected", { tag: visible.selected }) : t("trackers.orderPreview.unordered")}</p>
                    <ul className="space-y-1">{visible.groups.map(item => <li key={item.tag}>{item.tag}{!item.family && ` — ${t("trackers.orderPreview.unknown")}`}</li>)}</ul>
                    <p className="text-xs text-muted-foreground">{t("trackers.orderPreview.note")}</p>
                </>}
            </div>
        </div>
    </details>
}
