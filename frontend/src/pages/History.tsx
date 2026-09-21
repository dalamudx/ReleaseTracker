import { useEffect, useMemo, useState } from "react"
import {
    BookOpen,
    ExternalLink,
    FilterX,
    RefreshCw,
    Search,
    X,
} from "lucide-react"
import { useTranslation } from "react-i18next"
import { formatDistanceToNow } from "date-fns"
import { enUS, zhCN } from "date-fns/locale"
import { useQueryClient } from "@tanstack/react-query"

import { useDateFormatter } from "@/hooks/use-date-formatter"
import { QueryErrorState } from "@/components/common/QueryErrorState"
import { Button } from "@/components/ui/button"
import {
    InputGroup,
    InputGroupAddon,
    InputGroupInput,
    InputGroupText,
} from "@/components/ui/input-group"
import {
    Select,
    SelectContent,
    SelectItem,
    SelectTrigger,
    SelectValue,
} from "@/components/ui/select"
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
import { CopyableCode } from "@/components/common/CopyableCode"
import { ReleaseNotesModal } from "@/components/dashboard/ReleaseNotesModalLazy"
import { getReleaseChannelDisplayLabel } from "@/components/dashboard/releaseNotesModalHelpers"
import { getReleaseTypeLabel } from "@/lib/channel"
import { useReleaseHistory, useTrackers } from "@/hooks/queries"
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
    const queryClient = useQueryClient()
    const formatDate = useDateFormatter()
    const dateLocale = i18n?.language === "zh" ? zhCN : enUS

    const [page, setPage] = useState(1)
    const [pageSize, setPageSize] = usePageSize("settings.history.pageSize")
    const [search, setSearch] = useState("")
    const [debouncedSearch, setDebouncedSearch] = useState("")
    const [selectedTracker, setSelectedTracker] = useState<string>("all")
    const [selectedChannelType, setSelectedChannelType] = useState<string>("all")
    const [selectedChannel, setSelectedChannel] = useState<string>("all")
    const [selectedReleaseType, setSelectedReleaseType] = useState<"all" | "stable" | "prerelease">("all")

    const [selectedRelease, setSelectedRelease] = useState<ReleaseHistoryItem | null>(null)
    const [modalOpen, setModalOpen] = useState(false)

    // 预拉取追踪器列表供筛选使用
    const { data: trackersData } = useTrackers({ limit: 100 })
    const trackers = useMemo(() => trackersData?.items ?? [], [trackersData?.items])

    // 选定追踪器后，提取该追踪器配置的所有具体发布渠道
    const availableChannels = useMemo(() => {
        if (selectedTracker === "all") return []
        const currentTracker = trackers.find((item) => item.name === selectedTracker)
        if (!currentTracker) return []

        const channelNames = new Set<string>()
        for (const source of currentTracker.sources ?? []) {
            for (const rc of source.release_channels ?? []) {
                const name = rc.name || rc.release_channel_key
                if (name) channelNames.add(name)
            }
        }
        return Array.from(channelNames).sort()
    }, [selectedTracker, trackers])

    // 切换追踪器时重置具体渠道筛选
    const handleTrackerChange = (value: string) => {
        setSelectedTracker(value)
        setSelectedChannel("all")
        setPage(1)
    }

    useEffect(() => {
        const timer = setTimeout(() => setDebouncedSearch(search), 300)
        return () => clearTimeout(timer)
    }, [search])

    const prereleaseParam = useMemo(() => {
        if (selectedReleaseType === "stable") return false
        if (selectedReleaseType === "prerelease") return true
        return undefined
    }, [selectedReleaseType])

    const channelParam = useMemo(() => {
        if (selectedTracker !== "all" && selectedChannel !== "all") {
            return selectedChannel
        }
        return undefined
    }, [selectedTracker, selectedChannel])

    const skip = (page - 1) * pageSize
    const { data, isLoading, isFetching, isError, refetch } = useReleaseHistory({
        limit: pageSize,
        skip,
        search: debouncedSearch || undefined,
        tracker: selectedTracker !== "all" ? selectedTracker : undefined,
        prerelease: prereleaseParam,
        channel: channelParam,
    })

    const rawReleases = useMemo(() => data?.items ?? [], [data?.items])
    const total = data?.total ?? 0

    // 前端根据渠道类型（source_type）做二级过滤
    const releases = useMemo(() => {
        if (selectedChannelType === "all") return rawReleases
        return rawReleases.filter((release) => {
            const st = release.primary_source?.source_type ?? release.tracker_type
            return st === selectedChannelType
        })
    }, [rawReleases, selectedChannelType])

    const hasActiveFilters =
        search !== "" ||
        selectedTracker !== "all" ||
        selectedChannelType !== "all" ||
        selectedChannel !== "all" ||
        selectedReleaseType !== "all"

    const handleResetFilters = () => {
        setSearch("")
        setSelectedTracker("all")
        setSelectedChannelType("all")
        setSelectedChannel("all")
        setSelectedReleaseType("all")
        setPage(1)
    }

    const handleRefresh = () => {
        queryClient.invalidateQueries({ queryKey: ["releases", "history"] })
    }

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
            {/* 顶部丰富筛选工具栏 */}
            <div className="flex flex-none flex-col gap-2.5 rounded-lg border border-border/60 bg-card/60 p-3 shadow-xs sm:flex-row sm:flex-wrap sm:items-center sm:justify-between">
                <div className="flex flex-1 flex-wrap items-center gap-2">
                    {/* 搜索框 */}
                    <div className="w-full min-w-[14rem] sm:w-64">
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

                    {/* 追踪器过滤 */}
                    <Select value={selectedTracker} onValueChange={handleTrackerChange}>
                        <SelectTrigger className="h-9 w-full sm:w-[11rem]" aria-label={t("history.filters.trackerLabel")}>
                            <SelectValue placeholder={t("history.filters.allTrackers")} />
                        </SelectTrigger>
                        <SelectContent>
                            <SelectItem value="all">{t("history.filters.allTrackers")}</SelectItem>
                            {trackers.map((tracker) => (
                                <SelectItem key={tracker.name} value={tracker.name}>
                                    {tracker.name}
                                </SelectItem>
                            ))}
                        </SelectContent>
                    </Select>

                    {/* 渠道类型过滤 (GitHub / GitLab / Gitea / Container / Helm) */}
                    <Select
                        value={selectedChannelType}
                        onValueChange={(val) => {
                            setSelectedChannelType(val)
                            setPage(1)
                        }}
                    >
                        <SelectTrigger className="h-9 w-full sm:w-[9.5rem]" aria-label={t("history.filters.channelTypeLabel")}>
                            <SelectValue placeholder={t("history.filters.allChannelTypes")} />
                        </SelectTrigger>
                        <SelectContent>
                            <SelectItem value="all">{t("history.filters.allChannelTypes")}</SelectItem>
                            <SelectItem value="github">{t("trackers.aggregate.detail.channelType.github")}</SelectItem>
                            <SelectItem value="gitea">{t("trackers.aggregate.detail.channelType.gitea")}</SelectItem>
                            <SelectItem value="gitlab">{t("trackers.aggregate.detail.channelType.gitlab")}</SelectItem>
                            <SelectItem value="container">{t("trackers.aggregate.detail.channelType.container")}</SelectItem>
                            <SelectItem value="helm">{t("trackers.aggregate.detail.channelType.helm")}</SelectItem>
                        </SelectContent>
                    </Select>

                    {/* 选定具体追踪器后，显示其专有渠道过滤 (如 stable, canary, prerelease 等) */}
                    {availableChannels.length > 0 ? (
                        <Select
                            value={selectedChannel}
                            onValueChange={(val) => {
                                setSelectedChannel(val)
                                setPage(1)
                            }}
                        >
                            <SelectTrigger className="h-9 w-full sm:w-[9.5rem]" aria-label={t("history.filters.channelLabel")}>
                                <SelectValue placeholder={t("history.filters.allChannels")} />
                            </SelectTrigger>
                            <SelectContent>
                                <SelectItem value="all">{t("history.filters.allChannels")}</SelectItem>
                                {availableChannels.map((chan) => (
                                    <SelectItem key={chan} value={chan}>
                                        {chan}
                                    </SelectItem>
                                ))}
                            </SelectContent>
                        </Select>
                    ) : null}

                    {/* 版本类型过滤：全部 / 仅正式版 / 仅预发布版 */}
                    <Select
                        value={selectedReleaseType}
                        onValueChange={(val: "all" | "stable" | "prerelease") => {
                            setSelectedReleaseType(val)
                            setPage(1)
                        }}
                    >
                        <SelectTrigger className="h-9 w-full sm:w-[9.5rem]" aria-label={t("history.filters.typeLabel")}>
                            <SelectValue placeholder={t("history.filters.allTypes")} />
                        </SelectTrigger>
                        <SelectContent>
                            <SelectItem value="all">{t("history.filters.allTypes")}</SelectItem>
                            <SelectItem value="stable">{t("history.filters.stableOnly")}</SelectItem>
                            <SelectItem value="prerelease">{t("history.filters.prereleaseOnly")}</SelectItem>
                        </SelectContent>
                    </Select>

                    {/* 重置筛选按钮 */}
                    {hasActiveFilters ? (
                        <Button
                            variant="ghost"
                            size="sm"
                            onClick={handleResetFilters}
                            className="h-9 px-2.5 text-xs text-muted-foreground hover:text-foreground"
                            title={t("history.filters.reset")}
                        >
                            <FilterX className="mr-1.5 h-3.5 w-3.5" />
                            {t("history.filters.reset")}
                        </Button>
                    ) : null}
                </div>

                {/* 右侧操作：即时刷新与统计标签 */}
                <div className="flex items-center gap-2 self-end sm:self-center">
                    <span className="hidden text-xs text-muted-foreground sm:inline-block">
                        {t("history.filters.totalCount", { count: total })}
                    </span>
                    <Button
                        variant="outline"
                        size="icon"
                        className="h-9 w-9"
                        onClick={handleRefresh}
                        disabled={isFetching}
                        title={t("common.refresh")}
                    >
                        <RefreshCw className={`h-3.5 w-3.5 ${isFetching ? "animate-spin" : ""}`} />
                        <span className="sr-only">{t("common.refresh")}</span>
                    </Button>
                </div>
            </div>

            {/* 数据表格 */}
            <div className="flex min-h-0 flex-1 flex-col gap-3">
                {isError ? (
                    <QueryErrorState onRetry={() => void refetch()} />
                ) : (
                    <>
                <div className="min-h-0 overflow-auto rounded-md border sm:flex-1">
                    <Table containerClassName="overflow-visible">
                        <TableHeader className="sticky top-0 z-10 bg-background">
                            <TableRow>
                                <TableHead className="min-w-[7.5rem] sm:min-w-[12rem]">{t("history.table.tracker")}</TableHead>
                                <TableHead className="min-w-[7.5rem] sm:min-w-[10rem]">{t("history.table.version")}</TableHead>
                                <TableHead className="hidden md:table-cell">
                                    {t("history.table.releaseChannelType")}
                                </TableHead>
                                <TableHead className="hidden lg:table-cell">
                                    {t("history.table.identity")}
                                </TableHead>
                                <TableHead className="hidden sm:table-cell">{t("history.table.published")}</TableHead>
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
                                    const sourceType = release.primary_source?.source_type ?? release.tracker_type
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
                                                    {/* 代码仓库来源且标记为预发布时才显示预发布 Badge，容器来源不显示 */}
                                                    {release.prerelease && sourceType !== "container" ? (
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
                                                    <CopyableCode
                                                        value={release.digest || release.commit_sha || identityPrefix}
                                                        displayValue={identityPrefix}
                                                        className="text-muted-foreground"
                                                        title={release.digest || release.commit_sha || identityPrefix}
                                                    />
                                                ) : (
                                                    <span className="text-xs text-muted-foreground">—</span>
                                                )}
                                            </TableCell>

                                            {/* Published — relative time, absolute in tooltip. */}
                                            <TableCell className="hidden py-3 align-middle text-xs text-muted-foreground sm:table-cell">
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
                                                                <BookOpen className="h-3.5 w-3.5" />
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
                                                                        rel="noopener noreferrer"
                                                                    >
                                                                        <ExternalLink className="h-3.5 w-3.5" />
                                                                        <span className="sr-only">
                                                                            {t("common.openInNewTab")}
                                                                        </span>
                                                                    </a>
                                                                </Button>
                                                            </TooltipTrigger>
                                                            <TooltipContent>
                                                                {t("common.openInNewTab")}
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
                    onPageSizeChange={(newPageSize) => {
                        setPageSize(newPageSize)
                        setPage(1)
                    }}
                />
                    </>
                )}
            </div>

            <ReleaseNotesModal
                open={modalOpen}
                onOpenChange={setModalOpen}
                release={selectedRelease}
            />
        </div>
    )
}
