import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import tailwindcss from '@tailwindcss/vite';

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: { port: 5173, strictPort: true, proxy: {
    '^/api/explain(?:\\?|$)': 'http://127.0.0.1:4178',
    '/api': 'http://127.0.0.1:19388',
  } },
  build: { sourcemap: false },
});
