import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [
    react(),
    {
      name: "isolate-verification-cache",
      // API核验的中间件实例不能重写浏览器服务正在使用的React预构建文件。
      config(config) {
        if (config.server?.middlewareMode && !config.cacheDir) {
          return { cacheDir: `node_modules/.vite-verification/${process.pid}` };
        }
      },
    },
  ],
  build: {
    rollupOptions: {
      output: {
        manualChunks(id) {
          if (
            id.includes("node_modules/react-dom") ||
            id.includes("node_modules/react/") ||
            id.includes("node_modules/react-router")
          )
            return "vendor-react";
          if (id.includes("node_modules/antd") || id.includes("node_modules/@ant-design"))
            return "vendor-antd";
          if (id.includes("node_modules/recharts") || id.includes("node_modules/d3-"))
            return "vendor-charts";
          if (id.includes("node_modules/@tanstack")) return "vendor-query";
        },
      },
    },
  },
  server: {
    proxy: {
      "/api": "http://127.0.0.1:8000",
      "/health": "http://127.0.0.1:8000",
    },
  },
});
