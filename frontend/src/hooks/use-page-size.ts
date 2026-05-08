import { useCallback, useState } from "react"

/**
 * Persist a per-page size preference in localStorage.
 *
 * Returns a tuple matching useState so callers can drop it in place of
 * the previous hand-rolled lazy initializer + manual localStorage.setItem
 * that several pages duplicated.
 */
export function usePageSize(
    storageKey: string,
    defaultSize = 15,
): [number, (value: number) => void] {
    const [pageSize, setPageSizeState] = useState<number>(() => {
        try {
            const saved = localStorage.getItem(storageKey)
            if (saved) {
                const parsed = Number(saved)
                if (Number.isFinite(parsed) && parsed > 0) {
                    return parsed
                }
            }
        } catch {
            // localStorage may be unavailable in some environments (SSR, tests)
        }
        return defaultSize
    })

    const setPageSize = useCallback(
        (value: number) => {
            setPageSizeState(value)
            try {
                localStorage.setItem(storageKey, String(value))
            } catch {
                // ignore storage errors
            }
        },
        [storageKey],
    )

    return [pageSize, setPageSize]
}
