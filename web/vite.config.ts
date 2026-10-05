import { defineConfig } from "vite";

// Relative asset paths, so the built site works from any folder (Vercel, GitHub Pages, a file share).
// Pages: the comic (simulated chain), and the signet pages: a live order, paying a shop, and the shop.
export default defineConfig({
  base: "./",
  build: { rollupOptions: { input: { main: "index.html", live: "live.html", wallet: "wallet.html", shop: "shop.html" } } },
});
