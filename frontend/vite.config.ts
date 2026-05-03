import path from "path"
import tailwindcss from "@tailwindcss/vite"
import react from "@vitejs/plugin-react"
import { defineConfig } from "vitest/config"

// https://vite.dev/config/
export default defineConfig({
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
          if (id.includes('node_modules')) {
            if (id.includes('recharts')) {
              return 'chart-vendor';
            }
            if (id.includes('react') || id.includes('react-dom') || id.includes('react-router-dom') ||
              id.includes('lucide-react') || id.includes('framer-motion') || id.includes('@radix-ui')) {
              return 'react-vendor';
            }
            if (id.includes('date-fns')) {
              return 'date-vendor';
            }
          }
        }
      }
    }
  }
})
