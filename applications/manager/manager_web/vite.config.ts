import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import path from 'path'

export default defineConfig({
  plugins: [react()],
  // 与 identity_center / manager_server 共用 applications/manager/.env
  envDir: path.resolve(__dirname, '..'),
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
  server: {
    port: 5273,
    strictPort: true,
    // 与生产入口同一套路由前缀：/api→管理API、/idp→认证中心、
    // /web/invoke→Gateway HTTP/SSE、/file-api→web 后端。
    // 三段 SPA 路由(/auth /user /manager)由 vite 开发服务器自动回退 index.html。
    proxy: {
      '/api': { target: 'http://127.0.0.1:8765', changeOrigin: true },
      '/idp': {
        target: 'http://127.0.0.1:8770',
        changeOrigin: true,
        // 去掉 /idp 前缀，与 manager-web 生产反代行为一致
        rewrite: (path) => path.replace(/^\/idp/, ''),
      },
      '/web/invoke': {
        target: 'http://127.0.0.1:19001',
        changeOrigin: true,
        timeout: 0,
        proxyTimeout: 0,
      },
      '/file-api': { target: 'http://127.0.0.1:19000', changeOrigin: true },
    },
  },
})
