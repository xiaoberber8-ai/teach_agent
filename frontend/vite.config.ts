import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// 开发态：/api 代理到 FastAPI（uvicorn 8000），避免浏览器跨域；
// 生产态：npm run build 后由 FastAPI 直接托管 dist/。
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
    },
  },
})
