import { defineConfig } from "vite";

// Relative asset paths, so the built site works from any folder (Vercel, GitHub Pages, a file share).
export default defineConfig({ base: "./" });
