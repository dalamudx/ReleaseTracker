
import { createContext, useContext } from "react"
import type { Theme, Color, Radius, Zoom, ThemeConfig } from "@/types/theme"

export const DEFAULT_THEME_CONFIG: ThemeConfig = {
    mode: "system",
    color: "neutral",
    zoom: "default",
    radius: 0.5,
}

export interface ThemeProviderState {
    theme: Theme
    setTheme: (theme: Theme) => void
    color: Color
    setColor: (color: Color) => void
    radius: Radius
    setRadius: (radius: Radius) => void
    zoom: Zoom
    setZoom: (zoom: Zoom) => void
    themeConfig: ThemeConfig
    updateTheme: (config: Partial<ThemeConfig>) => void
}

export const initialState: ThemeProviderState = {
    theme: "system",
    setTheme: () => null,
    color: "neutral",
    setColor: () => null,
    radius: 0.5,
    setRadius: () => null,
    zoom: "default",
    setZoom: () => null,
    themeConfig: DEFAULT_THEME_CONFIG,
    updateTheme: () => null,
}

export const ThemeProviderContext = createContext<ThemeProviderState>(initialState)

export const useTheme = () => {
    const context = useContext(ThemeProviderContext)

    if (context === undefined)
        throw new Error("useTheme must be used within a ThemeProvider")

    return context
}
