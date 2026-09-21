import { useState } from "react"
import { useTranslation } from "react-i18next"
import { Outlet } from "react-router"
import { SidebarInset, SidebarProvider } from "@/components/ui/sidebar"
import { AppSidebar } from "@/components/layout/AppSidebar"
import { Header } from "@/components/layout/Header"

function getSidebarStateFromCookie(): boolean {
    const cookies = document.cookie.split('; ')
    const sidebarCookie = cookies.find(c => c.startsWith('sidebar_state='))
    if (sidebarCookie) {
        return sidebarCookie.split('=')[1] === 'true'
    }
    return true // default to open
}

export default function AppLayout() {
    const { t } = useTranslation()
    const [defaultOpen] = useState(getSidebarStateFromCookie)

    return (
        <SidebarProvider
            defaultOpen={defaultOpen}
            style={{ "--sidebar-width": "16rem", height: "100dvh", overflow: "hidden" } as React.CSSProperties}
        >
            <a
                href="#main-content"
                className="fixed left-3 top-3 z-[100] -translate-y-20 rounded-md bg-background px-3 py-2 text-sm font-medium text-foreground shadow-md ring-1 ring-border transition-transform focus:translate-y-0 focus:outline-none focus:ring-2 focus:ring-ring"
            >
                {t("common.skipToContent")}
            </a>
            <AppSidebar />
            <SidebarInset className="overflow-hidden">
                <Header />
                <main
                    id="main-content"
                    tabIndex={-1}
                    className="flex min-h-0 flex-1 flex-col gap-4 overflow-x-hidden overflow-y-auto p-4 focus:outline-none"
                >
                    <Outlet />
                </main>
            </SidebarInset>
        </SidebarProvider>
    )
}
