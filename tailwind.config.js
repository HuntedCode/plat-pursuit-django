module.exports = {
  content: ['./templates/**/*.html'], // Adjust for your template paths
  safelist: [
    // Dashboard module grid classes (constructed in Python/JS, not in templates)
    'col-span-2', 'lg:col-span-2', 'lg:col-span-4',
    '2xl:col-span-2', '2xl:col-span-3', '2xl:col-span-6',
    // Badge tier colors (dynamically constructed via badge_color template filter)
    'border-warning', 'border-secondary', 'border-error', 'border-primary',
    'bg-warning/10', 'bg-secondary/10', 'bg-error/10', 'bg-primary/10',
    'shadow-warning', 'shadow-secondary', 'shadow-error', 'shadow-primary',
    'shadow-warning/30', 'shadow-secondary/30', 'shadow-error/30', 'shadow-primary/30',
    'text-warning', 'text-secondary', 'text-error', 'text-primary',
    'hover:border-warning', 'hover:border-secondary', 'hover:border-error', 'hover:border-primary',
    'hover:text-warning', 'hover:text-secondary', 'hover:text-error', 'hover:text-primary',
    // Trophy type colors (dynamically constructed via trophy_color/trophy_type template filters)
    'hover:border-trophy-bronze', 'hover:border-trophy-silver', 'hover:border-trophy-gold', 'hover:border-trophy-platinum',
    'hover:shadow-trophy-bronze/30', 'hover:shadow-trophy-silver/30', 'hover:shadow-trophy-gold/30', 'hover:shadow-trophy-platinum/30',
    'group-hover:border-trophy-bronze', 'group-hover:border-trophy-silver', 'group-hover:border-trophy-gold', 'group-hover:border-trophy-platinum',
    'group-hover:text-trophy-bronze', 'group-hover:text-trophy-silver', 'group-hover:text-trophy-gold', 'group-hover:text-trophy-platinum',
    // Flag category cards (dynamically constructed via template variable)
    'badge-error', 'badge-warning', 'badge-accent', 'badge-info',
    'ring-error/30', 'ring-warning/30',
    'hover:border-error', 'hover:border-warning',
  ],
  theme: { extend: {} },
  plugins: [require('daisyui')],
};
