import { useEffect, useState } from "react"
import { Search, ExternalLink, FileText, ChevronLeft, ChevronRight, ChevronFirst, ChevronLast } from "lucide-react"
import { useTranslation } from "react-i18next"
import { useDateFormatter } from "@/hooks/use-date-formatter"

import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import {
    TableBody,
    TableCell,
    TableHead,
    TableHeader,
    TableRow,
} from "@/components/ui/table"

import { Badge } from "@/components/ui/badge"
import { api } from "@/api/client"
import type { Release } from "@/api/types"
import { ReleaseNotesModal } from "@/components/dashboard/ReleaseNotesModal"
import { getChannelLabel } from "@/lib/channel"

import {
    Select,
    SelectContent,
    SelectItem,
    SelectTrigger,
    SelectValue,
} from "@/components/ui/select"

export default function HistoryPage() {
    const { t } = useTranslation()
    const formatDate = useDateFormatter()
    const [releases, setReleases] = useState<Release[]>([])
    const [loading, setLoading] = useState(true)
    const [total, setTotal] = useState(0)
    const [page, setPage] = useState(1)
    const [pageSize, setPageSize] = useState(() => {
        const saved = localStorage.getItem('settings.history.pageSize')
        return saved ? Number(saved) : 15
    })
    const [search, setSearch] = useState("")
    const [selectedRelease, setSelectedRelease] = useState<Release | null>(null)
    const [modalOpen, setModalOpen] = useState(false)

    const [debouncedSearch, setDebouncedSearch] = useState("")

    // Debounce search input
    useEffect(() => {
        const timer = setTimeout(() => {
            setDebouncedSearch(search)
        }, 300)
        return () => clearTimeout(timer)
    }, [search])

    // Reset page when search term changes
    useEffect(() => {
        setPage(1)
    }, [debouncedSearch])

    const loadReleases = async () => {
        setLoading(true)
        try {
            const skip = (page - 1) * pageSize
            // Use debouncedSearch for actual API call
            const data = await api.getReleases({ limit: pageSize, skip, search: debouncedSearch })
            setReleases(data.items)
            setTotal(data.total)
        } catch (error) {
            console.error("Failed to load releases", error)
        } finally {
            setLoading(false)
        }
    }

    useEffect(() => {
        loadReleases()
    }, [page, pageSize, debouncedSearch])

    const handlePageSizeChange = (value: string) => {
        const newSize = Number(value)
        setPageSize(newSize)
        setPage(1) // Reset to first page when changing page size
        localStorage.setItem('settings.history.pageSize', String(newSize))
    }

    const handleViewNotes = (release: Release) => {
        setSelectedRelease(release)
        setModalOpen(true)
    }

    const totalPages = Math.ceil(total / pageSize)

    return (
        <div className="flex flex-col flex-1 min-h-0">
            <div className="flex items-center justify-end space-y-2 flex-shrink-0">
                <div className="flex items-center gap-2 w-[300px]">
                    <Search className="h-4 w-4 text-muted-foreground" />
                    <Input
                        placeholder={t('history.searchPlaceholder')}
                        value={search}
                        onChange={(e) => setSearch(e.target.value)}
                        className="h-9"
                    />
                </div>
            </div>

            <div className="rounded-md border mt-6 overflow-auto max-h-[calc(100vh-16rem)]">
                <table className="w-full caption-bottom text-sm">
                    <TableHeader className="sticky top-0 bg-background z-10">
                        <TableRow>
                            <TableHead>{t('history.table.tracker')}</TableHead>
                            <TableHead>{t('history.table.version')}</TableHead>
                            <TableHead>{t('history.table.channel')}</TableHead>
                            <TableHead>{t('history.table.published')}</TableHead>
                            <TableHead className="text-right">{t('history.table.link')}</TableHead>
                        </TableRow>
                    </TableHeader>
                    <TableBody>
                        {loading ? (
                            <TableRow>
                                <TableCell colSpan={5} className="h-24 text-center">
                                    Loading...
                                </TableCell>
                            </TableRow>
                        ) : releases.length === 0 ? (
                            <TableRow>
                                <TableCell colSpan={5} className="h-24 text-center">
                                    {t('history.noResults')}
                                </TableCell>
                            </TableRow>
                        ) : (
                            releases.map((release) => (
                                <TableRow
                                    key={`${release.id}-${release.published_at}`}
                                    className="hover:bg-muted/50 transition-colors"
                                >
                                    <TableCell className="font-medium py-2.5">{release.tracker_name}</TableCell>
                                    <TableCell className="py-2.5">
                                        <div className="relative inline-flex items-center">
                                            <span className="font-mono text-sm">{release.tag_name}</span>
                                            {release.prerelease && (
                                                <Badge
                                                    variant="outline"
                                                    className="absolute left-full -top-1.5 ml-0.5 h-3.5 px-1 text-[9px] font-medium leading-none rounded-full border-amber-500 text-amber-500 bg-transparent hover:bg-transparent transition-colors"
                                                >
                                                    Pre
                                                </Badge>
                                            )}
                                        </div>
                                    </TableCell>
                                    <TableCell className="py-2.5">
                                        <Badge variant="outline" className="text-xs">{getChannelLabel(release.channel_name || (release.prerelease ? "prerelease" : "stable"))}</Badge>
                                    </TableCell>
                                    <TableCell className="text-muted-foreground py-2.5 text-sm">
                                        {formatDate(release.published_at)}
                                    </TableCell>
                                    <TableCell className="text-right py-2.5">
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
                                                <a href={release.url} target="_blank" rel="noreferrer">
                                                    <ExternalLink className="h-4 w-4" />
                                                </a>
                                            </Button>
                                        </div>
                                    </TableCell>
                                </TableRow>
                            ))
                        )}
                    </TableBody>
                </table>
            </div>

            <div className="flex items-center justify-between mt-3 flex-shrink-0">
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
                                {[10, 15, 20, 30, 40, 50].map((pageSize) => (
                                    <SelectItem key={pageSize} value={`${pageSize}`}>
                                        {pageSize}
                                    </SelectItem>
                                ))}
                            </SelectContent>
                        </Select>
                    </div>

                    {totalPages > 1 && (
                        <div className="flex items-center space-x-2">
                            <div className="flex w-auto min-w-[100px] items-center justify-center text-sm font-medium whitespace-nowrap">
                                {t('pagination.pageOf', { page, total: totalPages })}
                            </div>
                            <div className="flex items-center space-x-2">
                                <Button
                                    variant="outline"
                                    className="hidden h-8 w-8 p-0 lg:flex"
                                    onClick={() => setPage(1)}
                                    disabled={page === 1}
                                >
                                    <span className="sr-only">{t('pagination.firstPage')}</span>
                                    <ChevronFirst className="h-4 w-4" />
                                </Button>
                                <Button
                                    variant="outline"
                                    className="h-8 w-8 p-0"
                                    onClick={() => setPage(p => Math.max(1, p - 1))}
                                    disabled={page === 1}
                                >
                                    <span className="sr-only">{t('pagination.previousPage')}</span>
                                    <ChevronLeft className="h-4 w-4" />
                                </Button>
                                <Button
                                    variant="outline"
                                    className="h-8 w-8 p-0"
                                    onClick={() => setPage(p => Math.min(totalPages, p + 1))}
                                    disabled={page === totalPages}
                                >
                                    <span className="sr-only">{t('pagination.nextPage')}</span>
                                    <ChevronRight className="h-4 w-4" />
                                </Button>
                                <Button
                                    variant="outline"
                                    className="hidden h-8 w-8 p-0 lg:flex"
                                    onClick={() => setPage(totalPages)}
                                    disabled={page === totalPages}
                                >
                                    <span className="sr-only">{t('pagination.lastPage')}</span>
                                    <ChevronLast className="h-4 w-4" />
                                </Button>
                            </div>
                        </div>
                    )}
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
