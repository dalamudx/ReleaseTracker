import { useEffect, useState } from "react"
import {
    ExternalLink,
    FileText,
    Search,
    X,
} from "lucide-react"
import { useTranslation } from "react-i18next"
import { formatDistanceToNow } from "date-fns"
import { enUS, zhCN } from "date-fns/locale"

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
import {
    Tooltip,
    TooltipContent,
    TooltipTrigger,
} from "@/components/ui/tooltip"
import type { ReleaseHistoryItem, TrackerSourceType } from "@/api/types"
import { buildReleaseIdentityPrefix } from "@/pages/historyHelpers"
import { ReleaseNotesModal } from "@/components/dashboard/ReleaseNotesModalLazy"
import { getReleaseChannelDisplayLabel } from "@/components/dashboard/releaseNotesModalHelpers"
import { getReleaseTypeLabel } from "@/lib/channel"
import { useReleaseHistory } from "@/hooks/queries"
import { DataPagination } from "@/components/common/DataPagination"
import { usePageSize } from "@/hooks/use-page-size"

// Source-type label resolution matches the other list pages.
function getSourceTypeLabel(
    sourceType: TrackerSourceType | null | undefined,
    t: ReturnType<typeof useTranslation>["t"],
): string {
    if (!sourceType) return t("trackers.aggregate.detail.channelType.unknown")
    return t(`trackers.aggregate.detail.channelType.${sourceType}`)
}

