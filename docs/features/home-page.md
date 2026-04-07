# Home Page (`/`)

The site root (`/`) is a smart router that branches the response based on user state. There is no single "homepage" template: which page renders depends on whether the visitor is logged in, whether they have a linked PSN profile, and whether that profile has finished syncing. The goal is that every visitor lands somewhere useful instead of bouncing off a generic page.

## Architecture Overview

`HomeView` (in `core/views.py`) inspects the request user in `dispatch()` time and resolves a state, then `get_template_names()` returns the matching template. Anonymous visitors get a marketing landing page; authenticated users get one of three increasingly-personalized shells based on how complete their PSN onboarding is.

The dashboard itself is not its own view in this flow. For fully-synced users, `HomeView` calls `build_dashboard_context()` from `trophies/views/dashboard_views.py` directly and renders `templates/trophies/dashboard.html`. The standalone `DashboardView` class is preserved as a thin wrapper around the same helper so the legacy `/dashboard/` URL alias and any direct callers keep working.

The four-state model exists because the dashboard is heavy: it queries dashboard config, hydrates 30+ modules, and pulls a tab system. Showing it to a user whose sync hasn't finished would render mostly-empty modules and feel broken. The intermediate `link_psn` and `syncing` shells fix that while still feeling like the same app, since they reuse the dashboard's design tokens, the site heartbeat ribbon partial, and the hotbar.

## State Resolution

`HomeView._resolve_state()` returns one of four values:

| State | Detection | Template |
|-------|-----------|----------|
| `anonymous` | `not request.user.is_authenticated` | `home/landing.html` |
| `no_psn` | No `Profile` attached, OR `profile.is_linked == False` | `home/link_psn.html` |
| `syncing` | Linked but `profile.sync_status != 'synced'` (covers both `'syncing'` and `'error'`) | `home/syncing.html` |
| `synced` | Linked and `profile.sync_status == 'synced'` | `trophies/dashboard.html` |

`'error'` is intentionally bucketed with `'syncing'` rather than `'synced'`. A user whose last sync errored should not be dropped into a dashboard built from stale or partial data; the syncing shell surfaces the error state explicitly with messaging that points them at the hotbar's retry button.

## File Map

| File | Purpose |
|------|---------|
| `core/views.py` | `HomeView` smart router + `SYNCING_DID_YOU_KNOW` rotating fact list |
| `trophies/views/dashboard_views.py` | `build_dashboard_context()` helper called by `HomeView` for synced users |
| `templates/home/landing.html` | Anonymous marketing page (hero, feature grid, dashboard preview, browse, CTA) |
| `templates/home/link_psn.html` | Logged-in but no PSN linked (welcome, 3-step preview, "what you'll unlock") |
| `templates/home/syncing.html` | Sync in progress / error (status card, Did You Know, what's coming) |
| `templates/trophies/dashboard.html` | Fully-synced state, rendered directly by `HomeView` |
| `static/js/hotbar.js` | Dispatches `platpursuit:sync-status-changed` CustomEvent on transitions |
| `templates/trophies/partials/dashboard/built_for_hunters.html` | Site heartbeat ribbon, included by all three home shells |

## Auto-Refresh on Sync Status Change

Both the dashboard and the syncing shell register listeners for the same `platpursuit:sync-status-changed` CustomEvent and reload the page when the user crosses the relevant boundary, so `HomeView` re-resolves the state and swaps templates without any extra wiring.

**Syncing shell listener** (in `templates/home/syncing.html`):

```js
document.addEventListener('platpursuit:sync-status-changed', function(e) {
    if (e.detail && e.detail.status === 'synced') {
        window.location.reload();
    }
});
```

**Dashboard listener** (in `templates/trophies/dashboard.html`):

```js
document.addEventListener('platpursuit:sync-status-changed', function(e) {
    if (e.detail && e.detail.status !== 'synced') {
        window.location.reload();
    }
});
```

`hotbar.js` polls `/api/profile-sync-status/` every 2 seconds while a sync is in progress (extended to 10 seconds after a minute). When it detects a real transition, it dispatches the CustomEvent on `document` exactly once. This means:

- A user on `home/syncing.html` automatically advances to the dashboard the moment the background sync finishes.
- A user on the dashboard who clicks the hotbar's "Sync Now!" button is automatically dropped into `home/syncing.html` as soon as the first poll confirms `'syncing'` (~2 seconds). Same for any other transition away from `'synced'` (e.g. an error state).

`lastSyncStatus` is a closure variable in `hotbar.js` initialized from the hotbar's `data-sync-status` attribute and updated only on real transitions, so the event fires once per change rather than once per poll. The polling itself only runs when there is something to watch (initial syncing state on page load, or after the user clicks Sync), so a synced user sitting on the dashboard does not poll until they ask for a sync.

## Reused Infrastructure

The home shells deliberately reuse existing pieces instead of building parallel ones:

- **Site heartbeat ribbon** (`built_for_hunters.html`): cached hourly by the `refresh_homepage_hourly` cron, read by all four states. Same component the dashboard uses, so the visual is consistent and there is no extra query cost.
- **Hotbar** (`partials/hotbar.html`): the syncing shell does NOT reimplement sync status display. The hotbar at the top of the page is already showing live status and has a working manual-sync button. The shell adds a larger progress card for prominence but the hotbar is the source of truth.
- **`build_dashboard_context()`**: extracted from `DashboardView.get_context_data` so `HomeView` can render the dashboard for synced users without going through view inheritance. Both views call the same helper.

## Gotchas and Pitfalls

- **`'error'` is treated like `'syncing'`**: A user whose sync errored sees the in-progress shell, not the dashboard. This is intentional but easy to miss when debugging "why isn't the dashboard rendering for this user." Check `profile.sync_status` first.
- **Profile may not exist**: `_resolve_state()` uses `getattr(request.user, 'profile', None)`, not `request.user.profile`, because the `OneToOneField` raises `RelatedObjectDoesNotExist` when no profile exists. Don't change to direct attribute access without the safety net; the `no_psn` state covers the no-profile case.
- **`SYNCING_DID_YOU_KNOW` is randomized server-side per request**: every page load picks a fresh fact, so an auto-reload on sync completion will replace the fact, but a manual reload while still syncing will too. That's a feature, not a bug, but worth knowing.
- **The `home_state` context key**: every shell receives `context['home_state']` set to the resolved state string. Useful for adding state-specific JS or styling in `base.html` later if needed (not currently used).
- **`/dashboard/` is a permanent redirect**: anything linking to `/dashboard/` will 301 to `/`. This is enforced by `RedirectView.as_view(pattern_name='home', permanent=True)` in `urls.py`. Update internal links to use `{% url 'home' %}` instead of `{% url 'dashboard' %}` going forward.
- **The site heartbeat partial silently hides if its cache is empty**: if the `refresh_homepage_hourly` cron is broken for more than two hours (the partial falls back one hour), the entire community-pulse section disappears from all four home states. Check the cron and the `site_heartbeat_*` cache keys if it goes missing.

## Related Docs

- [Dashboard System](dashboard.md): the synced-state experience and the design baseline that the home shells match.
- [Navigation](navigation.md): how the site's mega-menus link out from the home shells.
- [Design System](../reference/design-system.md): card anatomy, tokens, and patterns the shells use.
- [Template Architecture](../reference/template-architecture.md): `base.html` blocks, the zoom wrapper, and context processors.
