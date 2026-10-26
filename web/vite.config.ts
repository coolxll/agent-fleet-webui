import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig(() => {
  const backendHost = process.env.BACKEND_HOST || '127.0.0.1'
  const backendPort = process.env.BACKEND_PORT || '8000'
  const frontendPort = process.env.FRONTEND_PORT ? parseInt(process.env.FRONTEND_PORT, 10) : 5173
  // Bind to all interfaces by default so phones/other machines (LAN, Tailscale) can reach the dev server.
  const frontendHost = process.env.FRONTEND_HOST || true

  return {
    plugins: [react()],
    server: {
      host: frontendHost,
      port: frontendPort,
      // Allow access via MagicDNS / custom hostnames (dev only).
      allowedHosts: true,
      proxy: {
        '/api': { target: `http://${backendHost}:${backendPort}`, changeOrigin: true },
        '/icons': { target: `http://${backendHost}:${backendPort}`, changeOrigin: true }
      }
    },
    build: { outDir: 'dist', emptyOutDir: true }
  }
})
