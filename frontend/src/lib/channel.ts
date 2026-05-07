import type { TFunction } from 'i18next'
import i18n from '@/i18n/config'

export const CHANNEL_LABELS: Record<string, string> = {
    'stable': 'channel.stable',
    'prerelease': 'channel.prerelease',
    'beta': 'channel.beta',
    'canary': 'channel.canary',
}

export const RELEASE_TYPE_LABELS: Record<string, string> = {
    'release': 'releaseType.release',
    'prerelease': 'releaseType.prerelease',
}

export function getChannelLabel(name: string | null | undefined, t: TFunction = i18n.t): string {
    if (!name) return t('channel.unclassified')

    const translationKey = CHANNEL_LABELS[name]
    if (translationKey) {
        return t(translationKey)
    }

    return name
}

export function getReleaseTypeLabel(type: string | null | undefined, t: TFunction = i18n.t): string {
    if (!type) return t('channel.unclassified')

    const translationKey = RELEASE_TYPE_LABELS[type]
    if (translationKey) {
        return t(translationKey)
    }

    return type
}
