import { ArrowLeft, LayoutDashboard } from "lucide-react"
import { useTranslation } from "react-i18next"
import { Link, useNavigate } from "react-router"

import { Button } from "@/components/ui/button"

export default function NotFoundPage() {
    const { t } = useTranslation()
    const navigate = useNavigate()

    return (
        <section className="flex min-h-full flex-1 items-center justify-center px-4 py-12 text-center">
            <div className="max-w-md">
                <p className="font-mono text-sm font-semibold tabular-nums text-primary">404</p>
                <h1 className="mt-2 text-2xl font-bold tracking-tight">{t("common.notFoundTitle")}</h1>
                <p className="mt-2 text-sm leading-6 text-muted-foreground">{t("common.notFoundDescription")}</p>
                <div className="mt-6 flex flex-wrap justify-center gap-2">
                    <Button variant="outline" onClick={() => navigate(-1)}>
                        <ArrowLeft className="size-4" aria-hidden="true" />
                        {t("common.goBack")}
                    </Button>
                    <Button asChild>
                        <Link to="/">
                            <LayoutDashboard className="size-4" aria-hidden="true" />
                            {t("common.goToDashboard")}
                        </Link>
                    </Button>
                </div>
            </div>
        </section>
    )
}
