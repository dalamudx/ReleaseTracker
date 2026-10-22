import { useEffect, useId, useRef, useState } from "react"
import { keepPreviousData, useQuery } from "@tanstack/react-query"
import { useTranslation } from "react-i18next"
import { X } from "lucide-react"
import { api } from "@/api/client"
import { DataPagination } from "@/components/common/DataPagination"
import { QueryErrorState } from "@/components/common/QueryErrorState"
import { Button } from "@/components/ui/button"
import { Dialog, DialogClose, DialogContent, DialogDescription, DialogHeader, DialogTitle, DialogTrigger } from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Spinner } from "@/components/ui/spinner"
import { usePageSize } from "@/hooks/use-page-size"

type Props = { trackerName: string; sourceId: number; channelKey: string; revision: string }
const prefix = "trackers.aggregate.detail.selectionDiagnostics"

export function ChannelSelectionDiagnostics(props: Props) {
    const { t } = useTranslation()
    const [open, setOpen] = useState(false)
    return <Dialog open={open} onOpenChange={setOpen}>
        <DialogTrigger asChild>
            <Button variant="link" size="sm" className="h-8 px-0 text-xs">{t(`${prefix}.show`)}</Button>
        </DialogTrigger>
        <DialogContent showCloseButton={false} className="flex h-[min(42rem,calc(100dvh-2rem))] flex-col gap-0 overflow-hidden p-0 text-foreground sm:max-w-3xl">
            <DialogHeader className="shrink-0 flex-row items-start justify-between gap-4 border-b px-4 py-4 text-left sm:px-6">
                <div className="min-w-0 space-y-1">
                    <DialogTitle className="text-base">{t(`${prefix}.title`)}</DialogTitle>
                    <DialogDescription className="truncate text-xs" title={t(`${prefix}.dialogDescription`, { tracker: props.trackerName })}>{t(`${prefix}.dialogDescription`, { tracker: props.trackerName })}</DialogDescription>
                </div>
                <DialogClose asChild><Button variant="ghost" size="icon" className="size-9 shrink-0" aria-label={t("common.close")}><X className="size-4" aria-hidden="true" /></Button></DialogClose>
            </DialogHeader>
            {open && <DiagnosticResults {...props} />}
        </DialogContent>
    </Dialog>
}

