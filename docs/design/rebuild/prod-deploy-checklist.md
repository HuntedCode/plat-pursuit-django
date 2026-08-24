# Prod Deploy Checklist — Gamification Rebuild

> **Purpose.** The rebuild lives on the long-running `rebuild` branch and does not touch production until launch. Along the way, individual changes accumulate **deploy-time obligations** that can't be captured in code alone: data backfills, one-off command runs, cron registrations, manual dashboard config, and prod-bound PR merges. This doc is the single running list so none of it is lost between now and the cutover.
>
> **How to maintain this.** Whenever a change defers work to deploy (a migration that needs a follow-up backfill, a new command that must be run once, a new cron, a manual config step), **add a row here in the same commit**. Check items off as they're done. When the rebuild ships, this doc is the runbook.

## How to read this

| Field | Meaning |
|---|---|
| **When** | `Now` = a prod task outstanding today (already on `main`/prod path) · `Launch` = run at/after the rebuild cutover · `Post` = after launch, once data settles |
| **Idempotent?** | Safe to re-run? (re-runs that double-count or clobber are flagged) |
| **Blocks** | What stays broken / empty until this runs |

---

## Launch tasks (rebuild cutover)

### Data backfills & one-off commands

| # | Task | Command | When | Idempotent? | Blocks | Done |
|---|------|---------|------|-------------|--------|------|
| ~~1~~ | ~~Compute badge rarity~~ — **DROPPED.** `recalc_badge_rarity` was deleted in cutover 5b; rarity is derived live by `badge_rarity.py` and needs no backfill | — | — | — | — | n/a |
| ~~2~~ | ~~Backfill earn ranks~~ — **DROPPED.** `backfill_earn_ranks` was deleted in cutover 5b. There is no permanent `earn_rank` in the current model: rank is the hunter's LIVE position among current holders, ordered by `earned_at` | — | — | — | — | n/a |
| 3 | **Backfill per-edition `group_progress`** — populates the new `SeriesBadgeStanding.group_progress` read-model (per-edition `[cleared, gating]`) on every existing standing. `recompute_standing` writes it on each sync, but pre-existing standings hold `{}` until re-evaluated. | `python manage.py evaluate_badges --all` | Launch (after migrations) | Yes (full recompute from scratch) | Collection wall + badge-detail per-edition progress read empty/stale for pre-existing standings until their owner next syncs | ☐ |
| 3a | **Migration `0286_alter_seriesbadgestanding_group_progress`** — help_text only, no data change and no column change. Applies on deploy with the rest; listed so it is not mistaken for a schema change needing a backfill. | (auto, on `migrate`) | With the collection work | Yes | nothing | ☐ |
| 3b | **Re-run after the read-model widened (2026-08)** — `recompute_standing` now stores an entry for every EARNABLE edition, not only ones with cleared > 0, so an untouched edition carries its real `[0, gating]`. Until this runs, the Collection caption still shows no chase count on an edition the hunter has not started (it degrades to blank, never to a wrong number). Same command as #3; idempotent. | `python manage.py evaluate_badges --all` | With the rarity/collection work | Yes (full recompute) | "0 / N stages" missing on unstarted editions | ☐ |
| 4 | **Backfill grouping read-models** — materializes `Genre/Theme/Franchise/Company.representative_game` (the tile + detail-hero cover) AND `Genre/Theme.related_tags` (the detail-page related rail) for the rebuilt `/genres/` list, `/genres\|themes/<slug>/` detail, `/franchises/` list + detail, AND `/companies/` list pages. The franchise cover pick honors `is_excluded`/`is_spinoff` link flags. Until it runs, tiles render the neutral placeholder and tag detail pages show no related rail. Then keep fresh via the daily cron below. | `python manage.py recompute_tag_covers` | Launch (after migrations) | Yes (recomputes from scratch; stable pick) | `/genres/` + `/franchises/` + `/companies/` tiles show the glyph placeholder; tag detail pages omit the related-tags rail | ☐ |
| 5 | **Migration `0294_userconceptrating_recommendation`** — a single nullable-by-default column add, applies with the rest; listed so it is not mistaken for something needing a backfill. **There is no backfill and cannot be one:** a declared recommendation cannot be inferred from scores. Every pre-existing rating therefore re-enters the Rate My Games queue exactly once, which IS the mechanism (it also gives each old rating its one chance at a quick take). Expect prolific raters to see their "games waiting" figure jump on deploy — the header splits it ("3 new · 12 need a rec") so that reads as a prompt rather than lost data. | (auto, on `migrate`) | With the ratings work | Yes | nothing | ☐ |
| 6 | **Migration `0295_alter_userconceptrating_recommendation`** — narrows the choices from four to three and carries a **data migration** with it, mapping any `bad_game_good_plat` row to `worth_it`. Narrowing `choices` does not touch the column, so a row left holding the retired value would survive and render its raw slug through `get_recommendation_display`. In practice this touches nothing outside a dev database (the fourth option existed only between 0294 and 0295, neither of which has reached prod), but it must run AFTER 0294 — which it does, by dependency. | (auto, on `migrate`) | With the ratings work | Yes (no-op once run) | nothing | ☐ |
| 7 | **Migration `0296_alter_userconceptrating_recommendation`** — a LABEL-only reword of the middle option ("Great game, rough platinum" -> "Good game, tough plat", and its no-platinum twin "Great game, rough trophies" -> "Good game, tough trophies" -- the twin lives in `RECOMMENDATIONS_NO_PLAT`, which by design appears in no migration at all, since the VALUES are identical and Django validates values rather than labels). The value `good_game_bad_plat` is untouched, so no row changes and nothing needs to run in order; Django only wants a migration because `choices` differs from the last recorded state. Listed so it is not mistaken for a second data migration alongside `0295`. | (auto, on `migrate`) | With the ratings work | Yes | nothing | ☐ |

> **Ordering:** #1 and #2 depend on the STEP 2 schema migrations (`UserBadge.status`/`earn_rank`, `Badge` rarity fields); #3 depends on the `SeriesBadgeStanding.group_progress` migration (`0276`); #4 depends on the `Genre/Theme.representative_game` + `.related_tags` migrations (`0277`, `0278`), the `Franchise.representative_game` migration (`0279`), AND the `Company.representative_game` migration (`0280`). All run after migrations; run order between them doesn't matter. **Note:** #3 was verified on beta (evaluate_badges backfills group_progress as expected) — it still needs a run on prod at cutover.

### Cron / scheduling

| # | Task | When | Done |
|---|------|------|------|
| 1 | **Register `recompute_tag_covers` cron** — new daily Render Cron Job (`python manage.py recompute_tag_covers`, 03:45 UTC) keeping the genre/theme tile covers fresh as games sync. Documented in [cron-jobs.md](../../guides/cron-jobs.md). The one-time backfill is task #4 above; this is the ongoing schedule. | Launch (Render dashboard) | ☐ |
| 2 | **PAUSE the `process_scheduled_notifications` cron** — the notification system is [hidden pending rebuild](rebuild-playbook.md), and this hourly job is the only outbound delivery path still live. With the staff compose UI unrouted nothing new can be scheduled, but the job would keep delivering rows already queued, sending people to a page that redirects home. Un-pausing is the same toggle when the rebuild ships. | Launch (Render dashboard) | ☐ |

### Manual config (dashboards, env, third-party)

