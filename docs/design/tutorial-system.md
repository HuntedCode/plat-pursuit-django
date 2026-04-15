# Tutorial System (Design)

A planned post-IA-redesign feature: a brief Welcome Tour that introduces new users to PlatPursuit's four hubs (Dashboard, Browse, Community, My Pursuit) on first PSN-link. The goal is to make the hub-of-hubs IA accessible to new users without forcing power users to read tooltips on every page.

> **Status**: Implemented. Shipped on the `feature/welcome-tour` branch. See the Key Files section below for the implementation locations.

## Why this exists

The IA work in the Community Hub initiative built a **power-user IA**: 3 direct-link hub buttons in the navbar, a persistent sub-navigation strip on every hub page, no dropdowns, no chrome. It's clean and efficient, but it has a higher first-time-user discoverability cost than a chunkier menu-based IA. A new user clicking the "My Pursuit" button doesn't necessarily know what Milestones or Titles are; a new user landing on the dashboard doesn't necessarily know that My Stats and My Shareables live in the sub-nav strip below the navbar.

The tutorial system fills that gap. It's the missing piece that makes the power-user IA usable for new users without compromising the cleanliness of the IA itself for everyone else.

It also surfaces **features that are otherwise hard to discover**: the Platinum Grid wizard, Profile Cards, the Monthly Recap. These are real features that exist on the site but most users would never find them on their own. A brief tour gives them visibility without requiring permanent menu items or homepage real estate.

## Goals

1. **Teach the IA**: introduce each of the four hubs and what they're for
2. **Surface hidden features**: point at the Platinum Grid, Profile Cards, Recap, etc. as things users might not otherwise discover
3. **Stay out of the way**: run once, then disappear unless explicitly re-triggered
4. **Be brief**: a tour the user can complete in under 60 seconds, not a multi-screen onboarding

## Non-Goals

- **Not a tutorial for individual features.** This is a tour of the *site*, not a "how to use Badges" or "how to write a Review" guide. Per-feature help can come later as a separate system.
- **Not a forced onboarding sequence.** The user should be able to skip the tour and use the site immediately if they want. Skipping marks the tour as done, same as completing it.
- **Not a permanent UI overlay.** No persistent help icons on every page (yet). Just a one-time tour with a re-trigger button somewhere out of the way.
- **Not a notification.** Not in the notification inbox; not delivered as a banner. Modal-only.

## When it triggers

- **First visit AFTER PSN link.** A user who signs up but hasn't linked their PSN account doesn't get the tour yet — they should link first, then the tour runs the next time they land on the dashboard. This timing ensures the dashboard has real data to show during the tour (their avatar, their first sync results) instead of empty placeholders.
- **Manual re-trigger** via a button in the avatar dropdown (or a small "?" icon somewhere stable). Users who skipped the first time, or users who want a refresher, can re-run on demand.
- **Per-user, persistent dismissal.** Once a user has either completed or skipped the tour, the auto-trigger is suppressed forever. Only the manual re-trigger can resurface it.

## Tour content (7-step Welcome Tour)

The tour has 7 steps: two intro slides, one avatar/settings slide, and four hub walkthroughs. Each step is a single modal slide with an icon, description, and feature cards. Completable in under a minute.

| Step | Section | Title | Highlights |
|---|---|---|---|
| 1 | Welcome splash | "Welcome to Platinum Pursuit" | Logo shimmer, ambient particles, text cascade |
| 2 | Navigation intro | "How to Get Around" | Real navbar/tab bar highlighted with per-hub colors |
| 3 | Avatar menu | "Your Profile & Settings" | Avatar button highlighted, feature cards for Profile/Theme/Settings/Replay |
| 4 | Dashboard | "Your Trophy Hunting HQ" | Dashboard hub highlighted, sub-nav shown |
| 5 | Browse | "Find Your Next Platinum" | Browse hub highlighted, sub-nav shown |
| 6 | Community | "Hunt Together" | Community hub highlighted, sub-nav shown |
| 7 | My Pursuit | "Your Trophy Hunting Identity" | My Pursuit hub highlighted, sub-nav shown |

