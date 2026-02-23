module.exports = {
  content: ['./templates/**/*.html'], // Adjust for your template paths
  safelist: [
    // Dashboard module grid classes (constructed in Python/JS, not in templates)
    'col-span-2', 'lg:col-span-2', 'lg:col-span-4',
    '2xl:col-span-2', '2xl:col-span-3', '2xl:col-span-6',
  ],
  theme: { extend: {} },
  plugins: [require('daisyui')],
};
