import { Link } from "react-router-dom"
import { Separator } from "@/components/ui/separator"
import { SidebarTrigger } from "@/components/ui/sidebar"
import { ThemeCustomizer } from "@/components/ThemeCustomizer"
import { Languages } from "lucide-react"
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
                            <Button variant="ghost" size="icon">
                                <Languages className="h-[1.2rem] w-[1.2rem]" />
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
                    <Button variant="ghost" size="icon" asChild>
                        <a
                            href="https://github.com/dalamudx/ReleaseTracker"
                            target="_blank"
                            rel="noreferrer"
                        >
                            <svg
                                className="h-[1.2rem] w-[1.2rem]"
                                viewBox="0 0 24 24"
                                fill="currentColor"
                                aria-hidden="true"
                            >
                                <path d="M12 2C6.48 2 2 6.58 2 12.26c0 4.53 2.87 8.37 6.84 9.73.5.09.68-.22.68-.49 0-.24-.01-1.04-.01-1.89-2.78.62-3.37-1.21-3.37-1.21-.45-1.19-1.11-1.51-1.11-1.51-.91-.64.07-.63.07-.63 1 .07 1.53 1.06 1.53 1.06.89 1.56 2.34 1.11 2.91.85.09-.66.35-1.11.63-1.37-2.22-.26-4.55-1.14-4.55-5.07 0-1.12.39-2.03 1.03-2.75-.1-.26-.45-1.3.1-2.71 0 0 .84-.28 2.75 1.05A9.35 9.35 0 0 1 12 6.98c.85 0 1.71.12 2.51.35 1.91-1.33 2.75-1.05 2.75-1.05.55 1.41.2 2.45.1 2.71.64.72 1.03 1.63 1.03 2.75 0 3.94-2.34 4.8-4.57 5.06.36.32.68.94.68 1.9 0 1.37-.01 2.47-.01 2.8 0 .27.18.59.69.49A10.14 10.14 0 0 0 22 12.26C22 6.58 17.52 2 12 2Z" />
                            </svg>
                            <span className="sr-only">GitHub</span>
                        </a>
                    </Button>
                </div>
            </div>
        </header>
    )
}
