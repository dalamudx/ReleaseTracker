import { Link, useLocation } from "react-router"
import { navigationItems, isNavigationActive } from "@/lib/navigation"
import { useTranslation } from "react-i18next"

import { Badge } from "@/components/ui/badge"
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
    useSidebar,
} from "@/components/ui/sidebar"
import { UserNav } from "./UserNav"
import { assetPath } from "@/lib/base-path"

const appVersion = import.meta.env.VITE_APP_VERSION

export function AppSidebar({ ...props }: React.ComponentProps<typeof Sidebar>) {
    const location = useLocation()
    const { isMobile, setOpenMobile } = useSidebar()
    const { t } = useTranslation()

    const navItems = navigationItems.map(item => ({ ...item, title: t(item.titleKey) }))

    return (
        <Sidebar collapsible="icon" className="border-r border-border/50 bg-background/60 backdrop-blur-xl" {...props}>
            <SidebarHeader>
                <SidebarMenu>
                    <SidebarMenuItem>
                        <SidebarMenuButton size="lg" asChild>
                            <Link to="/">
                                <img src={assetPath("logo.svg")} alt="Logo" className="size-8" />
                                <div className="flex min-w-0 flex-1 items-center gap-2 text-left text-sm leading-tight">
                                    <span className="truncate font-semibold">ReleaseTracker</span>
                                    <Badge
                                        variant="outline"
                                        className="rounded-sm px-1 py-0 text-[10px] leading-4 text-muted-foreground"
                                    >
                                        v{appVersion}
                                    </Badge>
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
                                        isActive={isNavigationActive(location.pathname, item.url)}
                                        tooltip={item.title}
                                        className="data-[active=true]:bg-primary/10 data-[active=true]:text-primary data-[active=true]:font-medium transition-all duration-200"
                                    >
                                        <Link to={item.url} aria-current={isNavigationActive(location.pathname, item.url) ? "page" : undefined} onClick={() => { if (isMobile) setOpenMobile(false) }}>
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