On desktop, the real `.navbar` is cloned into the `<dialog>` and elements are highlighted per step. On mobile, the real `.mobile-tabbar` and `.hub-subnav` are cloned instead. No static mockups are used on any viewport.

On tour completion, confetti fires via `CelebrationManager.fireSideConfetti()` and the real navbar/sub-nav are spotlighted with a bouncing "Start your journey here!" callout.

## Where the re-trigger button lives

Three options:

1. **Avatar dropdown** — add a "Take the tour" item near the bottom of the dropdown. Discoverable for users who think to look in their account menu. Doesn't take up navbar real estate.
2. **Settings page** — a dedicated section called "Help & Tutorials" with the "Replay Welcome Tour" button. Even more out of the way; only users actively looking for it find it.
3. **Floating help icon** — a small `?` icon in a corner of the viewport (e.g. bottom-right). Most discoverable but adds permanent UI chrome.

**Recommendation: option 1 (avatar dropdown).** Lowest UI cost, most natural place for "settings-adjacent" functionality. Option 3 (floating icon) is tempting but adds noise to every page; defer it to a later phase if option 1 turns out to be too hidden.

## Technical shape (rough)

The tutorial system needs to:

1. **Track per-user dismissal state.** Either a new field on `Profile` (`tour_completed_at`) or a row in a new `UserTutorialState` model. A boolean is sufficient for v1; if we ever ship multiple tours we'd need a model. Lean toward the field for simplicity.
2. **Render a modal on the dashboard page when appropriate.** A small JS module triggered by a context flag from the dashboard view: `if not request.user.profile.tour_completed_at and request.user.profile.is_linked: context['show_welcome_tour'] = True`. The template includes a `welcome_tour.html` partial that renders an empty hidden modal; the JS picks up the flag and shows it on DOM ready.
3. **Persist the dismissal.** A `POST /api/v1/tutorial/welcome/complete/` endpoint that sets the field. Called on either Skip or Finish.
4. **Re-trigger from the avatar dropdown.** A link or button in the dropdown that calls a JS function to open the same modal manually. The endpoint is the same; the manual trigger just bypasses the auto-show check.

Reusable pieces from the existing codebase that might help:
- The existing modal system / `unsaved_changes_modal.html` pattern
- The notification system's "mark as read" API pattern (similar persistence shape)
- The hotbar's polling pattern (NOT recommended — tutorials shouldn't poll, they just need a one-shot context flag)

A new Django app (`tutorials/`) would be overkill for v1. Put everything in `core/` since the tutorial is cross-cutting site-wide infrastructure.

## Forward compatibility

Future expansions worth keeping in mind, but explicitly OUT OF SCOPE for v1:

- **Per-page first-visit coach marks.** Popovers that point at specific UI elements the first time a user visits a page (e.g. "click this button to start a challenge"). Requires per-page state tracking and a more complex UI.
- **Multiple named tours.** A "Premium features tour", a "Gamification tour" (when that ships), etc. Requires the model approach instead of a boolean field.
- **Help icons on every page.** A small `?` icon in each page header that opens a side panel explaining the page. Requires writing help content for every page.
- **Tooltip layer.** Rich tooltips on every interactive element. Lowest friction but highest content burden.

Each of these is a real improvement, but each is also its own scope of work. Ship the simple Welcome Tour first; revisit when we have actual usage data from the post-IA launch.

## Resolved Questions

- **Tour copy**: drafted during implementation. Each step has a title, pitch, and 3-4 feature cards.
- **Modal vs full-screen**: modal (`max-w-3xl`). Centered, with backdrop blur.
- **Animation/transitions**: slide transitions with scale (cubic-bezier ease-out-expo), spring-scale feature cards (overshoot easing), text cascade on step 1, platinum shimmer on logo, ambient particle drift, connected progress bar, confetti burst on completion. Coach mark cutouts pulse with a glowing ring.
- **Mobile experience**: feature cards stack to 1-column on `<md`. Real tab bar and sub-nav cloned into the modal (no static mockups). Modal responsive via daisyUI.
- **Desktop experience**: Real navbar cloned into the modal and highlighted per step. Hub buttons, avatar, and sub-nav all get pulsing-ring highlights.
- **Analytics**: yes. `SiteEvent` tracks `welcome_tour_complete` and `welcome_tour_skip` with the last step reached.
- **A/B variants**: not in v1.