| # | Task | When | Done |
|---|------|------|------|
| M1 | **Pause the `send_monthly_recap_emails` Render cron** (3rd of month, 06:00 UTC). The monthly recap is being rebuilt and nothing should go out carrying the old design. `settings.MONTHLY_RECAP_SEND_ENABLED` now defaults to **False**, so the command fails safe even if the cron fires — pausing it is belt-and-braces, and stops a pointless monthly run. **Note this stops the in-app notification too**: it is dispatched from inside the email loop, so the two cannot be separated without lifting it out. Re-enable by setting `MONTHLY_RECAP_SEND_ENABLED=True` in the environment when the rebuilt email ships. | With the recap rebuild | ☐ |
| M2 | **Run `collectstatic`.** `staticfiles/` is badly stale in this working copy (`monthly-recap.js` dated April, `output.css` two days behind), which is invisible in dev — runserver serves from `static/` — and fatal in prod, where WhiteNoise serves `STATIC_ROOT`. Shipping without it serves an April recap controller against the rebuilt templates. | Every deploy, but verify this one | ☐ |
| M3 | **Apply migrations `0287` and `0288`.** 0287 is a `help_text` change on `MonthlyRecap.badge_xp_earned`; 0288 adds the three JSON fields the new context beats persist (`taste_data`, `community_comparison_data`, `month_in_history_data`). Both are additive — no backfill, and recaps generated before them simply omit the new beats. | With the recap rebuild | ☐ |
| M4 | **Apply migrations `users.0019` and `trophies.0289`.** 0019 adds `CustomUser.timezone_confirmed_at` (nullable, no backfill -- null correctly means "never asked", which is true of everyone before this ships). 0289 is a data migration setting `has_been_viewed = False` on every MonthlyRecap, so the rebuilt ceremony reads as unwatched for everybody. **Note the blast radius**: the dashboard's recap module gates its share-card preview on that same flag, so it reverts to offering the recap rather than showing a stale card. That is intended. Non-destructive (a display signal only) and safe to unapply -- the reverse is a documented no-op, because which months each hunter had watched exists nowhere else once it runs. | With the recap landing page | ☐ |


---

## Outstanding prod tasks (independent of rebuild launch)

These are already on the `main`/production path and can/should happen before cutover.

| # | Task | Action | When | Done |
|---|------|--------|------|------|
| A | **Art Reveal self-heal** — auto-completes already-revealed funder claims (attribution + email) via an event-wide sweep | Merge the self-heal PR to `main`, redeploy | Now | ☐ |
| B | ~~**Retire deprecated milestones**~~ — **OBSOLETE (2026-08).** Superseded by the Lane 2 teardown: the entire legacy milestone engine (and the `retire_milestones` command itself) was deleted. Migrations `0282` + `0283` remove the ladder-granted `UserTitle` rows and drop the tables on deploy — nothing to run by hand. **One follow-up:** after `migrate`, run `python manage.py populate_user_titles` (idempotent) to re-create any badge-granted title that happened to carry `source_type='milestone'` and was swept up. | `python manage.py populate_user_titles` (after migrate) | With the teardown deploy | ☐ |
| C | **Recompute job XP under the flat curve** — the XP-economy engine PR switches per-job leveling from the old escalating capped curve to flat cap-less (K=3,000) + T=6,000. Ledger amounts (`ContractXPGrant`) are immutable; only the level *derivation* changes, so every `ProfileJobXP.level` must be re-derived. Idempotent (rebuilds from the ledger). Run AFTER migration `0255_*` is applied. | `python manage.py recompute_job_xp --all` | Now (with the economy PR deploy) | ☐ |
| D | **Backfill community completion stats** — populates the new `Game.plats_earned_count` / `full_completion_count` / `avg_completion` columns immediately (they'd otherwise fill within the nightly `recalc_earn_rates` budget). Idempotent (recomputes from ground truth). Run AFTER migration `0256_game_avg_completion...` is applied; use a low-traffic window (full pass over ProfileGame). | `python manage.py recalc_earn_rates --max-minutes 600` | Now (with the community-stats PR deploy) | ☐ |
| E | **Universal-search trigram indexes** — migration `0257_universal_search_trgm_indexes` runs `CREATE EXTENSION pg_trgm` then builds three GIN trigram indexes with `AddIndexConcurrently` (`atomic = False`). Auto-applies on deploy; **no command, no backfill**. The only prerequisites: the DB role can create the `pg_trgm` extension (Render Postgres allows it), and the deploy tolerates a non-atomic migration. Verify it applied (`\di *_trgm` shows the three indexes). Dormant until the rebuilt navbar (`site_suggest`) ships. | Watch the deploy migrate step | Now (main PR already merged) | ☐ |
| F | **Drop the dead `GroupBadge.rarity_*` columns** — badge rarity is now derived LIVE (`badge_rarity.group_rarity`, pursuer-relative over the maintained `earned_count`), so `rarity_pct` / `rarity_class` / `rarity_rank` on `GroupBadge` are unused scaffolding. Remove the three columns in a `main` migration (they were part of the dormant schema PR). No data migration / backfill — nothing reads them. | Add a `RemoveField` migration on `main`, redeploy | With badge cutover | ☐ |
| G | **Add the `recompute_milestones` nightly cron** — the milestones page's daily freshness sweep. It's the ONLY refresh of the rarity denominator (`total_hunters`) + the earned_count drift-correction + the safety-net for the per-sync recompute. Whale-safe (streamed, bounded aggregates). Run WITHOUT `--reconcile-discord` (per-sync + on-link handle roles). Also do a one-time launch backfill (`python manage.py recompute_milestones`) after seeding so existing hunters get their earned tiers before the first cron. | Create a Render Cron Job: `python manage.py recompute_milestones`, daily 05:30 UTC (after `recalc_profile_counters`). See [cron-jobs](../../guides/cron-jobs.md). | With milestones launch | ☐ |
| H | **Share-card fonts need `collectstatic`** — the plat card rebuild adds Bricolage Grotesque (3 weights) to `static/fonts/`, and the Playwright renderer reads fonts from **STATIC_ROOT**, not `static/`. `_build_font_faces()` *silently skips* any TTF it can't find, so a deploy that misses collectstatic doesn't error — every share card just renders in a fallback face that looks almost right. No command beyond the normal build step; this row exists because the failure mode is invisible. Verify after deploy: the card's game title should be Bricolage, not Inter. | Normal `collectstatic` on deploy | With the plat card rebuild | ☐ |
| I | **Backfill the game-detail stats denorms + set the web statement timeout** — branch `fix/anon-profile-render-cost` (off `main`) moves the game-detail community stats row onto denormed columns and adds `Game.total_earns_count` / `monthly_players_count` (migration `0274_game_community_stats_denorm`). Both land at **0**, so the header's "Total Earns" and "Monthly Players" read 0 until `recalc_earn_rates` fills them — run it immediately after migrate rather than waiting for the 03:00 UTC cron. Idempotent (recomputes from ground truth); use a low-traffic window (full pass over ProfileGame + EarnedTrophy). **Separately**, set `DB_STATEMENT_TIMEOUT_MS=15000` on the **web service only** in the Render dashboard — the default stays 60000 so the worker and one-off commands are unaffected, and a value above the gunicorn worker timeout re-creates the failure mode this branch fixed. **Also in that PR, no command needed but user-visible:** migration `0275` retires the Rarest Trophies showcase and deletes existing `ProfileShowcase` rows of that type (it ranked the profile's entire earned set on a joined column — the most expensive thing an anonymous visitor could trigger). Premium users who had it will find the slot empty and free to re-fill, so it is worth a line in release notes / Discord. | `python manage.py recalc_earn_rates --max-minutes 600`, then set the env var on web | Now (with the anon-render-cost PR deploy) | ☐ |
| J | **Backfill `Game.monthly_earners_count`** — migration `0285_game_monthly_earners_count` adds the column (+ its index) for the Browse Games **Trending** sort, which now ORDERS BY it instead of aggregating ProfileGame per game on every request. It lands at **0** for every game, so until a full `recalc_earn_rates` pass completes, Trending falls through to its secondary key and reads as "most popular" rather than "trending". That degradation is deliberate (the secondary key is `-played_count`, so the order stays sensible rather than arbitrary), but it IS wrong until backfilled — and the nightly run is budget-capped with a resume cursor, so it can take several nights to reach the whole catalogue on its own. Run it immediately after migrate. Idempotent. | `python manage.py recalc_earn_rates --max-minutes 600` | With the trending-denorm deploy | ☐ |

| K | **Reconcile series titles** — `UserTitle` under-records the new badge system two ways, and both understate a title's holder count, which is now the rarity numerator on `/titles/`. (1) `grant_series_title` only runs on the `award` branch, and `diff` only awards a badge that isn't already held — so a badge earned *before* its series had a title never gets one, and re-running `evaluate_badges` cannot fix it. (2) `UserTitle` is unique on `(profile, title)` without `source_type`, so a series reusing a legacy Badge's Title got the legacy row back from `get_or_create` and recorded nothing — the hunter holds and can equip a title that reads **"Be the first"**. Symptom on beta: a title whose easiest edition ~78% of the community holds graded Mythic at 0.7%. `grant_series_title` now adopts an existing row going forward; this backfills the history. Idempotent, set-based. Run `--dry-run` first: it reports badge-holders vs countable per title. | `python manage.py sync_series_titles --dry-run` then `python manage.py sync_series_titles` | With the rarity work | ☐ |

