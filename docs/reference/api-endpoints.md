# API Endpoints

All API endpoints live under `/api/v1/` and are defined in `api/urls.py`. The web app uses session/CSRF authentication; the mobile app uses token authentication (`Authorization: Token <token>`). Staff-only endpoints require the `StaffRequiredAPIMixin`. There are approximately 130 endpoints across 19 categories.

## Authentication Patterns

| Pattern | Usage |
|---------|-------|
| Session + CSRF | Web app (default for all non-mobile endpoints) |
| Token auth | Mobile app endpoints (`/api/v1/auth/`, `/api/v1/mobile/`) |
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
| PUT | `/api/v1/roadmap/<roadmap_id>/tab/<tab_id>/` | Staff | Update tab metadata (name, intro markdown) |
| GET/POST | `/api/v1/roadmap/<roadmap_id>/tab/<tab_id>/steps/` | Staff | List or create steps within a tab |
| PUT | `/api/v1/roadmap/<roadmap_id>/tab/<tab_id>/steps/reorder/` | Staff | Reorder steps within a tab |
| GET/PUT/DELETE | `/api/v1/roadmap/<roadmap_id>/tab/<tab_id>/steps/<step_id>/` | Staff | Step detail / edit / delete |
| POST | `/api/v1/roadmap/<roadmap_id>/tab/<tab_id>/steps/<step_id>/trophies/` | Staff | Attach trophies to a step |
| PUT | `/api/v1/roadmap/<roadmap_id>/tab/<tab_id>/trophy-guides/<trophy_id>/` | Staff | Edit per-trophy guide text |
| POST | `/api/v1/roadmap/<roadmap_id>/publish/` | Staff | Publish or unpublish a roadmap |
| POST | `/api/v1/roadmap/<roadmap_id>/upload-image/` | Writer+ on that roadmap | Upload an inline image for the editor (scoped per-roadmap so trial-writer escalation works) |
| GET | `/api/v1/youtube/attribution-lookup/?url=<youtube_url>` | Login | Resolve a YouTube URL to its channel name + URL via oEmbed (used by the editor's live attribution preview). Rate-limited 30/min/user. Returns `{"channel_name": str, "channel_url": str}` (empty strings on miss). |

### Notifications — WITHDRAWN (2026-08)

The notification system is **hidden pending its rebuild** ([notification-system.md](../architecture/notification-system.md)). Nine routes are unrouted and now 404: `GET /api/v1/notifications/`, `mark-all-read/`, `bulk-delete/`, `<pk>/read/`, `DELETE <pk>/`, `<pk>/rating/`, and the three admin compose endpoints (`send/`, `preview/`, `target-count/`). The views are parked in `api/notification_views.py`; the models, the data and every producer are untouched.

`POST /api/v1/notifications/<pk>/rating/` is worth calling out separately: it was the **second server-side writer of `UserConceptRating`**, and withdrawing it leaves `GroupRatingView` as the only one.

| Method | Path | Auth | Purpose |
|--------|------|------|---------|
| GET | `/api/v1/admin/notifications/user-search/` | Staff | Search users for targeting. **Still routed** — the Badge Creation page uses it as its user picker, so it outlived the block it belonged to. Wants rehoming somewhere neutral. |

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

### Game Lists

| Method | Path | Auth | Purpose |
|--------|------|------|---------|
| POST | `/api/v1/lists/` | Login | Create game list |
| GET | `/api/v1/lists/my/` | Login | User's game lists |
| POST | `/api/v1/lists/quick-add/` | Login | Quick add to default list |
| GET/PUT | `/api/v1/lists/<id>/` | Login | List detail/update |
| DELETE | `/api/v1/lists/<id>/delete/` | Login | Delete list |
| POST | `/api/v1/lists/<id>/items/` | Login | Add item |
| DELETE | `/api/v1/lists/<id>/items/<id>/` | Login | Remove item |
| PUT | `/api/v1/lists/<id>/items/<id>/update/` | Login | Update item |
| PUT | `/api/v1/lists/<id>/items/reorder/` | Login | Reorder items |
| POST | `/api/v1/lists/<id>/like/` | Login | Toggle like |
| POST | `/api/v1/lists/<id>/copy/` | Login | Copy list |
| GET | `/api/v1/games/search/` | Login | Game search (typeahead) |
| GET | `/api/v1/games/<np_comm_id>/players/` | Public | Game players list (JSON). Sets `authentication_classes = []` / `permission_classes = []`, so it is anonymous despite what this table said previously. Its in-app consumer (the game-detail players modal) was retired in favour of the Ranks tab; kept in case an external client uses it. See [Game Leaderboards](../features/game-leaderboards.md) |

### Game Families (Staff Only)

| Method | Path | Auth | Purpose |
|--------|------|------|---------|
| POST | `/api/v1/game-families/` | Staff | Create family |
| PUT | `/api/v1/game-families/<id>/` | Staff | Update family |
| DELETE | `/api/v1/game-families/<id>/delete/` | Staff | Delete family |
| POST | `/api/v1/game-families/<id>/add-concept/` | Staff | Add concept to family |
| POST | `/api/v1/game-families/<id>/remove-concept/` | Staff | Remove concept |
| POST | `/api/v1/game-families/proposals/<id>/approve/` | Staff | Approve proposal |
| POST | `/api/v1/game-families/proposals/<id>/reject/` | Staff | Reject proposal |
| GET | `/api/v1/game-families/search-concepts/` | Staff | Search concepts |

### Fundraiser

| Method | Path | Auth | Purpose |
|--------|------|------|---------|
| POST | `/api/v1/fundraiser/<slug>/donate/` | Login | Create donation checkout |
| POST | `/api/v1/fundraiser/claim/` | Login | Claim badge series |
| POST | `/api/v1/admin/fundraiser/claim-status/` | Staff | Update claim status |

### Dashboard

The dashboard is the synced-state home page for all users (see [Home Page Router](../features/home-page.md) and [Dashboard](../features/dashboard.md)). Auth requirements per endpoint reflect what is exposed in the redesigned site, not the temporary staff gate from the rebuild phase.

| Method | Path | Auth | Purpose |
|--------|------|------|---------|
| GET | `/api/v1/dashboard/module/<slug>/` | Login | Lazy module HTML |
| POST | `/api/v1/dashboard/config/` | Login | Update hidden modules / settings / order / tab config |
| POST | `/api/v1/dashboard/reorder/` | Login (Premium) | Save drag-drop order |
| POST | `/api/v1/user/quick-settings/` | Login | Quick Settings auto-save (toggles, timezone, region) |

### Stats Page

The `/stats/` premium stats page (12 sections, 120+ stats). See [Stats Page Inventory](stats-page-inventory.md).

> **Hidden for 1.0 (2026-08):** the page itself is parked -- `/stats/` redirects to Home -- so nothing
> calls this endpoint. It is kept for the rebuild. See [stats-page.md](../design/stats-page.md).

| Method | Path | Auth | Purpose |
|--------|------|------|---------|
| POST | `/api/v1/stats/premium/` | Login (premium) | Lazy section data for the My Stats page |

### Game Flags

User-submitted data quality flags (delisted, shovelware, VR-only, buggy trophies, etc.). See [Community Flags](../features/community-flags.md).

| Method | Path | Auth | Purpose |
|--------|------|------|---------|
| POST | `/api/v1/games/<game_id>/flag/` | Login | Submit a community flag report against a game |

### Contract Modals (HTML fragments)

Lazy-fetched contract-card HTML injected into the shared `.pp-detail-modal` on the Career, Badge Detail, and Game Detail pages (fetched with `X-Requested-With: XMLHttpRequest`; a non-200 lets the trigger fall back to its `href`).

| Method | Path | Auth | Purpose |
|--------|------|------|---------|
| GET | `/career/contracts/<slug>/modal/` | Login + linked | Full contract card with the **viewer's** per-game progress (`ContractModalView`) |
| GET | `/career/contracts/<slug>/preview/` | **Public** | **Anonymised** contract card (member games show trophy composition, not progress) + a sign-up / link-PSN CTA, for logged-out / unlinked viewers (`ContractModalPreviewView`). Renders only public contract/game data (`build_contract_modal(None, slug)` — no per-user work). |

### Profile Cards & Badge Showcase

Shareable profile card images, forum signatures, and the public badge showcase. See [Profile Cards](../features/profile-cards.md).

| Method | Path | Auth | Purpose |
|--------|------|------|---------|
| GET | `/api/v1/profile-card/html/` | No (token) | Profile card HTML for Playwright rendering |
| GET | `/api/v1/profile-card/png/` | No (token) | Profile card PNG (cached, served by token) |
| GET/POST | `/api/v1/profile-card/settings/` | Login | Profile card settings (theme, public sig toggle, displayed badge) |
| POST | `/api/v1/profile-card/regenerate-token/` | Login | Rotate the public sig token (invalidates the old image URL) |
| POST | `/api/v1/badges/displayed/` | Login | Set the badge displayed on the profile card |
| POST | `/api/v1/badges/showcase/` | Login | Toggle a badge in the 5-slot profile showcase |
| POST | `/api/v1/badges/showcase/reorder/` | Login (Premium) | Drag-reorder the showcase slots |

### Subscription Admin (Staff Only)

| Method | Path | Auth | Purpose |
|--------|------|------|---------|
| POST | `/api/v1/admin/subscriptions/action/` | Staff | Admin action (resend, deactivate) |
| GET | `/api/v1/admin/subscriptions/user/<id>/` | Staff | User subscription detail |

### Community Reviews

> **None of the `reviews/` routes below are wired.** The review system was archived 2026-05 and
> `api/urls.py` routes none of them; the table is kept as the shape a future rebuild would restore. The
> RATINGS half of the system is live and listed after it.

Review responses include a `body_html` field containing server-rendered markdown (via `ChecklistService.process_markdown()`), ready for direct insertion into the DOM without a client-side markdown library.

| Method | Path | Auth | Purpose |
|--------|------|------|---------|
| GET | `/api/v1/reviews/recent/` | No | Recent reviews feed (paginated, for landing page) |
| GET | `/api/v1/reviews/search/` | No | Search reviews by concept (typeahead) |
| GET | `/api/v1/reviews/<concept_id>/group/<group_id>/` | No | List reviews (sort: helpful/newest/oldest) |
| POST | `/api/v1/reviews/<concept_id>/group/<group_id>/create/` | Login | Create review (body + recommended) |
| GET | `/api/v1/reviews/<review_id>/` | No | Single review detail |
| PUT | `/api/v1/reviews/<review_id>/` | Login | Edit own review |
| DELETE | `/api/v1/reviews/<review_id>/` | Login | Delete own review |
| POST | `/api/v1/reviews/<review_id>/vote/` | Login | Toggle helpful/funny vote |
| POST | `/api/v1/reviews/<review_id>/report/` | Login | Report review |
| GET | `/api/v1/reviews/<review_id>/replies/` | No | List replies |
| POST | `/api/v1/reviews/<review_id>/replies/` | Login | Create reply |
| PUT | `/api/v1/reviews/replies/<reply_id>/` | Login | Edit own reply |
| DELETE | `/api/v1/reviews/replies/<reply_id>/` | Login | Delete own reply |

**Ratings** — the live half of this system. The Rate My Games wizard and the quick-rate modal call these:

| Method | Path | Auth | Purpose |
|--------|------|------|---------|
| GET | `/api/v1/ratings/wizard/queue/` | Login | Rate My Games queue. `queue_type=base\|dlc`, `limit`, `offset`, `include_shovelware=1`. Serves only UNRATED items, so nothing it returns carries an existing rating. Each item carries `concept_icon_url` (the cover) for the wizard's game header; it also sent `landscape_url` until the header's background wash was dropped, and that field went with it rather than being served to nobody. BOTH branches defer `igdb_match.raw_response` — the DLC branch lists every ratable concept's groups before paginating, so the ~30 KB blob per row is the difference between a page and tens of MB. |
| POST | `/api/v1/ratings/<concept_id>/group/<group_id>/rate/` | Login | Submit/update a rating. Body: `recommendation, difficulty, grindiness, hours_to_platinum, fun_ranking, overall_rating, blurb`. **`recommendation` is REQUIRED** (`worth_it` / `good_game_bad_plat` / `skip`) — except on an update of an existing rating, where an omitted one falls back to the stored value. An OMITTED `blurb` preserves the stored one; an empty string clears it. Responds with `community_averages` (incl. `recommendation_split`), `blurb`, `recommendation` and `recommendation_label`. Rate-limited 30/min. |
| GET | `/api/v1/ratings/<concept_id>/group/<group_id>/trophies/` | No | Condensed trophy list with earned status (the wizard's reference panel) |
| POST | `/api/v1/ratings/blurb/<rating_id>/report/` | Login | Report a public quick take |

### Ratings & Quick Takes

> The structured rating system now mounts under the `/api/v1/ratings/` prefix; the `/reviews/` rows above are legacy (the text-review system was archived 2026-05). Ratings power the game-detail Ratings tab, dashboard, and share cards.

| Method | Path | Auth | Purpose |
|--------|------|------|---------|
| POST | `/api/v1/ratings/<concept_id>/group/<group_id>/rate/` | Login (linked) | Submit/update a rating. **`recommendation` is required** on a new rating (an update of an existing one falls back to the stored value when it is omitted). Optional `blurb` (<=140 char public "quick take"), sanitized + banned-word filtered. A non-empty blurb requires guidelines agreement (403 `needs_guidelines` if not). Omitting `blurb` preserves an existing one; sending `""` clears it. |
| POST | `/api/v1/ratings/blurb/<rating_id>/report/` | Login (linked) | Report a rating's quick take for moderation (reactive: publish -> report -> staff soft-hide via `blurb_hidden`). Body `{reason, details?}`; rate-limited 10/m; can't report your own; deduped per reporter. |
| POST | `/api/v1/guidelines/agree/` | Login | Record community-guidelines agreement (idempotent). The blurb write path calls this on submit (the modal's fine print is the notice). |

Blurbs are read only through `UserConceptRating.visible_blurbs()` (present + not staff-hidden, backed by the partial `rating_blurb_idx`); the game-detail view previews the newest few per group with `select_related('profile')` (whale-safe). Reports are stored on `BlurbReport` (mirrors `ReviewReport`, triaged in Django admin; FKs the rating, so it follows the rating through `Concept.absorb()` with no absorb branch). The stored blurb is plain, **unescaped** text -- render it only in an auto-escaped HTML text context, never `|safe` or a JS/attribute/JSON context.

### Tutorial System

| Method | Path | Auth | Purpose |
|--------|------|------|---------|
| POST | `/api/v1/tutorial/welcome/dismiss/` | Login | Dismiss Welcome Tour (skip or complete). Body: `{action: 'complete'|'skip', last_step: 1-4}` |
| POST | `/api/v1/tutorial/game-detail/dismiss/` | Login | Dismiss Game Detail coach marks tour. Body: `{action: 'complete'|'skip', last_step: 1-5}` |
| POST | `/api/v1/tutorial/badge-detail/dismiss/` | Login | Dismiss Badge Detail coach marks tour. Body: `{action: 'complete'|'skip', last_step: 1-4}` |

### Misc

| Method | Path | Auth | Purpose |
|--------|------|------|---------|
| POST | `/api/v1/equip-title/` | Login | Equip/unequip title |
| POST | `/api/v1/user/timezone/` | Login | Update user timezone |
| POST | `/api/v1/tracking/site-event/` | No | Track client-side event |
| POST | `/api/v1/easter-eggs/roll/` | Login | Server-side easter-egg probability roll |
| GET | `/api/v1/game-backgrounds/` | Login | Search game backgrounds |

### Mobile App

| Method | Path | Auth | Purpose |
|--------|------|------|---------|
| POST | `/api/v1/auth/login/` | No | Login (returns token) |
| POST | `/api/v1/auth/signup/` | No | Create account |
| POST | `/api/v1/auth/logout/` | Token | Invalidate token |
| POST | `/api/v1/auth/password-reset/` | No | Password reset email |
| GET | `/api/v1/mobile/me/` | Token | Current user profile |
| GET | `/api/v1/mobile/profiles/<psn>/` | Token | Profile by PSN username |
| POST | `/api/v1/mobile/psn/generate-code/` | Token | Generate verification code |
| POST | `/api/v1/mobile/psn/verify/` | Token | Verify PSN account |
| GET | `/api/v1/mobile/psn/status/` | Token | PSN link status |
| GET | `/api/v1/mobile/sync/status/` | Token | Sync status |
| POST | `/api/v1/mobile/sync/trigger/` | Token | Trigger sync |
| POST | `/api/v1/device-tokens/` | Token | Register device token |
| DELETE | `/api/v1/device-tokens/<token>/` | Token | Unregister device |
| GET | `/api/v1/mobile/badges/` | Token | Badge series list |
| GET | `/api/v1/mobile/badges/<slug>/` | Token | Badge series detail |
| GET | `/api/v1/mobile/user/badges/` | Token | Current user's badges |
| GET | `/api/v1/mobile/profiles/<psn>/badges/` | Token | Profile's badges |
| GET | `/api/v1/mobile/profiles/<psn>/games/` | Token | Profile's games |
| GET | `/api/v1/mobile/games/<id>/trophies/` | Token | Game trophy list |

## Rate Limits

Rate limits are applied via `django-ratelimit` on specific endpoints:

| Endpoint | Limit | Purpose |
|----------|-------|---------|
| Recap detail | 60/min | Prevent excessive data generation |
| Recap regenerate | 10/min | Limit costly regeneration |
| Recap share PNG | 20/min | Limit Playwright rendering |
| Recap share HTML | 60/min | Limit share card generation |

## Related Docs

- [Mobile App](../guides/mobile-app.md): Mobile-specific endpoint details
- [Roadmap System](../features/roadmap-system.md): Roadmap editor API details (replaced the legacy Checklists API)
- [Challenge Systems](../features/challenge-systems.md): Challenge API details
- [Community Flags](../features/community-flags.md): Game flag categories and effects
- [Fundraiser](../features/fundraiser.md): Donation/claim API details
- [Comment System (Legacy)](../features/comment-system.md): Why the comment list/create endpoints are gone
