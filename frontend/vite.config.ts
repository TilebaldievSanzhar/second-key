import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// /api проксируется на бэкенд: в браузере один origin, cookie сессии работает без CORS
const apiTarget = process.env.VITE_API_PROXY ?? "http://localhost:8000";

export default defineConfig({
  plugins: [react()],
  server: {
    host: true,
    port: 5173,
    proxy: { "/api": { target: apiTarget, changeOrigin: false } },
    watch: { usePolling: process.env.CHOKIDAR_USEPOLLING === "true" },
  },
  build: { sourcemap: true, target: "es2022" },
});
