import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

const API_PORT = process.env.IMAGE_PIPELINE_UI_PORT || "8787";
const API_TARGET = `http://127.0.0.1:${API_PORT}`;

export default defineConfig({
  plugins: [react()],
  base: "/",
  build: {
    outDir: "../static",
    emptyOutDir: true,
  },
  server: {
    port: 5173,
    strictPort: true,
    proxy: {
      "/api": {
        target: API_TARGET,
        changeOrigin: true,
      },
      "/media": {
        target: API_TARGET,
        changeOrigin: true,
      },
    },
  },
});
