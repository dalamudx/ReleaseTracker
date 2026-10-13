import "@testing-library/jest-dom"
import { loadLanguage } from "@/i18n/config"

// Production loads one dictionary on demand; tests switch freely, so preload both.
await Promise.all([loadLanguage("zh"), loadLanguage("en")])
