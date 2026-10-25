import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// In development, /api/* is proxied to the FastAPI server, so the browser talks to
// one origin and there is no CORS to configure.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": { target: process.env.API_URL ?? "http://localhost:8000", changeOrigin: true },
    },
  },
});
