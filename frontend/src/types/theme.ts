
export type Theme = "dark" | "light" | "system"
export type Color = "neutral" | "red" | "rose" | "orange" | "green" | "blue" | "yellow" | "violet"
export type Radius = number
export type Zoom = "default" | "scaled" | "mono"

export interface ThemeConfig {
    mode: Theme
    color: Color
    zoom: Zoom
    radius: Radius
}
