# Information Architecture and Sub-Navigation

> **Current as of 2026-10-27** (rewritten by the [IA review](../design/reviews/ia.md), which found this doc
> describing four hubs, a Profile rail item and the retired hotbar). The chrome that carries this structure
> (navbar, tab bar, avatar menu, footer, search, trails) is in [Navigation](../features/navigation.md).

## The hubs

Five hubs, each answering one question. Every page belongs to at most one, and a sticky sub-nav rail below the
navbar lists that hub's pages.

| Hub | Opens | Question | Rail |
|-----|-------|----------|------|
| **My Pursuit** (signed in only) | `/career/` | "how am I doing" | Progress: Career, Collection, Milestones, Titles. Tools: Plat Cards, Recap, Rate My Games, My Lists, My Challenges |
| **Browse** | `/games/` | "what is there" | Catalog: Games, Trophy Lists, Badges, Jobs, Recently Added. Curation: Franchises, Companies, Genres & Themes |
| **Leaderboards** | `/leaderboards/` | "where do I stand" | none: one page, with its own board switchers |
| **Community** | `/hunters/` | "what is everyone doing" | Hunters, Game Lists, Challenges, Hall of Fame |
| **Support Us** | `/support/` | "how can I help" | The project: Supporter Levels, Roadmap, Badge Art. Yours: My Membership (members only) |

Every hub's nav button opens its rail's first item (Community lined up on 2026-10-27, review IA-05).

**Above the hubs: the lobby (`/`).** Where every sign-in lands (`LOGIN_REDIRECT_URL`), and the one page that
belongs to no hub, so it renders no rail: on a lobby the calls to action are the navigation. Its only chrome
route is the navbar wordmark, which takes a hub button's active state while you stand on it. Its job is narrow:
confirm the data is fresh, show the trophy floor, and put Career and Collection one click away. A glance that
links into pages, never a page's content embedded (the reason the old dashboard was retired, 2026-08).

**Outside every hub:** Settings, the auth pages, About, Contact, Privacy, Terms, What's New, the error pages and
staff pages. They render no rail and no trail.

## How pages are sorted

- **Mine and signed-in only goes to My Pursuit.** Gamification grows My Pursuit's rail; it does not earn a hub.
- **The private side of a public system is still mine.** My Lists and My Challenges sit in My Pursuit while the
  public Game Lists and Challenges sit in Community, exactly as Collection sits in My Pursuit while Badges sits
  in Browse. A hub is a mode, not a feature's address.
- **Who authored it is the second axis.** Browse and Leaderboards hold only what the site owns: PSN data (games,
  trophy lists, the boards), IGDB data (franchises, companies, genres) and what the team authored (badges,
  jobs). What hunters make (lists, challenge runs) is a different class, moderated and restrictable under the
  `all_ugc` scope, and lives in Community with the hunters themselves. That axis is why Community exists as a
  fifth hub (2026-09); a surface that is merely new still has to fit one of the five.
