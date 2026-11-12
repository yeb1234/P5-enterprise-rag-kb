import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      // 本地开发直连后端；SSE 需要关闭代理缓冲，vite 默认支持流式转发
      '/api': { target: 'http://localhost:8000', changeOrigin: true },
      '/health': { target: 'http://localhost:8000', changeOrigin: true },
    },
  },
  build: {
    outDir: 'dist',
    sourcemap: false,
    rollupOptions: {
      output: {
        // 骨架阶段就做分包：antd / charts / react 各自独立缓存，避免单文件 1.6MB
        manualChunks(id: string) {
          if (!id.includes('node_modules')) return;
          if (id.includes('recharts') || id.includes('d3-')) return 'charts';
          if (id.includes('antd') || id.includes('@ant-design') || id.includes('rc-')) return 'antd';
          // react 与其生态一起进 vendor，避免 vendor ↔ react 循环依赖
          return 'vendor';
        },
      },
    },
  },
});
