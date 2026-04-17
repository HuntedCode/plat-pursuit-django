/**
 * GameDetailTourManager - Coach-marks spotlight tour for the game detail page.
 *
 * Walks new users through the community features on a game detail page:
 * community stats, reporting issues, ratings/reviews/roadmaps, and list add.
 *
 * Powered by PlatPursuit.CoachMarks (utils.js). This file contributes only
 * the step copy and the DOM-id wiring for this specific page.
 */

const GAME_COACH_STEPS = [
    {
        target: '#community-stats-card',
        title: 'Community Stats',
        description: 'Every stat box is clickable. Tap any one to see which players earned trophies, who platinumed the game, completion rankings, and more.',
        icon: '<path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/>',
        position: 'top',
    },
    {
        target: '#game-status-section',
        title: 'Help Keep Data Accurate',
        description: 'See something wrong? Hit Report Issue to flag games as delisted, unobtainable, shovelware, or buggy. Your reports directly improve data quality for every hunter.',
        icon: '<path d="M4 15s1-1 4-1 5 2 8 2 4-1 4-1V3s-1 1-4 1-5-2-8-2-4 1-4 1z"/><line x1="4" y1="22" x2="4" y2="15"/>',
        position: 'bottom',
    },
    {
        target: '#community-tabs-section',
        title: 'Ratings, Reviews & Roadmaps',
        description: 'Rate the difficulty, time-to-beat, and fun factor. Share your experience and help other hunters. If a platinum roadmap exists, the step-by-step guide lives here too.',
        icon: '<polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"/>',
        position: 'bottom',
    },
    {
        target: '#quick-add-container',
        title: 'Build Your Lists',
        description: 'Quickly add any game to your personal lists. Track your backlog, plan your next platinum path, or curate themed collections.',
        icon: '<line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/>',
        position: 'top',
    },
];

window.PlatPursuit = window.PlatPursuit || {};
window.PlatPursuit.GameDetailTourManager = window.PlatPursuit.CoachMarks.createTour({
    steps: GAME_COACH_STEPS,
    dismissUrl: '/api/v1/tutorial/game-detail/dismiss/',
    elementIds: {
        overlay: 'coach-overlay',
        tooltip: 'coach-tooltip',
        title: 'coach-tooltip-title',
        desc: 'coach-tooltip-desc',
        svg: 'coach-tooltip-svg',
        counter: 'coach-step-counter',
        prev: 'coach-prev-btn',
        next: 'coach-next-btn',
        skip: 'coach-skip-btn',
    },
});
