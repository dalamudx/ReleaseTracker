import fs from "node:fs"
import path from "node:path"

import tailwindcss from "@tailwindcss/vite"
import react from "@vitejs/plugin-react"
import { defineConfig } from "vitest/config"

const packageJson = JSON.parse(
  fs.readFileSync(path.resolve(__dirname, "package.json"), "utf-8"),
) as { version: string }

// https://vite.dev/config/
export default defineConfig(({ command }) => ({
  // FastAPI injects a runtime <base> element for production sub-path deployments.
  // Relative build assets let the same image run under any validated BASE URL path.
  base: command === "build" ? "./" : "/",
  define: {
    "import.meta.env.VITE_APP_VERSION": JSON.stringify(packageJson.version),
  },
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
  test: {
    include: ["src/**/*.{test,spec}.{ts,tsx}"],
    environment: "jsdom",
    setupFiles: "./src/test/setup.ts",
    globals: true,
    clearMocks: true,
  },
  server: {
    host: "0.0.0.0",
    proxy: {
      "/api": {
        target: "http://localhost:8000",
        changeOrigin: true,
      },
    },
  },
  build: {
    // Release Notes intentionally loads one cohesive lazy Markdown chunk. The
    // bundle checker enforces its separate 650 KiB ceiling and chunk count.
    chunkSizeWarningLimit: 650,
    rollupOptions: {
      output: {
        // Priorities keep core dependencies out of feature-only Chart/Markdown
        // chunks. Broad manualChunks recursively pulled React into the entry.
        codeSplitting: {
          groups: [
            {
              name: "react-vendor",
              priority: 100,
              test: /node_modules[\\/](react|react-dom|react-router|scheduler)([\\/]|$)/,
            },
            {
              name: "utility-vendor",
              priority: 95,
              test: /node_modules[\\/](clsx|tailwind-merge|class-variance-authority)([\\/]|$)/,
            },
            {
              name: "radix-vendor",
              priority: 90,
              test: /node_modules[\\/](@radix-ui|@base-ui|radix-ui)([\\/]|$)/,
            },
            { name: "tanstack-vendor", priority: 80, test: /node_modules[\\/]@tanstack[\\/]/ },
            {
              name: "i18n-vendor",
              priority: 80,
              test: /node_modules[\\/](i18next|react-i18next)([\\/]|$)/,
            },
            { name: "date-vendor", priority: 80, test: /node_modules[\\/]date-fns[\\/]/ },
            { name: "icons-vendor", priority: 80, test: /node_modules[\\/]lucide-react[\\/]/ },
            {
              name: "form-vendor",
              priority: 80,
              test: /node_modules[\\/]react-hook-form[\\/]/,
            },
            {
              name: "motion-vendor",
              priority: 70,
              test: /node_modules[\\/](framer-motion|motion-dom|motion-utils)([\\/]|$)/,
            },
            {
              name: "chart-vendor",
              priority: 60,
              test: /node_modules[\\/](recharts|d3-|victory-vendor)([\\/]|$)/,
            },
            {
              // Keep the lazy Markdown dependency graph cohesive. Splitting this
              // group by size creates cross-chunk initialization cycles in Rolldown.
              name: "markdown-vendor",
              priority: 60,
              test: /node_modules[\\/](react-markdown|remark-|rehype-|micromark|mdast-|hast-|unist-|unified|parse5|node-emoji|emojilib)/,
            },
          ],
        },
      },
    },
  },
}))
