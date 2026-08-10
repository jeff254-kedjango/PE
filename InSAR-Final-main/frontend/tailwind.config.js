/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      fontFamily: {
        mono: ["JetBrains Mono", "ui-monospace", "Menlo", "monospace"],
      },
      colors: {
        ink:   { 950: "#070a0f", 900: "#0b0f14", 800: "#11161e", 700: "#1a2230" },
        wire:  { 800: "#1f2937", 700: "#2a3445", 600: "#3a4756", 500: "#5b6776" },
        signal: {
          red:   "#ef4444",
          amber: "#f59e0b",
          green: "#22c55e",
          cyan:  "#22d3ee",
        },
      },
    },
  },
  plugins: [],
};
