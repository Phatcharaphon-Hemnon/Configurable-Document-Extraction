import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// Dev proxy: lets the app call the API with the relative default "/api"
// (no VITE_API_BASE_URL needed for local development).
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
    },
  },
});
