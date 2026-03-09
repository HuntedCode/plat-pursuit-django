# API Endpoints

All API endpoints live under `/api/v1/` and are defined in `api/urls.py`. The web app uses session/CSRF authentication; the mobile app uses token authentication (`Authorization: Token <token>`). Staff-only endpoints require the `StaffRequiredAPIMixin`. There are approximately 120 endpoints across 16 categories.

## Authentication Patterns

| Pattern | Usage |
|---------|-------|
| Session + CSRF | Web app (default for all non-mobile endpoints) |
| Token auth | Mobile app endpoints (`/api/v1/auth/`, `/api/v1/mobile/`) |
| Login required | Most write operations |
| Staff required | Admin endpoints, game family management, dashboard (during dev) |
| No auth | Some read-only endpoints (comment lists, checklist lists) |

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

### Comments

| Method | Path | Auth | Purpose |
|--------|------|------|---------|
| GET | `/api/v1/comments/concept/<id>/` | No | List concept-level comments |
| GET | `/api/v1/comments/concept/<id>/trophy/<id>/` | No | List trophy-level comments |
| GET | `/api/v1/comments/concept/<id>/checklist/<id>/` | No | List checklist-level comments |
| POST | `/api/v1/comments/concept/<id>/create/` | Login | Create concept comment |
| POST | `/api/v1/comments/concept/<id>/trophy/<id>/create/` | Login | Create trophy comment |
| POST | `/api/v1/comments/concept/<id>/checklist/<id>/create/` | Login | Create checklist comment |
| GET/PUT/DELETE | `/api/v1/comments/<id>/` | Login | Comment detail/edit/delete |
| POST | `/api/v1/comments/<id>/vote/` | Login | Toggle upvote |
| POST | `/api/v1/comments/<id>/report/` | Login | Report comment |
| POST | `/api/v1/guidelines/agree/` | Login | Accept community guidelines |

### Checklists

| Method | Path | Auth | Purpose |
|--------|------|------|---------|
| GET | `/api/v1/checklists/concept/<id>/` | No | List checklists for concept |
| POST | `/api/v1/checklists/concept/<id>/create/` | Login | Create checklist |
| GET/PUT/DELETE | `/api/v1/checklists/<id>/` | Login | Checklist detail/edit/delete |
| POST | `/api/v1/checklists/<id>/publish/` | Login | Publish/unpublish |
| POST | `/api/v1/checklists/<id>/vote/` | Login | Toggle vote |
| POST | `/api/v1/checklists/<id>/report/` | Login | Report checklist |
| GET | `/api/v1/checklists/<id>/progress/` | Login | Get user's progress |
| POST | `/api/v1/checklists/<id>/progress/toggle/<item_id>/` | Login | Toggle item progress |
| POST | `/api/v1/checklists/<id>/sections/<id>/bulk-progress/` | Login | Bulk toggle section |
| GET/POST | `/api/v1/checklists/<id>/sections/` | Login | List/create sections |
| PUT | `/api/v1/checklists/<id>/sections/reorder/` | Login | Reorder sections |
| GET/PUT/DELETE | `/api/v1/checklists/<id>/sections/<id>/` | Login | Section detail |
| GET/POST | `/api/v1/checklists/sections/<id>/items/` | Login | List/create items |
| POST | `/api/v1/checklists/sections/<id>/items/bulk/` | Login | Bulk create items |
| POST | `/api/v1/checklists/sections/<id>/items/image/` | Login | Create image item |
| PUT | `/api/v1/checklists/sections/<id>/items/reorder/` | Login | Reorder items |
| PUT | `/api/v1/checklists/<id>/items/bulk-update/` | Login | Bulk update items |
| GET/PUT/DELETE | `/api/v1/checklists/items/<id>/` | Login | Item detail |
| POST | `/api/v1/checklists/<id>/image/` | Login | Upload checklist image |
| POST | `/api/v1/checklists/sections/<id>/image/` | Login | Upload section image |
| POST | `/api/v1/checklists/<id>/select-game/` | Login | Select game for trophy integration |
| GET | `/api/v1/checklists/<id>/available-trophies/` | Login | Available trophies for items |
| GET | `/api/v1/checklists/my-drafts/` | Login | User's draft checklists |
| GET | `/api/v1/checklists/my-published/` | Login | User's published checklists |
| GET | `/api/v1/checklists/my-progress/` | Login | Checklists user is tracking |
| POST | `/api/v1/markdown/preview/` | Login | Preview markdown rendering |

