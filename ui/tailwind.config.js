/** @type {import('tailwindcss').Config} */
export default {
  darkMode: 'class',
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        brand:  { 50: '#f0fdf4', 500: '#22c55e', 600: '#16a34a', 900: '#14532d' },
        danger: { 500: '#ef4444', 600: '#dc2626' },
        warn:   { 500: '#f59e0b', 600: '#d97706' },
      },
      fontFamily: { mono: ['JetBrains Mono', 'monospace'] },
    },
  },
  plugins: [],
}
