# Home Page (`/`)

The site root (`/`) is a smart router that branches the response based on user state. There is no single "homepage" template: which page renders depends on whether the visitor is logged in, whether they have a linked PSN profile, and whether that profile has finished syncing. The goal is that every visitor lands somewhere useful instead of bouncing off a generic page.

## Architecture Overview

`HomeView` (in `core/views.py`) inspects the request user in `dispatch()` time and resolves a state, then `get_template_names()` returns the matching template. Anonymous visitors get a marketing landing page; authenticated users get one of three increasingly-personalized shells based on how complete their PSN onboarding is.

For fully-synced users the router renders `templates/trophies/home.html`, the gamification Home (the dashboard was deleted in the 2026-08 cutover; `/dashboard/` 301s to `/`).

The four-state model exists because the synced Home assumes real data. Since the gates merge
(2026-08), every PRE-SYNCED state renders the same template -- `home/landing.html`, the gates
surface -- with `home_state` driving a per-state hero, close section, and SEO blocks. The
landing's cached sections (heartbeat band, medallion shelf, showcase card, ratings carousel)
render for all three states: the site's best showcase is also the welcome mat and the waiting
room. The syncing state doubles as the new-user onboarding surface (see
[onboarding.md](onboarding.md)): a first sync is a captive 10-30 minute window, so the hero
greets the user with their real PSN numbers and ends in an in-place "Your Pursuer has emerged"
moment while the landing's sections play the tour below.

## State Resolution

`HomeView._resolve_state()` returns one of four values:

| State | Detection | Template |
|-------|-----------|----------|
| `anonymous` | `not request.user.is_authenticated` | `home/landing.html` |
| `no_psn` | No `Profile` attached, OR `profile.is_linked == False` | `home/landing.html` (link hero) |
| `syncing` | Linked but `profile.sync_status != 'synced'` (covers both `'syncing'` and `'error'`) | `home/landing.html` (status hero) |
| `synced` | Linked and `profile.sync_status == 'synced'` | `trophies/home.html` |

`'error'` is intentionally bucketed with `'syncing'` rather than `'synced'`. A user whose last sync errored should not land on a Home built from stale or partial data; the status hero surfaces the error state explicitly with messaging that points them at the account menu's retry.

Two team-preview doors exist for reviewing states an account can't naturally reach: `/?preview=landing` (staff/moderators see the anonymous landing) and `/?preview=syncing` (staff/moderators see the syncing state; the preview forces the first-sync view so the greeting, progress bar, and finale all render, and the DEBUG simulate panel drives the state machine).

## File Map

| File | Purpose |
|------|---------|
| `core/views.py` | `HomeView` smart router + `SYNCING_DID_YOU_KNOW` rotating fact list |
| `templates/home/landing.html` | Anonymous marketing page (search-first hero, systems tour, live demos) |
| `templates/home/_hero_no_psn.html` | The no-PSN hero: greeting, link CTA, 3-step preview, privacy warning |
| `templates/home/_hero_syncing.html` | The syncing hero: status card, live progress, enter finale, dev panel |
| `templates/trophies/home.html` | Fully-synced state: the gamification Home |
| `static/js/navsync.js` | Owns the poll; dispatches `platpursuit:sync-status-changed` on transitions and `platpursuit:sync-progress` on every poll |
| `static/js/syncing.js` | The syncing hero's live personalization + enter-moment state machine (event consumer only, never polls) |
| `static/css/components/syncing.css` | The enter finale's styles |

## Auto-Refresh on Sync Status Change

The syncing hero's transition handling lives in `static/js/syncing.js` and branches three ways on `platpursuit:sync-status-changed`:

- **`synced` on a FIRST sync** (the finale block only renders when `is_initial_sync`): no reload. The status card swaps in place to the enter moment ("Your Pursuer has emerged" + the final trophy counts + one "Enter your Pursuit" CTA to `/`). The user arrives on the Home on purpose.
- **`synced` on a quick refresh**: `window.location.reload()` as before; `HomeView` re-resolves to the synced Home.
- **`error`**: `window.location.reload()`; `HomeView` re-renders the shell with error styling server-side.

