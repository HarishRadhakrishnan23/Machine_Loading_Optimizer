// Colours for charts / SVG, where Tailwind token classes cannot reach (Recharts props, inline styles).
// `dark` is exactly what the charts always used; `light` is the blue palette with NO orange.
// Pick with `useTheme().colors`.
export const CHART_COLORS = {
  dark: {
    grid: '#30363d',
    axis: '#8b949e',
    tipBg: '#161b22',
    tipBorder: '#30363d',
    tipText: '#e6edf3',
    tipLabel: '#f97316',
    cursor: 'rgba(255,255,255,0.04)',
    primary: '#f97316', // single-series bars, reference lines
    good: '#22c55e',
    mid: '#eab308',
    bad: '#ef4444',
    markStroke: '#0d1117', // outline of bubble shapes
    neutralMark: '#8b949e', // legend shape swatch
    pie: ['#f97316', '#38bdf8', '#a78bfa', '#f472b6', '#fbbf24', '#34d399', '#818cf8', '#2dd4bf'],
    gantt: ['#f97316', '#38bdf8', '#a78bfa', '#f472b6', '#fb7185', '#fbbf24', '#34d399', '#818cf8', '#2dd4bf', '#f87171'],
    ganttOutline: 'rgba(0,0,0,0.45)',
    ganttBarOpacity: 0.92,
    moc: { 'Carbon Steel': '#ff7a1a', 'Stainless Steel': '#1ec8ff', Duplex: '#b05cff', 'Super Duplex': '#2dffa8' },
    mocFallback: ['#ff3d8b', '#ffe14d', '#6b7bff', '#b6ff3d', '#ff5a4d', '#2de2e6'],
    markGlow: true, // glow-on-dark only
    glow: ['#f97316', '#38bdf8', '#fb923c'],
    honeyFrame: '#e6edf3',
  },
  light: {
    grid: '#e2e2e0',
    axis: '#595f61',
    tipBg: '#fbfafe',
    tipBorder: '#d3d3d1',
    tipText: '#1c1c1b',
    tipLabel: '#6331ff',
    cursor: 'rgba(99,49,255,0.07)',
    primary: '#6331ff',
    good: '#00794a',
    mid: '#a16207',
    bad: '#dc2626',
    markStroke: '#fbfafe',
    neutralMark: '#828280',
    pie: ['#6331ff', '#00bf71', '#7b30dc', '#009859', '#b59bf3', '#595f61', '#4600c3', '#a3a3a1'],
    gantt: ['#6331ff', '#00794a', '#7b30dc', '#595f61', '#4600c3', '#008a52', '#8a57e8', '#521d95', '#4f5bd5', '#0a9d6b'],
    ganttOutline: 'rgba(28,28,27,0.5)',
    ganttBarOpacity: 0.95,
    moc: { 'Carbon Steel': '#6331ff', 'Stainless Steel': '#00bf71', Duplex: '#b59bf3', 'Super Duplex': '#3c3c3b' },
    mocFallback: ['#7b30dc', '#009859', '#a4a5ff', '#828280', '#4600c3', '#00eb8c'],
    markGlow: false,
    glow: ['#6331ff', '#b59bf3', '#00bf71'],
    honeyFrame: '#6331ff',
  },
}