export default function HistoryPage() {
    const { t, i18n } = useTranslation()
    const formatDate = useDateFormatter()
    const dateLocale = i18n?.language === "zh" ? zhCN : enUS
    const [page, setPage] = useState(1)
    const [pageSize, setPageSize] = usePageSize("settings.history.pageSize")
    const [search, setSearch] = useState("")
    const [selectedRelease, setSelectedRelease] = useState<ReleaseHistoryItem | null>(null)
    const [modalOpen, setModalOpen] = useState(false)
    const [debouncedSearch, setDebouncedSearch] = useState("")

    useEffect(() => {
        const timer = setTimeout(() => setDebouncedSearch(search), 300)
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

    const handleViewNotes = (release: ReleaseHistoryItem) => {
        setSelectedRelease(release)
        setModalOpen(true)
    }

    const formatRelative = (value: string): string => {
        try {
            return formatDistanceToNow(new Date(value), { addSuffix: true, locale: dateLocale })
        } catch {
            return formatDate(value)
        }
    }

    return (
        <div className="flex h-full min-h-0 flex-col gap-4">
            {/* Toolbar — search only (no add action on this page). */}
            <div className="flex flex-none flex-wrap items-center justify-between gap-3">
                <div className="w-full max-w-sm">
                    <InputGroup>
                        <InputGroupAddon align="inline-start">
                            <InputGroupText>
                                <Search className="h-4 w-4" />
                            </InputGroupText>
                        </InputGroupAddon>
                        <InputGroupInput
                            placeholder={t("history.searchPlaceholder")}
                            value={search}
                            onChange={(event) => {
                                setSearch(event.target.value)
                                setPage(1)
                            }}
                        />
                        {search ? (
                            <InputGroupAddon align="inline-end">
                                <Button
                                    variant="ghost"
                                    size="icon"
                                    className="h-6 w-6"
                                    onClick={() => {
                                        setSearch("")
                                        setPage(1)
                                    }}
                                    title={t("common.clear")}
                                >
                                    <X className="h-3.5 w-3.5" />
                                </Button>
                            </InputGroupAddon>
                        ) : null}
                    </InputGroup>
                </div>
            </div>

            <div className="flex min-h-0 flex-1 flex-col gap-3">
                <div className="min-h-0 flex-1 overflow-auto rounded-md border">
                    <Table containerClassName="overflow-visible">
                        <TableHeader className="sticky top-0 z-10 bg-background">
                            <TableRow>
                                <TableHead className="min-w-[12rem]">{t("history.table.tracker")}</TableHead>
                                <TableHead className="min-w-[10rem]">{t("history.table.version")}</TableHead>
                                <TableHead className="hidden md:table-cell">
                                    {t("history.table.releaseChannelType")}
                                </TableHead>
                                <TableHead className="hidden lg:table-cell">
                                    {t("history.table.identity")}
                                </TableHead>
                                <TableHead>{t("history.table.published")}</TableHead>
                                <TableHead className="w-[1%] text-right">
                                    {t("common.actions")}
                                </TableHead>
                            </TableRow>
                        </TableHeader>
                        <TableBody>
                            {isLoading ? (
                                <TableRow>
                                    <TableCell colSpan={6} className="h-24 text-center text-sm text-muted-foreground">
                                        {t("common.loading")}
                                    </TableCell>
                                </TableRow>
                            ) : releases.length === 0 ? (
                                <TableRow>
                                    <TableCell colSpan={6} className="h-24 text-center text-sm text-muted-foreground">
                                        {t("history.noResults")}
                                    </TableCell>
                                </TableRow>
                            ) : (
                                releases.map((release) => {
                                    const sourceType = release.primary_source?.source_type
                                    const sourceTypeLabel = getSourceTypeLabel(sourceType, t)
                                    const releaseChannelLabel = getReleaseChannelDisplayLabel(release, t)
                                    const identityPrefix = buildReleaseIdentityPrefix(release)
                                    const linkHref = release.changelog_url || release.url
                                    const absolutePublished = formatDate(release.published_at)
                                    const relativePublished = formatRelative(release.published_at)

                                    return (
                                        <TableRow
                                            key={`${release.tracker_release_history_id}-${release.published_at}`}
                                            className="transition-colors hover:bg-muted/40"
                                        >
                                            {/* Tracker — name + source type label. */}
                                            <TableCell className="py-3 align-middle">
                                                <div className="min-w-0 space-y-0.5">
                                                    <div
                                                        className="truncate text-sm font-medium text-foreground"
                                                        title={release.tracker_name}
                                                    >
                                                        {release.tracker_name}
                                                    </div>
                                                    <div className="text-[11px] uppercase tracking-wide text-muted-foreground">
                                                        {sourceTypeLabel}
                                                    </div>
                                                </div>
                                            </TableCell>

                                            {/* Version tag + prerelease badge. */}
                                            <TableCell className="py-3 align-middle">
                                                <div className="flex flex-wrap items-center gap-1.5">
                                                    <span
                                                        className="max-w-[14rem] truncate font-mono text-sm text-foreground"
                                                        title={release.tag_name}
                                                    >
                                                        {release.tag_name}
                                                    </span>
                                                    {release.prerelease ? (
                                                        <Badge
                                                            variant="outline"
                                                            className="h-4 shrink-0 rounded-full border-warning bg-transparent px-1.5 text-[9px] font-medium uppercase leading-none text-warning"
                                                        >
                                                            {getReleaseTypeLabel("prerelease", t)}
                                                        </Badge>
                                                    ) : null}
                                                </div>
                                            </TableCell>

                                            {/* Release channel (hidden on narrow screens). */}
                                            <TableCell className="hidden py-3 align-middle md:table-cell">
                                                {releaseChannelLabel ? (
                                                    <Badge
                                                        variant="outline"
                                                        className="border-border/60 bg-muted/30 text-xs"
                                                    >
                                                        {releaseChannelLabel}
                                                    </Badge>
                                                ) : (
                                                    <span className="text-xs text-muted-foreground">—</span>
                                                )}
                                            </TableCell>

                                            {/* Commit / digest prefix (hidden on narrow screens). */}
                                            <TableCell className="hidden py-3 align-middle lg:table-cell">
                                                {identityPrefix ? (
                                                    <code className="rounded bg-muted/40 px-1.5 py-0.5 font-mono text-xs text-muted-foreground">
                                                        {identityPrefix}
                                                    </code>
                                                ) : (
                                                    <span className="text-xs text-muted-foreground">—</span>
                                                )}
                                            </TableCell>

                                            {/* Published — relative time, absolute in tooltip. */}
                                            <TableCell className="py-3 align-middle text-xs text-muted-foreground">
                                                <span className="whitespace-nowrap tabular-nums" title={absolutePublished}>
                                                    {relativePublished}
                                                </span>
                                            </TableCell>

                                            {/* Actions. */}
                                            <TableCell className="w-[1%] whitespace-nowrap py-3 text-right align-middle">
                                                <div className="flex items-center justify-end gap-0.5">
                                                    <Tooltip>
                                                        <TooltipTrigger asChild>
                                                            <Button
                                                                variant="ghost"
                                                                size="icon"
                                                                className="h-7 w-7"
                                                                disabled={!release.body}
                                                                onClick={() => handleViewNotes(release)}
                                                            >
                                                                <FileText className="h-3.5 w-3.5" />
                                                                <span className="sr-only">
                                                                    {t("dashboard.recentReleases.viewNotes")}
                                                                </span>
                                                            </Button>
                                                        </TooltipTrigger>
                                                        <TooltipContent>
                                                            {t("dashboard.recentReleases.viewNotes")}
                                                        </TooltipContent>
                                                    </Tooltip>
                                                    {linkHref ? (
                                                        <Tooltip>
                                                            <TooltipTrigger asChild>
                                                                <Button
                                                                    variant="ghost"
                                                                    size="icon"
                                                                    className="h-7 w-7"
                                                                    asChild
                                                                >
                                                                    <a
                                                                        href={linkHref}
                                                                        target="_blank"
                                                                        rel="noreferrer"
                                                                    >
                                                                        <ExternalLink className="h-3.5 w-3.5" />
                                                                        <span className="sr-only">
                                                                            {t("dashboard.releaseNotes.viewSource")}
                                                                        </span>
                                                                    </a>
                                                                </Button>
                                                            </TooltipTrigger>
                                                            <TooltipContent>
                                                                {t("dashboard.releaseNotes.viewSource")}
                                                            </TooltipContent>
                                                        </Tooltip>
                                                    ) : null}
                                                </div>
                                            </TableCell>
                                        </TableRow>
                                    )
                                })
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

            <ReleaseNotesModal
                open={modalOpen}
                onOpenChange={setModalOpen}
                release={selectedRelease}
            />
        </div>
    )
}
