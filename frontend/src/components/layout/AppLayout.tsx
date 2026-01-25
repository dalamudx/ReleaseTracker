import { useState } from "react"
import { Outlet } from "react-router-dom"
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
    const [defaultOpen] = useState(getSidebarStateFromCookie)

    return (
        <SidebarProvider
            defaultOpen={defaultOpen}
            style={{ "--sidebar-width": "16rem", height: "100vh", overflow: "hidden" } as React.CSSProperties}
        >
            <AppSidebar />
            <SidebarInset className="overflow-hidden">
                <Header />
                <main className="flex flex-1 flex-col gap-4 p-4 overflow-hidden">
                    <Outlet />
                </main>
            </SidebarInset>
        </SidebarProvider>
    )
}
