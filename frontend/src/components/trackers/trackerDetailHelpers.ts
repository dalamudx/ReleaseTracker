import type { useTranslation } from "react-i18next"

export function getTrackerChannelConfigValueLabel(
    key: string,
    value: unknown,
    t: ReturnType<typeof useTranslation>["t"],
): string {
    if (key === "fetch_mode") {
        if (value === "rest_first") {
            return t("tracker.fields.githubFetchModeRest")
        }
        if (value === "graphql_first") {
            return t("tracker.fields.githubFetchModeGraphql")
        }
    }

    return String(value)
}