function DiagnosticResults({ trackerName, sourceId, channelKey, revision }: Props) {
    const { t } = useTranslation()
    const searchId = useId()
    const listRef = useRef<HTMLDivElement>(null)
    const [search, setSearch] = useState("")
    const [query, setQuery] = useState("")
    const [page, setPage] = useState(1)
    const [pageSize, setPageSize] = usePageSize()
    useEffect(() => {
        const timer = setTimeout(() => setQuery(search.trim()), 300)
        return () => clearTimeout(timer)
    }, [search])
    useEffect(() => {
        if (listRef.current) listRef.current.scrollTop = 0
    }, [page, pageSize, query])
    const result = useQuery({
        queryKey: ["trackers", trackerName, "selection-diagnostics", sourceId, channelKey, revision, page, pageSize, query],
        queryFn: ({ signal }) => api.getSelectionDiagnostics(trackerName, { tracker_source_id: sourceId, channel_key: channelKey, offset: (page - 1) * pageSize, limit: pageSize, search: query || undefined }, signal),
        placeholderData: keepPreviousData,
    })
    const data = result.data
    const conflicting = !!data?.conflicting_families.length
    const basis = data?.ordering_basis
    const basisKind = basis?.startsWith("natural:") ? "natural" : basis?.startsWith("variant:") ? "variant" : ["version", "extracted", "integer", "pinned"].includes(basis || "") ? basis : "unknown"
    const familyLabel = (family: string) => family === "version" ? t(`${prefix}.versionFamily`) : family.startsWith("variant:") ? family.slice(8) : family
    const emptyKey = query ? "noMatching" : conflicting ? "noConflicts" : "empty"

    return <div className="flex min-h-0 flex-1 flex-col" aria-busy={result.isFetching}>
        {data && <div className="shrink-0 space-y-3 border-b px-4 py-3 sm:px-6">
            <div className="space-y-1">
                <p className="break-words text-sm font-medium">{t(`${prefix}.${conflicting && !data.ordering_basis ? "conflictSummary" : "summary"}`, { source: data.source_label, channel: data.channel_name, count: data.affected_count })}</p>
                {(!conflicting || basis) && <p className="text-xs leading-relaxed text-muted-foreground">{t(`${prefix}.${conflicting ? "conflictDescription" : "description"}`)}</p>}
            </div>
            <p className="text-xs text-muted-foreground">{t(`${prefix}.basis`)} <span className="font-medium text-foreground">{t(`${prefix}.basisLabels.${basisKind}`)}{basisKind === "variant" && basis ? ` · ${familyLabel(basis)}` : ""}</span></p>
            {data.affected_count > 0 && <div className="space-y-1.5">
                <label htmlFor={searchId} className="text-xs font-medium">{t(`${prefix}.search`)}</label>
                <Input id={searchId} placeholder={t(`${prefix}.searchPlaceholder`)} value={search} onChange={event => { setSearch(event.target.value); setPage(1) }} className="min-w-0" />
            </div>}
        </div>}
        <div ref={listRef} className="min-h-0 flex-1 overflow-y-auto overscroll-contain px-4 py-3 sm:px-6" tabIndex={0} role="region" aria-label={t(`${prefix}.problemTags`)}>
            {result.isError ? <QueryErrorState onRetry={() => void result.refetch()} /> : result.isPending || result.isPlaceholderData ? <div role="status" className="flex h-full items-center justify-center gap-2 text-sm text-muted-foreground"><Spinner className="size-4" />{t("common.loading")}</div> : data && <>
                {(data.include_pattern || data.exclude_pattern) && <details className="mb-3 rounded-md border px-3 py-2 text-xs">
                    <summary className="cursor-pointer font-medium">{t(`${prefix}.rules`)}</summary>
                    <div className="mt-2 space-y-2 text-muted-foreground">
                        {data.include_pattern && <p className="break-all">{t(`${prefix}.include`)} <code>{data.include_pattern}</code></p>}
                        {data.exclude_pattern && <p className="break-all">{t(`${prefix}.exclude`)} <code>{data.exclude_pattern}</code></p>}
                    </div>
                </details>}
                {!data.ordering_basis && conflicting ? <div className="space-y-3">
                    <p className="text-sm leading-relaxed text-muted-foreground">{t(`${prefix}.ambiguousFamilies`)}</p>
                    <ul aria-label={t(`${prefix}.conflictingFamilies`)} className="divide-y divide-border">
                        {data.conflicting_families.map(group => <li key={group.family} className="space-y-2 py-3">
                            <p className="break-all text-sm font-medium">{familyLabel(group.family)} <span className="font-normal text-muted-foreground">· {t(`${prefix}.familyCount`, { count: group.count })}</span></p>
                            <div className="flex flex-wrap gap-x-3 gap-y-1">{group.examples.map(tag => <code key={tag} className="min-w-0 select-text break-all text-xs text-muted-foreground">{tag}</code>)}</div>
                        </li>)}
                    </ul>
                </div> :
                data.items.length === 0 ? <p className="py-6 text-center text-sm leading-relaxed text-muted-foreground">{t(`${prefix}.${emptyKey}`)}</p> : <>
                    <div className={`mb-1 grid gap-3 border-b pb-2 text-xs font-medium text-muted-foreground ${conflicting ? "grid-cols-[minmax(0,1fr)_6rem]" : "grid-cols-1"}`} aria-hidden="true">
                        <span>{t(`${prefix}.tag`)}</span>{conflicting && <span>{t(`${prefix}.familyColumn`)}</span>}
                    </div>
                    <ul aria-label={t(`${prefix}.problemTags`)} className="divide-y divide-border">
                        {data.items.map(item => <li key={item.tag} className={`grid min-w-0 gap-3 py-3 text-sm ${conflicting ? "grid-cols-[minmax(0,1fr)_6rem]" : "grid-cols-1"}`}>
                            <div className="min-w-0 space-y-1.5">
                                <code className="block select-text break-all text-xs leading-relaxed">{item.tag}</code>
                                {!conflicting && <>
                                    <p className="text-xs leading-relaxed text-muted-foreground">{t(`${prefix}.reasons.${item.reason}`)}</p>
                                    {Object.entries(item.fields).map(([name, value]) => <p key={name} className="break-all text-xs text-muted-foreground"><code>{name}</code>: <code>{value || t(`${prefix}.${name === "revision" ? "defaultRevision" : "missingField"}`)}</code></p>)}
                                </>}
                            </div>
                            {conflicting && <span className="break-all text-xs leading-relaxed text-muted-foreground">{item.family ? familyLabel(item.family) : "—"}</span>}
                        </li>)}
                    </ul>
                </>}
            </>}
        </div>
        {data && data.affected_count > 0 && <div className="shrink-0 border-t px-4 py-3 sm:px-6" inert={result.isPlaceholderData || undefined}>
            <DataPagination page={page} pageSize={pageSize} total={data.total} onPageChange={setPage} onPageSizeChange={size => { setPageSize(size); setPage(1) }} className="mt-0" />
        </div>}
    </div>
}
