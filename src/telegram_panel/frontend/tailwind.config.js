/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,ts,jsx,tsx}'],
  theme: {
    extend: {
      keyframes: {
        shimmer: {
          '100%': { transform: 'translateX(200%)' },
        },
      },
      colors: {
        tg: {
          bg: '#09090b',
          text: '#fafafa',
          hint: '#a1a1aa',
          link: '#10b981',
          button: '#3b82f6',
          'button-text': '#ffffff',
          'secondary-bg': '#18181b',
          'header-bg': '#09090b',
          'accent-text': '#ffffff',
          'section-bg': '#18181b',
          'section-header': '#a1a1aa',
          subtitle: '#a1a1aa',
          destructive: '#ef4444',
        },
      },
    },
  },
  plugins: [],
};
