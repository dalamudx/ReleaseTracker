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

export function getTrackerChannelConfigEntries(config: Record<string, unknown>) {
    return Object.entries(config).filter(([key, value]) => key !== "published_at_mode" && Boolean(value))
}

type ArtifactTimeFields = {
    artifact_type?: string | null
    published_at?: string | null
    registry_pushed_at?: string | null
    registry_created_at?: string | null
    registry_modified_at?: string | null
    source_created_at?: string | null
}

export function getArtifactReleaseTimes(artifact: ArtifactTimeFields) {
    // Legacy container publication dates may be build/observation fallbacks.
    const fields = artifact.artifact_type === "container_image"
        ? [["pushed", artifact.registry_pushed_at], ["registered", artifact.registry_created_at], ["modified", artifact.registry_modified_at]] as const
        : [
            ["publication", artifact.published_at],
            ["pushed", artifact.registry_pushed_at],
            ["index", artifact.source_created_at],
        ] as const
    return fields.flatMap(([kind, value]) => value && Number.isFinite(Date.parse(value))
        ? [{ kind, value }]
        : [])
}
