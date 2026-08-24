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

### Game Lists — RETIRED (2026-08)

The Game Lists feature is hidden pending a revamp; all 11 `/api/v1/lists/*` endpoints are unrouted. The
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
| POST | `/api/v1/user/quick-settings/` | Login | Single-setting writes: profile toggles (hide hiddens / zeros), 24hr clock, timezone, browse defaults, and one-shot `ui_flag` education flags (see [onboarding.md](../features/onboarding.md)) |

## Rate Limits

Rate limits are applied via `django-ratelimit` on specific endpoints:

| Endpoint | Limit | Purpose |
|----------|-------|---------|
| Recap detail | 60/min | Prevent excessive data generation |
| Recap regenerate | 10/min | Limit costly regeneration |
| Recap share PNG | 20/min | Limit Playwright rendering |
| Recap share HTML | 60/min | Limit share card generation |

## Related Docs

- [Mobile App](../guides/mobile-app.md): why the mobile API was removed, and what to know when rebuilding it
- [Roadmap System](../features/roadmap-system.md): Roadmap editor API details (replaced the legacy Checklists API)
- [Challenge Systems](../features/challenge-systems.md): Challenge API details
- [Community Flags](../features/community-flags.md): Game flag categories and effects
- [Fundraiser](../features/fundraiser.md): Donation/claim API details
- [Comment System (Legacy)](../features/comment-system.md): Why the comment list/create endpoints are gone
