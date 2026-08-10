/// <reference types="vitest" />
// vite.config.ts
import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  // Dev server on 5174 so it never collides with the InSAR frontend (5173).
  // The Weespas backend CORS allow-list already includes 5174; the app talks to
  // the backend via VITE_API_BASE_URL (defaults to http://127.0.0.1:8000/api/v1).
  server: { port: 5174 },
  test: {
    globals: true,
    environment: 'jsdom',
    setupFiles: './src/setupTests.ts',
    css: false,
  },
});
