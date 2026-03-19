import { defineConfig } from "vite";
import { svelte } from "@sveltejs/vite-plugin-svelte";

export default defineConfig({
  plugins: [svelte()],
  server: {
    port: 7400,
    proxy: {
      "/api": "http://127.0.0.1:7300",
      "/ws": {
        target: "ws://127.0.0.1:7300",
        ws: true,
      },
    },
  },
});
