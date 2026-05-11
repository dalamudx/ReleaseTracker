import fs from "node:fs"
import path from "node:path"

import tailwindcss from "@tailwindcss/vite"
import react from "@vitejs/plugin-react"
import { defineConfig } from "vitest/config"

const packageJson = JSON.parse(
  fs.readFileSync(path.resolve(__dirname, "package.json"), "utf-8"),
) as { version: string }

// https://vite.dev/config/
export default defineConfig({
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
    environment: "jsdom",
    setupFiles: "./src/test/setup.ts",
    globals: true,
    clearMocks: true,
  },
  server: {
    host: '0.0.0.0',
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
    },
  },
  build: {
    rollupOptions: {
      output: {
        manualChunks(id) {
          if (!id.includes('node_modules')) {
            return
          }

          // Chart libraries are heavy and only loaded on pages that surface
          // visualisations.
          if (id.includes('recharts') || id.includes('d3-')) {
            return 'chart-vendor'
          }

          // Markdown rendering stack only pulled in by the lazy-loaded
          // ReleaseNotesModal. Keeping it isolated allows the browser to
          // cache it independently and skip downloading it entirely when the
          // modal is never opened.
          if (
            id.includes('react-markdown')
            || id.includes('remark-')
            || id.includes('rehype-')
            || id.includes('micromark')
            || id.includes('mdast-')
            || id.includes('hast-')
            || id.includes('unist-')
            || id.includes('unified')
          ) {
            return 'markdown-vendor'
          }

          if (id.includes('framer-motion')) {
            return 'motion-vendor'
          }

          if (id.includes('@radix-ui') || id.includes('@base-ui') || id.includes('radix-ui')) {
            return 'radix-vendor'
          }

          if (id.includes('@tanstack')) {
            return 'tanstack-vendor'
          }

          if (id.includes('i18next') || id.includes('react-i18next')) {
            return 'i18n-vendor'
          }

          if (id.includes('date-fns')) {
            return 'date-vendor'
          }

          if (id.includes('lucide-react')) {
            return 'icons-vendor'
          }

          if (id.includes('react-hook-form')) {
            return 'form-vendor'
          }

          // Core React runtime shared by the entire app. Keep this last so
          // the more specific groups above win.
          if (
            id.includes('/react/')
            || id.includes('/react-dom/')
            || id.includes('scheduler')
            || id.includes('react-router')
          ) {
            return 'react-vendor'
          }
        }
      }
    }
  }
})
