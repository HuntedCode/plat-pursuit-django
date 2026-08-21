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
Django admin" again -- `save()` keeps it in lockstep (`role == 'admin'` or superuser). Existing
staff were backfilled as admin; moderators are demoted by hand in the Django admin.

Moderators currently unlock ONE gate beyond the mark: unpublished badge preview
(`trophies/views/badge_views.py`). Everything else (analytics, beta site, staff mixins) stays
admin-only deliberately -- the wider mod toolset is a planned rebuild.

## Rendering

One partial: `components/name_mark.html` via `{% name_mark name=... mark=profile.display_mark %}`
(`users/templatetags/mark_tags.py`). Name in the mark colour (the `pp-supname` primitive) plus the
glyph -- level stars exactly as the storefront previews them, wrench/shield stroked in the service
colour. Applied on: leaderboard rows, leaderboard user cells, Browse Hunters cards, the profile
header. The Credits wall keeps its own card but takes the service override on the NAME only
(`--svc-t`): a paying staff member's name is crimson while the stars and "PlatPursuit {Level}"
sub-line stay their paid level -- the wall is about who pays.

## Gotchas and Pitfalls

- **Never write `display_mark` directly.** Change the inputs (role, premium) and let the writers
  run; a hand-write will be clobbered on the next reconcile.
- **The legacy classes are gone**: `.legendary-title`, `.lb-row__prem`, `.pp-hcard--supporter`,
  `.pp-hcard__supp` were removed with this lane. Do not reintroduce them; use the partial.
- **Service colours are distance-checked** against the whole giving ramp
  (`test_service_colours_keep_their_distance_from_the_giving_ramp`). New marks must pass it.
- **`user.save()` triggers a profile write** (the mark refresh). Bulk user updates via
  `queryset.update()` skip it -- if a bulk operation changes roles, refresh marks explicitly.
- **Comment/game-list surfaces** are legacy/hidden and got colour-only or plain treatment; they
  adopt the full partial when their own rebuilds happen.
