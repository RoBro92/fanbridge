import { defineConfig } from 'vite';
import { resolve } from 'path';

export default defineConfig({
  // The base path for all assets will be /static/dist/ so Flask can serve them
  base: '/static/dist/',
  build: {
    // Output everything to the static/dist folder
    outDir: '../container/static/dist',
    emptyOutDir: true,
    rollupOptions: {
      input: resolve(__dirname, 'index.html'),
    }
  }
});
