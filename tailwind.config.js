/** @type {import('tailwindcss').Config} */
module.exports = {
  content: [
    "./templates/**/*.html",
    "./static/js/**/*.js",
  ],
  darkMode: 'class',

  // Safelist for dynamic color classes used in Jinja2 macros
  // These classes are interpolated at runtime (e.g., from-{{ color }}-500)
  // and must be explicitly included since Tailwind can't detect them at build time
  safelist: [
    // Dynamic color patterns for all theme colors used in macros
    {
      pattern: /^(from|to|via)-(amber|orange|cyan|blue|violet|purple|teal|emerald|indigo|rose|green|slate|red|pink)-(50|100|200|300|400|500|600|700|800|900|950)(\/\d+)?$/,
      variants: ['hover', 'dark', 'group-hover'],
    },
    {
      pattern: /^bg-(amber|orange|cyan|blue|violet|purple|teal|emerald|indigo|rose|green|slate|red|pink)-(50|100|200|300|400|500|600|700|800|900|950)(\/\d+)?$/,
      variants: ['hover', 'dark', 'group-hover'],
    },
    {
      pattern: /^text-(amber|orange|cyan|blue|violet|purple|teal|emerald|indigo|rose|green|slate|red|pink)-(50|100|200|300|400|500|600|700|800|900|950)$/,
      variants: ['hover', 'dark', 'group-hover'],
    },
    {
      pattern: /^border-(amber|orange|cyan|blue|violet|purple|teal|emerald|indigo|rose|green|slate|red|pink)-(100|200|300|400|500|600|700)(\/\d+)?$/,
      variants: ['hover', 'dark', 'focus'],
    },
    {
      pattern: /^shadow-(amber|orange|cyan|blue|violet|purple|teal|emerald|indigo|rose|green|slate|red|pink)-(500)(\/\d+)?$/,
      variants: ['hover'],
    },
    {
      pattern: /^ring-(amber|orange|cyan|blue|violet|purple|teal|emerald|indigo|rose|green|slate|red|pink)-(500)(\/\d+)?$/,
      variants: ['focus', 'dark'],
    },
  ],
  theme: {
    extend: {
      fontFamily: {
        sans: ['Plus Jakarta Sans', 'system-ui', 'sans-serif'],
        display: ['Outfit', 'system-ui', 'sans-serif'],
      },
      colors: {
        // Distinctive teal-cyan palette with warmth
        primary: {
          50: '#ecfeff',
          100: '#cffafe',
          200: '#a5f3fc',
          300: '#67e8f9',
          400: '#22d3ee',
          500: '#06b6d4',
          600: '#0891b2',
          700: '#0e7490',
          800: '#155e75',
          900: '#164e63',
          950: '#083344',
        },
        // Warm accent for contrast
        accent: {
          50: '#fff7ed',
          100: '#ffedd5',
          200: '#fed7aa',
          300: '#fdba74',
          400: '#fb923c',
          500: '#f97316',
          600: '#ea580c',
          700: '#c2410c',
          800: '#9a3412',
          900: '#7c2d12',
          950: '#431407',
        }
      },
      animation: {
        'fade-in': 'fadeIn 0.2s ease-in-out',
        'stagger-in': 'staggerFadeIn 0.5s ease-out forwards',
        'counter-pop': 'counterPop 0.4s cubic-bezier(0.4, 0, 0.2, 1)',
      },
      keyframes: {
        fadeIn: {
          '0%': { opacity: '0', transform: 'translateY(-4px)' },
          '100%': { opacity: '1', transform: 'translateY(0)' },
        },
        staggerFadeIn: {
          '0%': { opacity: '0', transform: 'translateY(12px)' },
          '100%': { opacity: '1', transform: 'translateY(0)' },
        },
        counterPop: {
          '0%': { transform: 'scale(0.8)', opacity: '0' },
          '50%': { transform: 'scale(1.05)' },
          '100%': { transform: 'scale(1)', opacity: '1' },
        },
      },
    }
  },
  plugins: [],
}
