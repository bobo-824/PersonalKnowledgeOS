import { fileURLToPath } from "node:url"

import tailwindcss from "@tailwindcss/vite"
import react from "@vitejs/plugin-react"
import { defineConfig } from "vite"

// 端口从环境变量读取，默认值与 .env.example 保持一致。
// Blueprint 6.4 要求端口写入配置，不在代码里散落硬编码。
const backendPort = Number(process.env.PKOS_PORT ?? 8000)
const frontendPort = Number(process.env.PKOS_FRONTEND_PORT ?? 5173)

const srcDir = fileURLToPath(new URL("./src", import.meta.url))

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      "@": srcDir,
    },
  },
  server: {
    host: "127.0.0.1",
    port: frontendPort,
    // 端口被占用时直接报错，而不是悄悄换一个端口让人找不到页面
    strictPort: true,
    proxy: {
      "/api": {
        target: `http://127.0.0.1:${backendPort}`,
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: "dist",
    emptyOutDir: true,
    sourcemap: true,
  },
})