## Key Files

### Welcome Tour (hub navigation)

| File | Purpose |
|------|---------|
| `trophies/models.py` | `Profile.tour_completed_at` field |
| `trophies/migrations/0189_add_tour_completed_at.py` | Schema migration |
| `templates/partials/welcome_tour_modal.html` | Tour modal template (7 steps, particles, shimmer, progress bar) |
| `static/js/welcome-tour.js` | `WelcomeTourManager` class (nav cloning, animations, confetti) |
| `static/js/celebrations.js` | `CelebrationManager` (confetti on tour completion) |
| `static/css/input.css` | Tour styles: nav clones, highlights, shimmer, particles, progress bar, coach ring |
| `api/tutorial_views.py` | `WelcomeTourDismissAPIView` |
| `api/urls.py` | `POST /api/v1/tutorial/welcome/dismiss/` |
| `core/views.py` | `show_welcome_tour` context injection in `HomeView` |
| `templates/base.html` | Modal include + JS load |
| `templates/partials/navbar.html` | "Welcome Tour" in avatar dropdown |
| `core/models.py` | `SiteEvent` choices for analytics |

### Game Detail Tour (page coach marks)

| File | Purpose |
|------|---------|
| `trophies/models.py` | `Profile.game_detail_tour_completed_at` field |
| `trophies/migrations/0190_add_game_detail_tour_completed_at.py` | Schema migration |
| `templates/trophies/partials/game_detail/game_detail_tour.html` | Coach marks overlay + tooltip template |
| `static/js/game-detail-tour.js` | `GameDetailTourManager` class |
| `static/css/input.css` | Coach mark styles (search for "Game Detail Coach Marks") |
| `api/tutorial_views.py` | `GameDetailTourDismissAPIView` |
| `api/urls.py` | `POST /api/v1/tutorial/game-detail/dismiss/` |
| `trophies/views/game_views.py` | `show_game_detail_tour` context injection in `GameDetailView` |
| `templates/trophies/game_detail.html` | Tour include + section IDs |
| `templates/trophies/partials/game_detail/game_detail_header.html` | "Page Guide" button + target IDs |

### Badge Detail Tour (page coach marks)

| File | Purpose |
|------|---------|
| `trophies/models.py` | `Profile.badge_detail_tour_completed_at` field |
| `trophies/migrations/0191_add_badge_detail_tour_completed_at.py` | Schema migration |
| `templates/trophies/partials/badge_detail/badge_detail_tour.html` | Coach marks overlay + tooltip template |
| `static/js/badge-detail-tour.js` | `BadgeDetailTourManager` class |
| `api/tutorial_views.py` | `BadgeDetailTourDismissAPIView` |
| `api/urls.py` | `POST /api/v1/tutorial/badge-detail/dismiss/` |
| `trophies/views/badge_views.py` | `show_badge_detail_tour` context injection in `BadgeDetailView` |
| `templates/trophies/badge_detail.html` | Tour include + section IDs |
| `templates/trophies/partials/badge_detail/badge_detail_header.html` | "Page Guide" button + header ID |

## Related Docs

- [IA and Sub-Nav](../architecture/ia-and-subnav.md): the hub-of-hubs IA structure that the Welcome Tour teaches
- [Dashboard](../features/dashboard.md): step 1 of the tour (the personal cockpit)
- [Community Hub](../features/community-hub.md): step 3 of the tour (community discovery)
- [My Pursuit Hub](../features/my-pursuit-hub.md): step 4 of the tour (personal progression)
- [Navigation](../features/navigation.md): the navbar/sub-nav structure the tour points at
