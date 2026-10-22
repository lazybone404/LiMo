import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

export default defineConfig(({ mode }) => {
  // 静态演示站（`npm run build:demo` = `vite build --mode demo` + `.env.demo`）。
  // 演示站把 `window.fetch` 换成假后端，构建产物纯静态 —— 可挂到 GitHub Pages / Vercel。
  const env = loadEnv(mode, process.cwd())
  const demo = env.VITE_DEMO === '1'

  return {
    plugins: [react(), tailwindcss()],
    // GitHub Pages 项目页在子路径下（`/<repo>/`），Vercel / 桌面版一律根路径。
    // ⚠️ 桌面版永远走 `'/'`：demo 之外的分支必须与改造前逐字相同。
    base: demo ? env.VITE_DEMO_BASE || '/' : '/',
    server: { port: 5173, proxy: { '/api': 'http://127.0.0.1:7413', '/static': 'http://127.0.0.1:7413' } },
    // ⚠️ 演示站输出到 `dist-demo/`，**不能**用 `dist/` —— Tauri 打包读的是 `frontend/dist`，
    // 覆盖它会静默打出「一个没有后端的桌面版」。
    build: demo ? { outDir: 'dist-demo', emptyOutDir: true } : {},
  }
})
