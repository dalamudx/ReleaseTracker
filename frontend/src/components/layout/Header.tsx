import { Link } from "react-router-dom"
import { Separator } from "@/components/ui/separator"
import { SidebarTrigger } from "@/components/ui/sidebar"
import { ThemeCustomizer } from "@/components/ThemeCustomizer"
import { Code2, Languages } from "lucide-react"
import { useTranslation } from "react-i18next"
import { Button } from "@/components/ui/button"
import {
    DropdownMenu,
    DropdownMenuContent,
    DropdownMenuItem,
    DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import {
    Breadcrumb,
    BreadcrumbList,
    BreadcrumbItem,
    BreadcrumbLink,
    BreadcrumbPage,
    BreadcrumbSeparator,
} from "@/components/ui/breadcrumb"
import { useBreadcrumb } from "@/hooks/useBreadcrumb"

export function Header() {
    const breadcrumbItems = useBreadcrumb()
    const { i18n, t } = useTranslation()

    const changeLanguage = (lng: string) => {
        i18n.changeLanguage(lng)
        localStorage.setItem('language', lng)
    }

    return (
        <header className="sticky top-0 z-50 flex h-14 shrink-0 items-center gap-2 border-b bg-background/95 backdrop-blur supports-[backdrop-filter]:bg-background/60 transition-[width,height] ease-linear group-has-data-[collapsible=icon]/sidebar-wrapper:h-14">
            <div className="flex w-full items-center justify-between gap-1 px-4 lg:gap-2 lg:px-6">
                <div className="flex items-center gap-1 lg:gap-2">
                    <SidebarTrigger className="-ml-1" />
                    <Separator
                        orientation="vertical"
                        className="mx-2 data-[orientation=vertical]:h-4"
                    />
                    <Breadcrumb>
                        <BreadcrumbList>
                            {breadcrumbItems.map((item, index) => [
                                index > 0 && <BreadcrumbSeparator key={`separator-${index}`} />,
                                <BreadcrumbItem key={`item-${index}`}>
                                    {item.href ? (
                                        <BreadcrumbLink asChild>
                                            <Link to={item.href}>{item.label}</Link>
                                        </BreadcrumbLink>
                                    ) : (
                                        <BreadcrumbPage>{item.label}</BreadcrumbPage>
                                    )}
                                </BreadcrumbItem>
                            ]).flat().filter(Boolean)}
                        </BreadcrumbList>
                    </Breadcrumb>
                </div>
                <div className="flex items-center gap-2">
                    <DropdownMenu>
                        <DropdownMenuTrigger asChild>
                            <Button variant="ghost" size="icon" className="h-8 w-8">
                                <Languages className="h-4 w-4" />
                                <span className="sr-only">{t('common.switchLanguage')}</span>
                            </Button>
                        </DropdownMenuTrigger>
                        <DropdownMenuContent align="end">
                            <DropdownMenuItem onClick={() => changeLanguage('zh')}>
                                中文
                            </DropdownMenuItem>
                            <DropdownMenuItem onClick={() => changeLanguage('en')}>
                                English
                            </DropdownMenuItem>
                        </DropdownMenuContent>
                    </DropdownMenu>
                    <ThemeCustomizer />
                    <Button variant="ghost" size="icon" className="h-8 w-8" asChild>
                        <a
                            href="https://github.com/dalamudx/ReleaseTracker"
                            target="_blank"
                            rel="noreferrer"
                        >
                            <Code2 className="h-4 w-4" />
                            <span className="sr-only">GitHub</span>
                        </a>
                    </Button>
                </div>
            </div>
        </header>
    )
}
