import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'
import path from 'path'

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')
  const apiKey = env.VITE_API_KEY ?? ''

  return {
    plugins: [react()],
    resolve: {
      alias: { '@': path.resolve(__dirname, './src') },
    },
    server: {
      port: 3000,
      proxy: {
        '/api': {
          target: 'http://localhost:8000',
          rewrite: (p: string) => p.replace(/^\/api/, ''),
          configure: (proxy: any) => {
            proxy.on('proxyReq', (proxyReq: any) => {
              if (apiKey) proxyReq.setHeader('X-Api-Key', apiKey)
            })
          },
        },
        '/ws': { target: 'ws://localhost:8000', ws: true },
      },
    },
  }
})
