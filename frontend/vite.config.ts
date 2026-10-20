import vue from '@vitejs/plugin-vue'
import { defineConfig } from 'vitest/config'

export default defineConfig({
  plugins: [vue()],
  build: { outDir: 'dist', emptyOutDir: true, sourcemap: false, target: 'es2022' },
  server: {
    port: 5173,
    proxy: { '/api': 'http://127.0.0.1:8000', '/t': 'http://127.0.0.1:8000' },
  },
  test: { environment: 'jsdom', globals: true, include: ['tests/**/*.test.ts'], setupFiles: ['tests/setup.ts'] },
})
