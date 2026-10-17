/** @type {import('tailwindcss').Config} */
module.exports = {
  content: ["./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        void: "#02060F",
        horizon: "#0A1628",
        slate2: "#162438",
        phosphor: "#38BDF8",
        core: "#E0F2FE",
        amber: { laser: "#FDE68A", deep: "#F59E0B" },
        fault: "#1E293B",
        crimson: "#E11D48",
        silica: "#F8FAFC",
        muted: "#64748B",
      },
      fontFamily: {
        mono: ["var(--font-geist-mono)", "ui-monospace", "monospace"],
        sans: ["var(--font-geist-sans)", "ui-sans-serif", "system-ui", "sans-serif"],
      },
      fontSize: { hud: ["10px", { lineHeight: "14px", letterSpacing: "0.08em" }] },
    },
  },
  plugins: [],
};
