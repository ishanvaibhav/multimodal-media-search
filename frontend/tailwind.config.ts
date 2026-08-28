import type { Config } from "tailwindcss";

const config: Config = {
  content: [
    "./app/**/*.{ts,tsx}",
    "./components/**/*.{ts,tsx}",
    "./hooks/**/*.{ts,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        surface: {
          950: "#0a0c12",
          900: "#0f131c",
          850: "#141926",
          800: "#1a2030",
          700: "#232b40",
          600: "#2e3850",
        },
        accent: {
          DEFAULT: "#5b8cff",
          soft: "#8fb0ff",
        },
      },
    },
  },
  plugins: [],
};

export default config;
