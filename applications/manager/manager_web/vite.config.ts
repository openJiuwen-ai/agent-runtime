import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import path from 'path'

export default defineConfig(() => {
  const envDir = path.resolve(__dirname, '..')
  const managerTarget = process.env.MANAGER_WEB_PROXY_TARGET || 'http://127.0.0.1:8765'
  const idpTarget = process.env.MANAGER_WEB_IDP_TARGET || 'http://127.0.0.1:8770'
  const userWebTarget = process.env.MANAGER_WEB_USER_WEB_TARGET || 'http://127.0.0.1:5173'
  const gatewayHttpTarget =
    process.env.MANAGER_WEB_GATEWAY_HTTP_TARGET || 'http://127.0.0.1:19002'
  const gatewayWsTarget =
    process.env.MANAGER_WEB_GATEWAY_WS_TARGET || 'http://127.0.0.1:19000'

  return {
    envDir,
    plugins: [react()],
    resolve: {
      alias: {
        '@': path.resolve(__dirname, './src'),
      },
    },
    server: {
      port: 5273,
      strictPort: true,
      // 与生产入口同一套路由前缀：/api→管理 API、/idp→认证中心、
      // /chat→User Web、/ws→Gateway WebSocket、/gateway-api 及文件路由→Gateway Web HTTP。
      // 三段 SPA 路由(/auth /user /manager)由 vite 开发服务器自动回退 index.html。
      // proxy target 统一读 MANAGER_WEB_*（与生产 manager-web Python 入口同一套 env，避免两套变量）
      // 本地开发默认 127.0.0.1；容器内由 manager-web.template.yaml 注入集群 DNS
      proxy: {
        '/gateway-api': {
          target: gatewayHttpTarget,
          changeOrigin: true,
          timeout: 0,
          proxyTimeout: 0,
          rewrite: (requestPath) => requestPath.replace(/^\/gateway-api/, '/api'),
        },
        '/file-api': { target: gatewayHttpTarget, changeOrigin: true },
        '/share-api': { target: gatewayHttpTarget, changeOrigin: true },
        '/ws': { target: gatewayWsTarget, ws: true, changeOrigin: true },
        '/chat': {
          target: userWebTarget,
          ws: true,
          changeOrigin: true,
          rewrite: (requestPath) => requestPath.replace(/^\/chat/, '') || '/',
        },
        '/manager-api': {
          target: managerTarget,
          changeOrigin: true,
          rewrite: (requestPath) => requestPath.replace(/^\/manager-api/, '/api'),
        },
        '/api': { target: managerTarget, changeOrigin: true },
        '/idp': {
          target: idpTarget,
          changeOrigin: true,
          // 去掉 /idp 前缀，与 manager-web 生产反代行为一致
          rewrite: (path) => path.replace(/^\/idp/, ''),
        },
      },
    },
  }
})
