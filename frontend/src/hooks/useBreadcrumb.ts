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
        "/history": t("sidebar.history"),
        "/credentials": t("sidebar.credentials"),
    }

    const items: BreadcrumbItem[] = []

    let currentPath = ""
    pathnames.forEach((segment, index) => {
        currentPath += `/${segment}`
        const isLast = index === pathnames.length - 1

        // 特殊处理：如果路径在映射中存在，使用映射的名称
        // 否则如果在映射中有对应的 key，也使用它 (例如 /trackers)
        let label = routeLabels[currentPath]

        if (!label) {
            // 尝试直接匹配 segment (简单回退)
            label = segment
        }

        items.push({
            label,
            href: isLast ? undefined : currentPath,
        })
    })

    // 如果是根路径，显示 Dashboard
    if (pathnames.length === 0) {
        items.push({
            label: t("sidebar.dashboard"),
        })
    }

    return items
}
