import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Config de build. Objetivo: reducir el tiempo de carga inicial separando el código
// de terceros (React) del de la app, para que el navegador lo cachee aparte y no lo
// vuelva a descargar en cada despliegue de la aplicación.
export default defineConfig({
  plugins: [react()],
  build: {
    target: "es2020",
    rollupOptions: {
      output: {
        manualChunks: {
          // React + ReactDOM cambian poco entre versiones: chunk propio, cache larga.
          react: ["react", "react-dom", "react-dom/client"],
        },
      },
    },
  },
});
