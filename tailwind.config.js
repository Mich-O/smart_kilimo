/** @type {import('tailwindcss').Config} */
module.exports = {
  content: ["./templates/**/*.html"],
  theme: {
    extend: {
      colors: {
        ink:      { DEFAULT: '#14181C', muted: '#5B6460', faint: '#8A928D' },
        surface:  { DEFAULT: '#FFFFFF', sunk: '#F6F8F6' },
        border:   { DEFAULT: '#E3E7E1' },
        brand:    { 50: '#EAF4F1', 100: '#D2E8E2', 400: '#1D8F78', 500: '#0E6E5D', 600: '#0B5346', 700: '#083E34' },
        warn:     { 50: '#FBF2E2', 400: '#C08A2E', 600: '#8F6620' },
        danger:   { 50: '#FBEAE9', 400: '#C1352B', 600: '#96261E' },
      },
      fontFamily: {
        sans: ['Inter', 'ui-sans-serif', 'system-ui'],
        mono: ['JetBrains Mono', 'ui-monospace', 'monospace'],
      },
    },
  },
  plugins: [],
};
