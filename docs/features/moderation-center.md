# Moderation Center

The tools moderators use to keep user-submitted content and game data honest. Two queues (reported
quick takes, reported game problems), one landing page that says what is waiting and what was just
decided, and an audit log that records who did what and why for every decision.

Lives at `/mod/`, open to **moderators and admins only**. Built 2026-09.

## Architecture Overview

Three ideas carry the whole system.

**Every decision goes through one service.** `moderation_service` applies the change and writes the
audit entry inside a single transaction. A view that writes `blurb_hidden = True` and then logs is a
view that can write and not log: on an early return, on an exception, or because whoever adds the
third queue copies the write and misses the logging. Here the log *is* the write, and no view touches
a model directly.

**The log has to say what actually happened.** Atomicity alone is not enough. Without a status
precondition and a row lock, two moderators acting on one report both succeed, and the second writes
an entry claiming a change it did not make. For an appeal record, an entry saying "moderator B hid
this" when B hid nothing is worse than no entry: it is affirmatively misleading evidence. Every
action re-reads its target `FOR UPDATE` and refuses anything already handled, with a message fit to
show the moderator.

**A reason is required at the service, not in a form.** A form guarantees nothing about the next
caller (a management command, a shell, the admin dashboard that does not exist yet), and a log of
timestamps with no reasons answers "what happened" while leaving "why" (the only question an appeal
asks) unanswered.

Reversal follows from the same reasoning: an earlier decision is undone by writing a **new** entry
that points at the old one, never by editing or deleting it. An audit trail that can be rewritten is
not one.

## File Map

| File | Purpose |
|------|---------|
| `trophies/services/moderation_service.py` | Every decision. Applies the change, writes the entry, enforces the reason, owns the queue counts |
| `trophies/views/moderation_views.py` | The landing, the two queues, the four action endpoints. Deliberately thin |
| `trophies/mixins.py` | `is_mod_or_admin()` and `ModeratorRequiredMixin`: the gate as one expression |
| `templates/moderation/mod_center.html` | The landing: queue cards + recent decisions |
| `templates/moderation/_queue_shell.html` | The shell both queues share: header, status switcher, rows, paging |
| `templates/moderation/quick_takes.html` | What a quick-take row shows |
| `templates/moderation/game_flags.html` | What a game-flag row shows |
| `plat_pursuit/context_processors.py` | `moderation_alert`: the avatar-menu entry and its attention count |
| `tests/engine/test_moderation_service.py` | What a decision does |
| `tests/engine/test_mod_center.py` | Reaching it: the gate, the queues, the navbar entry |

## Data Model

### `ModerationAction`

The audit log. One row per decision, never updated after it is written.

