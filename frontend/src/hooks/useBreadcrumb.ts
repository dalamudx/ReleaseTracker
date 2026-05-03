import { useLocation } from "react-router-dom"
import { useTranslation } from "react-i18next"

interface BreadcrumbItem {
    label: string
    href?: string
}

export function useBreadcrumb(): BreadcrumbItem[] {
    const location = useLocation()
    const { t } = useTranslation()
    const pathnames = location.pathname.split("/").filter((x) => x)

    const routeLabels: Record<string, string> = {
        "/": t("sidebar.dashboard"),
        "/trackers": t("sidebar.trackers"),
        "/executors": t("sidebar.executors"),
        "/runtime-connections": t("sidebar.runtimeConnections"),
        "/history": t("sidebar.history"),
        "/credentials": t("sidebar.credentials"),
        "/notifications": t("sidebar.notifications"),
        "/settings": t("sidebar.settings"),
    }

    const items: BreadcrumbItem[] = []

    let currentPath = ""
    pathnames.forEach((segment, index) => {
        currentPath += `/${segment}`
        const isLast = index === pathnames.length - 1

        // Special handling: if the path exists in the map, use the mapped name
        // Otherwise, if the map has a matching key, use it as well, for example /trackers
        let label = routeLabels[currentPath]

        if (!label) {
            // Try to match the segment directly as a simple fallback
            label = segment
        }

        items.push({
            label,
            href: isLast ? undefined : currentPath,
        })
    })

    // Show Dashboard for the root path
    if (pathnames.length === 0) {
        items.push({
            label: t("sidebar.dashboard"),
        })
    }

    return items
}
