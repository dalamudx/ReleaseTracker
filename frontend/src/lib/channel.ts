import i18n from '@/i18n/config'

export const CHANNEL_LABELS: Record<string, string> = {
    'stable': 'channel.stable',
    'prerelease': 'channel.prerelease',
    'beta': 'channel.beta',
    'canary': 'channel.canary',
}

export function getChannelLabel(name: string | null | undefined): string {
    if (!name) return i18n.t('channel.unclassified')

    const translationKey = CHANNEL_LABELS[name]
    if (translationKey) {
        return i18n.t(translationKey)
    }

    return name
}
