import { useMemo } from "react"
import { useLocation } from "react-router"
import { useTranslation } from "react-i18next"
import { navigationItems, canonicalNavigationPath } from "@/lib/navigation"

interface BreadcrumbItem {
    label: string
    href?: string
}

export function useBreadcrumb(): BreadcrumbItem[] {
    const location = useLocation()
    const { t } = useTranslation()

    return useMemo(() => {
        const routeLabels: Record<string, string> = Object.fromEntries(
            navigationItems.map(item => [item.url, t(item.titleKey)])
        )

        const pathnames = canonicalNavigationPath(location.pathname).split("/").filter((x) => x)
        const items: BreadcrumbItem[] = []

        let currentPath = ""
        pathnames.forEach((segment, index) => {
            currentPath += `/${segment}`
            const isLast = index === pathnames.length - 1
            const label = routeLabels[currentPath] ?? segment
            items.push({
                label,
                href: isLast ? undefined : currentPath,
            })
        })

        // Show Dashboard for the root path
        if (pathnames.length === 0) {
            items.push({ label: t("sidebar.dashboard") })
        }

        return items
    }, [location.pathname, t])
}