Ordering note (load-bearing): navsync dispatches `status-changed` BEFORE the same poll's `sync-progress`, so the finale's cached stats are one poll stale at swap time. The stat fill is idempotent and re-runs from the `sync-progress` listener after completion; the trailing event corrects the figures immediately.

`navsync.js` polls `/api/profile-sync-status/` every 2 seconds while a sync is in progress (extended to 10 seconds after a minute). When it detects a real transition, it dispatches the CustomEvent on `document` exactly once. This means:

- A first-time user on the syncing state gets the in-place enter moment when the sync finishes; a refreshing user advances to the Home automatically.
- A user who triggers a manual sync from the account menu keeps their current page; the navbar's sync panel shows the live progress (the synced Home does not watch for de-sync transitions).

`lastSyncStatus` is a closure variable in `navsync.js` initialized from the navbar sync panel's `data-sync-status` attribute and updated only on real transitions, so the event fires once per change rather than once per poll. The polling itself only runs when there is something to watch (initial syncing state on page load, or after the user triggers a sync), so a synced user browsing the site does not poll until they ask for a sync.

## Live Progress Mirroring (`platpursuit:sync-progress`)

The syncing hero renders its own larger progress card for prominence, but it does not poll. Instead, `navsync.js` dispatches a second CustomEvent, `platpursuit:sync-progress`, after every successful poll, carrying the full sync status payload (`sync_status`, `sync_progress`, `sync_target`, `sync_percentage`, `queue_position`, `is_finalizing`, etc.). An inline mirror in the landing template's syncing branch mirrors `sync_percentage` into `#home-sync-progress-bar` and the count text into `#home-sync-progress-text`, so the larger card stays in lockstep with the navbar panel without doing any extra network work. The payload also carries the additive `psn_found` key (PSN's own totals) while syncing, which drives the greeting's live personalization upgrade.

This split keeps the navbar panel as the single polling source of truth while letting other parts of the page subscribe to live updates declaratively. New consumers should listen to `platpursuit:sync-progress` rather than starting their own polling loop.

### Finalizing State (`is_finalizing` + `finalize_phase`)

