import { useLocation } from "react-router-dom"

interface BreadcrumbItem {
    label: string
    href?: string
}

const ROUTE_LABELS: Record<string, string> = {
    "/": "仪表板",
    "/trackers": "追踪器",
    "/history": "历史记录",
    "/credentials": "凭证管理",
}

export function useBreadcrumb(): BreadcrumbItem[] {
    const location = useLocation()
    const pathnames = location.pathname.split("/").filter((x) => x)

    const items: BreadcrumbItem[] = []

    let currentPath = ""
    pathnames.forEach((segment, index) => {
        currentPath += `/${segment}`
        const isLast = index === pathnames.length - 1
        const label = ROUTE_LABELS[currentPath] || segment

        items.push({
            label,
            href: isLast ? undefined : currentPath,
        })
    })

    // 如果是根路径，显示 Dashboard
    if (pathnames.length === 0) {
        items.push({
            label: "仪表板",
        })
    }

    return items
}
