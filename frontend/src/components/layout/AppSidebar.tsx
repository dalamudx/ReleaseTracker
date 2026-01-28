import { Link, useLocation } from "react-router-dom"
import { LayoutDashboard, Boxes, Key, Package, Bell } from "lucide-react"
import { useTranslation } from "react-i18next"

import {
    Sidebar,
    SidebarContent,
    SidebarHeader,
    SidebarMenu,
    SidebarMenuButton,
    SidebarMenuItem,
    SidebarGroup,
    SidebarGroupContent,
    SidebarRail,
    SidebarFooter,
} from "@/components/ui/sidebar"
import { UserNav } from "./UserNav"

export function AppSidebar({ ...props }: React.ComponentProps<typeof Sidebar>) {
    const location = useLocation()
    const { t } = useTranslation()

    const navItems = [
        { title: t('sidebar.dashboard'), url: "/", icon: LayoutDashboard },
        { title: t('sidebar.trackers'), url: "/trackers", icon: Boxes },
        { title: t('sidebar.history'), url: "/history", icon: Package },
        { title: t('sidebar.credentials'), url: "/credentials", icon: Key },
        { title: t('sidebar.notifications'), url: "/notifications", icon: Bell },
    ]

    return (
        <Sidebar collapsible="icon" className="border-r border-border/50 bg-background/60 backdrop-blur-xl" {...props}>
            <SidebarHeader>
                <SidebarMenu>
                    <SidebarMenuItem>
                        <SidebarMenuButton size="lg" asChild>
                            <Link to="/">
                                <img src="/logo.svg" alt="Logo" className="size-8" />
                                <div className="grid flex-1 text-left text-sm leading-tight">
                                    <span className="truncate font-semibold">ReleaseTracker</span>
                                </div>
                            </Link>
                        </SidebarMenuButton>
                    </SidebarMenuItem>
                </SidebarMenu>
            </SidebarHeader>
            <SidebarContent>
                <SidebarGroup>
                    <SidebarGroupContent>
                        <SidebarMenu>
                            {navItems.map((item) => (
                                <SidebarMenuItem key={item.url}>
                                    <SidebarMenuButton
                                        asChild
                                        isActive={location.pathname === item.url || (item.url !== "/" && location.pathname.startsWith(item.url))}
                                        tooltip={item.title}
                                        className="data-[active=true]:bg-primary/10 data-[active=true]:text-primary data-[active=true]:font-medium transition-all duration-200"
                                    >
                                        <Link to={item.url}>
                                            <item.icon />
                                            <span>{item.title}</span>
                                        </Link>
                                    </SidebarMenuButton>
                                </SidebarMenuItem>
                            ))}
                        </SidebarMenu>
                    </SidebarGroupContent>
                </SidebarGroup>
            </SidebarContent>
            <SidebarFooter>
                <UserNav />
            </SidebarFooter>
            <SidebarRail />
        </Sidebar>
    )
}