The sync status API exposes an `is_finalizing` boolean derived from the `sync_complete_in_progress:{profile_id}` Redis key (see [Token Keeper docs](../architecture/token-keeper.md#sync_complete-atomic-guard)). It is `true` while `_job_sync_complete()` is running the post-sync pipeline (health check, badges, milestones, challenges, cache invalidation) and `false` otherwise. The navbar sync panel uses it to swap the "Syncing..." badge for "Finalizing..." and replace the percentage, so users do not see the bar parked at 100% during the (sometimes lengthy) finalization phase. The home shell's progress card listener mirrors the same swap by replacing the count text with "Finalizing sync...".

The API also exposes a `finalize_phase` string with values `health_check`, `stats_badges`, `milestones`, `challenges`, or `finishing` (see the [Finalize Sub-Phase Tracking section in token-keeper.md](../architecture/token-keeper.md#finalize-sub-phase-tracking)). The navbar panel shows it inside the badge as `Finalizing... (Badges)`; the home shell shows the friendlier copy ("Updating stats and awarding badges...") in its phase text element underneath the bar. Together these turn an opaque "stuck at 100%" experience into visible movement through five named stages.

If the health check finds a trophy count mismatch and re-queues child jobs, the flag correctly toggles back off (the `finally` block in `_job_sync_complete()` always clears the key), the bar drops below 100% naturally, and the badge reverts to "Syncing..." until the next finalization pass.

## Syncing Shell UX Features

The syncing state layers several pieces of context on top of the basic progress card so the page never feels frozen, regardless of how long the sync takes:

- **Initial vs incremental sync detection**: `HomeView.get_context_data` sets `is_initial_sync = (profile.total_trophies == 0)`. First-timers get a different H1 ("Setting up your Pursuit...") and friendlier copy explaining that first syncs take 10-30 minutes; returning users get a tighter "Quick refresh in progress" message. The signal is also correct after an unlink/relink because `total_trophies` resets to 0 on relink.
- **Elapsed time counter**: `HomeView` reads `sync_started_at:{profile_id}` from Redis, computes initial elapsed seconds, and renders them into `#home-sync-elapsed` via `data-elapsed`. A `setInterval` in the page script counts up every second with progressive formatting (`Started just now` → `Started 23s ago` → `Started 4m ago` → `Started 1h 23m ago`). Note: the `sync_started_at` key is cleared in the `_job_sync_complete()` `finally` block, so on the rare mismatch-retry path the counter resets to 0 between rounds. Acceptable trade-off: changing the cleanup behavior would also affect queue position and stuck-sync detection.
- **PSN outage state**: when the `psn_outage` context flag is set, the card swaps to `info` styling (no pulse), shows "Sync paused" as the H1 with a "PSN Down" badge, hides the elapsed timer and progress bar, and explains that the sync will resume automatically when PSN comes back. The site-wide outage banner already covers the global state but the shell card now matches it instead of pretending the sync is still running.
- **Personalized greeting (`psn_found`)**: PSN's own totals (`Profile.earned_trophy_summary` + `trophy_level`) land seconds into the sync, long before our per-game walk finishes. The view passes `psn_found` (or `None` before the summary lands; `get_total_trophies_from_summary()` returns None on empty, so never render it raw) and the template ships BOTH sentence variants; `syncing.js` fills the numbers and swaps when the poll payload's additive `psn_found` key arrives.
- **The tour is the landing itself**: the gates merge retired the bespoke walkthrough carousel; the landing's real sections (medallion shelf with the 3D inspect, the live ratings carousel, the showcase card) render below the hero for every pre-synced state.
- **Error transition**: reloads so users never stare at a misleading in-progress card after a mid-sync failure (the shell re-renders with error styling server-side).
- **Labeled progress count**: the `X / Y` count text is suffixed with "tasks" so users intuit what the numbers mean (each unit is a per-game sync job, not a trophy or game).

## Reused Infrastructure

The home shells deliberately reuse existing pieces instead of building parallel ones:

- **Site heartbeat band** (the landing's `land-pulse` section): cached hourly by the `refresh_homepage_hourly` cron, rendered for every pre-synced state. The old `_built_for_hunters.html` ribbon partial was deleted with the gates merge.
- **Navbar sync panel** (`#nav-sync`, fed by `navsync.js`): the syncing shell does NOT reimplement sync polling. The navbar owns the poll and the shell subscribes to its events; killing or restructuring `#nav-sync` on this page freezes the progress bar and the enter moment never fires.

## Gotchas and Pitfalls

- **`'error'` is treated like `'syncing'`**: A user whose sync errored sees the in-progress shell, not the Home. This is intentional but easy to miss when debugging "why isn't the Home rendering for this user." Check `profile.sync_status` first.
- **Profile may not exist**: `_resolve_state()` uses `getattr(request.user, 'profile', None)`, not `request.user.profile`, because the `OneToOneField` raises `RelatedObjectDoesNotExist` when no profile exists. Don't change to direct attribute access without the safety net; the `no_psn` state covers the no-profile case.
- **The `home_state` context key**: every shell receives `context['home_state']` set to the resolved state string. Useful for adding state-specific JS or styling in `base.html` later if needed (not currently used).
- **`/dashboard/` is a permanent redirect**: anything linking to `/dashboard/` will 301 to `/`. This is enforced by `RedirectView.as_view(pattern_name='home', permanent=True)` in `urls.py`. Update internal links to use `{% url 'home' %}` instead of `{% url 'dashboard' %}` going forward.
- **The site heartbeat partial silently hides if its cache is empty**: if the `refresh_homepage_hourly` cron is broken for more than two hours (the partial falls back one hour), the entire community-pulse section disappears from all four home states. Check the cron and the `site_heartbeat_*` cache keys if it goes missing.

## Related Docs

- [Onboarding](onboarding.md): the sync-wait walkthrough, the enter moment, and the Career first-visit explainer.
- [Navigation](navigation.md): how the site's mega-menus link out from the home shells.
- [Design System](../reference/design-system.md): card anatomy, tokens, and patterns the shells use.
- [Template Architecture](../reference/template-architecture.md): `base.html` blocks, the zoom wrapper, and context processors.
