import { useMemo, useSyncExternalStore } from "react"
import type { RestoreReceipt } from "@/api/backups"
const KEY = "instance-restore-receipt"
let memoryReceipt: string | null = null
function subscribe(callback: () => void) {
    window.addEventListener(KEY, callback)
    return () => window.removeEventListener(KEY, callback)
}
function snapshot() {
    let value = memoryReceipt
    try { value = sessionStorage.getItem(KEY) ?? memoryReceipt } catch { /* Keep the in-tab result when browser storage is unavailable. */ }
    try {
        const data = JSON.parse(value ?? "null")
        if (!data || !/^[a-f0-9]{32}$/.test(data.id) || !/^[A-Za-z0-9_-]{43}$/.test(data.token) || Date.now() - data.at >= 3600000) return null
        return value
    } catch { return null }
}
export function useRestoreReceipt() {
    const value = useSyncExternalStore(subscribe, snapshot, () => null)
    return useMemo(() => {
        if (!value) return null
        const data = JSON.parse(value) as RestoreReceipt
        return { id: data.id, token: data.token }
    }, [value])
}
export function saveReceipt(value: RestoreReceipt | null) {
    memoryReceipt = value ? JSON.stringify({ ...value, at: Date.now() }) : null
    try {
        if (memoryReceipt) sessionStorage.setItem(KEY, memoryReceipt)
        else sessionStorage.removeItem(KEY)
    } catch { /* The read-only capability still works in this loaded tab. */ }
    window.dispatchEvent(new Event(KEY))
}
export function restoreError(error: unknown) {
    const code = (error as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail
    return typeof code === "string" ? `restoreErrors.${code}` : "restoreErrors.restore_failed"
}
