import { useTranslation } from "react-i18next"

/** Accept legacy diagnostics without inventing a health result for missing data. */
export function ReadinessSummary({ result, recheck = false }: { result?: unknown; recheck?: boolean }) {
    const { t } = useTranslation()
    if (!result || typeof result !== "object") return null
    const data = result as Record<string, unknown>
    if (typeof data.outcome !== "string") return null
    const services = Array.isArray(data.services) ? data.services.filter((item): item is Record<string, unknown> => !!item && typeof item === "object") : []
    return <div className="space-y-1 py-1 text-xs break-words" role={data.outcome === "pending" ? "status" : undefined}>
        <p className="font-medium">{t(recheck ? "readiness.latestRecheck" : "readiness.summary")}: {t(`readiness.outcome.${data.outcome}`, { defaultValue: data.outcome })}
            {typeof data.update_duration_seconds === "number" && Number.isFinite(data.update_duration_seconds) ? ` · ${t("readiness.updateElapsed", { count: Math.round(data.update_duration_seconds) })}` : null}
            {typeof data.elapsed_seconds === "number" && Number.isFinite(data.elapsed_seconds) ? ` · ${t("readiness.elapsed", { count: Math.round(data.elapsed_seconds) })}` : null}
        </p>
        {recheck && <p className="text-muted-foreground">{t("readiness.recheckHelp")}</p>}
        {services.length > 0 && <ul className="space-y-1 text-muted-foreground">
            {services.map((service, index) => <li key={index}>
                {typeof service.service === "string" ? service.service : "—"}: {typeof service.status === "string" ? t(`readiness.outcome.${service.status}`, { defaultValue: service.status }) : "—"}
                {typeof service.method === "string" && service.method ? ` · ${service.method}` : ""}
                {typeof service.message === "string" && service.message ? ` — ${service.message}` : ""}
            </li>)}
        </ul>}
    </div>
}
