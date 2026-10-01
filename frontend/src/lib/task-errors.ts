import type { TFunction } from "i18next"

/** Localized task reason; unknown future codes never surface as raw keys. */
export function taskErrorLabel(t: TFunction, code: string): string {
    return t(`tasks.errors.${code}`, { defaultValue: t("tasks.errors.other") })
}
