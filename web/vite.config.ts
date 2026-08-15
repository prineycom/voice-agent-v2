import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'

export default defineConfig(({ mode }) => {
  const reviewBuild = mode === 'review'
  return {
    root: reviewBuild ? 'review' : undefined,
    plugins: [react()],
    build: {
      target: 'es2022',
      sourcemap: false,
      outDir: 'dist',
      emptyOutDir: true,
    },
    test: {
      environment: 'jsdom',
      globals: true,
    },
  }
})
