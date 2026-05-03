import type { TFunction } from "i18next"

import type { ReleaseNotesSubject } from "@/api/types"
import { CHANNEL_LABELS } from "@/lib/channel"

export function getReleaseChannelDisplayLabel(
    release: Pick<ReleaseNotesSubject, "channel_name"> & {
        channel_keys?: string[]
    },
    t: TFunction,
): string | null {
    const channelName = release.channel_name?.trim()
    if (!channelName) {
        const fallbackKey = release.channel_keys?.[0]
        const fallbackParts = fallbackKey?.split("-") ?? []
        const normalizedFallback = fallbackParts.length > 0 ? fallbackParts[fallbackParts.length - 1]?.trim() : undefined
        if (!normalizedFallback) {
            return null
        }

        return CHANNEL_LABELS[normalizedFallback]
            ? t(CHANNEL_LABELS[normalizedFallback])
            : normalizedFallback
    }

    return CHANNEL_LABELS[channelName]
        ? t(CHANNEL_LABELS[channelName])
        : channelName
}

export function getReleaseChannelTypeLabel(
    release: Pick<ReleaseNotesSubject, "channel_type" | "prerelease">,
    t: TFunction,
): string | null {
    const channelType = release.channel_type
    if (channelType === "release") {
        return t("tracker.fields.release")
    }
    if (channelType === "prerelease") {
        return t("tracker.fields.preRelease")
    }
    return release.prerelease
        ? t("tracker.fields.preRelease")
        : t("tracker.fields.release")
}

export function getReleaseChannelBadgeText(
    release: Pick<ReleaseNotesSubject, "channel_name"> & {
        channel_keys?: string[]
    },
    t: TFunction,
): string | null {
    return getReleaseChannelDisplayLabel(release, t)
}
