import type { TrackerSourceType, TrackerStatus } from "@/api/types"

export function getTrackerError(tracker: TrackerStatus): string | null {
    return tracker.status.error ?? null
}

export function getTrackerLastVersion(tracker: TrackerStatus): string | null {
    return tracker.status.last_version ?? null
}

export function getTrackerLastCheck(tracker: TrackerStatus): string | null {
    return tracker.status.last_check ?? null
}

export function formatChannelSummary(tracker: TrackerStatus): string[] {
    const statusSourceTypes = (tracker.status.source_types ?? []).filter(
        (sourceType): sourceType is TrackerSourceType => sourceType !== undefined,
    )
    const sourceTypes = statusSourceTypes.length > 0
        ? statusSourceTypes
        : (tracker.sources ?? []).flatMap((source) => source.source_type ? [source.source_type] : [])

    return Array.from(new Set(sourceTypes)) as string[]
}
