import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Client env (read via import.meta.env, see src/vite-env.d.ts):
//   VITE_WEESPAS_API — base URL of the Weespas API that receives InSAR commercial-usage
//     telemetry (default http://localhost:8000/api/v1 — set in src/lib/telemetry.ts).
//     UNSET ⇒ telemetry is inert and InSAR behaves exactly as standalone. This is a
//     DIRECT cross-origin Bearer call, NOT proxied (Weespas CORS allows :5173–:5176).
//     This dev server is pinned to :5173 below; the Weespas FE owns :5174.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      // Proxy API in dev so the UI can use same-origin fetches and we keep
      // CORS untouched in prod-shaped deployments.
      // Target is the InSAR READ app on :8002 — it was moved off the default
      // :8000 to avoid colliding with the Weespas backend (see PE/work_flow.md §6).
      // Override with VITE_INSAR_API_TARGET if you run the read app elsewhere.
      "/api": {
        target: process.env.VITE_INSAR_API_TARGET || "http://localhost:8002",
        changeOrigin: true,
        rewrite: (p) => p.replace(/^\/api/, ""),
      },
    },
  },
});
