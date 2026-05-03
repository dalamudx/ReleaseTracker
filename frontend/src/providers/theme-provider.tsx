import { useEffect, useState, useCallback } from "react"
import { THEME_COLORS } from "@/config/theme-config"
import { ThemeProviderContext, DEFAULT_THEME_CONFIG } from "@/context/theme-context"
import { useMounted } from "@/hooks/use-mounted"
import type { Theme, Color, Radius, Zoom, ThemeConfig } from "@/types/theme"



type ThemeProviderProps = {
    children: React.ReactNode
    defaultTheme?: Theme
    defaultColor?: Color
    defaultRadius?: Radius
    defaultZoom?: Zoom
    storageKey?: string
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
    const [themeConfig, setThemeConfig] = useState<ThemeConfig>(() => {
        // Try to read the full configuration from localStorage
        const saved = localStorage.getItem(storageKey + "-config")
        if (saved) {
            try {
                return JSON.parse(saved)
            } catch {
                return DEFAULT_THEME_CONFIG
            }
        }
        return {
            mode: defaultTheme,
            color: defaultColor,
            zoom: defaultZoom,
            radius: defaultRadius,
        }
    })

    const mounted = useMounted()

    // Apply theme configuration to the DOM
    const applyTheme = useCallback((config: ThemeConfig) => {
        const root = window.document.documentElement

        // 1. Apply light/dark mode
        root.classList.remove("light", "dark")
        if (config.mode === "system") {
            const systemTheme = window.matchMedia("(prefers-color-scheme: dark)").matches
                ? "dark"
                : "light"
            root.classList.add(systemTheme)
        } else {
            root.classList.add(config.mode)
        }

        // 2. Apply color theme
        root.setAttribute("data-theme", config.color)

        // 3. Apply scale mode
        root.setAttribute("data-scale", config.zoom)

        // 4. Apply border radius
        root.style.setProperty("--radius", `${config.radius}rem`)

        // 5. Important: dynamically set CSS variables
        const colorTheme = THEME_COLORS.find((t) => t.value === config.color)
        if (colorTheme && config.color !== "neutral") {
            document.documentElement.setAttribute("data-theme", config.color)
            root.style.setProperty("--primary", colorTheme.color)
            root.style.setProperty("--primary-foreground", colorTheme.foreground)
            root.style.setProperty("--ring", colorTheme.color)
        } else {
            // Remove custom theme and use default neutral
            document.documentElement.removeAttribute("data-theme")
            // Default theme: remove overrides and restore original CSS definitions
            root.style.removeProperty("--primary")
            root.style.removeProperty("--primary-foreground")
            root.style.removeProperty("--ring")
        }
    }, [])

    // Centralized theme update method
    const updateTheme = useCallback((newConfig: Partial<ThemeConfig>) => {
        setThemeConfig((prev) => {
            const updatedConfig = { ...prev, ...newConfig }
            applyTheme(updatedConfig)
            localStorage.setItem(storageKey + "-config", JSON.stringify(updatedConfig))
            // Trigger a custom event to notify other components
            window.dispatchEvent(new CustomEvent("theme-change", { detail: updatedConfig }))
            return updatedConfig
        })
    }, [applyTheme, storageKey])

    // Listen for configuration changes and apply the theme
    useEffect(() => {
        if (mounted) {
            applyTheme(themeConfig)
        }
    }, [applyTheme, themeConfig, mounted])

    // Listen for system theme changes
    useEffect(() => {
        if (themeConfig.mode !== "system") return

        const mediaQuery = window.matchMedia("(prefers-color-scheme: dark)")
        const handleChange = () => {
            applyTheme(themeConfig)
        }

        mediaQuery.addEventListener("change", handleChange)
        return () => mediaQuery.removeEventListener("change", handleChange)
    }, [themeConfig, applyTheme])

    if (!mounted) {
        return null
    }

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
