import { useEffect, useState } from "react"
import { Search, ExternalLink, FileText, ChevronLeft, ChevronRight } from "lucide-react"
import { useTranslation } from "react-i18next"
import { useDateFormatter } from "@/hooks/use-date-formatter"

import { Button } from "@/components/ui/button"
import {
    InputGroup,
    InputGroupAddon,
    InputGroupInput,
    InputGroupText,
} from "@/components/ui/input-group"
import {
    Table,
    TableBody,
    TableCell,
    TableHead,
    TableHeader,
    TableRow,
} from "@/components/ui/table"

import { Badge } from "@/components/ui/badge"
import type { ReleaseHistoryItem, TrackerSourceType } from "@/api/types"
import { buildReleaseIdentityPrefix } from "@/pages/historyHelpers"
import { ReleaseNotesModal } from "@/components/dashboard/ReleaseNotesModal"
import { getReleaseChannelDisplayLabel } from "@/components/dashboard/releaseNotesModalHelpers"
import { useReleaseHistory } from "@/hooks/queries"
import {
    Select,
    SelectContent,
    SelectItem,
    SelectTrigger,
    SelectValue,
} from "@/components/ui/select"

function getSourceTypeLabel(sourceType: TrackerSourceType | null | undefined, t: ReturnType<typeof useTranslation>["t"]): string {
    if (!sourceType) {
        return t("trackers.aggregate.detail.channelType.unknown")
    }

    return t(`trackers.aggregate.detail.channelType.${sourceType}`)
}

