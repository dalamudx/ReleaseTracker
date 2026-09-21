import { useCallback, useEffect, useLayoutEffect, useState } from "react"
import { THEME_COLORS } from "@/config/theme-config"
import { ThemeProviderContext } from "@/context/theme-context"
import type { Theme, Color, Radius, Zoom, ThemeConfig } from "@/types/theme"

const VALID_MODES = new Set<Theme>(["dark", "light", "system"])
const VALID_COLORS = new Set<Color>(["neutral", "red", "rose", "orange", "green", "blue", "yellow", "violet"])
const VALID_ZOOMS = new Set<Zoom>(["default", "scaled", "mono"])

type ThemeProviderProps = {
    children: React.ReactNode
    defaultTheme?: Theme
    defaultColor?: Color
    defaultRadius?: Radius
    defaultZoom?: Zoom
    storageKey?: string
}

function readStoredConfig(storageKey: string, fallback: ThemeConfig): ThemeConfig {
    try {
        const raw = localStorage.getItem(`${storageKey}-config`)
        if (!raw) return fallback
        const parsed: unknown = JSON.parse(raw)
        if (!parsed || typeof parsed !== "object") return fallback
        const candidate = parsed as Partial<ThemeConfig>
        return {
            mode: candidate.mode && VALID_MODES.has(candidate.mode) ? candidate.mode : fallback.mode,
            color: candidate.color && VALID_COLORS.has(candidate.color) ? candidate.color : fallback.color,
            zoom: candidate.zoom && VALID_ZOOMS.has(candidate.zoom) ? candidate.zoom : fallback.zoom,
            radius: typeof candidate.radius === "number" && Number.isFinite(candidate.radius)
                ? Math.min(1, Math.max(0, candidate.radius))
                : fallback.radius,
        }
    } catch {
        return fallback
    }
}

export function ThemeProvider({
    children,
    defaultTheme = "system",
    defaultColor = "neutral",
    defaultRadius = 0.5,
    defaultZoom = "default",
    storageKey = "vite-ui-theme",
    ...props
}: ThemeProviderProps) {
    const [themeConfig, setThemeConfig] = useState<ThemeConfig>(() => readStoredConfig(storageKey, {
        mode: defaultTheme,
        color: defaultColor,
        zoom: defaultZoom,
        radius: defaultRadius,
    }))

    const applyTheme = useCallback((config: ThemeConfig) => {
        const root = window.document.documentElement
        root.classList.remove("light", "dark")
        const resolvedMode: "light" | "dark" = config.mode === "system"
            ? window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light"
            : config.mode
        root.classList.add(resolvedMode)
        root.setAttribute("data-scale", config.zoom)
        root.style.setProperty("--radius", `${config.radius}rem`)

        const colorTheme = THEME_COLORS.find((theme) => theme.value === config.color)
        if (colorTheme && config.color !== "neutral") {
            const palette = colorTheme[resolvedMode]
            root.setAttribute("data-theme", config.color)
            root.style.setProperty("--primary", palette.color)
            root.style.setProperty("--primary-foreground", palette.foreground)
            root.style.setProperty("--ring", palette.color)
        } else {
            root.removeAttribute("data-theme")
            root.style.removeProperty("--primary")
            root.style.removeProperty("--primary-foreground")
            root.style.removeProperty("--ring")
        }
    }, [])

    const updateTheme = useCallback((newConfig: Partial<ThemeConfig>) => {
        setThemeConfig((previous) => {
            const updatedConfig = { ...previous, ...newConfig }
            applyTheme(updatedConfig)
            try {
                localStorage.setItem(`${storageKey}-config`, JSON.stringify(updatedConfig))
            } catch {
                // Keep the in-memory preference when browser storage is unavailable.
            }
            window.dispatchEvent(new CustomEvent("theme-change", { detail: updatedConfig }))
            return updatedConfig
        })
    }, [applyTheme, storageKey])

    useLayoutEffect(() => {
        applyTheme(themeConfig)
    }, [applyTheme, themeConfig])

    useEffect(() => {
        if (themeConfig.mode !== "system") return
        const mediaQuery = window.matchMedia("(prefers-color-scheme: dark)")
        const handleChange = () => applyTheme(themeConfig)
        mediaQuery.addEventListener("change", handleChange)
        return () => mediaQuery.removeEventListener("change", handleChange)
    }, [themeConfig, applyTheme])

    const value = {
        theme: themeConfig.mode,
        setTheme: (theme: Theme) => updateTheme({ mode: theme }),
        color: themeConfig.color,
        setColor: (color: Color) => updateTheme({ color }),
        radius: themeConfig.radius,
        setRadius: (radius: Radius) => updateTheme({ radius }),
        zoom: themeConfig.zoom,
        setZoom: (zoom: Zoom) => updateTheme({ zoom }),
        themeConfig,
        updateTheme,
    }

    return (
        <ThemeProviderContext.Provider {...props} value={value}>
            {children}
        </ThemeProviderContext.Provider>
    )
}
