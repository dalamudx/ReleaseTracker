import i18n from "i18next"

const translateKey = (key: string, options?: Record<string, unknown>) =>
  options?.count === undefined ? key : `${key}:${options.count}`

/** Stable keys for behavior assertions; retain counts without coupling to translated copy. */
export function useTranslation() {
  return { t: translateKey, i18n }
}
