import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import path from 'node:path'

export default defineConfig({
  base: './', plugins: [react()], publicDir: 'assets',
  build: { rollupOptions: { input: { main: path.resolve('index.html'), toast: path.resolve('toast.html') } } },
})
