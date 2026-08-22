# Marks & Roles

The site-wide mark system (who is this person, shown beside their name) and the admin/moderator
role split, built together 2026-08-22.

## The three registers, one slot

| Register | Marks | Source |
|---|---|---|
| Service | staff (crimson wrench), mod (green shield) | `CustomUser.role` |
| Giving | the six supporter levels (colour + building stars), legacy tiers via `LEGACY_TIER_LEVEL_MAP` | `premium_tier` + premium state |
| Earned | trophy metals, badges, ranks | untouched -- marks never borrow from it |

**Precedence: staff > mod > supporter level.** One mark per name, the highest wins, decided at
WRITE time in `users/services/marks.py` and denormalised to `Profile.display_mark` -- surfaces
that render hundreds of names read one field and never re-derive it (whale-safe by construction).

**Writers (exactly two):** `Profile.update_profile_premium` (reached via `reconcile_premium`, the
premium truth-writer) and `CustomUser.save` on role changes. Both call `refresh_display_mark`.

## The role split

`CustomUser.role`: `admin` / `moderator` / empty. `is_staff` means exactly "can log into the
Django admin" again -- `save()` keeps it in lockstep (`role == 'admin'` or superuser), and the
lockstep is symmetric: un-ticking "staff status" on an Administrator demotes the role too, so the
two fields never disagree. `role` is editable in the Django admin (Permissions fieldset, list
column + filter). `create_superuser` sets `role='admin'` at the source. Existing staff were
backfilled as admin; moderators are demoted by hand in the Django admin. Narrow saves whose
`update_fields` cannot move the mark (e.g. login's `last_login`) skip the profile refresh.

Moderators currently unlock ONE gate beyond the mark: unpublished badge preview
(`trophies/views/badge_views.py`). Everything else (analytics, beta site, staff mixins) stays
admin-only deliberately -- the wider mod toolset is a planned rebuild.

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
card, platinum grid -- explicit hex, no stylesheet). The Credits wall keeps its own card but
takes the service override on the NAME + sub-line only (`--svc-t`, read from the denorm): a
paying staff member's name is crimson while the stars stay their paid level -- the wall is about
who pays.

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