### Notifications

| Method | Path | Auth | Purpose |
|--------|------|------|---------|
| GET | `/api/v1/notifications/` | Login | List notifications |
| POST | `/api/v1/notifications/mark-all-read/` | Login | Mark all read |
| POST | `/api/v1/notifications/bulk-delete/` | Login | Bulk delete |
| POST | `/api/v1/notifications/<pk>/read/` | Login | Mark single read |
| DELETE | `/api/v1/notifications/<pk>/` | Login | Delete single |
| POST | `/api/v1/notifications/<pk>/rating/` | Login | Rate platinum notification |
| POST | `/api/v1/notifications/<pk>/share-image/generate/` | Login | Generate share image |
| GET | `/api/v1/notifications/<pk>/share-image/status/` | Login | Check generation status |
| GET | `/api/v1/notifications/<pk>/share-image/html/` | Login | Share card HTML |
| GET | `/api/v1/notifications/<pk>/share-image/png/` | Login | Share card PNG |
| GET | `/api/v1/notifications/<pk>/share-image/<format>/` | Login | Share image by format |

### Admin Notifications

| Method | Path | Auth | Purpose |
|--------|------|------|---------|
| POST | `/api/v1/admin/notifications/send/` | Staff | Send notification to users |
| POST | `/api/v1/admin/notifications/preview/` | Staff | Preview notification |
| GET | `/api/v1/admin/notifications/target-count/` | Staff | Count target audience |
| GET | `/api/v1/admin/notifications/user-search/` | Staff | Search users for targeting |

### Shareable Images

| Method | Path | Auth | Purpose |
|--------|------|------|---------|
| GET | `/api/v1/shareables/platinum/<id>/html/` | Login | Platinum share card HTML |
| GET | `/api/v1/shareables/platinum/<id>/png/` | Login | Platinum share card PNG |
| GET | `/api/v1/share-temp/<filename>` | No | Serve temp share image |

### Monthly Recaps

| Method | Path | Auth | Purpose |
|--------|------|------|---------|
| GET | `/api/v1/recap/available/` | Login | Available recap months |
| GET | `/api/v1/recap/<year>/<month>/` | Login | Full recap data (60/min) |
| POST | `/api/v1/recap/<year>/<month>/regenerate/` | Login | Force regenerate (10/min) |
| GET | `/api/v1/recap/<year>/<month>/html/` | Login | Recap share card HTML (60/min) |
| GET | `/api/v1/recap/<year>/<month>/png/` | Login | Recap share card PNG (20/min) |
| GET | `/api/v1/recap/<year>/<month>/slide/<type>/` | Login | Individual slide partial |

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
| GET | `/api/v1/games/<np_comm_id>/players/` | Login | Game players list |

### A-Z Challenge

| Method | Path | Auth | Purpose |
|--------|------|------|---------|
| POST | `/api/v1/challenges/az/` | Login | Create challenge |
| GET | `/api/v1/challenges/az/game-search/` | Login | Search games by letter |
| GET | `/api/v1/challenges/az/<id>/` | Login | Challenge detail |
| PUT | `/api/v1/challenges/az/<id>/update/` | Login | Update settings |
| DELETE | `/api/v1/challenges/az/<id>/delete/` | Login | Delete challenge |
| POST | `/api/v1/challenges/az/<id>/slots/<letter>/assign/` | Login | Assign game to slot |
| POST | `/api/v1/challenges/az/<id>/slots/<letter>/clear/` | Login | Clear slot |
| GET | `/api/v1/challenges/az/<id>/share/html/` | Login | Share card HTML |
| GET | `/api/v1/challenges/az/<id>/share/png/` | Login | Share card PNG |

### Calendar Challenge

| Method | Path | Auth | Purpose |
|--------|------|------|---------|
| POST | `/api/v1/challenges/calendar/` | Login | Create challenge |
| GET | `/api/v1/challenges/calendar/<id>/` | Login | Challenge detail |
| PUT | `/api/v1/challenges/calendar/<id>/update/` | Login | Update settings |
| DELETE | `/api/v1/challenges/calendar/<id>/delete/` | Login | Delete challenge |
| GET | `/api/v1/challenges/calendar/<id>/day/<month>/<day>/` | Login | Day detail (all platinums) |
| GET | `/api/v1/challenges/calendar/<id>/share/html/` | Login | Share card HTML |
| GET | `/api/v1/challenges/calendar/<id>/share/png/` | Login | Share card PNG |

