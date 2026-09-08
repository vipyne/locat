import { defineConfig } from "vite";

export default defineConfig({
  base: "./",
  server: {
    proxy: {
      "/start": "http://127.0.0.1:7860",
    },
  },
});
