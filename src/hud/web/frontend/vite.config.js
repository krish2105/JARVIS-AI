import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Builds into ../dist, which src/main.py loads directly as a file:// URL in
// the pywebview window — so base must be relative, not absolute.
export default defineConfig({
  plugins: [react()],
  base: "./",
  build: {
    outDir: "../dist",
    emptyOutDir: true,
  },
});
