/**
 * BadgeDetailTourManager - Coach-marks spotlight tour for the badge detail page.
 *
 * Teaches users how badge series work: tier progression, stage requirements,
 * games within stages, and XP leaderboards.
 *
 * Powered by PlatPursuit.CoachMarks (utils.js). This file contributes only
 * the step copy and the DOM-id wiring for this specific page.
 */

const BADGE_COACH_STEPS = [
    {
        target: '#badge-header-card',
        title: 'Badge Overview',
        description: 'This is a badge series. Your progress ring shows how many stages you\'ve completed. Earn platinums (or 100%) in the games within each stage to advance through the tiers.',
        icon: '<circle cx="12" cy="8" r="7"/><polyline points="8.21 13.89 7 23 12 20 17 23 15.79 13.88"/>',
        position: 'bottom',
    },
    {
        target: '#tier-selector',
        title: 'Tier Progression',
        description: 'Badges have up to four tiers: Bronze, Silver, Gold, and Platinum. Each tier has its own requirements and XP reward. Tap a tier to see what it takes to earn it.',
        icon: '<path d="M12 2 2 7l10 5 10-5-10-5Z"/><path d="m2 17 10 5 10-5"/><path d="m2 12 10 5 10-5"/>',
        position: 'bottom',
    },
    {
        target: '#stage-1',
        title: 'Stages & Games',
        description: 'Each stage contains a group of games. Tap a stage to expand it and see which games qualify. Earn the required trophies in those games to complete the stage.',
        icon: '<line x1="8" y1="6" x2="21" y2="6"/><line x1="8" y1="12" x2="21" y2="12"/><line x1="8" y1="18" x2="21" y2="18"/><line x1="3" y1="6" x2="3.01" y2="6"/><line x1="3" y1="12" x2="3.01" y2="12"/><line x1="3" y1="18" x2="3.01" y2="18"/>',
        position: 'top',
    },
    {
        target: '#badge-leaderboard-section',
        title: 'Badge Leaderboards',
        description: 'Every stage you complete earns XP. Compete with other hunters on the badge leaderboard and climb the ranks.',
        icon: '<line x1="12" y1="20" x2="12" y2="10"/><line x1="18" y1="20" x2="18" y2="4"/><line x1="6" y1="20" x2="6" y2="16"/>',
        position: 'top',
    },
];

window.PlatPursuit = window.PlatPursuit || {};
window.PlatPursuit.BadgeDetailTourManager = window.PlatPursuit.CoachMarks.createTour({
    steps: BADGE_COACH_STEPS,
    dismissUrl: '/api/v1/tutorial/badge-detail/dismiss/',
    elementIds: {
        overlay: 'badge-coach-overlay',
        tooltip: 'badge-coach-tooltip',
        title: 'badge-coach-title',
        desc: 'badge-coach-desc',
        svg: 'badge-coach-svg',
        counter: 'badge-coach-counter',
        prev: 'badge-coach-prev',
        next: 'badge-coach-next',
        skip: 'badge-coach-skip',
    },
});
