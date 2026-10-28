import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Development: proxies /api → FastAPI on port 8000 (CLAUDE.md "API proxy")
// Production: Express.js (server.js) handles the /api proxy instead.
// API_TARGET (optional) points the proxy at another backend, e.g. a second test server on 8002.
const API_TARGET = process.env.API_TARGET || 'http://localhost:8000'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: API_TARGET,
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ''),
      },
    },
  },
})
