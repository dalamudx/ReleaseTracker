export function getCsrfToken(): string | null {
    for (const name of ["__Host-releasetracker-csrf", "releasetracker-csrf"]) {
        const cookie = document.cookie.split(";").map(value => value.trim()).find(value => value.startsWith(`${name}=`))
        if (cookie) return cookie.slice(name.length + 1)
    }
    return null
}

export async function withSessionLock<T>(operation: () => Promise<T>): Promise<T> {
    // Serialize refresh/migration across tabs on browsers with Web Locks. The
    // Axios single-flight promise also covers browsers without that API.
    if (navigator.locks) return navigator.locks.request("releasetracker-session", operation)
    return operation()
}
