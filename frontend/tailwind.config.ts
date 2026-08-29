import type { Config } from "tailwindcss";
const config: Config = {
  content: [
    "./app/**/*.{ts,tsx}",
    "./components/**/*.{ts,tsx}",
    "./hooks/**/*.{ts,tsx}",
  ],
  darkMode: "class",
  theme: {
    extend: {
      colors: {
        "surface-dim": "#d9d9e5",
        "on-secondary-container": "#57657a",
        "on-tertiary-fixed": "#360f00",
        "on-primary-fixed": "#00174b",
        "on-secondary-fixed-variant": "#3a485b",
        "on-secondary": "#ffffff",
        "tertiary-container": "#bc4800",
        "surface-container-low": "#f3f3fe",
        "on-secondary-fixed": "#0d1c2e",
        "surface-container-high": "#e7e7f3",
        "secondary-fixed": "#d5e3fc",
        "error-container": "#ffdad6",
        "on-background": "#191b23",
        "primary-fixed": "#dbe1ff",
        "inverse-on-surface": "#f0f0fb",
        "tertiary-fixed-dim": "#ffb596",
        "on-tertiary-fixed-variant": "#7d2d00",
        "error": "#ba1a1a",
        "outline-variant": "#c3c6d7",
        "on-error": "#ffffff",
        "on-primary": "#ffffff",
        "surface-bright": "#faf8ff",
        "background": "#faf8ff",
        "tertiary-fixed": "#ffdbcd",
        "primary-fixed-dim": "#b4c5ff",
        "surface-tint": "#0053db",
        "inverse-surface": "#2e3039",
        "inverse-primary": "#b4c5ff",
        "primary": "#004ac6",
        "on-surface-variant": "#434655",
        "surface-variant": "#e1e2ed",
        "on-tertiary": "#ffffff",
        "secondary": "#515f74",
        "surface-container": "#ededf9",
        "on-surface": "#191b23",
        "surface-container-lowest": "#ffffff",
        "outline": "#737686",
        "surface-container-highest": "#e1e2ed",
        "surface": "#faf8ff",
        "primary-container": "#2563eb",
        "on-primary-fixed-variant": "#003ea8",
        "tertiary": "#943700",
        "on-tertiary-container": "#ffede6",
        "on-error-container": "#93000a",
        "secondary-container": "#d5e3fc",
        "secondary-fixed-dim": "#b9c7df",
        "on-primary-container": "#eeefff"
      },
      borderRadius: {
        "DEFAULT": "0.25rem",
        "lg": "0.5rem",
        "xl": "0.75rem",
        "full": "9999px"
      },
      spacing: {
        "2xl": "48px",
        "xs": "4px",
        "3xl": "64px",
        "md": "16px",
        "base_unit": "4px",
        "lg": "24px",
        "xl": "32px",
        "sm": "8px"
      },
      fontFamily: {
        sans: ["Inter", "sans-serif"]
      },
      fontSize: {
        "label-sm": ["11px", { lineHeight: "14px", fontWeight: "500" }],
        "body-md": ["16px", { lineHeight: "24px", fontWeight: "400" }],
        "label-md": ["12px", { lineHeight: "16px", letterSpacing: "0.05em", fontWeight: "600" }],
        "body-sm": ["14px", { lineHeight: "20px", fontWeight: "400" }],
        "body-lg": ["18px", { lineHeight: "28px", fontWeight: "400" }],
        "display-lg": ["48px", { lineHeight: "56px", letterSpacing: "-0.02em", fontWeight: "700" }],
        "headline-lg": ["32px", { lineHeight: "40px", letterSpacing: "-0.01em", fontWeight: "600" }],
        "headline-md": ["24px", { lineHeight: "32px", fontWeight: "600" }]
      }
    }
  },
  plugins: []
};
export default config;
