// A theme colour: a CSS variable holding "R G B"; with `alpha` it is a fixed translucent tint,
// without it Tailwind's opacity modifiers (`bg-ink-700/40`) work.
const v = (name, alpha) => (alpha === undefined ? `rgb(var(${name}) / <alpha-value>)` : `rgb(var(${name}) / ${alpha})`)

/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,jsx}'],
  darkMode: 'class',
  theme: {
    extend: {
      colors: {
        // Apeiron design tokens. Every value is a CSS variable (RGB channels, so `/40` opacity
        // modifiers keep working) defined in index.css for BOTH themes: dark (default, charcoal +
        // Emerson orange) and light (`:root[data-theme='light']`, blue palette). Never hard-code a
        // hex in a component - add or reuse a token here.
        ink: {
          950: v('--ink-950'), // page background
          900: v('--ink-900'), // inset surfaces: inputs, table header, sidebar
          800: v('--ink-800'), // card background
          700: v('--ink-700'), // hover / secondary fill / row divider
          600: v('--ink-600'), // border
          500: v('--ink-500'),
        },
        text: {
          DEFAULT: v('--text'),
          muted: v('--text-muted'),
          faint: v('--text-faint'),
        },
        accent: {
          DEFAULT: v('--accent'), // dark: Emerson orange | light: electric blue
          hover: v('--accent-hover'),
          soft: v('--accent', 0.13),
        },
        onAccent: v('--on-accent'), // text/icon colour that sits ON an accent fill
        sky: {
          DEFAULT: v('--sky'), // secondary highlight (dark: sky blue | light: indigo-violet)
          soft: v('--sky', 0.13),
        },
        partial: v('--partial'), // heatmap "partially open"
        status: {
          safe: v('--status-safe'),
          safeBg: v('--status-safe', 0.1),
          warn: v('--status-warn'),
          warnBg: v('--status-warn', 0.1),
          risk: v('--status-risk'),
          riskBg: v('--status-risk', 0.1),
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
        card: 'var(--shadow-card)',
        popover: 'var(--shadow-popover)',
        glow: 'var(--shadow-glow)',
      },
    },
  },
  plugins: [],
}
