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
        ink: { DEFAULT: "#EEF0F3", 1: "#D2D7DD", 2: "#AFB6BF", 3: "#8F98A3", 4: "#7C8591" },
        muted: "#8B95A3",
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
