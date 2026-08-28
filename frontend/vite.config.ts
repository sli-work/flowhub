import path from "path"
import react from "@vitejs/plugin-react"
import { defineConfig } from "vite"
import { fileViewerRenderers } from "@file-viewer/vite-plugin"

// https://vite.dev/config/
export default defineConfig({
  base: './',
  plugins: [
    react(),
    // @file-viewer 渲染器资源（pdf worker / wasm / 字体）自托管拷贝，内网可用
    fileViewerRenderers({ copyAssets: true }),
  ],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
  server: {
    proxy: {
      // 前后端联调：/api → FastAPI 后端（后端默认端口 8000）
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
    },
  },
});
