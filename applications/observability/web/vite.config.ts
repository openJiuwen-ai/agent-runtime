import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import path from 'path';

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
  server: {
    port: 5274,
    strictPort: true,
    proxy: {
      // api.ts httpLoki 实际请求前缀（Loki API 反代到本机网关）
      '/loki': {
        target: 'http://127.0.0.1:9090',
        changeOrigin: true,
      },
      // Prometheus API 反代（MonitoringTab 资源监控）
      '/prometheus': {
        target: 'http://127.0.0.1:9090',
        changeOrigin: true,
        // 后端 Prometheus API 无 /prometheus 前缀，转发时去掉
        rewrite: (p) => p.replace(/^\/prometheus/, ''),
      },
    },
  },
});
