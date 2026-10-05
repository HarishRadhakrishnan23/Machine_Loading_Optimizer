/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,jsx}'],
  darkMode: 'class',
  theme: {
    extend: {
      colors: {
        // Samantha design tokens — carried over from the prototype's dark
        // navy/orange/sky-blue identity (Frontend doc: "always follow a
        // strict colour pattern"), formalized here instead of inline hex.
        ink: {
          950: '#0d1117', // page background
          900: '#111722',
          800: '#161b22', // card background
          700: '#1c2330',
          600: '#30363d', // border
          500: '#454c56',
        },
        text: {
          DEFAULT: '#e6edf3',
          muted: '#8b949e',
          faint: '#5c6370',
        },
        accent: {
          DEFAULT: '#f97316', // Emerson orange
          hover: '#fb923c',
          soft: '#f9731622',
        },
        sky: {
          DEFAULT: '#38bdf8',
          soft: '#38bdf822',
        },
        status: {
          safe: '#22c55e',
          safeBg: '#22c55e1a',
          warn: '#eab308',
          warnBg: '#eab3081a',
          risk: '#ef4444',
          riskBg: '#ef44441a',
        },
        // Legacy brand/risk tokens kept so untouched components (Engine 2
        // views, minimal-change per Frontend doc) don't break mid-migration.
        brand: {
          50: '#eff6ff', 100: '#dbeafe', 200: '#bfdbfe', 300: '#93c5fd', 400: '#60a5fa',
          500: '#3b82f6', 600: '#2563eb', 700: '#1d4ed8', 800: '#1e40af', 900: '#1e3a8a',
        },
        risk: {
          safe: '#16a34a', safeBg: '#f0fdf4',
          atrisk: '#d97706', atriskBg: '#fffbeb',
          breach: '#dc2626', breachBg: '#fef2f2',
        },
      },
      fontFamily: {
        sans: ['Space Grotesk', 'Inter', 'ui-sans-serif', 'system-ui', 'sans-serif'],
        mono: ['JetBrains Mono', 'ui-monospace', 'SFMono-Regular', 'monospace'],
      },
      boxShadow: {
        card: '0 1px 3px 0 rgb(0 0 0 / 0.25), 0 1px 2px -1px rgb(0 0 0 / 0.2)',
        popover: '0 10px 25px -5px rgb(0 0 0 / 0.45), 0 8px 10px -6px rgb(0 0 0 / 0.3)',
        glow: '0 0 0 1px rgb(249 115 22 / 0.3), 0 0 24px rgb(249 115 22 / 0.15)',
      },
    },
  },
  plugins: [],
}