- **A hub with one page carries no rail.** A single pill naming the page you are on is not navigation. That is
  why Leaderboards has none (its three board directories were removed in 2026-08 as copies of `/games/`,
  `/badges/` and `/jobs/`; each entity's board lives on its own detail page), and why Support and Community each
  turned their rail on only when a second destination arrived.
- **A page whose hub depends on who is looking says so itself.** Your own profile, game list or challenge run
  sits under My Pursuit; anybody else's sits under Community. The view sets `request.hub_subnav_override`.

## Resolution

`core.hub_subnav.resolve_hub_subnav(request)` answers in this order:

1. **The view's own answer**, `request.hub_subnav_override = (hub_key, slug)`. The context processor runs after
   the view, so the attribute is there in time. An unknown hub key falls through rather than blanking the rail.
   Today: a profile and its day pages (`OWN_PROFILE_HUB`, My Pursuit with no item lit), a game list
   (`my_lists`) and a challenge run (`my_challenges`), each for its owner only.
2. **`_URL_NAME_TO_SLUG_OVERRIDES`**, for pages whose url name is not a rail item's. Every detail page needs a
   line here: prefix matching finds the hub, but the lit item is an exact url name match, so a page shipping
   without a line renders its hub's rail with nothing lit, silently (the `job_detail` failure, and IA-04's
   optimizer, How badges work and a visitor's day page).
3. **The bare `/`** returns no hub. An equality check, so no deeper path falls into it.
4. **Longest prefix wins** across every hub's `prefixes`.

The context processor (`plat_pursuit/context_processors.py`, `hub_subnav`) then drops the My Pursuit rail for a
signed-out viewer (so public members like `/milestones/` sprout no personal rail) and builds the items with
`build_rendered_items`, which drops `auth_required` items for signed-out viewers and `membership_required`
items for non-members, except whichever item is the current page, which always names itself.

## Configuration

| File | Purpose |
|------|---------|
| `core/hub_subnav.py` | `HubSubnavConfig` / `HubSubnavItem`, `HUB_SUBNAV_CONFIG` (nav order), `resolve_hub_subnav`, `build_rendered_items`, `footer_columns` |
| `plat_pursuit/context_processors.py` | `hub_subnav(request)`: the rail's template context, plus the attention marks (`_subnav_marks`) |
| `templates/partials/hub_subnav.html` | The rail: desktop row with More overflow, phone collapse bar and sheet |
| `core/templatetags/chrome_tags.py` | `{% footer_hubs %}`: the footer's columns, from the same config |

- `HubSubnavItem(slug, label, url_name, icon, auth_required, membership_required, group, tag, tag_aria)`. Items
  are listed in group order; the template regroups consecutive runs under a quiet group label. `icon` is not
  rendered anywhere (the rail draws only the hub's icon).
- `HubSubnavConfig.landing` names the page a hub with no items opens. Only the footer reads it.
- **Attention marks** (2026-09-30) are per-viewer chips on an existing item, passed as
  `build_rendered_items(..., tags={slug: (text, kind, aria)})`. A tag overrides text, kind and spoken form
  together or not at all. `tag_kind` `count` is the nav's number and `xp` its lozenge; neither borrows amber,
  which on this site means "not ready yet". The one producer, `_subnav_marks`, puts the claimable count on
  Career and the XP mark on My Challenges, reading the nav's two cached keys.
- **The moments pill** sits at the end of the My Pursuit rail and beside the phone collapse bar: "3 new
  platinums" opens the platinum reel. See [Celebration Moments](../features/moments.md).

## Gotchas and Pitfalls

- **Anything that mirrors the config must be built from it.** The footer was a hand-kept copy and drifted with
  every rail change (Hunters under Browse for a commit; later Jobs, Challenges, My Lists and My Challenges
  missing and Badges under My Pursuit, review IA-01). It now reads `footer_columns`. The navbar and tab bar still
  name the five hubs by hand, so a hub added or renamed means editing both.
- **Every detail page needs an overrides line** (see Resolution, step 2). Test that the rail lights, not that it
  renders: an unlit rail still renders.
- **`build_rendered_items` reverses each item with no arguments**, so a rail item cannot need URL kwargs.
- **`CloudflareOriginGuardMiddleware` does not follow a move.** It guards profile pages with a hardcoded path
  regex, so moving a guarded surface means editing that regex in the same change (`test_profiles_moved.py`).
  Profile paths are unreachable from the Django test client without a `CF-Ray` header.
- **`/community/` 301s to `/leaderboards/`** and must keep doing so: a permanent redirect live since 2026-08 is
  cached in browsers indefinitely. Community needs no landing page; its nav button opens Hunters.
- **The rail gives way to a pinned page bar** (2026-10-23). While a page's mini-bar (`.pp-minibar`, revealed by
  `StickyReveal`) is pinned, `pp-bar-pinned` on `<html>` slides the rail up behind the navbar and makes it
  `visibility: hidden`; the bar moves into its place with `translate`. An open phone sheet or More menu closes on
  the way out. `main.js` publishes the rail's height as `--chrome-rail`, and `--rail-shift` is that while a bar is
  pinned; anything pinned below the rail subtracts it. The stack starts at the navbar's bottom, `top` included,
  so the PSN outage banner moves it all down.
- **Phone sheet accessibility.** The closed sheet is `visibility: hidden` plus `aria-hidden` (not just zero
  height), so its links leave the tab order; Escape closes it and returns focus to the bar.

## Related Docs

- [Navigation](../features/navigation.md): the chrome that carries this structure
- [Page Principles](../design/page-principles.md): what a page shows, and in what order
- [Leaderboards rebuild](../design/rebuild/leaderboards-rebuild.md): why Leaderboards is one page
- [Fundraiser](../features/fundraiser.md): the badge art campaign the Support Us hub houses
