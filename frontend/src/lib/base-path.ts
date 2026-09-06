const ROOT_BASE_PATH = "/"

function normalizeBasePath(value: string): string {
  if (!value || value === "." || value === "./") {
    return ROOT_BASE_PATH
  }

  const pathname = new URL(value, window.location.origin).pathname
  const normalized = pathname.replace(/\/+$/, "")
  return normalized || ROOT_BASE_PATH
}

/**
 * The server injects a <base> element from the validated public BASE URL in
 * production. Vite uses its root path during development and tests.
 */
export function appBasePath(): string {
  if (typeof document === "undefined") {
    return ROOT_BASE_PATH
  }

  const baseHref = document.querySelector("base")?.getAttribute("href")
  return normalizeBasePath(baseHref ?? import.meta.env.BASE_URL)
}

export function appPath(path: string): string {
  const normalizedPath = path.startsWith("/") ? path : `/${path}`
  const basePath = appBasePath()
  return basePath === ROOT_BASE_PATH ? normalizedPath : `${basePath}${normalizedPath}`
}

export function assetPath(asset: string): string {
  return appPath(asset)
}
