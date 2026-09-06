# Admin Hub

The admin team's tools, at `/staff/`. Moderators have the [Moderation Center](moderation-center.md);
this is everything else: accounts, restrictions, the decision log, and the way into every staff page.

`is_staff` only — which the `CustomUser.save()` lockstep keeps **false** for a moderator. Built
2026-09.

## Why `/staff/` and not somewhere new

`/admin/` is Django's. `/staff/` was already the namespace of four of the five existing staff tools,
`static/robots.txt` already blocks it, and `tests/engine/test_staff_design_strip.py` asserts those
four still answer 200 at their current paths. Choosing it means the hub appears without moving
anything and without editing a single pinned test.

`/staff/moderation/` is a **pinned 404** from the 2026-08 strip-down. The decision log lives at
`/staff/decisions/` for that reason.

## The three surfaces

### The landing, `/staff/`

Three bands, in this order, because the question an admin arrives with is "is there anything for me
today" and a page that opens with a grid of links makes them work that out themselves.

1. **Needs a human** — reports waiting, badge claims waiting, worker backlog. Reports waiting reads
   `moderation_service.queue_counts()` *verbatim*, so the hub cannot claim a different amount of work
   from the page it points at.
2. **Recently done** — both audit logs interleaved by time, newest first, each row badged with its
   source. Two bounded slices merged in Python; no UNION view, no merge table.
3. **Tools** — the doors that have no number.

**There is deliberately no subscription figure yet.** `/staff/subscriptions/` defines "past due" as a
djstripe status *joined to a still-premium user*, and "action required" as a deduplicated 7-day
notification window. Counting those cheaply here means writing that definition a second time, and a
number that disagrees with the page it links to is worse than no number. It is a door until the
rebuild owns one definition.

**Redis down renders as "cannot reach Redis", not 0.** Those are opposite facts, and showing both as
zero tells an admin the workers are idle when they may be unreachable.

### People, `/staff/people/` and `/staff/people/<user_id>/`

Search-only. There is no listing of every account, because a staff tool that renders one invites
browsing through people and the reason to open this page is always a specific person. Search matches
PSN handle or email, case-insensitively.

The person page answers "what is the story with this hunter": both logs filtered to them, their own
quick takes (**hidden ones included and marked** — omitting them would omit the evidence; showing
them unmarked would misrepresent what is live), their restriction history, and the form to apply a
new one. The form lives here rather than on the restriction list because the decision needs what is
above it; restricting from a list of names is restricting without looking.

### Decisions, `/staff/decisions/`

Every moderation decision, with the power to reverse one — the single thing the Mod Center does not
offer. `reverse_action` existed from the day the log was built and was reachable from no page at all
until this hub.

A **record first, a workbench second**: `all` is the default filter, not `reversible`, which is the
opposite of the queues where `pending` leads because it is the only thing that means work.

Reverse is offered only for what `moderation_service.UNDOABLE_ACTIONS` says the service can undo —
derived from `_UNDO`, not restated — because offering a button the service refuses is the worst of
both: the admin has typed a reason by then.

## Restrictions

A hunter barred from writing, for a period or indefinitely. Scopes: `quick_takes`, `reports`,
`all_ugc`.

### What it is, and what it is not

- It stops **new words only**. Nothing already published is hidden.
- It never touches trophies, badges, ratings or ranking.
- It is **not** `is_active=False`, which kills login and every read. This is a targeted write ban.
- **Lapsing is not lifting.** Expiry happens by the clock with nobody writing a row, so `is_live` is
  derived and never stored, and the DB query evaluates `expires_at` against `now` every time.

The UI says the first two out loud, because an admin reaching for *restrict* when they meant *hide*
is the likely mistake.

### It remembers the account AND the profile

`UserRestriction` carries both FKs, and this is the design decision worth understanding.

The first cut keyed on `CustomUser` alone, reasoning that hanging it off the profile would make
unlinking PSN an escape hatch. That reasoning was right and incomplete. Settings offers a
**self-service account deletion**, `Profile.user` is `SET_NULL`, and
`VerificationService.link_profile_to_user` reattaches *the same profile row* to a new account. So:
delete the account, re-register, re-verify the same PSN account — and a `CASCADE` took every
restriction while the trophies, badges, ranking and handle all came back.

Neither half is durable alone. The account is gone after a deletion; the profile is absent for an
account that never linked PSN. `active_scopes_for()` matches on either.

### The gates

`is_restricted_from(profile, scope)` is the one question, asked at every write path. It **fails
closed** on the wrong argument type: it read `getattr(profile, 'user_id', None)` once, so handing it
a `CustomUser` returned "not restricted", and a gate whose default is permissive is not a gate.

| Where | File | Covers |
|-------|------|--------|
| `CommentService.can_interact` | `trophies/services/comment_service.py` | comment votes and reports, review writes and reports, blurb reports, **and now game flags and comment edits** |
| `GameFlagService.submit_flag` | `trophies/services/game_flag_service.py` | the only writer of `GameFlag`, so a future caller cannot route around the view |
| `GroupRatingView.post` | `api/rating_views.py` | writing a quick take |
| `roadmap_note_service` | `trophies/services/roadmap_note_service.py` | notes, on create **and edit** |
| `FundraiserDonateView` | `api/fundraiser_views.py` | the donor-wall message only — the donation itself is never refused |

