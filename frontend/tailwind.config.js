/** @type {import('tailwindcss').Config} */
module.exports = {
  content: ["./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        void: "#02060F",
        horizon: "#0A1628",
        slate2: "#162438",
        phosphor: "#7AA7D9",
        core: "#C9D1D9",
        amber: { laser: "#E0B156", deep: "#C98A1B" },
        fault: "#1E293B",
        crimson: "#E5534B",
        silica: "#E6E8EB",
        // solid neutral text greys: crisper than translucent white over navy
        // text hierarchy by colour, not grey: white for content, steel blue for labels, pale blue for notes
        ink: { DEFAULT: "#F2F4F7", 1: "#F2F4F7", 2: "#93BBEA", 3: "#B8D0EE", 4: "#B8D0EE" },
        muted: "#93BBEA",
      },
      fontFamily: {
        mono: ["var(--font-mono)", "ui-monospace", "monospace"],
        sans: ["var(--font-sans)", "ui-sans-serif", "system-ui", "sans-serif"],
      },
      fontSize: { hud: ["10px", { lineHeight: "14px", letterSpacing: "0.08em" }] },
    },
  },
  plugins: [],
};