> **`--prune` is deliberately not part of the above.** It deletes `badge_series` titles no held badge backs, which includes anything whose GroupBadge rows were dropped during re-authoring — that would silently strip earned titles. Run it only after eyeballing the orphan counts in a dry run.

---

## Post-deploy verification (milestone teardown)

`0282` deleted ladder titles by `source_id`, which misses rows with a NULL or already-dangling `source_id`
(they'd linger as bogus "Special" titles — this actually happened on dev). `0283` cleans those up by title
name. To confirm, only the three genuine one-off awards should remain:

```sql
SELECT DISTINCT t.name FROM trophies_usertitle ut
JOIN trophies_title t ON t.id = ut.title_id
WHERE ut.source_type = 'milestone';
```

Expected: nothing outside `Patron of the Arts`, `Fastest Plat in the West`, `Case Hardened`.

---

## Completed

_(Move rows here as they're done, with the deploy date, so the runbook keeps its history.)_

## IA: Community retired -> Leaderboards hub (2026-08)

Every item here is deploy-side; the code change is complete and tested.

- [ ] **Resubmit the sitemap.** Every profile URL changed (`/community/profiles/<u>/` -> `/profiles/<u>/`),
      and profiles are the largest indexed set on the site. The sitemap already emits the new locations
      (it reverses by name), but Search Console should be re-pinged so the 301s are crawled promptly
      rather than at the crawler's leisure.
- [ ] **Watch 404s for a week**, specifically anything under `/community/`. Every known path redirects
      (hub, profiles x3, leaderboards x2, rate-my-games), but hand-built links in the wild -- Discord
      posts, PlatBot messages, old emails -- are the ones that surface here.
- [ ] **Check PlatBot** for hardcoded web URLs. It consumes `/api/v1/*` (unaffected), but any message
      template that links a hunter to their profile or to Rate My Games needs the new path. Redirects
      cover it either way; this is about avoiding a needless hop in a bot people use constantly.
- [ ] **Grep PlatBot for `/api/v1/notifications`** before the withdrawn notification routes reach prod.
      Nine of them now 404, and unlike the redirected pages an unrouted API path has no soft landing.
      Nothing in this repo suggests PlatBot polls them (the Discord side is push, via
      `discord_notifications.py` webhooks, and the documented consumption is `/api/community-stats/*`),
      but the Game Lists retirement proved its equivalent claim by grepping the bot's tree rather than
      reasoning about it -- and "notification" will be far noisier there than "lists" was, since
      discord.py uses the word itself. Grep for the PATH, not the word.
- [ ] **Cloudflare cache purge** for `/community/*` so cached 200s aren't served over the new 301s.
- [ ] **Confirm the origin guard covers the new profile paths in prod.** The guard is skipped when
      `DEBUG`/`IS_BETA`, so its behaviour is only real on prod: a direct-origin GET of
      `/profiles/<user>/` without a `CF-Ray` header must bounce to the public host.

## IA: Profiles renamed to Hunters, `/profiles/` -> `/hunters/` (2026-08)

The section's SECOND move this month (it came out from under `/community/` a few weeks earlier), and it
covers the largest indexed set on the site: browse + every profile detail + every trophy case. The code
change is complete and tested; everything here is deploy-side.

Redirect shape, so nobody re-derives it: every legacy route targets a `pattern_name`, which resolves at
REQUEST time. That is what re-aimed the `/community/profiles/*` wave at `/hunters/*` automatically, so
**both** old spellings reach the canonical in a single hop rather than chaining. Pinned by
`test_the_oldest_paths_reach_the_canonical_in_ONE_hop`.

- [ ] **Resubmit the sitemap.** Every profile URL changed again. The sitemap emits the new locations (it
      reverses by name), but Search Console should be re-pinged so the 301s are crawled promptly. This is
      the second resubmit in a month for the same URLs — expect the recrawl to take a while.
- [ ] **Watch 404s for a week**, specifically `/profiles/*`. Every known path redirects; hand-built links
      in the wild (Discord, PlatBot, old emails) are what surface here.
- [ ] **Check PlatBot** for hardcoded profile URLs. It consumes `/api/v1/*` (unaffected — the mobile API's
      own `/api/v1/mobile/profiles/...` routes are a separate namespace and did NOT move), but any message
      template linking a hunter to their profile wants the new path. Redirects cover it either way.
- [ ] **Cloudflare cache purge** for `/profiles/*` so cached 200s aren't served over the new 301s.
- [ ] **Confirm the origin guard covers `/hunters/<user>/` in prod.** The guard is skipped when
      `DEBUG`/`IS_BETA`, so its behaviour is only real on prod: a direct-origin GET of `/hunters/<user>/`
      without a `CF-Ray` header must bounce to the public host. The regex now carries all three spellings;
      losing the new one silently un-guards the most scraped page type on the site.
- [ ] **`collectstatic`** — `robots.txt` is a static file and now carries all three `Disallow` lines.

## Advertising removed: the site is ad-free (2026-08)

Code-side is complete: loader, CMP, both desktop rails, the mobile banner, `ad_unit.html` and all 19
in-content slots, the `ads` context processor, the three `ADSENSE_*` settings, the `.has-mobile-ad` CSS
variants, and the `ad_free` tier are gone, and ~20 Google ad origins plus `'wasm-unsafe-eval'` came out of
the CSP. Everything below is deploy-side or platform-side.

The one shape worth not re-deriving: there is **no kill switch**. Ads were removed rather than gated,
deliberately, because an env flag is exactly how this creeps back. Turning ads on again is a code change
and a PR, not a dashboard toggle. `tests/engine/test_ads_removed.py` will fail the build if the layer
returns.

- [ ] **Apply the `premium_tier` choices migration.** Generated by dropping `ad_free` from
      `PREMIUM_TIER_CHOICES`. It is a **no-op at the database level** (CharField `choices` is not a DB
      constraint) and touches zero rows — but it still has to be applied so the migration state matches
      the model, or the next `makemigrations` re-emits it.
- [ ] **Remove `ADSENSE_PUB_ID`, `ADSENSE_ENABLED` and `ADSENSE_TEST_MODE` from the Render environment.**
      Harmless if left (nothing reads them), but leaving them implies a switch that no longer exists.
- [ ] **Archive the AdSense ad units** (all 19 slots + the 2 rails + the mobile banner).
- [ ] **Do NOT close the AdSense account.** Leave it dormant. Re-approval is slow and there is no upside
      to burning it; the decision being reversed is unlikely, not impossible.
- [ ] **Archive the `ad_free` Stripe product/price** (`prod_ThtXPwe3AD46Au` / `price_1SkR4XR5jhcbjB325xchFZm5`)
      **and the PayPal plan** (`P-51097223GD3632526NGLBPBA`). Zero subscribers, so there is nobody to
      migrate — but an active plan can still be subscribed to from a stale link, and the webhook would then
      arrive naming a tier the code no longer knows.
- [ ] **Retire the Funding Choices GDPR message** in the AdSense dashboard. Optional, cosmetic: the CMP
      script no longer loads, so the message cannot render either way.
- [ ] **Verify no cookie consent prompt appears** on a fresh incognito load of prod. This is the
      user-visible half of the change and the easiest to assume rather than check. There is no Google
      Analytics, so the whole Consent Mode v2 + Funding Choices layer was ads-only and went with them.
- [ ] **Cloudflare cache purge (full).** Cached HTML still carries the ad markup, the rails and the CMP
      loader — including the narrower content column, so the width change will not appear until the purge.
- [ ] **Watch the CSP report/console for a week.** This deploy REMOVED origins, so the failure mode is the
      opposite of the usual one: something legitimate that quietly rode on an ad origin now gets blocked.
      One entry was kept on purpose and is pinned by tests (`images.igdb.com` on `img-src`;
      `fonts.gstatic.com` was kept then but has since been removed by the SEO Lane 3 font
      self-hosting, pinned by `test_csp_fonts_are_self_only`); another, `'wasm-unsafe-eval'`, is gone and would break any future
      WebAssembly dependency loudly.
- [ ] **Re-check `/privacy/` in prod.** It now states as fact that the site serves no advertising and sets
      no advertising cookies. That has to be true the moment it is published, which it is only after the
      deploy and the cache purge above.

---

## Leaderboards rebuild (steps 1-3, 2026-08)

Spec: [leaderboards-rebuild.md](leaderboards-rebuild.md). Migrations `0297`, `0298`, `0299`.

- [ ] **Run `evaluate_badges --all` AFTER migrating, and before anyone sees a board.** This is the real
      backfill and it is easy to miss because nothing errors without it. The migrations add
      `trophies_*`, `advanced_at` and `country_code` with defaults, so every existing standing starts at
      **a NULL advance date and no country**. Until a full evaluation rewrites them: the per-series board
      loses its tiebreak entirely, so each rung of chasers falls back to profile id, and every country
      slice returns nothing. The pages render perfectly throughout — they are simply wrong.
      (The `trophies_*` columns this used to also warn about were removed in 2026-08; the Trophies board
      reads `Profile`'s own counters, which no badge backfill touches.)
- [ ] **Run `recompute_job_xp --all`** for the same reason on `ProfileCareerStanding`: without it the
      Career XP board is empty rather than absent.
- [ ] **Sanity-check one known hunter on each board** after the backfills — a name you can verify by
      hand. The boards are correct-looking under every failure mode above, so "it rendered" proves
      nothing.
- [x] ~~**Confirm `update_leaderboards` still succeeds.**~~ **SUPERSEDED by cutover 5b: the command is
      DELETED.** Its Render entry must be removed, not verified -- left in place it fails with
      `Unknown command` every 6 hours and alerts. See the 5b section below.
- [x] ~~**Do NOT flush the `lb:*` Redis keys.**~~ **SUPERSEDED by cutover 5b: every named consumer
      (`frame_service`, `profile_card_service`, the dashboard modules) is deleted.** All `lb:*` keys are
      now garbage and safe to drop or leave to expire. See the 5b section below.

---

## Badge cutover 5a: the new engine goes live on sync (2026-08)

Spec: [badge-backend-rebuild.md](badge-backend-rebuild.md) §6 step 5a. No migration.

- [ ] **Run `evaluate_badges --all` BEFORE the first sync lands.** From now on sync evaluates only the
      series a hunter TOUCHED, so without a full pass first, a hunter's untouched series stay at whatever
      state they were in — badges they already qualify for will not appear until they happen to play
      something in that series again. The backfill is what makes the incremental path correct.
- [ ] **Expect Discord traffic on the first synced hunters.** They will be awarded every badge the new
      engine agrees with at once, and that is announced. Not a bug, but it will look like one if it is a
      surprise. `evaluate_badges --all` itself stays silent (`notify` defaults off), so the backfill will
      not fire webhooks — only the syncs after it.
- [ ] **Watch for `sync_complete group-badge evaluation failed` in the logs.** The call is wrapped so it
      can never fail a sync, which also means a broken evaluation is invisible except here.
- [ ] **Titles change hands, one way.** Once the new system grants a series title it OWNS it, and if the
      group badge later lapses the title goes — even where the hunter's legacy `UserBadge` still stands.
      Documented in `badge_adapters.grant_series_title` and covered by `test_badge_apply`; expected, but
      it is a real behaviour change for legacy holders.
- [ ] **On-site badge notifications are NOT ported** and will be absent from the inbox when the
      notifications rebuild ships. `emit_badge_earned` is the seam it should consume. Tracked in
      badge-backend-rebuild.md §6.
- [ ] **DELETE the `update_leaderboards` Render Cron entry.** It rebuilt the legacy Redis badge boards
      every 6 hours; every board it fed now reads indexed Postgres columns. Harmless if left, but it is a
      full-population pass four times a day writing sorted sets nothing reads.
- [ ] **ADD a nightly `evaluate_badges --all` Render Cron entry, 04:00 UTC.** Sync only evaluates the
      series a hunter touched, so this is what closes the gaps: badges authored after someone's last sync,
      curator edits to stages or platform groups, and any evaluation swallowed by the non-fatal wrapper in
      `_job_sync_complete`. Without it there is no drift correction for the badge subsystem at all.
      Documented in [cron-jobs.md](../../guides/cron-jobs.md).

---

## Leaderboards: the edition filter + the Progress rename (2026-08)

Spec: [leaderboards-rebuild.md](leaderboards-rebuild.md). Migration `0300`.

- [ ] **`evaluate_badges --all` again, AFTER migrating.** `ProfileEditionStanding` is created empty and
      `SeriesBadgeStanding.group_xp` defaults to `{}`, so until a full evaluation runs, **every edition
      board is empty and reads as "nobody is on Legacy HD yet"**. Same failure shape as the backfill
      above: the page is perfect, the answer is wrong. If the earlier `--all` has not run yet, one run
      covers both.
- [ ] **Check the picker actually offers both editions.** `active_editions()` requires BOTH `is_active` and
      at least one live group badge, so an edition missing from the dropdown means one of those is off.
      Both directions matter and only one is benign:
      - `is_active=True, is_live=False` (seeded, unlaunched) -> absent. Deliberate: it could only ever
        offer an empty board.
      - `is_active=False, is_live=True` (deactivated, badges still live) -> also absent, and this is the
        one that used to be a silent defect. The picker gated on `is_live` alone while the WRITE seam gates
        on `is_active`, so the edition stayed selectable while its rows stopped being maintained and were
        deleted on each re-sync: the board drained over days and finished at "Nobody is on Legacy HD yet",
        a sentence about a config flag presented as a fact about hunters.
- [ ] **Spot-check one hunter's edition figures against their all-editions row.** They will NOT sum, by
      design — a cross-gen game counts in both editions. What to verify is that each edition is
      *no larger than* the all-editions total.
- [ ] **`?tab=progress` must still land on Badge Trophies.** The board key changed and the old one is
      aliased; the links in the wild are bookmarks and Discord posts, which nobody will report as broken —
      they will just quietly land on the default board.
- [ ] **`badges_held` (migration `0301`) is covered by the same `evaluate_badges --all`.** It defaults to
      0, so until that runs the Badge Points board shows every hunter as holding **0 badges** beside a
      correct points total — a wrong number rather than a missing one, which is the harder kind to notice.
      If you are running the backfill for the edition standings anyway, this rides along with it.

## Badge cutover 5b: deletions (2026-08)

Migrations `0303` / `0304` (profile showcases, dashboard config) and `notifications.0017_drop_device_token`.

- [ ] **`notifications.0017_drop_device_token` drops a table with rows in it.** `DeviceToken` collected
      Expo/FCM push tokens from the mobile logout view. Nothing ever read them and the
      `PushNotificationService` its docstring named was never written, so the data has no consumer — but it
      is real user data being dropped, not an empty scaffold. Take the pre-migrate snapshot you would take
      for any destructive migration.
- [ ] **Delete the `update_leaderboards` cron entry in the Render dashboard.** Already removed from the
      docs; the schedule itself lives in Render and outlives the deploy. It will keep firing against a
      command that no longer exists.
- [ ] **Add the nightly `evaluate_badges --all` cron entry in Render.** This is the reconcile that keeps
      every badge figure honest, and it is the ONLY thing that keeps the sync-path evaluation from drifting.
      Without it the boards are as stale as the last manual run — the exact condition the cutover set out
      to fix.
- [ ] **15 mobile endpoints are gone (`/api/v1/auth/*`, `/api/v1/mobile/*`, `/api/v1/device-tokens/*`).**
      Verified unconsumed: PlatBot calls only the bot endpoints. If anything external was quietly polling
      them it will start seeing 404s, which is the intended outcome but worth knowing before it is reported
      as an outage. See [../../guides/mobile-app.md](../../guides/mobile-app.md).
- [ ] **DRF token auth was deliberately KEPT.** `rest_framework.authtoken` and `TokenAuthentication` read
      as mobile scaffolding, but `IsDiscordBot` authorises by matching the token key against `BOT_API_KEY`.
      If a later cleanup pass removes them "with the mobile stack", every bot endpoint rejects the bot.
      Pinned by `tests/engine/test_mobile_api_removed.py`.

## Badge cutover 5b.4 + 5b.5: the engine comes out (2026-08)

Migration `trophies.0305_group_badge_announcement`.

- [x] **`GroupBadgeAnnouncement` backfill: handled by migration 0305, no action needed.**

      The table records who has already been told about which badge, so a revoke -> re-earn cannot
      re-announce. `0305` seeds one marker per currently held badge in the same migration that creates the
      table, so hunters whose badges predate the column are covered from the first flux onward.

      **Correction to an earlier draft of this checklist:** it claimed the backfill prevented a first-sync
      announcement storm at deploy. That was wrong. `diff()` only emits an `award` when the hunter does
      not already hold the badge, and announcements fire only on awards, so held badges are silent either
      way. The deploy is quiet regardless; the backfill is about flux, not launch day.
- [ ] **Sync now announces badges** (`evaluate_for_sync` flipped to `notify=True`). This is intended --
      the legacy engine was the only reason it was silent -- but it is the first deploy where the new
      engine can post to Discord. Watch the webhook after the first few syncs.
- [ ] **`/staff/badge-create/` is gone.** Anyone authoring badges now does it through Django admin on
      `BadgeSeries` -> `GroupBadge` (plus `PlatformGroup`). Tell whoever authors badges before they go
      looking for the form.
- [ ] **`art_reveal` still FKs the legacy `Badge`**, so `BadgeAdmin` is deliberately retained. Do not
      "finish the cleanup" by deleting it -- `admin.E039` fails the entire admin site, not just
      art_reveal. Repointing art_reveal is a tracked follow-up.
- [ ] **Every `lb:*` Redis key is now garbage.** Nothing reads or writes them. They can be left to expire
      or dropped by hand; no action is required for correctness.

## Badge cutover: post-audit additions (2026-08)

Migration `trophies.0306_user_group_badge_created_at` (adds the award timestamp + backfills it).

- [ ] **Purge orphaned DRF auth tokens.** `MobileLoginView` minted a `Token` on every login and
      `MobileSignupView` was publicly reachable with `AllowAny`; both are deleted, and so is the logout
      endpoint that invalidated them. Surviving non-bot tokens are permanent, non-expiring credentials
      that still authenticate against ~60 session/token endpoints and bypass CSRF by design. They cannot
      reach bot endpoints (`IsDiscordBot` matches the key against `BOT_API_KEY`). Check
      `SELECT count(*) FROM authtoken_token` and delete every row whose key is not `BOT_API_KEY`.
      Probably zero -- no mobile client was ever built -- but the count is unknown and nothing else covers
      it.
- [ ] **Clear the `CORS_ALLOWED_ORIGINS` env var.** `django-cors-headers` existed only for the Expo dev
      server. Not currently permissive (`CORS_ALLOW_ALL_ORIGINS` and `CORS_ALLOW_CREDENTIALS` are both
      unset, and `CORS_URLS_REGEX` confines it to `^/api/v1/.*$`), but any origin still listed keeps
      cross-origin read access to public API responses for no reason. The app and middleware are staying;
      a mobile rebuild will want them.
- [ ] **Expect "Unique badges: +N" to show the whole catalogue for 7 days.** The ribbon's weekly delta
      counts `BadgeSeries.created_at`, and every series row is created by the cutover backfill. Cosmetic
      and self-correcting, but it will look like a bug and someone will report it.
- [ ] **The nightly `evaluate_badges --all` Render entry does not exist yet.** It is documented in
      cron-jobs.md but has to be created by hand. Without it, badge figures are only as fresh as whatever
      each hunter's own sync last touched, and a newly authored series reaches nobody who does not happen
      to play one of its games.

### Known-stale, tracked separately

- **The artwork fundraiser and art_reveal still write the legacy `Badge` table.** `donation_service`
  credits a donor via `Badge.funded_by` on donation completion, but the medallion renders
  `GroupBadge.effective_funded_by` (`funded_by_override or series.funded_by`) -- so **donor artwork credit
  currently lands where nothing displays it**. This is pre-existing (it broke silently when the new badge
  display went live, not in this cutover) and is a live payment-flow correctness issue. Repointing both
  apps onto `BadgeSeries` is what actually retires the tier model and lets `BadgeAdmin` go. Pinned by
  `KNOWN_LEGACY_WRITERS` in `tests/engine/test_legacy_badge_engine_removed.py`.

## Fundraiser + art_reveal repoint (2026-08)

Migrations `fundraiser.0006_claim_series_fk` and `art_reveal.0004_item_series_fk`.

- [ ] **RUN `python manage.py convert_series_to_groups --all` FIRST.** This is a hard prerequisite, not
      advice. Both migrations map their rows to a `BadgeSeries` by slug and **refuse to run** if any row
      cannot be mapped -- they raise with the offending slugs named rather than nulling the FK, because
      every `DonationBadgeClaim` row is a payment somebody made. A failed migration is recoverable; a
      detached donation record is not.
- [ ] **Snapshot `fundraiser_donationbadgeclaim` and `art_reveal_artrevealitem` before migrating.** Real
      donation records and commissioned artwork, and the old FK is dropped at the end.
- [ ] **Verify donor credit renders after the first completed claim.** The whole point: complete a claim
      in staff admin, then open that badge's detail page and confirm the donor is named ON THE MEDALLION.
      Before this change the credit was written to `Badge.funded_by`, which nothing displays -- so
      "the claim completed successfully" was never evidence that it worked.
- [ ] **`BadgeAdmin` is gone.** The legacy `Badge` / `UserBadge` tables now have NO writer anywhere, which
      is what makes the soak-then-decommission step meaningful. If you need to inspect them, use dbshell.
- [ ] **The claim API accepts `series_id` and still tolerates the old `badge_id` key**, so a browser
      holding the fundraiser page open across the deploy does not get a confusing 400 on a payment
      action. The tolerance can be dropped a release later.

## Staff badge-creation tool (2026-08)

- [ ] **`/staff/badge-create/` is back**, rebuilt for `BadgeSeries` + editions. Tell whoever authors
      badges: it creates the series and one `GroupBadge` per checked edition, hidden by default. Stages
      are still added in Django admin afterwards.
- [ ] No migration, no data change. Route name is unchanged (`badge_creation`), so old bookmarks work.

### Pre-flight for the repoint migrations (run BEFORE taking the window)

Both migrations refuse to run against an unmappable row. Find out in advance rather than mid-deploy:

```sql
-- Claims whose series_slug has no BadgeSeries. Any row here blocks fundraiser.0006.
SELECT c.id, c.series_slug, c.series_name
FROM fundraiser_donationbadgeclaim c
LEFT JOIN trophies_badgeseries s ON s.series_slug = c.series_slug
WHERE s.id IS NULL;

-- Same for art_reveal.0004, which maps through the legacy badge's slug.
SELECT i.id, i.event_id, b.series_slug
FROM art_reveal_artrevealitem i
JOIN trophies_badge b ON b.id = i.badge_id
LEFT JOIN trophies_badgeseries s ON s.series_slug = b.series_slug
WHERE s.id IS NULL;

-- Two claims that would collide on one series (the OneToOne cannot hold both).
SELECT series_slug, count(*) FROM fundraiser_donationbadgeclaim
GROUP BY series_slug HAVING count(*) > 1;
```

- [ ] **`convert_series_to_groups --all` does NOT fix every case, despite what the migration error says.**
      It only sweeps `Badge.objects.filter(is_live=True)`, and it SKIPS any slug whose games match no
      active `PlatformGroup` -- printing a skip line and still exiting green. Two reachable cases it
      cannot resolve: a claim on a series later set `is_live=False` (the old claim path never checked
      liveness), and a series whose games map to no active platform group. For those, hand-create the
      `BadgeSeries` (the new `/staff/badge-create/` page does it in one form) and re-run migrate.
- [ ] **These migrations are IRREVERSIBLE on a non-empty table.** `RemoveField` destroys `badge_id`, so
      `migrate fundraiser 0005` fails with a NOT NULL violation and rolls back atomically. That is safe
      (no half-reverted schema, no data loss) but it means the rollback plan is restore-from-snapshot,
      not `migrate` backwards. Take the snapshot.
- [ ] **Brief window on a payment action.** `ADD COLUMN` takes ACCESS EXCLUSIVE on
      `fundraiser_donationbadgeclaim`. Milliseconds at this table size, but an in-flight `claim_badge`
      from an old worker can block and then fail after commit. Deploying outside a fundraiser push, or
      accepting a single possible 500, is the trade.
- [ ] **`fundraiser.0007` makes the claim FK PROTECT.** Deleting a `BadgeSeries` that has a claim now
      raises `ProtectedError` in the admin instead of silently deleting the payment record. If a series
      genuinely must go, delete or re-point its claim first, deliberately.

## Leaderboard performance (2026-08)

Migration `trophies.0307_partial_board_indexes`.

> `0308_board_entrants` was written, applied on dev, and then **deleted** when the three board
> directories were removed -- `BadgeSeries.entrants` / `Job.entrants` existed only so those pages could
> gate and sort across the whole catalogue before pagination, and nothing else read them. It never
> reached prod, so there is nothing to undo here and no drop migration to run. `Game.played_count` is a
> different, older column and is unaffected.

- [ ] **`0307` rebuilds two indexes on `Profile` CONCURRENTLY.** It makes them partial on the Trophies
      board's population (`is_linked AND total_trophies > 0`), which takes `trophy_rank` off a seq scan of
      a 48-column table on every authenticated page view. `atomic = False`, so it does NOT write-lock
      Profile -- but that also means it is NOT transactional: **if a `CONCURRENTLY` build fails partway,
      Postgres leaves an INVALID index behind that must be dropped by hand before re-running**:

      ```sql
      DROP INDEX CONCURRENTLY IF EXISTS profile_board_idx;
      DROP INDEX CONCURRENTLY IF EXISTS profile_board_cc_idx;
      ```

      Verify afterwards that both exist and are valid:

      ```sql
      SELECT indexrelid::regclass, indisvalid FROM pg_index
      WHERE indexrelid::regclass::text IN ('profile_board_idx','profile_board_cc_idx');
      ```

- [ ] **Replace the three nightly Render entries with ONE `nightly` entry at 04:00 UTC.** Delete
      `evaluate_badges --all` (04:00), `detect_dlc_and_refresh` (04:30) and `audit_badge_coverage`
      (05:00); `nightly` runs all three in dependency order. Leaving the old entries in place is not
      harmless: they would run the same work a second time, concurrently with the orchestrator.
      (`recompute_standing` now takes a per-profile `select_for_update` lock, so a concurrent pair
      serializes instead of both INSERTing -- but serializing two full passes over ~300,000 profiles is
      not a thing to leave scheduled.)
- [ ] **`ProfileEditionStanding` rows are written only for editions a hunter HOLDS something in**, so a
      newly seeded edition has no rows until each hunter's next evaluation. Its board is genuinely empty
      on day one rather than broken; the first `nightly` after seeding fills it. Nothing to run -- noted
      because "the new edition's board is empty" reads like a bug for the first 24 hours.

### Board population now gates on `is_linked` (behaviour change, no migration)

Every board in `badge_leaderboards` filters `profile__is_linked=True`, which only the Trophies board did
before. **Badge Points, Career XP, the per-series boards, the per-job boards and the earners lists will
all lose rows on deploy** -- specifically the scraped, unverified profiles that `evaluate_badges --all`
has been writing standings for all along, scout accounts included. That is the intended correction, but
it means published ranks move, so it is worth saying so rather than letting hunters discover it.

Nothing to run. The gate is at READ, so verifying an account puts a hunter on the boards immediately with
no re-evaluation.

> **Game boards WERE exempt and no longer are** (2026-08, see below). This paragraph used to say they
> were "deliberately exempt (they record who played a game, not who competes)" and had their own
> `members_only` toggle. Both halves are now false, and the population change on those boards is the
> largest in this whole section -- read the game-board entry further down before deploying.

### `is_linked` is mirrored onto the standing stores (2026-08)

Migrations `trophies.0308_profile_mirrors_is_linked` and `trophies.0309_partial_board_indexes_on_standings`.

- [ ] **`0308` backfills in the migration, and it is not optional.** The column defaults to False and
      `badge_leaderboards._linked()` reads it directly, so between the AddField and the backfill EVERY
      BOARD ON THE SITE IS EMPTY. Set-based `UPDATE ... FROM`, one statement per table. No action needed;
      recorded so it is not mistaken for something to run afterwards.
- [ ] **`0308` also repairs a pre-existing `country_code` bug on `ProfileJobXP`.** Rows were created with
      `country_code = ''` at both creation sites, and the propagation signal fires only on CHANGE -- so a
      hunter whose country never moved after their first XP grant has been invisible to the
      country-sliced job board since that column landed. The write sites now stamp it; the migration
      repairs the existing rows. **Expect the country-sliced job board to gain hunters on deploy.**
- [ ] **`0309` rebuilds six indexes CONCURRENTLY** (`atomic = False`). Same failure mode as `0307`: a
      build that fails partway leaves an INVALID index that must be dropped by hand before re-running.
      The migration's docstring carries the exact `DROP INDEX CONCURRENTLY` and validity-check SQL.
- [ ] **Run the migrations in order and do NOT skip 0308.** 0309's partial indexes have `is_linked=True`
      in their condition, so building them against an unbackfilled column produces six empty indexes.

### Board containers + per-entity indexes (2026-08)

Migration `trophies.0311_partial_indexes_for_scrolled_boards`.

- [ ] **`0311` rebuilds six indexes CONCURRENTLY and DROPS two** (`atomic = False`). Same failure mode as
      0307/0309/0310: a build that fails partway leaves an INVALID index to drop by hand. The migration's
      docstring carries the exact SQL and the validity check.
- [ ] **The two dropped indexes are not replaced.** `sbs_series_xp_idx` / `sbs_series_cc_xp_idx` served
      `series_xp_rows`, deleted in the 2026-08 audit for having no caller; they were pure write cost on a
      table every badge evaluation writes. Expect badge evaluation to get slightly cheaper.
- [ ] **Nothing to run afterwards.** Index-only change; no backfill, no data movement.
- [ ] **Badge detail and job detail now load their board on TAB ACTIVATION**, not with the page. If board
      traffic looks like it dropped after deploy, that is the change: previously every reader who scrolled
      badge detail to the bottom fetched the board whether or not they wanted it.


### The four boards converge, and two populations change (2026-08)

Migration `trophies.0312_series_board_ranks_on_points`. Spec:
[leaderboards-rebuild.md](leaderboards-rebuild.md) §8, steps 9-12.

**Two of these move published ranks and one of them empties most of a board. Read before deploying.**

- [ ] **GAME BOARDS ARE NOW `is_linked`-GATED.** This is the biggest user-visible change in the section.
      They ranked EVERY scraped PSN profile; they now rank verified hunters only, the same rule the other
      five boards apply. Roughly 300,000 profiles become roughly 50,000 -- so every game board on the
      site gets dramatically shorter, and a hunter who was #40 among owners may now be #6 among members.
      **Nothing to run** (the gate is at READ), but it is worth announcing rather than letting people
      discover it. The paragraph above about game boards being exempt is superseded by this one.
- [ ] **The per-series badge board ranks on POINTS, not progress.** It ordered on `progress_bp` -- the
      furthest-along EDITION's fraction -- so its default "All editions" view ranked people by their best
      single edition and ignored the rest. `xp` is already summed across editions before it reaches the
      standing, so **no data changes and nothing needs recomputing**; the ORDER changes, which means
      published per-series ranks move.
- [ ] **`0312` rebuilds two indexes CONCURRENTLY** (`atomic = False`), repointing them from
      `-progress_bp` to `-xp`. Same failure mode as 0307/0309/0310/0311: a build that fails partway
      leaves an INVALID index behind that must be dropped by hand before re-running. The migration's
      docstring carries the exact `DROP INDEX CONCURRENTLY` statements and the verification query.
- [ ] **No data migration is needed for any of the above.** `xp` has always been populated; only the
      ordering reads a different column.

**Dropped query params.** `?invert=` and `?registered=` on the game leaderboard endpoint are gone and are
now silently ignored rather than redirected -- they only ever existed inside controls that no longer
ship, so nothing in the wild carries them. Recorded so an old bookmark producing an unfiltered board is
not mistaken for a bug.

**New, no action:** a per-EDITION badge board (`?edition=` on badge detail's Ranks tab), a country filter
on the game and job boards, a hunter search on all four, and a sticky minibar on `/leaderboards/`. All
read-side; nothing to run.

> ~~**KNOWN LIMITATION, shipped deliberately.** The per-edition board tiebreaks on `advanced_at`, which
> is SERIES-wide.~~ **FIXED before deploy** -- see the next section. Left here because the reasoning that
> made it acceptable ("only the ORDER WITHIN a points tie on a filtered board") is exactly the reasoning
> that would have shipped it, and the fix turned out to be one table and no new engine work.

### The per-edition badge board gets a store (2026-08)

Migration `trophies.0313_series_edition_standing`. Spec:
[leaderboards-rebuild.md](leaderboards-rebuild.md) §8, step 12.

**Deploys with the section above. Nothing new to run** -- the `evaluate_badges --all` this checklist
already requires in four places covers it.

- [ ] **`0313` creates `SeriesEditionStanding`** -- one row per (profile, series, STARTED edition),
      carrying that edition's points and its own `advanced_at`. Plain `CreateModel`, indexes inline, no
      `CONCURRENTLY` needed: the table is new and empty, so unlike 0307 / 0309 / 0310 / 0311 there is
      nothing live to lock. Fast, and it takes no data with it.
- [ ] **The `evaluate_badges --all` above fills it.** Same shape as the `ProfileEditionStanding` entry
      further up: created empty, so until a full evaluation runs **every per-edition badge board reads as
      "nobody is chasing this edition"** -- the page is perfect and the answer is wrong. One `--all` run
      covers this, `group_progress`, `group_xp`, `badges_held` and `ProfileEditionStanding` together.
      There is deliberately NO bespoke backfill command: one was written and deleted, because the only
      `advanced_at` it could derive is `SeriesBadgeStanding`'s series-wide value -- which is the exact
      tiebreak this table exists to remove, on a board that has never shipped and so has no published
      ranks to preserve. Seeding it would have meant a deploy that looks finished and silently is not.
- [ ] **Nightly cost goes up slightly.** `evaluate_badges --all` now writes one extra row per started
      edition per engaged series. No extra evaluation (the loop already held each edition's result), two
      queries per profile however many series they are engaged with, and storing only STARTED editions
      keeps it roughly half of what `group_progress` carries. Worth a glance at the nightly's runtime the
      first morning after, not worth pre-emptive action.

### The three board directories are gone (no deploy step)

`/leaderboards/{games,badges,jobs}/` were removed without redirects -- they never left a dev machine, and
nothing linked to them but the hub rail, which existed because they did. The Leaderboards hub is back to
`items=()`, so it is one destination reached from the navbar, like Support. `/leaderboards/badges/` keeps
its pre-directory 301 to the landing, because the per-series redirect below it is still live and that
path is its parent.

### Still on wall-clock ordering (not part of this change)

`populate_title_ids` (02:00), `recalc_earn_rates` (03:00), `recalc_profile_counters` (03:30),
`recompute_tag_covers` (03:45), `update_shovelware` (04:00) and `recompute_milestones` (05:30) remain
separate entries whose ordering is implied by their times. `recompute_milestones` already documents a
dependency on `recalc_profile_counters` rather than enforcing it -- the same hazard `nightly` was built to
remove for the badge chain. **Folding them in is a tracked follow-up** (see the FOLLOW-UP block in
[cron-jobs.md](../../guides/cron-jobs.md)); it was left out here to keep the change to one subsystem.

---

## Storefront moved to `/support/` (2026-08-19)

No migration, no data change. Two things to be aware of on the deploy that ships this, both because
it is a **live payment page** rather than a display surface.

### Watch the first real checkout, both providers

`/users/subscribe/` no longer serves the form -- it 302s to `/support/`, which now owns both the form
and its POST handler. That move was mandatory: the form carries no `action` and self-POSTs, so leaving
the handler behind would have downgraded every checkout to a GET with the body dropped. It is covered
by tests (`tests/engine/test_support_storefront.py`) but those mock the provider, so **the first live
Stripe and PayPal checkout after deploy are worth watching**. There had been no automated coverage of
this path at all before this change, so there is no historical signal to compare against.

`/users/subscribe/success/` deliberately did **not** move: it is baked into the `success_url` /
`return_url` of every checkout ever created, including subscriptions bought months ago that may still
redirect through it.

The redirect is **302 on purpose**. Do not "tidy" it into a 301 -- a permanent redirect on a payment
URL is cached by the browser and cannot be withdrawn if the assumption behind it turns out wrong.

### Nothing else to run

The `support:stats` cache key (5 min TTL; supporter counts, monthly total, the wall) populates on first request. (An earlier draft of this note named `support:proof`, a key that never shipped — wrong cache keys in a runbook get flushed at 3am.)
Perk copy is a Python constant, so it ships with the code.

### The ladder is ARMED in test mode; live ids are the last remaining step (2026-08-21)

`SUPPORT_TIERS_ARE_PLACEHOLDERS` is now **False**: the 24 test/sandbox SKUs exist (bootstrapped
2026-08-21), their ids live in `STRIPE_LADDER_PRICES['test']` / `PAYPAL_LADDER_PLANS['sandbox']`,
and the full purchase loop passed e2e on beta (both providers, both cycles, cancellation webhook).

**The only thing still empty is the LIVE half of both maps**, and that is deliberate (the fan-out
hazard below) and test-pinned by `test_live_ids_stay_empty_until_cutover` -- a test whose FAILING
on cutover day is the intended signal to update it. Until the live paste lands, prod renders the
"memberships briefly unavailable" state: the runtime guard filters the ladder to the levels that
actually have live prices, which is currently none. No dead buttons, ever
(`test_placeholders_can_never_reach_live_stripe`).

Existing subscribers need NO migration: legacy tiers stay renewable through the untouched webhook
paths and are simply no longer purchasable. On the Credits wall they wear the price-nearest ladder
level via `LEGACY_TIER_LEVEL_MAP` (premium tiers -> Backer; legacy Supporter, $20/mo -> Sponsor)
-- presentation only, nothing reads that map for billing or roles, no deploy action attached.

### The revenue-off window (re-scoped 2026-08-21) — now just the cutover gap

The window has shrunk to exactly this: **from the cutover deploy until the live-ids deploy (steps
1-5 below), no NEW membership is purchasable from the UI.** Existing subscriptions renew fine
throughout (webhooks untouched); `/support/` shows the "memberships briefly unavailable" box;
`/users/subscribe/` 302s there. Deliberate and test-pinned. Shortening it means running the
cutover sequence promptly in one sitting -- it is five steps and none of them are long.

### Cutover-day sequence for the ladder (do these IN ORDER, same day)

1. **Deploy `rebuild` to prod** (migrations 0314 + users/0021 ride along; both are safe-any-order
   DB no-ops/additive). Prod is now ladder-aware: it can RECOGNISE ladder product ids, which is
   what makes step 2 safe.
2. **Prod shell**: `python manage.py bootstrap_support_skus --live-ok` (both providers; prod env
   already has live keys for both, nothing new to configure). Creates the 24 live objects and
   syncs the 12 Stripe prices into prod's djstripe -- the sync is what keeps checkout from 500ing
   in step 5. Idempotent; re-run on any partial failure.
3. **Paste all THREE printed blocks** -- `STRIPE_LADDER_PRICES['live']`,
   `PAYPAL_LADDER_PLANS['live']`, and the `STRIPE_PRODUCTS['live']` merge block (the products
   paste is what lets webhook tier recovery resolve a ladder purchase; missing it deactivates the
   buyer) -- into `users/constants.py`, and **update `test_live_ids_stay_empty_until_cutover`** --
   it now fails by design; flip it to assert the live ids are PRESENT. Commit together.
4. **Deploy again.** Buy buttons go live.
5. **Watch the first real checkout on each provider** (the storefront POST handler had zero live
   traffic history before this page). Then verify: supporter mark + Discord role on the buyer,
   an open `SubscriptionPeriod`, the support band ticking, webhook deliveries showing 200s in
   both provider dashboards.

**Post-cutover verifications (same day):**
- The weekly audit cron pair survived the service changes (see the cron note below).
- A legacy subscriber's page still shows premium, and the Credits wall shows them wearing their
  mapped level.
- No new webhook events or endpoints are needed on either provider -- everything the ladder emits
  is already subscribed for the legacy tiers.

At go-live, the checkout markup is already real -- a `<form method="post">` with CSRF wrapping the
tier radios and both provider buttons, pinned by `test_the_checkout_is_a_real_form` (an audit found
the buttons had shipped formless, which would have made them inert exactly at go-live).

### Migration 0314: supporter wall consent (2026-08-20)

`Profile.show_on_supporter_wall`, a boolean defaulting **True**.

The default is the decision, not an accident. It **auto-opts-in everyone already supporting** when
the wall shipped, because they never saw a checkout step to be asked at. New supporters are asked
explicitly during checkout (lane 2), and anyone can switch it off from `/users/subscription-management/`.

It is inert for non-supporters: the wall query filters on an active premium tier first, so a `True`
on a profile with no tier means nothing. Pinned by `test_a_non_supporter_is_never_on_the_wall`, which
exists because if that tier filter were ever dropped, the default would put the entire user base on a
public page.

No backfill needed; the column default does the work. Safe to run on a live DB (one nullable-free
boolean with a default, no table rewrite on Postgres 11+).

### The supporter-ladder lane (2026-08-20) — migration users/0021

One migration: the ladder slugs join `premium_tier`'s choices (a DB no-op on Postgres). Safe on a
live DB in any order relative to the code deploy.

(Gifting and comps were built in this lane and then deliberately cut — a giftable supporter mark
dilutes what every paying supporter's mark means, and contest rewards belong in the earned register:
badges, not bought flair. The `PremiumGrant` machinery lives in git history at `b71fec59`/`f65438a9`
if a real case ever materialises.)

**⚠ SKU bootstrap remains gated by the prod-safety rule recorded in the payments plan: test-mode
anytime, LIVE only at rebuild cutover** — prod's `main` build deactivates subscribers whose product
id it does not recognise, and webhooks fan out to every registered endpoint. `bootstrap_support_skus`
refuses live mode without `--live-ok` for exactly this reason.

**Cron: the weekly subscription-audit pair must exist after cutover.** Created on prod 2026-08-21
(PR #58): `djstripe_sync_models Subscription` then `audit_subscription_status --fix`, in that order
(the audit reads only djstripe's local mirror; a stale mirror is how a paying subscriber reads as
`[NO SUB]`). Render crons live in the dashboard, not config, so at rebuild cutover VERIFY the entry
survived the service changes and recreate it if it did not. Row + rationale:
[cron-jobs.md](../../guides/cron-jobs.md).

### Marks & Roles (2026-08-22) — migrations users/0022, users/0023, trophies/0315

Additive fields (`CustomUser.role`, `Profile.display_mark`) plus a data backfill: every
`is_staff` user becomes role `admin`, and every profile's worn mark is computed once. Safe on a
live DB. **After deploy: demote the moderators by hand in the Django admin** (they were all
backfilled as admin, the pre-split meaning); each demotion syncs `is_staff` off and flips their
mark to the green shield automatically. The supporter marks appear site-wide in the same deploy
-- the intended splash.

### Email parking + settings rebuild (2026-08-22)

Non-vital emails are OFF pending the email-system rebuild: only auth (verification, password
reset, welcome), billing/subscription lifecycle, fundraiser (receipt, claim, artwork), and the
membership welcome still send. At deploy:

- **Suspend the Render cron for `send_weekly_digest`** (Monday 08:00 UTC). The command now
  fails safe behind `WEEKLY_DIGEST_SEND_ENABLED` (default False), same pattern as the recap
  sender, but a paused cron beats a weekly no-op log. Do NOT delete the job definition; the
  email rebuild will want the slot back.
- `send_monthly_recap_emails` was already paused (2026-08); no change.
- `/users/email-preferences/` now 302s to Settings (tokened links in old email footers land
  there); no action needed, listed so nobody hunts for the missing page.

### Account deletion webhook follow-up (2026-08-22) -- SUPERSEDED, see the self-heal entry below

Deletion's cancel-first guard blocks every site-visible payment state (membership_status,
has_active_subscription, a PayPal id with no scheduled end, any non-terminal Stripe sub in the
djstripe mirror). The residual race it cannot see: a checkout approved seconds before deletion
whose activation webhook has not landed (both processors write our identifiers only from the
webhook). The complete fix is a payments-lane follow-up for the email/notifications rebuild
era: when an activation webhook resolves to no user (CustomUser.DoesNotExist), CANCEL the
subscription at the processor instead of only logging. Until then the orphan keeps billing
with no site-side cancel path; the webhook handlers already no-op safely (test-pinned).

### Subscription-audit report email + webhook self-heal (2026-08-22) -- CLOSES the deletion race

The "account deletion webhook follow-up" above is now BUILT, plus the weekly report email:

- **Set `AUDIT_REPORT_EMAIL` on the CRON SERVICE's environment** (his address) -- on Render a
  cron job is its own service, so the web service's env does not reach it; use the cron's env
  or a shared env group. **Set `PAYMENT_SELF_HEAL_ENABLED=True` there AND on the web service**
  (the webhooks run on web) -- it is default-off so staging clones can never cancel real subs. The weekly audit cron
  then mails its full run report after every run, topline counts in the subject -- no more
  remembering to check the logs. Empty/unset = no email (dev default).
- The audit also sweeps for ORPHANED subscriptions (live Stripe sub, no user row, no djstripe
  subscriber) and lists them loudly; report-only, cancel by hand in the dashboard.
- The activation webhooks now SELF-HEAL the deletion race: a Stripe subscription event for a
  customer with no user AND no djstripe subscriber (the subscriber check protects the
  duplicate-customer [MISMATCH] case from wrongful cancellation) cancels the sub at Stripe;
  a PayPal ACTIVATED whose valid custom_id resolves to no user cancels at PayPal. Both log
  loudly as SELF-HEAL; the audit sweep backstops any failure.

### Migration trophies/0316: badge set numbers dropped (2026-08-23)

Two RemoveFields plus a help_text-only AlterField (no DDL), safe on a live DB. Note for the rollback-audit ledger: this also drops the
LEGACY Badge.set_number column, which held real assigned numbers from the pre-cutover system --
the numbering concept is abandoned (his call), so a rollback would come back without them.

## Anon landing rebuild (2026-08-23)
- [ ] Set `LANDING_SHOWCASE_PSN` on the CRON service env (the only reader: `refresh_homepage_hourly` renders the card + ratings into the shared cache; the web server only reads the cache and needs nothing). The hunter whose real Profile Card fronts the landing; unset = the fixture card renders with its "sample" caption.
- [ ] Confirm `refresh_homepage_hourly` cron logs "Landing showcase card cached" after the first run.

## SEO Lane 0 (2026-08-23)
- [ ] Jeffrey: create a Google Search Console DOMAIN property for platpursuit.com (DNS TXT verification), submit /sitemap.xml, add a monthly look to the routine. Every SEO lane's success is measured there.
- [ ] SEO Lane 0 ships WITH the cutover (his call 2026-08-23: no early cherry-pick). Known cost accepted: prod's robots.txt keeps blocking canonical game/badge/jobs pages until then, so expect index recovery to START at cutover, not before. After cutover, verify in GSC that /games/<np>/ pages report Allowed under the robots tester.
- [ ] `collectstatic` after the robots.txt change (WhiteNoise serves staticfiles/).
- [ ] After cutover: resubmit /sitemap.xml in GSC (the badge section changes model; roadmaps section is withdrawn).


## SEO Lane 3 (2026-08-23)
- [ ] Eyeball the four quantized badge backdrops (static/images/badges/backdrops/) on beta: open a
  medallion's inspect modal and the Collection. They dropped 1.47 MB -> 172 KB via 256-color dithered
  quantization; they render at <=400px so banding should be invisible, but the call is visual.
  Revert candidates via git if any gradient looks stepped.
- [ ] Post-cutover: re-baseline Lighthouse against PROD (the table in docs/design/seo-strategy.md is
  dev-lab only) and note the numbers next to it. Fonts now self-host, so also confirm the woff2s serve
  with long-cache headers from WhiteNoise (immutable far-future, same as other static).
