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
    },
  },
});
