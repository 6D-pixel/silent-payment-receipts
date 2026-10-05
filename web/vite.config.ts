import { defineConfig } from "vite";

// Relative asset paths, so the built site works from any folder (Vercel, GitHub Pages, a file share).
// Two pages: the comic (simulated chain) and the live signet demo.
export default defineConfig({
  base: "./",
  build: { rollupOptions: { input: { main: "index.html", live: "live.html" } } },
});
