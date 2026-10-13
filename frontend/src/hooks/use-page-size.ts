import { useCallback, useEffect, useState } from "react"

export const SHARED_PAGE_SIZE_KEY = "settings.pagination.pageSize"
export const DEFAULT_PAGE_SIZE = 15

/**
 * Shared per-page size preference persisted in a single localStorage key.
 *
 * All list pages (Trackers, Executors, Tasks, History, Credentials, RuntimeConnections, etc.)
 * share this single configuration key so the user's rows-per-page choice applies globally.
 */
export function usePageSize(
    storageKey = SHARED_PAGE_SIZE_KEY,
    defaultSize = DEFAULT_PAGE_SIZE,
): [number, (value: number) => void] {
    const [pageSize, setPageSizeState] = useState<number>(() => {
        try {
            // Check global shared key first
            const shared = localStorage.getItem(SHARED_PAGE_SIZE_KEY)
            if (shared) {
                const parsed = Number(shared)
                if (Number.isFinite(parsed) && parsed > 0) {
                    return parsed
                }
            }
            // Fallback to legacy page-specific key if set
            if (storageKey && storageKey !== SHARED_PAGE_SIZE_KEY) {
                const legacy = localStorage.getItem(storageKey)
                if (legacy) {
                    const parsed = Number(legacy)
                    if (Number.isFinite(parsed) && parsed > 0) {
                        return parsed
                    }
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
                localStorage.setItem(SHARED_PAGE_SIZE_KEY, String(value))
                if (storageKey && storageKey !== SHARED_PAGE_SIZE_KEY) {
                    localStorage.setItem(storageKey, String(value))
                }
                window.dispatchEvent(
                    new CustomEvent("local-storage-page-size", { detail: value }),
                )
            } catch {
                // ignore storage errors
            }
        },
        [storageKey],
    )

    useEffect(() => {
        const handleCustom = (event: Event) => {
            const next = (event as CustomEvent<number>).detail
            if (Number.isFinite(next) && next > 0) {
                setPageSizeState(next)
            }
        }
        const handleStorage = (event: StorageEvent) => {
            if (event.key === SHARED_PAGE_SIZE_KEY && event.newValue) {
                const next = Number(event.newValue)
                if (Number.isFinite(next) && next > 0) {
                    setPageSizeState(next)
                }
            }
        }
        window.addEventListener("local-storage-page-size", handleCustom)
        window.addEventListener("storage", handleStorage)
        return () => {
            window.removeEventListener("local-storage-page-size", handleCustom)
            window.removeEventListener("storage", handleStorage)
        }
    }, [])

    return [pageSize, setPageSize]
}
