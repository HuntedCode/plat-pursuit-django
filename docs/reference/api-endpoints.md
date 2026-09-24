# API Endpoints

All API endpoints live under `/api/v1/` and are defined in `api/urls.py`. The web app uses session/CSRF authentication; PlatBot uses token authentication (`Authorization: Token <token>`, matched against `BOT_API_KEY` by `IsDiscordBot`). Staff-only endpoints require the `StaffRequiredAPIMixin`.

## Authentication Patterns

| Pattern | Usage |
|---------|-------|
| Session + CSRF | Web app (the default) |
| Token auth | PlatBot endpoints (`/api/v1/verify/`, `/sync-roles/`, `/recheck-badges/`, ...) |
| Login required | Most write operations |
| Staff required | Admin endpoints, game family management, roadmap editor, subscription admin |
| No auth | Some read-only endpoints (recent reviews feed, review list/detail/replies, profile card image rendering) |

## Endpoints by Category

### PSN Account Linking

| Method | Path | Auth | Purpose |
|--------|------|------|---------|
| POST | `/api/v1/generate-code/` | Login | Generate PSN verification code |
| POST | `/api/v1/verify/` | Login | Verify PSN account |
| GET | `/api/v1/check-linked/` | Login | Check if account is linked |
| POST | `/api/v1/unlink/` | Login | Unlink PSN account |
| POST | `/api/v1/sync-roles/` | Login | Sync Discord roles |
| POST | `/api/v1/recheck-badges/` | Login | Force badge re-evaluation |
| POST | `/api/v1/refresh/` | Login | Trigger profile sync |
| GET | `/api/v1/summary/` | Login | Profile summary data |
| POST | `/api/v1/trophy-case/` | Login | Update trophy case selections |
| POST | `/api/v1/projects/accept/` | Login (linked) | Accept a claimable Project (`{slug}`) or all (`{all:true}`); banks XP. Returns `{granted, accepted[], claimable_count}` |

### Universal Nav Search (Open)

The navbar search bar and the anonymous landing hero. All three are open to anonymous callers on
purpose: the search IS the pitch, and a hunter has to be able to look someone up before signing up.
Driven by `navbar-search.js` and `landing.js`.

| Method | Path | Auth | Purpose |
|--------|------|------|---------|
| GET | `/api/site-suggest/` | Open | Typeahead over the catalogue, grouped (games / badges / franchises / hunters) |
| POST | `/api/search-sync-profile/` | Open | Add a PSN Online ID and start its sync. Creates the Profile if new; otherwise refreshes it, **or refuses with 429 if its cooldown is still running** |
| GET | `/api/add-sync-status/` | Open | Poll the added hunter's ingestion state. Read-only, consumes no PSN tokens. **The most-polled endpoint on the site** and deliberately un-rate-limited: see below |

**The add-and-sync contract** (pinned by `tests/engine/test_navbar_add_sync.py`). A first sync pulls an
entire trophy history through rate-limited workers, so it is measured in minutes; the client has to
narrate a wait, not await a response.

`POST /api/search-sync-profile/` returns `{success, reason, message, psn_username, slug}`. Poll with
the **`psn_username` from the response, not the raw input**: a newly created Profile is stored
lowercased. The client must read the body of a non-ok response to surface a refusal (see
`API.failureBody` in [js-utilities](js-utilities.md)); `API.request` throws on any non-ok, so an
unread body means the server's sentence is discarded.

**`reason` is on every body, success included**, so a caller has one field to switch on rather than
inferring from the status code. See the shared refusal contract below -- all three refresh surfaces
return the same shape, pinned by `test_all_three_refresh_surfaces_return_one_body_shape`.

`GET /api/add-sync-status/?psn_username=<name>` (`__iexact`) always returns 200 for a lookup that
has a name. A known profile returns `{sync_status, account_id, psn_username, slug, stats}`; an unknown
one returns only `{sync_status: 'error', account_id: ''}`, so a caller must not assume the rest are
present. Three states matter:

