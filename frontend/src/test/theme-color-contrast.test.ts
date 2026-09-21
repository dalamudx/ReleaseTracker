import { describe, expect, it } from "vitest"
import { THEME_COLORS } from "@/config/theme-config"

type Oklch = readonly [number, number, number]

function parseOklch(value: string): Oklch {
    const match = value.match(/oklch\(([\d.]+)\s+([\d.]+)\s+([\d.]+)/)
    if (!match) throw new Error(`Unsupported color: ${value}`)
    return [Number(match[1]), Number(match[2]), Number(match[3])]
}

function relativeLuminance([lightness, chroma, hue]: Oklch): number {
    const radians = hue * Math.PI / 180
    const a = chroma * Math.cos(radians)
    const b = chroma * Math.sin(radians)
    const lPrime = lightness + 0.3963377774 * a + 0.2158037573 * b
    const mPrime = lightness - 0.1055613458 * a - 0.0638541728 * b
    const sPrime = lightness - 0.0894841775 * a - 1.291485548 * b
    const l = lPrime ** 3
    const m = mPrime ** 3
    const s = sPrime ** 3
    const linearRgb = [
        4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s,
        -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s,
        -0.0041960863 * l - 0.7034186147 * m + 1.707614701 * s,
    ].map(channel => Math.max(0, Math.min(1, channel)))
    return 0.2126 * linearRgb[0] + 0.7152 * linearRgb[1] + 0.0722 * linearRgb[2]
}

function contrast(a: Oklch, b: Oklch): number {
    const first = relativeLuminance(a)
    const second = relativeLuminance(b)
    return (Math.max(first, second) + 0.05) / (Math.min(first, second) + 0.05)
}

describe("theme accent contrast", () => {
    const surfaces = {
        light: parseOklch("oklch(1 0 0)"),
        dark: parseOklch("oklch(0.139 0.006 285.885)"),
    } as const

    for (const themeColor of THEME_COLORS) {
        for (const mode of ["light", "dark"] as const) {
            it(`keeps ${themeColor.value} readable in ${mode} mode`, () => {
                const accent = parseOklch(themeColor[mode].color)
                const foreground = parseOklch(themeColor[mode].foreground)
                expect(contrast(accent, surfaces[mode])).toBeGreaterThanOrEqual(4.5)
                expect(contrast(accent, foreground)).toBeGreaterThanOrEqual(4.5)
            })
        }
    }
})