### Genre Challenge

| Method | Path | Auth | Purpose |
|--------|------|------|---------|
| POST | `/api/v1/challenges/genre/` | Login | Create challenge |
| GET | `/api/v1/challenges/genre/concept-search/` | Login | Search concepts by genre |
| GET | `/api/v1/challenges/genre/<id>/` | Login | Challenge detail |
| PUT | `/api/v1/challenges/genre/<id>/update/` | Login | Update settings |
| DELETE | `/api/v1/challenges/genre/<id>/delete/` | Login | Delete challenge |
| POST | `/api/v1/challenges/genre/<id>/slots/<genre>/assign/` | Login | Assign concept to slot |
| POST | `/api/v1/challenges/genre/<id>/slots/<genre>/clear/` | Login | Clear slot |
| POST | `/api/v1/challenges/genre/<id>/bonus/add/` | Login | Add bonus slot |
| POST | `/api/v1/challenges/genre/<id>/bonus/<id>/clear/` | Login | Clear bonus slot |
| POST | `/api/v1/challenges/genre/<id>/move/` | Login | Move concept between slots |
| GET | `/api/v1/challenges/genre/<id>/move-targets/` | Login | Valid move targets |
| GET | `/api/v1/challenges/genre/<id>/share/html/` | Login | Share card HTML |
| GET | `/api/v1/challenges/genre/<id>/share/png/` | Login | Share card PNG |

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

### Dashboard (Staff During Dev)

| Method | Path | Auth | Purpose |
|--------|------|------|---------|
| GET | `/api/v1/dashboard/module/<slug>/` | Staff | Lazy module HTML |
| POST | `/api/v1/dashboard/config/` | Staff | Update module config |
| POST | `/api/v1/dashboard/reorder/` | Staff | Save module order |

### Subscription Admin (Staff Only)

| Method | Path | Auth | Purpose |
|--------|------|------|---------|
| POST | `/api/v1/admin/subscriptions/action/` | Staff | Admin action (resend, deactivate) |
| GET | `/api/v1/admin/subscriptions/user/<id>/` | Staff | User subscription detail |

### Community Reviews

Review responses include a `body_html` field containing server-rendered markdown (via `ChecklistService.process_markdown()`), ready for direct insertion into the DOM without a client-side markdown library.

| Method | Path | Auth | Purpose |
|--------|------|------|---------|
| GET | `/api/v1/reviews/recent/` | No | Recent reviews feed (paginated, for landing page) |
| GET | `/api/v1/reviews/wizard/queue/` | Login | Rate My Games queue (filters: unrated/unreviewed/both, queue_type: base/dlc) |
| GET | `/api/v1/reviews/<concept_id>/group/<group_id>/` | No | List reviews (sort: helpful/newest/oldest) |
| POST | `/api/v1/reviews/<concept_id>/group/<group_id>/create/` | Login | Create review (body + recommended) |
| POST | `/api/v1/reviews/<concept_id>/group/<group_id>/rate/` | Login | Submit/update rating |
| GET | `/api/v1/reviews/<concept_id>/group/<group_id>/trophies/` | No | Condensed trophy list with earned status |
| GET | `/api/v1/reviews/<review_id>/` | No | Single review detail |
| PUT | `/api/v1/reviews/<review_id>/` | Login | Edit own review |
| DELETE | `/api/v1/reviews/<review_id>/` | Login | Delete own review |
| POST | `/api/v1/reviews/<review_id>/vote/` | Login | Toggle helpful/funny vote |
| POST | `/api/v1/reviews/<review_id>/report/` | Login | Report review |
| GET | `/api/v1/reviews/<review_id>/replies/` | No | List replies |
| POST | `/api/v1/reviews/<review_id>/replies/` | Login | Create reply |
| PUT | `/api/v1/reviews/replies/<reply_id>/` | Login | Edit own reply |
| DELETE | `/api/v1/reviews/replies/<reply_id>/` | Login | Delete own reply |

### Misc

| Method | Path | Auth | Purpose |
|--------|------|------|---------|
| POST | `/api/v1/equip-title/` | Login | Equip/unequip title |
| POST | `/api/v1/user/timezone/` | Login | Update user timezone |
| POST | `/api/v1/tracking/site-event/` | No | Track client-side event |
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
- [Checklist System](../features/checklist-system.md): Checklist API details
- [Challenge Systems](../features/challenge-systems.md): Challenge API details
- [Fundraiser](../features/fundraiser.md): Donation/claim API details