| Field | Notes |
|-------|-------|
| `actor` / `actor_label` | `SET_NULL` FK plus the name captured at write time, so an entry stays readable after a staff account is deleted. `related_name='moderation_decisions'` because `moderation_actions` is taken by the legacy `ModerationLog` |
| `action` | `blurb_hidden`, `blurb_report_dismissed`, `game_flag_approved`, `game_flag_dismissed`, `blurb_restored` |
| `reason` | Required, minimum 3 characters, enforced in the service |
| `blurb_report` / `game_flag` | Both `SET_NULL`: the entry outlives its subject |
| `target_id` / `target_label` | What was acted on, in words, frozen at write time |
| `changed` | `{field: [before, after]}`. What this action **wrote**. Empty dict is a real outcome, not a bug |
| `evidence` | Things worth keeping beside the diff that the action did not write (the blurb's text) |
| `reverses` | Self-FK. Unique-constrained where non-null: one reversal per action |

`is_reversed` is derived from `reversed_by_action`, not stored.

**Why `changed` and `evidence` are separate.** `changed` means "what this action wrote", and hiding a
quick take does not write the blurb. Filing the text under `evidence` keeps a generic diff view from
rendering a misleading "blurb: unchanged" row, and means "did this action modify field X" never
answers yes for the blurb.

### Naming a moderator

Anywhere this system names a person it uses **`CustomUser.display_name`**: the PSN handle
(`display_psn_username`, then `psn_username`), with the email address only as the fallback for an
account with no linked profile.

Two reasons, privacy first. An email address is personal data, and a staff page is still a page. It
is also simply worse information: a colleague's PSN handle identifies them at a glance, and an
address nobody recognises does not.

`display_psn_username` leads because `Profile.save()` lowercases `psn_username` -- that field is the
lookup key, not the spelling.

`moderation_service._label()` freezes the same property onto `ModerationAction.actor_label` at write
time, so the landing rail and the queue rows never name one person two ways, and the name survives
the account being deleted. One rule, two readers.

**Any queryset that renders it needs the profile joined.** `display_name` reaches through to
`profile`, so a queue of handled rows is one extra query per row without
`select_related('reviewed_by__profile')`. `test_naming_the_moderator_does_not_cost_a_query_per_handled_row`
pins it: the general per-row guard cannot, because every row it builds is pending, and a pending row
has no moderator to name.

### What the queues read

| Queue | Model | "Waiting" means |
|-------|-------|-----------------|
| Quick Takes | `BlurbReport` | `status='pending'` |
| Game Flags | `GameFlag` | `status='pending'` |

## Key Flows

### Hiding a reported quick take

1. Moderator types a reason and posts to `mod_hide_blurb`.
2. Service re-reads the report `FOR UPDATE`; refuses it if somebody already handled it.
3. `UserConceptRating.blurb_hidden = True`. The **rating survives**: its scores stay in every average
   and the hunter keeps their rating. Only the free text goes. That is the whole reason
   `blurb_hidden` is a separate field, so hiding words does not silently rewrite a game's numbers.
4. Report closes as `action_taken`; a `ModerationAction` records the diff plus the blurb as evidence.
5. The moderator is redirected back to the list they were reading, not to the top of `pending`.

### Approving a game flag

Same shape, except the mutation is delegated to `GameFlagService.approve_flag`, which owns the
per-type rules. The before/after is captured **around** that call, so the log records what the
service actually did rather than what the view expected.

Some flag types change no field at all: approving one means "confirmed, a human should act", and the
row says so rather than implying a change is coming. **Which ones is derived, not listed** --
`GameFlagService.NO_OP_FLAG_TYPES` is every valid type minus the ones with a field to write. The
list was the bug: the queue template and two code comments each hand-named `missing_vr` and
`region_incorrect`, and all three missed `other`, so an `other` row promised a moderator that
approving would "update this game's flags directly" for a flag that updates nothing.

Two types set `shovelware_lock`, which permanently overrides the automated classifier
(`SHOVELWARE_FLAG_TYPES`, read by the template the same way). The row calls that out in its own
right: it is by some distance the heaviest button on the page and does not otherwise look it.

### Reversing a decision

Only `blurb_hidden` can be reversed automatically today. The previous value is read out of the
original entry's `changed` rather than assumed to be `False`, because a take that was already hidden
when it was actioned would otherwise be *un*hidden and the system would call that a restoration.

## URLs

| Method | Path | Auth | Purpose |
|--------|------|------|---------|
| GET | `/mod/` | Mod/admin | Landing: what is waiting, what was just decided |
| GET | `/mod/quick-takes/` | Mod/admin | Quick-take queue (`?status=`, `?page=`) |
| GET | `/mod/game-flags/` | Mod/admin | Game-flag queue |
| POST | `/mod/quick-takes/<pk>/hide/` | Mod/admin | Hide the take, close the report |
| POST | `/mod/quick-takes/<pk>/dismiss/` | Mod/admin | Close the report, leave the take |
| POST | `/mod/game-flags/<pk>/approve/` | Mod/admin | Uphold the flag; applies the change |
| POST | `/mod/game-flags/<pk>/dismiss/` | Mod/admin | Reject the flag; game untouched |

Actions are POST-only: a decision mutates live data, and a GET would be followed by a crawler, a
prefetcher, or a bookmark.

## The Gate

`ModeratorRequiredMixin` on every view. It **redirects** rather than 403s, so a hunter who guesses a
URL gets the home page rather than confirmation that something is there.

`is_mod_or_admin()` exists as a function as well as a mixin because the same question is asked from
templates and from a context processor, and three hand-written copies of `is_staff or is_moderator`
is how one of them ends up subtly different. It uses `is_staff` rather than `role == 'admin'`:
`CustomUser.save()` keeps the two in lockstep, and `is_staff` additionally covers superusers, who
have no role set at all and would otherwise be locked out of the tools they are most likely to be
asked to fix. It also checks `is_active`, because revoking access is precisely the moment a stale
user object must not still say yes.

`test_nobody_but_a_moderator_can_reach_anything_under_mod` enumerates the routes **from the URL conf**
rather than from a hand-written list, so an eighth route added without the mixin fails on the day it
is added.

## Reaching It: The Avatar Menu Entry

The queues are only useful if a moderator knows there is something in them, so the avatar menu
carries a Mod Center entry with a marker when work is waiting.

This **reverses** the 2026-08 strip-down's "no staff links in the avatar menu" rule, and the reversal
is deliberately narrow. Moderation is the only staff surface with a queue: it has something to say
without being visited, and a bookmark cannot tell anyone three reports came in overnight. Every other
staff page stays bookmark-reached, and a future admin dashboard needs its own reason rather than this
precedent.

Three properties worth keeping:

- **The gate runs before the work.** The processor fires on every page render on the site and almost
  nobody who triggers it is a moderator, so non-moderators return an empty dict before any query.
  The gate call itself sits *inside* the try/except: `is_mod_or_admin` reads `user.is_moderator` by
  bare attribute access on purpose, so that losing the property breaks loudly, and "loudly" for a
  site-wide context processor means a 500 on every authenticated request including Django admin.
- **The entry is not conditional on the count.** Only the marker is. A link that materialises when
  there is work is a link nobody can find when they go looking for it.
- **The marker and the Mod Center read one definition.** `moderation_service.queue_counts()`. A
  marker that counts differently from the page it points at is worse than no marker.

The marker sits at the opposite corner from the sync LED (`.pp-av__queue` vs `.pp-av__dot`): the ring
and dot are about *your account*, the marker is about *the site's work*. It clamps at `9+`; the exact
figure stays in the menu row, where there is room.

It is **error red**, and that is a decided trade rather than an accident. Red is also the
errored-sync ring and the LED that ring colours, so a moderator with a failed sync sees red in
several places at once. It was briefly `--pp-primary` to dodge that, and the owner asked for red
back (2026-09): red is materially easier to notice, and being noticed is this element's whole job. A
marker nobody catches is worth less than one sharing a hue with a rarer state.

What separates them in that state is **shape, not colour**: a bordered pill carrying a NUMBER at the
top corner, against a bare 12px dot at the bottom one. Two properties carry that load and are not
cosmetic:

- the `2px solid var(--pp-bg-1)` **border** punches the pill out of the ring it overlaps, so the two
  reds never touch;
- **`z-index: 1`**, because `.pp-av::after` (the ring) is generated content and paints after every
  real child regardless of source order. Without it the ring draws over the number, which is what
  shipped first and what the owner caught in a browser rather than in the suite.

If the two ever are confused in practice, change the sync LED: sync has three colours to choose
between and this has one job.

## Cache Keys

**None. The count is live, and that is the decision worth recording.**

It was cached for five minutes under `moderation:open_total`, with a story about the staleness being
acceptable in one direction (a new report raising the marker late) and busted in the other (a
decision clearing it at once). The audit took that story apart three ways:

- **The Django-admin bulk actions never went through the service at all**, and `queryset.update()`
  fires no signal, so nothing could have caught them. An admin clearing twelve flags left every
  moderator wearing a marker pointing at an empty queue.
- **The Mod Center computed the true number on the very same render and threw it away.** The page
  body could say "3 waiting" while the navbar beside it, in the same HTML, showed nothing.
- **The get / compute / set was racy.** A read straddling a bust could reinstate the number the bust
  had just removed, for a full TTL.

All three end in the same place: a marker claiming work against a page saying "nothing waiting", one
click apart, which is the exact failure the shared definition exists to prevent.

Both report tables index `status`, so `open_report_count()` is two index-served counts, and it runs
only for moderators and admins: an audience of about ten accounts, not the whole internet. The cache
was protecting a per-request path that almost nobody takes and buying three ways for the marker to
lie. If it ever does need caching, cache it where the truth is known (`queue_counts()`), not at the
read.

## Integration Points

- [Community Flags](community-flags.md): where `GameFlag` rows come from, and what each type means
- [Game Ratings Tab](game-ratings.md): where quick takes and `BlurbReport` rows come from
- [Marks & Roles](marks-and-roles.md): the moderator/admin role split this gate reads, and what
  `is_staff` means since Django admin was narrowed to superusers (2026-09)
- [Navigation](navigation.md): the avatar menu the entry lives in
- [Shovelware Detection](../reference/shovelware-detection.md): what `shovelware_lock` overrides

## Gotchas and Pitfalls

- **`WATCHED_GAME_FIELDS` is derived from a DATA MAP, never from source text.** It is an alias for
  `GameFlagService.WATCHED_FIELDS`, which is computed from `FIELD_ACTIONS` and `SHOVELWARE_FIELDS`.
  What was removed is the original derivation by *regex over `approve_flag`'s source*: a reformat, a
  quoting change, a hoisted constant, or a field name with a digit each returned *fewer* fields,
  silently. The real guard is `test_every_flag_type_lands_in_the_log`, which approves every flag type
  and fails if a field the DB actually changed is missing from `changed`.
- **`_UNDO` is a dict, not a set of names.** The first cut gated on a set while the body was
  hardcoded to the blurb path, so adding a key would have told a moderator "the report behind this
  decision is gone" for a report that was never involved. A key with no handler is now a `KeyError`
  at edit time.
- **`next` is validated.** It arrives in the POST body so a moderator lands back on the list they
  were reading. Unvalidated that is an open redirect. The leading-slash check is *also* a correctness
  check: `redirect()` treats a slashless string as a view name and raises `NoReverseMatch`.
- **`quick_takes.html` uses `.games.all|first`, never `.first`.** `.first()` adds `ORDER BY` +
  `LIMIT` and so bypasses the prefetch cache, which is one query per row on a page of 25. (The game
  flags queue reaches `row.game` directly and needs no such care.)
- **`{% load %}` is not inherited from a parent template.** Each queue template loads `humanize`
  itself, and `{% extends %}` must be the first tag in `_queue_shell.html`.
- **The switcher class is `pp-switch__chip`.** An invented `__opt` has no CSS behind it and fails
  silently: the filters render as bare inline links with nothing separating them, which is how the
  first cut shipped.
- **Do not re-add a cache without reading the Cache Keys section above.** Three separate paths made
  the cached version lie, and two of them are still there: Django admin still writes these statuses
  without the service, and the Mod Center still computes the truth on its own render.

## Related Docs

- [Community Flags](community-flags.md): the flag types and where they are submitted
- [Game Ratings Tab](game-ratings.md): quick takes, blurb reporting, guidelines
- [Marks & Roles](marks-and-roles.md): `role`, `is_staff` lockstep, what a moderator is
- [Comment System (Legacy)](comment-system.md): `ModerationLog`, the read-only ancestor of this system
- [Redis Keys](../reference/redis-keys.md): the cache-key table
