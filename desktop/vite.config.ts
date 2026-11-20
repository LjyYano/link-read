import { defineConfig } from "vite"

export default defineConfig({
  clearScreen: false,
  // Only the application entry is scanned. Rust/PyInstaller outputs also
  // contain HTML, but must never become dependency-optimizer entries.
  optimizeDeps: { entries: ["index.html"] },
  server: {
    host: "localhost",
    port: 1420,
    strictPort: true,
    watch: { ignored: ["**/src-tauri/**", "**/build/**"] },
  },
})
