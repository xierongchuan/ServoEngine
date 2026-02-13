/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,ts,jsx,tsx}'],
  theme: {
    extend: {
      colors: {
        tg: {
          bg: 'var(--tg-theme-bg-color, #1a1a2e)',
          text: 'var(--tg-theme-text-color, #e0e0e0)',
          hint: 'var(--tg-theme-hint-color, #7a7a8e)',
          link: 'var(--tg-theme-link-color, #5ebbff)',
          button: 'var(--tg-theme-button-color, #3b82f6)',
          'button-text': 'var(--tg-theme-button-text-color, #ffffff)',
          'secondary-bg': 'var(--tg-theme-secondary-bg-color, #232340)',
          'header-bg': 'var(--tg-theme-header-bg-color, #1a1a2e)',
          'accent-text': 'var(--tg-theme-accent-text-color, #5ebbff)',
          'section-bg': 'var(--tg-theme-section-bg-color, #232340)',
          'section-header': 'var(--tg-theme-section-header-text-color, #7a7a8e)',
          subtitle: 'var(--tg-theme-subtitle-text-color, #7a7a8e)',
          destructive: 'var(--tg-theme-destructive-text-color, #ef4444)',
        },
      },
    },
  },
  plugins: [],
};
