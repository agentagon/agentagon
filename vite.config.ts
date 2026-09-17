import { resolve } from "node:path";

import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  root: "frontend",
  plugins: [react()],
  build: {
    outDir: "../src/agentagon/dashboard_assets",
    emptyOutDir: false,
    cssCodeSplit: false,
    sourcemap: false,
    rollupOptions: {
      external: ["/theme.js"],
      input: resolve(__dirname, "frontend/webapp.html"),
      output: {
        entryFileNames: "webapp.js",
        chunkFileNames: "webapp-[name].js",
        assetFileNames: (asset) =>
          asset.names.some((name) => name.endsWith(".css")) ? "webapp.css" : "webapp-[name][extname]",
      },
    },
  },
});
