# Marks & Roles

The site-wide mark system (who is this person, shown beside their name) and the admin/moderator
role split, built together 2026-08-22.

## The three registers, one slot

| Register | Marks | Source |
|---|---|---|
| Service | staff (crimson wrench), mod (amber shield) | `CustomUser.role` |
| Giving | the six supporter levels (colour + building stars), legacy tiers via `LEGACY_TIER_LEVEL_MAP` | `premium_tier` + premium state |
| Earned | trophy metals, badges, ranks | untouched -- marks never borrow from it |

**Precedence: staff > mod > supporter level.** One mark per name, the highest wins, decided at
WRITE time in `users/services/marks.py` and denormalised to `Profile.display_mark` -- surfaces
that render hundreds of names read one field and never re-derive it (whale-safe by construction).

**Writers (exactly two):** `Profile.update_profile_premium` (reached via `reconcile_premium`, the
premium truth-writer) and `CustomUser.save` on role changes. Both call `refresh_display_mark`.

## The role split

`CustomUser.role`: `admin` / `moderator` / empty. `is_staff` means "reaches the admin tools at
`/staff/`" -- `save()` keeps it in lockstep (`role == 'admin'` or superuser), and the
lockstep is symmetric: un-ticking "staff status" on an Administrator demotes the role too, so the
two fields never disagree. `role` is editable in the Django admin (Permissions fieldset, list
column + filter). `create_superuser` sets `role='admin'` at the source. Existing staff were
backfilled as admin; moderators are demoted by hand in the Django admin. Narrow saves whose
`update_fields` cannot move the mark (e.g. login's `last_login`) skip the profile refresh.

Moderators unlock four things beyond the mark:

| Gate | Where |
|------|-------|
| Unpublished badge preview | `trophies/views/badge_views.py` |
| The [Moderation Center](moderation-center.md) at `/mod/` (2026-09) | `is_mod_or_admin()` / `ModeratorRequiredMixin` |
| The beta / staging site | `BetaStaffGateMiddleware` (2026-08-23: the mod team reviews the beta too) |
| The home-page team previews (`?preview=landing` / `syncing` / `launch-welcome`) | `core/views.py` |

Analytics and the staff mixins stay admin-only deliberately. (This list said "one gate, everything
else admin-only including the beta site" until 2026-09, which contradicted both the middleware and
`docs/guides/staging.md`. If you add a moderator gate, add its row.)

The gate reads `is_staff` rather than `role == 'admin'` for the admin half, because the lockstep
above guarantees they agree AND `is_staff` additionally covers superusers, who have no role set at
all and would otherwise be locked out of the tools they are most likely to be asked to fix.

### Django admin is superusers only (2026-09)

`is_staff` used to mean exactly "can log into the Django admin", which is Django's own default gate.
It no longer does, and this is the one place that sentence changed.

**`/admin/` now requires `is_superuser`.** `core/admin_site.py` overrides `AdminSite.has_permission`,
wired in through `core.admin_apps.SuperuserOnlyAdminConfig` replacing `django.contrib.admin` in
`INSTALLED_APPS` — so `admin.site` *is* the narrowed site and every one of the ~140 `@admin.register`
calls lands on it, including any a future dependency adds.

The reason is the asymmetry in what the two surfaces cost if misused. `/staff/` actions go through a
service that requires a reason and writes an audit entry. Django admin is ~90 bulk actions that
mutate live data, most of which write nothing, plus raw edit access to every model. Owner's call: the
admin *team* gets the Admin Hub; Django admin is the owner's.

| Flag | Means |
|------|-------|
| `is_moderator` | `/mod/`, plus the four rows above |
| `is_staff` | `/mod/` **and** the Admin Hub at `/staff/` |
| `is_superuser` | all of the above **and** Django admin |

`is_superuser` rather than a hardcoded username or id: Django-native, grantable and revocable from
the admin itself, and it does not rot when an email changes.

Two consequences worth knowing. The Admin Hub hides its Django-admin card from anyone who would be
turned away — advertising a door somebody cannot open reads as a fault rather than a boundary. And
Django's per-model permission system is no longer what keeps an Administrator out of a changelist:
`tests/engine/test_django_admin_is_the_owners.py` grants its Administrator **every** permission
deliberately, because a permission-less fixture would be refused by machinery that was always there,
and every test in that file would then pass with this gate removed.

## Rendering

One partial: `components/name_mark.html` via `{% name_mark name=... mark=profile.display_mark %}`
(`users/templatetags/mark_tags.py`). Name in the mark colour (the `pp-supname` primitive) plus the
glyph -- level stars exactly as the storefront previews them, wrench/shield filled in the service
colour. Unmarked names render BARE (no wrapper: a flex span cannot ellipsize, so wrapping the
~99% unmarked case broke truncation site-wide). The optional `index` param staggers the flow per
row (negative delays) so a column of marked names never pulses in unison.

Applied on: leaderboard rows, Browse Hunters cards, the profile header (`size='lg'`), comments,
Career hero (`size='lg'`), the Pursuer Card name line, game-detail quick takes (the JS
live-prepend twin builds the same `.pp-markname > .pp-supname` structure), and inline on the
three Playwright share templates via `components/_mark_glyphs_inline.html` (recap card, plat
card, platinum grid -- explicit hex, no stylesheet). The Credits wall keeps its own card and
takes the FULL service override (`--svc-t` + `service_key`, read from the denorm): a paying
staff member's name is crimson, the sub-line names the role, and the mark slot draws the
service GLYPH (wrench/shield) rather than the paid level's stars -- reversed 2026-08-23 (a
backer star beside the word "Staff" read as the wrong icon). The fundraiser's donor wall and
claimed-badge tiles wear marks too, STATICALLY (colour + glyph run, never the flow -- they are
grids of names).

Register/state rules (supporter.css): service names hold STILL (the flow is the bought
register's signature); the Pursuer Card name is still + 800 weight (the earned rank line keeps
the top of the hierarchy); and state colour out-ranks identity (`is-you`, search landing, sort
axis, hover) -- the glyph keeps telling the identity while the name answers the state. On
mobile (<768px) star runs facepile (overlap + a `--mark-rim` surface-colour rim); product
surfaces (tier chips, become-preview) keep the flat run at every size.

## Gotchas and Pitfalls

- **Never write `display_mark` directly.** Change the inputs (role, premium) and let the writers
  run; a hand-write will be clobbered on the next reconcile.
- **The legacy classes are gone**: `.legendary-title`, `.lb-row__prem`, `.pp-hcard--supporter`,
  `.pp-hcard__supp` were removed with this lane. Do not reintroduce them; use the partial.
- **Service colours are distance-checked** against the whole giving ramp
  (`test_service_colours_keep_their_distance_from_the_giving_ramp`). New marks must pass it.
- **`user.save()` triggers a profile write** (the mark refresh). Bulk user updates via
  `queryset.update()` skip it -- if a bulk operation changes roles, refresh marks explicitly.
- **Comments render the full partial** (glyph included -- a moderator's authority must not be
  hue-alone, and staff crimson sits near `--pp-error`). Game-list surfaces are dormant and stay
  plain until the Game Lists revamp.
- **Unlinking a PSN profile clears the mark** (`update_profile_premium` handles the orphaned
  no-user case) -- an orphaned profile keeps rendering on Browse Hunters and the boards.
