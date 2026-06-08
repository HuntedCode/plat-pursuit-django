# Badge Art Reveal Event

A time-boxed, community-driven event that reveals badge artwork as the whole
community earns platinum trophies. It lives in the self-contained **`art_reveal`**
app. The event machinery is disposable (built for one campaign), but the artwork
it reveals is permanent: on release, each piece is pushed onto the badge's real
`badge_image` and stays there after the event ends.

It is the donation-free sibling of the fundraiser's badge-artwork reveal
(`/staff/badge-reveal/`): the trigger is community platinum progress, not money.

---

## How it works

1. Staff create one `ArtRevealEvent` and add `ArtRevealItem`s (one per badge),
   each with an `order` (1-based) and the to-be-revealed `artwork` uploaded ahead
   of time. Uploaded art is **hidden** until its item is released.
2. The community earns platinum trophies. The **counter** is community-wide
   platinums earned **since `started_at`** on non-shovelware games whose `Concept`
   is covered by **any** badge's stages (see [Counter definition](#counter-definition)).
3. Every `platinums_per_reveal` platinums (default **5**) unlocks the next item in
   `order`. With 40 items at 5 each, all art is out at 200 community platinums.
4. On release, the item's `artwork` is copied onto `badge.badge_image`, so the art
   goes live everywhere (badge pages, dashboard, etc.) permanently.
5. A site-wide **banner** shows progress to the next reveal and overall progress,
   with a CTA to the **event page** (`/events/badge-reveal/`): a hero carousel of
   revealed art, a progress-to-next bar, and a full grid with locked placeholders.

## Counter definition

`compute_badge_platinum_count(since)` counts `EarnedTrophy` rows where:

- `earned=True` and `earned_date_time >= event.started_at`
- `trophy__trophy_type='platinum'`
- the trophy's `Concept` is covered by **any** `Badge` (via `Stage.series_slug`)
- the game is **not** shovelware-flagged (`auto_flagged`/`manually_flagged`;
  `manually_cleared` still counts)

It is community-wide (all profiles, not just Discord-linked). The badge-covered
concept ids are passed as an `__in` subquery, so each platinum counts once (no
join multiplication, no need for an outer `DISTINCT`).

## Performance contract

The count is **heavy** (community-wide aggregation) and runs **only in the cron**
(`process_art_reveals` → `reconcile_event`), which stores the result on
`ArtRevealEvent.last_platinum_count`. The event page reads that cheap stored
counter; it never recomputes on the request path.

The **site-wide banner** renders on every page, so its data is a cache of plain
primitives (`get_active_banner`, 60s TTL): name, the `progress()` dict, and the
latest-unlock summary. After a warm cache the banner does **zero** per-request DB
work; the count + latest-unlock lookups run at most once per TTL, not once per
render. `reconcile_event` invalidates the cache on each reveal so the banner
refreshes immediately. This follows the project rules: per-user/community
aggregates must be DB-side, and heavy work must stay off the render path.

## Data model

| Model | Key fields | Notes |
|-------|-----------|-------|
| `ArtRevealEvent` | `slug`, `is_active`, `started_at`, `ended_at`, `platinums_per_reveal`, `banner_*`, `last_platinum_count`, `last_counted_at` | `is_live()` / `show_banner()` / `progress()` mirror the fundraiser. `progress()` is derived entirely from `last_platinum_count` so the banner never disagrees with the released flags. |
| `ArtRevealItem` | `event`, `badge`, `order`, `artwork`, `placeholder_label`, `released`, `released_at` | Unique `(event, order)` and `(event, badge)`. `badge` should be the **tier-1** badge of a series (tiers 2-4 inherit art via `base_badge`); the admin inline enforces this. `release()` copies art onto the badge, idempotently, without overwriting existing art. |

## Operations

- **Set up**: create the event in admin (set `started_at`, `platinums_per_reveal`,
  `banner_active`), add items (badge + order + artwork), then flip `is_active`.
- **Cron**: register `process_art_reveals` (~every 10-15 min). It reconciles the
  released set to the current count each run, so a missed run self-heals.
- **Manual trigger**: the admin action **"Recount community platinums & release
  now"** runs `reconcile_event` immediately (handy for testing / forcing a reveal).
- **Banner**: set `banner_active=True`; `banner_dismiss_days=0` re-shows every
  session, `>0` persists the dismissal that many days.

## Gotchas and Pitfalls

- **The art file exists in media before release.** Display is gated (the template
  only emits `artwork.url` for `released` items, and the carousel iterates only
  released items), but the underlying file sits in `media/art_reveal/` at an
  upload-named path. Don't link the raw path anywhere; treat the gate as
  display-level, not access-control.
- **Counter is ALL badges, not just the event's badges.** "Platinums toward
  badges" means any badge-covered game. If you want only the event's 40 badges to
  drive the bar, narrow `_badge_concept_ids()` in `services.py` to the event's
  badge series.
- **`release()` never overwrites existing art.** If a badge already has a
  `badge_image`, the item is marked released but the art isn't replaced.
- **Count runs before the row lock** in `reconcile_event` (intentional: don't hold
  the lock during a multi-second aggregation). Releases are forward-only and
  idempotent, so any drift self-corrects next run.
- **One live event at a time.** `get_active_event()` picks the most recent active,
  date-valid event. Overlapping active events aren't supported by the banner.
- **Tailwind**: the banner + event page introduced new class combos; rerun
  `npm run build` if classes look unstyled.
