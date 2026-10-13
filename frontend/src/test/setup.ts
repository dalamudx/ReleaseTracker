import "@testing-library/jest-dom"
// Production loads one dictionary on demand; DOM tests switch freely, so preload both.
// Pure Node tests only inspect data and should not initialize browser i18n.
if (typeof document !== "undefined") {
    const { loadLanguage } = await import("@/i18n/config")
    await Promise.all([loadLanguage("zh"), loadLanguage("en")])
}

export {}
