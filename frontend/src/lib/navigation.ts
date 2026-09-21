import { LayoutDashboard, Boxes, Key, Package, Webhook, Settings, Waypoints, Plug2, ListTodo } from "lucide-react"

// One source for sidebar entries and header breadcrumbs, including legacy redirects.
export const navigationItems = [
    { titleKey: "sidebar.dashboard", url: "/", icon: LayoutDashboard },
    { titleKey: "sidebar.trackers", url: "/trackers", icon: Boxes },
    { titleKey: "tasks.title", url: "/tasks", icon: ListTodo },
    { titleKey: "sidebar.executors", url: "/executors", icon: Waypoints },
    { titleKey: "sidebar.runtimeConnections", url: "/runtime-connections", icon: Plug2 },
    { titleKey: "sidebar.history", url: "/history", icon: Package },
    { titleKey: "sidebar.credentials", url: "/credentials", icon: Key },
    { titleKey: "sidebar.webhooks", url: "/webhooks", icon: Webhook },
    { titleKey: "sidebar.settings", url: "/settings", icon: Settings },
] as const

export function canonicalNavigationPath(path: string): string {
    return path === "/notifications" || path.startsWith("/notifications/")
        ? path.replace("/notifications", "/webhooks") : path
}

export function isNavigationActive(pathname: string, url: string): boolean {
    const path = canonicalNavigationPath(pathname)
    return path === url || (url !== "/" && path.startsWith(`${url}/`))
}