| Response | Meaning | What the client does |
|----------|---------|----------------------|
| `sync_status: 'error'` | Unknown to PSN, or the sync failed | Stop polling, offer the spelling/privacy line |
| `account_id` truthy **and** `slug` set | Basic ingestion landed | Stop polling, reveal the profile link |
| anything else | Queued or mid-flight | Keep polling, narrate `sync_status` |

**`stats` is the live tally.** `{plats, golds, silvers, bronzes}`, and these four are the **only
profile figures that move during a sync** — they are per-type denorms incremented by the `EarnedTrophy`
signals as the walk proceeds. Costs no query (the profile is already loaded for the lookup) and exposes
nothing the profile page does not already print. `ProfileSyncStatusView` sends the same block.

**Do NOT derive a displayed "total trophies" from them without checking the PROFILE OWNER's filters**
(not the viewer's — on a public profile page those are different people). Their sum is by definition
`Profile.total_trophies_raw`, which ignores `hide_hiddens`; the figure the profile hero displays is
`Profile.total_trophies`, which is **filter-respecting** (`hide_hiddens` *and* `hide_zeros`, via
`profile_stats_service.update_profile_trophy_counts`). For a hunter who hides games the derived sum is
therefore **higher** than the figure finalize will write, so a UI built on it climbs all sync and then
drops on reload — which reads as data loss.

`ProfileDetailView` answers this with **two** context values, and keeping them separate matters:

| | |
|---|---|
| `can_derive_trophy_total` | *Would* a derived figure be honest for this hunter — i.e. no display filter on. Drives the `data-live-total` **hook**, and is therefore true on a **settled** profile too |
| `live_trophy_total` | Is the rendered value stale *right now* — `sync_status != 'synced'`, so syncing **or** error. Drives the **value** |

Gating the hook on the value instead was a real bug: the client captures that element once at load, so a
settled page carried no hook and pressing Refresh left the figure frozen for the page's life. See
`test_a_settled_profile_shows_its_real_total_but_keeps_the_hook` and
`test_a_hunter_who_hides_games_keeps_the_static_total`.

**Gotchas**
- `Profile.sync_status` defaults to **`'synced'`**, so a brand-new row reports 'synced' before
  anything has synced. `account_id` is what gates the link, never the status.
- `total_trophies` waits for **finalize**; only the four `stats` counters climb during the walk. Any
  other figure read mid-sync is a pre-sync snapshot.
- **Never look this column up with `iexact`.** On Postgres it compiles to
  `UPPER("psn_username") = UPPER(%s)`, which neither the unique constraint nor `psn_username_idx` can
  serve, so it sequentially scans every Profile. `Profile.save()` lowercases the column unconditionally,
  so `.lower()` + exact is correct as well as cheap. The failure is invisible from outside -- the wrong
  lookup returns exactly the right answer, just slowly -- which is why it is pinned at the source.
- Callers still need a poll cap; both clients stop after 120 ticks at 2.5s. A status that never moves
  is reachable whenever the worker is down.
- The Profile row is created **before** PSN confirms the name exists, so a typo leaves a row behind
  that later reports `sync_status: 'error'`.

### Refreshing a tracked hunter

| Method | Path | Auth | Purpose |
|--------|------|------|---------|
| POST | `/api/trigger-sync/` | Login | Refresh YOUR OWN profile. Drives the navbar avatar panel's "Sync Now" |
| POST | `/api/hunters/<psn_username>/refresh/` | Login | Ask for **another** hunter's profile to be refreshed. Drives the control on `/hunters/<name>/`. Jumps the orchestrator queue |

The rule these follow: **open to ADD a hunter nobody tracks yet, signed-in to REFRESH one we already
have.** Adding is the anonymous landing pitch and stays open; refreshing a tracked profile is not part
of that pitch, and tying it to an account is what makes a per-caller rate limit mean anything.

**The shared refusal contract.** All three surfaces (`trigger_sync`, `search_sync_profile` for an
existing hunter, and `hunters/<name>/refresh/`) go through `SyncService.request_refresh` and return one
body shape. Switch on `reason`, not the status code:

| `reason` | Status | Meaning | What a client should do |
|----------|--------|---------|-------------------------|
| `''` | 200 | A refresh was queued | Show progress; poll `add-sync-status` |
| `already_syncing` | 200 | One was **already** running; nothing new queued | Show progress, but do not narrate a fresh start |
| `cooldown` | 429 | Synced too recently. `seconds_to_next_sync` says when | **Offer the profile anyway** -- see below |
| `outage` | 503 | PSN is down site-wide | Say so; retrying will not help |
| `rate_limited` | 429 | The CALLER asked too often | Say so; this is about them, not the profile |

Every body also carries `psn_username` and `slug`. **A `cooldown` refusal is not a dead end**: it means
the hunter exists and is current, so a client should reveal the profile link rather than paint an error.
Before the refusal was made honest, a tracked hunter returned 200 and the poll revealed that link on its
first tick, so treating 429 as a failure was a regression dressed as a fix -- it broke the anonymous
hero, whose whole job is getting a stranger onto a profile page.

**`add-sync-status` is deliberately NOT rate-limited**, unlike its login-gated sibling
`/api/profile-sync-status/` (60/m per user). Three clients poll it on a timer -- `navbar-search.js` and
`landing.js` every 2.5s (24/min each), `refresh-control.js` every 4s (15/min) -- and one viewer can have
two running at once. Anonymous polling here is a **first-class use, not abuse**: the landing hero *is*
the pitch to a stranger. A cap tight enough to bound scraping would have to be keyed on IP, and any
household, office or mobile carrier behind one address would hit it; the front door is the worst thing on
the site to break. The lookup is a single indexed read of figures the profile page already prints
unauthenticated, so scraping it is no cheaper than crawling the public, sitemapped profile pages.

Its lookup uses `.lower()` + exact, never `iexact`, for the reason recorded in the Gotchas below.
Pinned by `test_no_sync_endpoint_seq_scans_the_profile_table`.

**Two independent bounds, and the important one is not the rate limit.** The cooldown on
`Profile.last_synced` is a **per-profile** throttle (5 min premium / 1 hour basic, `sync_tier`'s only
effect on a sync path), so any number of people asking about the same hunter inside its window produce
**one** refresh. The per-caller rate limit bounds how many *different* profiles one account can spend
tokens on.

**Gotchas**
- `last_synced` is stamped at the **START** of a sync, not its completion, so a sync that runs longer
  than the cooldown leaves the cooldown open while `sync_status` is still `'syncing'`. That is what
  `already_syncing` exists for: `PSNManager.profile_refresh` has branches for `'error'` and `'synced'`
  only and silently does nothing on `'syncing'`, so without the check the endpoint reported success for
  a job nobody queued.
- The queue jump front-runs the **orchestrator** job only. That job then fans per-game work out to
  `low_priority`, which queues normally, so the jump buys a sooner start and not a sooner finish. Never
  promise a duration in the UI.

### Comments (Legacy / Read-Only)

The comment system no longer accepts new comments. The list/create endpoints have been removed; only vote, report, and detail/edit/delete on existing rows remain. See [Comment System (Legacy)](../features/comment-system.md) for the full story.

| Method | Path | Auth | Purpose |
|--------|------|------|---------|
| GET/PUT/DELETE | `/api/v1/comments/<comment_id>/` | Login | Detail/edit/delete a historical comment (owner only for write ops) |
| POST | `/api/v1/comments/<comment_id>/vote/` | Login | Toggle upvote on an existing comment |
| POST | `/api/v1/comments/<comment_id>/report/` | Login | Flag an existing comment for staff review |
| POST | `/api/v1/guidelines/agree/` | Login | Accept community guidelines (still required by other systems, e.g. reviews) |

### Roadmaps (Staff Only)

Staff-authored platinum guides on game detail pages. Replaces the old Checklists API. See [Roadmap System](../features/roadmap-system.md).

| Method | Path | Auth | Purpose |
|--------|------|------|---------|
| POST | `/api/v1/roadmap/<roadmap_id>/publish/` | Staff | Publish or unpublish a roadmap |
| POST | `/api/v1/roadmap/<roadmap_id>/upload-image/` | Writer+ on that roadmap | Upload an inline image for the editor (scoped per-roadmap so trial-writer escalation works) |
| GET | `/api/v1/youtube/attribution-lookup/?url=<youtube_url>` | Login | Resolve a YouTube URL to its channel name + URL via oEmbed (used by the editor's live attribution preview). Rate-limited 30/min/user. Returns `{"channel_name": str, "channel_url": str}` (empty strings on miss). |

### Roadmap Collaboration (Staff / Authors)

Concurrent editing for the roadmap editor. Locks stop two authors overwriting each other; notes are the
review thread on a draft.

| Method | Path | Auth | Purpose |
|--------|------|------|---------|
| POST | `/api/v1/roadmap/<id>/lock/acquire` | Author | Take the edit lock |
| POST | `/api/v1/roadmap/<id>/lock/heartbeat` | Author | Keep it alive while editing |
| POST | `/api/v1/roadmap/<id>/lock/release` | Author | Give it up |
| POST | `/api/v1/roadmap/<id>/lock/branch` | Author | Branch rather than wait |
| POST | `/api/v1/roadmap/<id>/lock/break` | Staff | Force-break a stale lock |
| POST | `/api/v1/roadmap/<id>/lock/merge` | Author | Merge a branch back |
| GET/POST | `/api/v1/roadmap/<id>/notes` | Author | List / add review notes |
| GET/PATCH/DELETE | `/api/v1/roadmap/<id>/notes/<note_id>` | Author | One note |
| POST | `/api/v1/roadmap/<id>/notes/<note_id>/resolve` | Author | Resolve a note |
| POST | `/api/v1/roadmap/<id>/notes/mark-read` | Author | Mark the thread read |
| GET | `/api/v1/roadmap/<id>/preview` | Author | Render an unpublished draft |
| GET | `/api/v1/roadmap/<id>/hidden-authors` | Staff | Authors hidden from credits |
| GET | `/api/v1/roadmap/<id>/trial-writers` | Staff | Trial-writer roster |

### Community Stats

Denormalized daily community aggregates, feeding the Discord tracker and the site ribbon.

| Method | Path | Auth | Purpose |
|--------|------|------|---------|
| GET | `/api/v1/community-stats/today` | No | Today's running totals |
| GET | `/api/v1/community-stats/<date>` | No | One day's totals |
| GET | `/api/v1/community-stats/records` | No | All-time record days |

### Notifications — WITHDRAWN (2026-08)

The notification system is **hidden pending its rebuild** ([notification-system.md](../architecture/notification-system.md)). Nine routes are unrouted and now 404: `GET /api/v1/notifications/`, `mark-all-read/`, `bulk-delete/`, `<pk>/read/`, `DELETE <pk>/`, `<pk>/rating/`, and the three admin compose endpoints (`send/`, `preview/`, `target-count/`). The views are parked in `api/notification_views.py`; the models, the data and every producer are untouched.

`POST /api/v1/notifications/<pk>/rating/` is worth calling out separately: it was the **second server-side writer of `UserConceptRating`**, and withdrawing it leaves `GroupRatingView` as the only one.

| Method | Path | Auth | Purpose |
|--------|------|------|---------|

### Shareable Images

| Method | Path | Auth | Purpose |
|--------|------|------|---------|
| GET | `/api/v1/shareables/completion/<trophy_group_id>/html/` | Login | Plat card preview markup + art options, plus what the share modal's chrome needs: `game_name` / `game_url` (the link out), `has_rating` + `user_rating` (so the rate form opens prefilled), `playtime` |
| GET | `/api/v1/shareables/completion/<trophy_group_id>/png/?theme=&art=` | Login | Plat card PNG download |
| GET | `/api/v1/shareables/platinum/<earned_trophy_id>/html/` | Login | Legacy alias for the pair above (notifications + external consumers) |
| GET | `/api/v1/shareables/platinum/<earned_trophy_id>/png/` | Login | Legacy alias |
| GET | `/api/v1/share-temp/<filename>` | No | Serve temp share image |

### Monthly Recaps

| Method | Path | Auth | Purpose |
|--------|------|------|---------|
| GET | `/api/v1/recap/available/` | Login | Available recap months |
| GET | `/api/v1/recap/<year>/<month>/` | Login | Full recap data (60/min) |
| POST | `/api/v1/recap/<year>/<month>/regenerate/` | Login | Force regenerate (10/min) |
| GET | `/api/v1/recap/<year>/<month>/html/` | Login | Recap share card HTML (60/min) |
| GET | `/api/v1/recap/<year>/<month>/png/` | Login | Recap share card PNG (20/min) |
| GET | `/api/v1/recap/<year>/<month>/deck/` | Login | Every slide's HTML in one response (what the deck uses) |
| GET | `/api/v1/recap/<year>/<month>/slide/<type>/` | Login | One slide partial. No in-repo caller |

### Game Lists — REBUILT (2026-09), off `/api/v1/`

The rebuilt feature's endpoints live under the PAGE's path, not here: they share the pages' gate,
they answer one template's fetches, and routing them through DRF would mean a second permission stack
that has to agree with the first. They are listed in
[docs/features/game-lists.md](../features/game-lists.md#api-endpoints) with their rate limits.

| Route | Name | Method |
|---|---|---|
| `/community/lists/create/` | `list_create` | POST (form) |
| `/community/lists/<id>/update/` | `list_update` | POST — rename, describe, publish |
| `/community/lists/<id>/reorder/` | `list_reorder` | POST — Ranked lists only |
| `/community/lists/<id>/like/` | `list_like` | POST |
| `/community/lists/<id>/follow/` | `list_follow` | POST |
| `/community/lists/<id>/add/` | `list_add_game` | POST — optional `section` files the game on arrival (empty or absent = the ungrouped bucket); echoes back `section`, the value the service settled on under the list lock |
| `/community/lists/<id>/items/<item>/remove/` | `list_remove_game` | POST |
| `/community/lists/<id>/items/<item>/section/` | `list_item_assign` | POST — file one entry under a section |
| `/community/lists/<id>/sections/` | `list_section_create` | POST — **members only** |
| `/community/lists/<id>/sections/reorder/` | `list_sections_reorder` | POST |
| `/community/lists/<id>/sections/<s>/rename/` | `list_section_rename` | POST — **members only** |
| `/community/lists/<id>/sections/<s>/delete/` | `list_section_delete` | POST |
| `/community/lists/<id>/search/` | `list_game_search` | GET |
| `/community/lists/for-game/<concept_id>/` | `lists_for_concept` | GET — where can this game go (quick-add) |
| `/community/lists/new-with-game/<concept_id>/` | `list_create_with_concept` | POST — create a list and file the game, atomically |

A cross-section drag posts to `list_reorder` **or** `list_item_assign` depending on whether the page
is showing the list's real sequence — see
[game-lists.md](../features/game-lists.md#filing-a-game-two-payloads-on-purpose) for why that is two
endpoints rather than one. Sections and items are resolved **within their list**, never by their own
id, so a sub-resource on somebody else's list 404s exactly as a missing one does.

Every one that takes a list id resolves it through `readable_by()` and answers a uniform 404, so an
id alone cannot confirm a list exists or whose it is. `list_create` has no list to resolve and
redirects with a message instead.

The LEGACY `/api/v1/lists/*` endpoints remain unrouted. `GameSearchView` (`/api/v1/games/search/`),
which outlived that cut, was **deleted in 2026-09**: its last caller died with the legacy templates,
and its `?exclude_list=` parameter read `GameListItem` for any list id with no ownership or
visibility check. The
models and templates are retained.

### Game Families (Staff Only)

> **REMOVED 2026-08** (staff strip-down): all six `game-families/` staff endpoints were deleted with
> the `/staff/game-families/` management page. The `GameFamily` model and the IGDB linking pipeline
> are untouched; manual overrides go through Django admin until a rebuild.

### Fundraiser

| Method | Path | Auth | Purpose |
|--------|------|------|---------|
| POST | `/api/v1/fundraiser/<slug>/donate/` | Login | Create donation checkout |
| POST | `/api/v1/fundraiser/claim/` | Login | Claim badge series |
| POST | `/api/v1/admin/fundraiser/claim-status/` | Staff | Update claim status |

### Dashboard — DELETED (2026-08)

The modular dashboard was deleted in badge cutover 5b, along with `DashboardConfig` (migration `0304`)
and its three config/reorder/module endpoints. `/dashboard/` 301s to `/`. See
[dashboard.md](../features/dashboard.md).

### Stats Page — HIDDEN (2026-08)

My Stats is hidden pending a rebuild; `/stats/` 302s to Home and the premium stats endpoint is unrouted.

### Game Flags

User-submitted data quality flags (delisted, shovelware, VR-only, buggy trophies, etc.). See [Community Flags](../features/community-flags.md).

| Method | Path | Auth | Purpose |
|--------|------|------|---------|
| POST | `/api/v1/games/<game_id>/flag/` | Login | Submit a community flag report against a game |
| POST | `/api/v1/games/<game_id>/flag/versions/` | Login | Submit ONE flag across several versions of that game (the Game page's selector). Body carries `game_ids`; ids are intersected server-side against the anchor's own versions. Shares one 5/min bucket with the endpoint above; a tripped limit is a 403 with `detail` |

### Contract Modals (HTML fragments)

Lazy-fetched contract-card HTML injected into the shared `.pp-detail-modal` on the Career, Badge Detail, and Game Detail pages (fetched with `X-Requested-With: XMLHttpRequest`; a non-200 lets the trigger fall back to its `href`).

| Method | Path | Auth | Purpose |
|--------|------|------|---------|
| GET | `/career/contracts/<slug>/modal/` | Login + linked | Full contract card with the **viewer's** per-game progress (`ContractModalView`) |
| GET | `/career/contracts/<slug>/preview/` | **Public** | **Anonymised** contract card (member games show trophy composition, not progress) + a sign-up / link-PSN CTA, for logged-out / unlinked viewers (`ContractModalPreviewView`). Renders only public contract/game data (`build_contract_modal(None, slug)` — no per-user work). |

### Profile Cards & Badge Showcase — DELETED (2026-08)

Both systems were deleted in badge cutover 5b: the profile-card renderer with its four endpoints, and the
showcase system including its models (migration `0303`). The Pursuer Card replaced the profile card; see
`/api/v1/pursuer-card/` under Misc.

### Subscription Admin (Staff Only)

| Method | Path | Auth | Purpose |
|--------|------|------|---------|
| POST | `/api/v1/admin/subscriptions/action/` | Staff | Admin action (resend, deactivate) |
| GET | `/api/v1/admin/subscriptions/user/<id>/` | Staff | User subscription detail |

### Community Reviews — ARCHIVED (2026-05)

The Review Hub was archived; all 10 `/api/v1/reviews/*` endpoints are unrouted. Ratings survive at
`/community/rate-my-games/` — see Ratings & Quick Takes below.

### Ratings & Quick Takes

> The structured rating system now mounts under the `/api/v1/ratings/` prefix; the `/reviews/` rows above are legacy (the text-review system was archived 2026-05). Ratings power the game-detail Ratings tab, dashboard, and share cards.

| Method | Path | Auth | Purpose |
|--------|------|------|---------|
| POST | `/api/v1/ratings/<concept_id>/group/<group_id>/rate/` | Login (linked) | Submit/update a rating. **`recommendation` is required** on a new rating (an update of an existing one falls back to the stored value when it is omitted). Optional `blurb` (<=140 char public "quick take"), sanitized + banned-word filtered. A non-empty blurb requires guidelines agreement (403 `needs_guidelines` if not). Omitting `blurb` preserves an existing one; sending `""` clears it. |
| POST | `/api/v1/ratings/blurb/<rating_id>/report/` | Login (linked) | Report a rating's quick take for moderation (reactive: publish -> report -> staff soft-hide via `blurb_hidden`). Body `{reason, details?}`; rate-limited 10/m; can't report your own; deduped per reporter. |
| POST | `/api/v1/guidelines/agree/` | Login | Record community-guidelines agreement (idempotent). The blurb write path calls this on submit (the modal's fine print is the notice). |
| GET | `/api/v1/ratings/wizard/queue/` | Login (linked) | The rating wizard's next batch of unrated groups |
| GET | `/api/v1/ratings/<concept_id>/group/<group_id>/trophies/` | Login | Trophy list for a group, for the rating modal |

Blurbs are read only through `UserConceptRating.visible_blurbs()` (present + not staff-hidden, backed by the partial `rating_blurb_idx`); the game-detail view previews the newest few per group with `select_related('profile')` (whale-safe). Reports are stored on `BlurbReport` (mirrors `ReviewReport`, triaged in Django admin; FKs the rating, so it follows the rating through `Concept.absorb()` with no absorb branch). The stored blurb is plain, **unescaped** text -- render it only in an auto-escaped HTML text context, never `|safe` or a JS/attribute/JSON context.

### Tutorial System — UNROUTED

The tutorial endpoints are not currently routed.

### Misc

| Method | Path | Auth | Purpose |
|--------|------|------|---------|
| POST | `/api/v1/equip-title/` | Login | Equip/unequip title |
| POST | `/api/v1/user/timezone/` | Login | Update user timezone |
| POST | `/api/v1/tracking/site-event/` | No | Track client-side event |
| POST | `/api/v1/easter-eggs/roll/` | Login | Server-side easter-egg probability roll |
| GET | `/api/v1/game-backgrounds/` | Login | Search game backgrounds |
| GET | `/api/v1/pursuer-card/` | Login | Refresh the Pursuer Card (identity signature) |
| POST | `/api/v1/collectibles/items/<id>/progress` | Login | Toggle a collectible checklist item |
| GET | `/api/v1/game-backgrounds/<concept_id>/images` | Login | Background images for a concept |
| GET | `/api/v1/shareables/completion/<trophy_group_id>/png` | Login | Completion share card (PNG) |
| GET | `/api/v1/youtube/attribution-lookup` | Staff | Resolve a YouTube video's attribution |
| GET | `/api/v1/games/search` | Login | Game search (autocomplete/typeahead) |
| GET | `/api/v1/games/<np_communication_id>/players` | No | Players of a game, for the game-detail panel |
| POST | `/api/v1/user/quick-settings/` | Login | Single-setting writes: profile toggles (hide hiddens / zeros), 24hr clock, timezone, browse defaults, one-shot `ui_flag` education flags (see [onboarding.md](../features/onboarding.md)), `whats_new_seen` — the id of the newest announcement dismissed, validated against the shipped entries (see [whats-new.md](../features/whats-new.md)), and `contracts_seen` — an ISO timestamp, the newest announcement the Career new-contracts modal has SHOWN this hunter; validated, clamped to now, floored a year back and monotonic (see [onboarding.md](../features/onboarding.md)) |

## Rate Limits

Rate limits are applied via `django-ratelimit` on specific endpoints:

| Endpoint | Limit | Purpose |
|----------|-------|---------|
| Recap detail | 60/min | Prevent excessive data generation |
| Recap regenerate | 10/min | Limit costly regeneration |
| Recap share PNG | 20/min | Limit Playwright rendering |
| Recap share HTML | 60/min | Limit share card generation |
| Refresh a tracked hunter | 10/min by user | PSN-token cost, across DIFFERENT profiles. The per-profile cooldown caps the cost of any one of them |
| Search-sync profile (authed) | 15/min by user | PSN-token cost of a sync |
| Search-sync profile (anon) | 3/min by IP | Same, for an open endpoint. The user bucket is checked first, so a member behind a NAT'd IP is not held to this |

## Related Docs

- [Mobile App](../guides/mobile-app.md): why the mobile API was removed, and what to know when rebuilding it
- [Roadmap System](../features/roadmap-system.md): Roadmap editor API details (replaced the legacy Checklists API)
- [Challenge Systems](../features/challenge-systems.md): Challenge API details
- [Community Flags](../features/community-flags.md): Game flag categories and effects
- [Fundraiser](../features/fundraiser.md): Donation/claim API details
- [Comment System (Legacy)](../features/comment-system.md): Why the comment list/create endpoints are gone
