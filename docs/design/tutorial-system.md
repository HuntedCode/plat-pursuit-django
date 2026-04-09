# Tutorial System (Design)

A planned post-IA-redesign feature: a brief Welcome Tour that introduces new users to PlatPursuit's four hubs (Dashboard, Browse, Community, My Pursuit) on first PSN-link. The goal is to make the hub-of-hubs IA accessible to new users without forcing power users to read tooltips on every page.

> **Status**: design only. Not implemented. The IA collapse it complements landed in the Community Hub initiative; this tutorial system is the planned next branch after that initiative merges.

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

## Tour content (4-step Welcome Tour)

The tour walks through each of the four hubs in IA order. Each step is a single modal slide with a screenshot/icon, a one-sentence description, and Next/Skip buttons. The whole thing should be completable in under a minute.

| Step | Hub | Pitch (one sentence) | Highlights |
|---|---|---|---|
| 1 | **Dashboard** | "Your personal cockpit — track your stats, manage your hunts, customize your view." | Sub-nav strip (Stats, Shareables, Recap) |
| 2 | **Browse** | "Find your next platinum — browse games, trophies, companies, genres, and themes." | Sub-nav strip + filter system on game pages |
| 3 | **Community** | "See what other hunters are up to — reviews, challenges, lists, leaderboards, and our Discord." | Discord callout |
| 4 | **My Pursuit** | "Track your trophy hunting goals — earn badges, hit milestones, and unlock titles." | Sub-nav strip (Badges, Milestones, Titles) |

The exact wording is a copywriting exercise for implementation time. The structure is the load-bearing thing.

**Optional 5th step**: a "you're ready" screen with cards pointing at 2-3 hidden gems (Platinum Grid wizard, Profile Cards, Monthly Recap). Skippable, but a nice exit ramp that surfaces features the 4-step tour didn't get to.

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

## Open Questions

- **Tour copy**: needs a copywriting pass. The pitch lines above are placeholders.
- **Modal vs full-screen**: the tour could be a small centered modal (less intrusive) or a full-screen overlay (more cinematic). I lean modal — full-screen feels heavyweight for a 4-step tour.
- **Animation/transitions**: minimal? Slide between steps? Fade? Should be cheap to build either way.
- **Mobile experience**: the sub-nav strip is the main thing the tour teaches, and the strip is fine on mobile. The tour modal needs a mobile-friendly layout but otherwise works the same.
- **Analytics**: do we want to track who completes the tour vs skips? Would inform future iteration but adds scope. Probably yes — we already have `track_page_view` infrastructure that could be extended.
- **A/B variants**: probably not for v1. Ship one good version, measure, iterate.

## Related Docs

- [IA and Sub-Nav](../architecture/ia-and-subnav.md): the hub-of-hubs IA structure that the Welcome Tour teaches
- [Dashboard](../features/dashboard.md): step 1 of the tour (the personal cockpit)
- [Community Hub](../features/community-hub.md): step 3 of the tour (community discovery)
- [My Pursuit Hub](../features/my-pursuit-hub.md): step 4 of the tour (personal progression)
- [Navigation](../features/navigation.md): the navbar/sub-nav structure the tour points at
