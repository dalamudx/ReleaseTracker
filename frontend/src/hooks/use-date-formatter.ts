import { useCallback } from 'react'
import { format } from 'date-fns'
import { zhCN, enUS } from 'date-fns/locale'
import { useTranslation } from 'react-i18next'

export function useDateFormatter() {
    const { i18n } = useTranslation()

    const formatDate = useCallback((date: Date | string | number, customPattern?: string) => {
        const d = new Date(date)
        const locale = i18n.language === 'zh' ? zhCN : enUS

        // Default patterns
        // zh: 2024年01月25日 18:30
        // en: Jan 25, 2024 18:30
        const defaultPattern = i18n.language === 'zh'
            ? 'yyyy年MM月dd日 HH:mm'
            : 'MMM d, yyyy HH:mm'

        return format(d, customPattern || defaultPattern, { locale })
    }, [i18n.language])

    return formatDate
}