**A quick-take restriction stops the words and nothing else.** The scores save as normal, because
dropping them would rewrite a game's averages as a side effect of a decision about somebody's prose —
the same principle that keeps `blurb_hidden` separate from the blurb.

`can_interact` takes an `action` parameter so its refusal names what the caller was doing. All three
of its messages said "interact with comments" while it is called from the quick-take report endpoint
and now the game-flag one; a hunter told they cannot "interact with comments" after pressing Report
on a game goes looking for a comment they never wrote.

## The audit log

Two tables, sharing their rules and not their schema. See
[Moderation Center](moderation-center.md#the-audit-log) for `ModerationAction`, and
`core/services/audit.py` for the shared `require_reason` / `frozen_label`.

`core.AdminAction` covers account, billing and system acts: restrictions applied and lifted, and
(from the next branch) subscription actions and claim-status changes. It carries `subject_user` for
"everything ever done to this account" and a `target_type`/`target_id`/`target_label` triple instead
of a GenericForeignKey — a GFK stores a content-type id and no label, so a deleted target reads as
"somebody did something to nothing", which is the failure the frozen labels exist to prevent.

**Lifting a restriction writes a new entry whose `reverses` points at the one that applied it** —
the same grammar as reversing a moderation decision, so the two read alike in the log.

## File Map

| File | Purpose |
|------|---------|
| `core/staff_views.py` | Every hub view: landing, people, person, decisions, restrictions, and the four POST actions |
| `core/models.py` (`AdminAction`) | The account/billing/system audit log |
| `core/services/audit.py` | The rules both logs obey |
| `core/admin_site.py`, `core/admin_apps.py` | Django admin narrowed to superusers |
| `users/models.py` (`UserRestriction`) | The restriction itself |
| `users/services/restriction_service.py` | Apply, lift, and the gate query |
| `trophies/mixins.py` (`PostActionMixin`) | POST-only + `_safe_next` + outcome reporting, shared with `/mod/` |
| `templates/staff/` | `_staff_shell.html` and the five pages |
| `tests/engine/test_admin_hub.py`, `test_restrictions.py`, `test_admin_audit.py`, `test_django_admin_is_the_owners.py` | |

## URLs

All `StaffRequiredMixin`. POST-only where they mutate.

| Path | Name |
|------|------|
| `/staff/` | `admin_hub` |
| `/staff/people/`, `/staff/people/<id>/` | `admin_people`, `admin_person` |
| `/staff/decisions/` | `admin_decisions` |
| `/staff/restrictions/` | `admin_restrictions` |
| POST `/staff/decisions/<pk>/reverse/` | `admin_reverse_decision` |
| POST `/staff/quick-takes/<pk>/hide/` | `admin_hide_take` |
| POST `/staff/people/<pk>/restrict/` | `admin_restrict` |
| POST `/staff/restrictions/<pk>/lift/` | `admin_lift_restriction` |

## Nav: there isn't one, and that is the answer

`moderation-center.md` asked that a future admin dashboard supply its own reason for a navbar row
rather than inheriting the Mod Center's. It does not have one. The Mod Center earned that row because
it has a **queue that accrues while nobody is looking**; nothing here does — a lapsed restriction
lapses by itself, subscriptions have webhooks, claims arrive with a donation email. The only
accruing thing the hub reports is the mod queues, which already have their marker, and every admin
already sees that row.

So the hub sits one click inside the Mod Center behind `{% if user.is_staff %}`
(`templates/staff/_admin_link.html`). `navbar.html` is untouched and a test pins its emptiness.

## Gotchas and Pitfalls

- **Do not add a cached count.** `moderation_service.open_report_count()` is deliberately live. Three
  separate paths made a cached version disagree with the page it pointed at, including Django-admin
  bulk actions that bypass the service and fire no signal. See moderation-center.md's Cache Keys.
- **A restriction gate belongs in the SERVICE, not only the view.** `submit_flag` carries one because
  it is the only writer of its model; a view-only check is one refactor from being routed around.
- **New UGC endpoint? Add it to the sweep.** `test_a_full_restriction_blocks_every_way_of_writing` is
  the single place that answers "did we cover everything" — and it claimed to be that while covering
  three of seven, which is how four ungated writers shipped.
- **`select_for_update` on a filtered queryset locks nothing when no rows match.** `apply_restriction`
  locks the `CustomUser` row instead, because that one always exists. Locking the restrictions
  prevented a double-apply only in the case an unlocked read would also have caught.
- **Restriction durations are bounded** (`MAX_RESTRICTION_DAYS`). `timedelta` raises `OverflowError`
  past ~2.9 million days, and `OverflowError` is not a `ValueError` — an unbounded form field was a
  500.
- **`?page=` is clamped.** A page number that parses fine as an int can still be out of range for a
  Postgres bigint, which is a 500 from a querystring.
- **`PostActionMixin.error_class` has no default.** It was `Exception`, which would catch a
  subclass's programming errors and render the traceback text to an admin as an explanation.

## Related Docs

- [Moderation Center](moderation-center.md): the queues, the decision log's model, the reversal rules
- [Marks & Roles](marks-and-roles.md): the three flags, and why Django admin is superusers only
- [Community Flags](community-flags.md) and [Game Ratings](game-ratings.md): where the reports come from
- [Navigation](navigation.md): the avatar menu, and the one staff link in it