export default function HistoryPage() {
    const { t } = useTranslation()
    const formatDate = useDateFormatter()
    const [page, setPage] = useState(1)
    const [pageSize, setPageSize] = useState(() => {
        const saved = localStorage.getItem('settings.history.pageSize')
        return saved ? Number(saved) : 15
    })
    const [search, setSearch] = useState("")
    const [selectedRelease, setSelectedRelease] = useState<ReleaseHistoryItem | null>(null)
    const [modalOpen, setModalOpen] = useState(false)
    const [debouncedSearch, setDebouncedSearch] = useState("")

    useEffect(() => {
        const timer = setTimeout(() => {
            setDebouncedSearch(search)
        }, 300)
        return () => clearTimeout(timer)
    }, [search])

    const skip = (page - 1) * pageSize
    const { data, isLoading } = useReleaseHistory({
        limit: pageSize,
        skip,
        search: debouncedSearch || undefined,
    })

    const releases = data?.items ?? []
    const total = data?.total ?? 0

    const handlePageSizeChange = (value: string) => {
        const newSize = Number(value)
        setPageSize(newSize)
        setPage(1)
        localStorage.setItem('settings.history.pageSize', String(newSize))
    }

    const handleViewNotes = (release: ReleaseHistoryItem) => {
        setSelectedRelease(release)
        setModalOpen(true)
    }

    const totalPages = Math.ceil(total / pageSize)

    return (
            <div className="flex h-full flex-col space-y-6 pr-1">
            <div className="flex flex-shrink-0 items-center justify-end">
                <div className="w-[300px]">
                    <InputGroup>
                        <InputGroupAddon align="inline-start">
                            <InputGroupText>
                                <Search className="h-4 w-4" />
                            </InputGroupText>
                        </InputGroupAddon>
                        <InputGroupInput
                            placeholder={t('history.searchPlaceholder')}
                            value={search}
                            onChange={(e) => {
                                setSearch(e.target.value)
                                setPage(1)
                            }}
                        />
                    </InputGroup>
                </div>
            </div>

            <div className="flex min-h-0 flex-1 flex-col space-y-4">
                <div className="min-h-0 flex-1 overflow-auto rounded-md border">
                    <Table containerClassName="overflow-visible">
                        <TableHeader className="sticky top-0 z-10 bg-background">
                            <TableRow>
                                <TableHead>{t('history.table.tracker')}</TableHead>
                                <TableHead>{t('history.table.channelType')}</TableHead>
                                <TableHead>{t('history.table.version')}</TableHead>
                                <TableHead>{t('history.table.channel')}</TableHead>
                                <TableHead>{t('history.table.published')}</TableHead>
                                <TableHead className="text-right">{t('history.table.link')}</TableHead>
                            </TableRow>
                        </TableHeader>
                        <TableBody>
                            {isLoading ? (
                                <TableRow>
                                    <TableCell colSpan={6} className="h-24 text-center">
                                        {t('common.loading')}
                                    </TableCell>
                                </TableRow>
                            ) : releases.length === 0 ? (
                                <TableRow>
                                    <TableCell colSpan={6} className="h-24 text-center">
                                        {t('history.noResults')}
                                    </TableCell>
                                </TableRow>
                            ) : (
                                releases.map((release) => {
                                    const releaseChannelLabel = getReleaseChannelDisplayLabel(release, t)
                                    const sourceTypeLabel = getSourceTypeLabel(release.primary_source?.source_type, t)
                                    const identityPrefix = buildReleaseIdentityPrefix(release)

                                    return (
                                    <TableRow
                                        key={`${release.tracker_release_history_id}-${release.published_at}`}
                                        className="transition-colors hover:bg-muted/50"
                                    >
                                        <TableCell className="py-2.5 font-medium">{release.tracker_name}</TableCell>
                                        <TableCell className="py-2.5">
                                            <Badge variant="secondary" className="text-[10px] uppercase">
                                                {sourceTypeLabel}
                                            </Badge>
                                        </TableCell>
                                        <TableCell className="py-2.5">
                                            <div className="relative inline-flex items-center gap-1.5">
                                                <span className="font-mono text-sm">{release.tag_name}</span>
                                                {identityPrefix ? (
                                                    <span
                                                        className="rounded bg-muted px-1.5 py-0.5 font-mono text-[10px] text-muted-foreground"
                                                        title={release.digest || release.commit_sha || undefined}
                                                    >
                                                        {identityPrefix}
                                                    </span>
                                                ) : null}
                                                {release.prerelease ? (
                                                    <Badge
                                                        variant="outline"
                                                        className="h-3.5 rounded-full border-amber-500 bg-transparent px-1 text-[9px] font-medium leading-none text-amber-500 transition-colors hover:bg-transparent"
                                                    >
                                                        Pre
                                                    </Badge>
                                                ) : null}
                                            </div>
                                        </TableCell>
                                        <TableCell className="py-2.5">
                                            {releaseChannelLabel ? (
                                                <Badge variant="outline" className="text-xs">
                                                    {releaseChannelLabel}
                                                </Badge>
                                            ) : (
                                                <span className="text-xs text-muted-foreground">—</span>
                                            )}
                                        </TableCell>
                                        <TableCell className="py-2.5 text-sm text-muted-foreground">
                                            {formatDate(release.published_at)}
                                        </TableCell>
                                        <TableCell className="py-2.5 text-right">
                                            <div className="flex justify-end gap-1">
                                                <Button
                                                    variant="ghost"
                                                    size="icon"
                                                    className="h-8 w-8"
                                                    disabled={!release.body}
                                                    onClick={() => handleViewNotes(release)}
                                                    title="View Release Notes"
                                                >
                                                    <FileText className="h-4 w-4" />
                                                </Button>
                                                <Button variant="ghost" size="icon" className="h-8 w-8" asChild>
                                                    <a href={release.changelog_url || release.url} target="_blank" rel="noreferrer">
                                                        <ExternalLink className="h-4 w-4" />
                                                    </a>
                                                </Button>
                                            </div>
                                        </TableCell>
                                    </TableRow>
                                    )
                                })
                            )}
                        </TableBody>
                    </Table>
                </div>

                <div className="flex flex-shrink-0 items-center justify-between">
                    <div className="flex-1 text-sm text-muted-foreground">
                        {t('pagination.totalItems', { count: total })}
                    </div>

                    <div className="flex items-center space-x-6 lg:space-x-8">
                        <div className="flex items-center space-x-2">
                            <p className="text-sm font-medium">{t('pagination.rowsPerPage')}</p>
                            <Select
                                value={`${pageSize}`}
                                onValueChange={handlePageSizeChange}
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

                        <div className="flex w-auto min-w-[100px] items-center justify-center whitespace-nowrap text-sm font-medium">
                            {t('pagination.pageOf', { page, total: totalPages || 1 })}
                        </div>

                        <div className="flex items-center space-x-2">
                            <Button
                                variant="outline"
                                className="h-8 w-8 p-0"
                                onClick={() => setPage(page - 1)}
                                disabled={page <= 1}
                            >
                                <span className="sr-only">{t('pagination.previousPage')}</span>
                                <ChevronLeft className="h-4 w-4" />
                            </Button>
                            <Button
                                variant="outline"
                                className="h-8 w-8 p-0"
                                onClick={() => setPage(page + 1)}
                                disabled={page >= totalPages}
                            >
                                <span className="sr-only">{t('pagination.nextPage')}</span>
                                <ChevronRight className="h-4 w-4" />
                            </Button>
                        </div>
                    </div>
                </div>
            </div>

            <ReleaseNotesModal
                open={modalOpen}
                onOpenChange={setModalOpen}
                release={selectedRelease}
            />
        </div>
    )
}
