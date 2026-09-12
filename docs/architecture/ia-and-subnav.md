# Information Architecture and Sub-Navigation

> **Shipped state (IA rebuild, 2026-07).** This replaces the earlier hub-of-hubs model (Home /
> Browse / Community / My Pursuit with a separate Dashboard). The four-part IA build: personal-hub
> unify, the Support hub, ownership-aware profile chrome, and the mobile collapse-to-grid strip.

## The 4 hubs

The IA is four top-level hubs, reached from the global navbar (and the mobile bottom tab bar). Each
owns a family of pages; a sticky sub-nav strip below the navbar surfaces that hub's pages,
URL-matched.

| Hub | Landing | Owns | Mental mode |
|-----|---------|------|-------------|
| **My Pursuit** (personal) | `/career/` | the personal, login-gated surfaces (at root URLs) | "my identity + progression" |
| **Browse** | `/games/` | public discovery / list pages | "find content" |

> **2026-08:** Browse's Catalog rail gained **Jobs** (`/jobs/`, + `/jobs/<slug>/`). A catalogue of
> jobs is a browse surface, not a Leaderboards one -- its relationship to Career's Dossier is the
> Collection-vs-Browse-Badges split (*scope, not pagination*): Career shows YOUR standing across the
> 24 jobs, `/jobs/` shows what they are. (2026-08: `/jobs/` was brought onto the shared HTMX browse
> contract with the rest of the hub, and its wall is five across at `lg` so each discipline occupies
> one row -- see the rebuild playbook.) Leaderboards briefly gained a sub-nav at the same time
> (Global / Game / Badge / Job Boards) and **lost it again before the end of the month**: the three
> board directories were removed as second copies of `/games/`, `/badges/` and `/jobs/`, and the hub is
> back to `items=()` -- the shape it was designed with, and the one Support runs in. A hub landing is
> reached by its own navbar entry; a one-pill rail naming the page you are on is not navigation. See
> [leaderboards-rebuild](../design/rebuild/leaderboards-rebuild.md).
| **Leaderboards** | `/leaderboards/` | how everyone ranks (PSN-derived) | "where do I stand" |
| **Community** | `/community/`, `/hunters/` | hunters, and what they make: lists, challenges, the Hall of Fame | "what is everyone doing" |
| **Support Us** | `/support/` | the membership storefront (live) + `/support/roadmap/` (live) + `/support/membership/` (live) + the coming fundraiser sub-page | "ways to support us" |

**Above the hubs: the lobby (`/`).** Where every login lands (`LOGIN_REDIRECT_URL`), and the one page
that belongs to NO hub — so it renders no sub-nav strip, because on a lobby the CTAs *are* the navigation
and a hub rail underneath them would be a second, competing set of directions. Its only nav affordance is
the navbar wordmark, which takes the same active treatment a hub button does when you are standing on it.
Its job is narrow and should stay that way: confirm the data is fresh, show the trophy floor (first, since
it is what everyone arrives for and the one thing that is full on day one), and put the two moats — Career
and Collection — one click away. **The rule it lives by, and the reason the old dashboard was retired: a
curated GLANCE that teases and links into a page, never the page's content embedded.** Overview, which
used to be the personal hub's first tab, was narrowed into this in 2026-08.

**Organizing principle — "login-gated + mine."** A surface belongs to My Pursuit if it's personal
AND login-gated. Browse = find; Leaderboards = standings; Support Us = ways to support.
Gamification expands My Pursuit's strip; it does not earn its own hub.

**The "resist a 5th hub" rule was amended in 2026-09, deliberately and narrowly.** It used to end
"four mental modes, four hubs — resist a 5th," and that is still the right default. What it missed is
that the four modes sort by the reader's INTENT (find / rank / mine / support) while there is a second
axis the nav has to respect: **who authored the thing**.

Everything in Browse and Leaderboards is content the SITE owns — Games, Trophy Lists and Recently
Added are PSN; Franchises, Companies and Genres are IGDB; Badges and Jobs are PlatPursuit-authored;
the boards rank facts derived from PSN. Not one item is something a hunter wrote.

User-generated content is a different class, and the codebase already says so: `restriction_service`
carries an `all_ugc` scope — one switch governing comments, reviews, ratings and game lists — because
UGC is moderated, reportable, restrictable and removable in ways site-owned content is not. Taking
down somebody's list is categorically unlike delisting a game. **So the fifth hub exists because of a
different rule set, not a different intent**, and the default stands for everything else: a surface
that is merely *new* still has to fit one of the four.

## Community (returned 2026-09)

| | |
|---|---|
| Holds | Hunters, Game Lists, Challenges, Hall of Fame / community stats |
| Prefixes | `/community/`, `/hunters/` |
| Question it answers | "what is everyone making and doing" |

**Hunters moved back from Browse.** It went there in 2026-08 only because this hub was retired
("hunters are another thing you browse"), and while a profile is mostly PSN data, you look for a
community member where the community is. It keeps `/hunters/` — a sub-nav move, not a URL move.

**What did NOT move, and why the line holds:**

- **My Lists and My Challenges stay in My Pursuit → Tools.** The private side of a public system is
  still personal and login-gated, exactly as *Collection* stays in My Pursuit while *Badges* sits in
  Browse. A hub is not a feature's address; it is a mode.
- **Leaderboards stays raw PSN standings.** Challenge boards and the Hall of Fame go to Community,
  because what they rank is participation in a PlatPursuit activity rather than a PSN fact.
- **Browse stays the site-owned catalogue**, which is what it was already.
- **Community challenges** (collective, site-wide goals) follow the Badge Art Reveal shape — a
  site-wide banner plus an event page — rather than becoming a hub item.

`/community/` itself still 301s to `/leaderboards/` and that is fine: hubs here are nav groupings
with no landing URL of their own, so nothing needs to live there. The redirect is exact-path and was
set for inbound links; `/community/lists/` is unaffected. It also cannot be repointed — a permanent
redirect that has been live since 2026-08 is cached in browsers indefinitely.

### The mobile trade

The tab bar holds four, and five does not fit: items are `flex: 1` at `0.62rem`, so a fifth takes
each from ~93px to ~75px at 375px, and "Leaderboards" alone runs ~68-72px before padding. So
**Support Us leaves the tab bar for the avatar dropdown** (its own labelled entry near the top,
marked active when `hub_section == 'support'`) and Community takes the slot. Every size from `md`
shows all five.

Support Us is the right one to demote by frequency: a storefront, a roadmap, a fundraiser page and a
membership manager are occasional visits, where lists and challenges are habitual. The cost is that
no tab highlights while you are in Support Us — accepted knowingly, and smaller than it sounds,
because that hub carries its own sub-nav rail showing which of its four pages you are on. The tab bar
echo is redundant orientation there, not the only signal.

### "Support Us", not "Support"

Renamed 2026-09. "Support" alone reads as a help desk on most of the web, and this is the one place a
confused reader would look for one. Naming the ask is also the more earnest form, which is the site's
voice. It resolves a small existing oddity too: the hub and its first item were both called Support,
so the rail read "Support → Support".

> **Open, small:** that first item — the tier storefront — probably wants a truer name now that it
> sits under "Support Us" beside "My Membership". Something like *Tiers* or *Ways to Help*.

> **Community was retired (2026-08) and returned (2026-09).** It was retired not because community
> failed but because everything in it had gone somewhere else: Challenges retired, Reviews archived,
> Lists hidden pending a revamp, Profiles moved to Browse, Rate My Games to My Pursuit → Tools (it
> makes community DATA, but the act is personal and login-only), and Leaderboards promoted to a hub of
> their own. What remained was a landing page with nothing of its own to land on.
>
> It came back when Game Lists was rebuilt and Challenges was scoped with a public browser, a
> nearest-completion board and a Hall of Fame — i.e. when there was user-generated content again. See
> **[Community (returned 2026-09)](#community-returned-2026-09)** below for the hub and the amended
> principle behind it. The question this note used to leave open — "if Lists, Reviews or the Pursuit
> Feed come back, they need a home" — is answered for Lists and Challenges. **Reviews and the Pursuit
> Feed are still open**, though the same provenance rule would put both here.

## The personal hub (My Pursuit)

My Pursuit's landing is **Career** (the nav button and mobile tab point there). The logged-in Home (`/`)
is the lobby above the hubs, not this hub's root. The strip is grouped
**5 progress + 3 tools** with a divider between:

`Overview · Collection · Career · Milestones · Titles` **|** `Plat Cards · Recap · Profile`

(The Lab + Research Panel merged into **Career**. **My Stats** was pulled for the 1.0 launch — `/stats/`
redirects to Home pending its rebuild; see [stats-page.md](../design/stats-page.md).)

- **Root URLs.** The personal pages live at root: `/collection/`, `/lab/`, `/research-panel/`,
  `/milestones/`, `/titles/`, `/shareables/`, `/recap/` (+ `/profile-editor/`). The old
  `/my-pursuit/*` and `/dashboard/*` paths 301-redirect to them
  (`RedirectView(pattern_name=…, permanent=True, query_string=True)`). URL `name=`s are unchanged,
  so no `{% url %}` calls moved. Bare `/my-pursuit/` and `/dashboard/` now redirect to `/`.
- **Auth-gated strip.** The personal strip is a login-gated wayfinder. For anonymous viewers the
  context processor returns `hub_section=None`, so `/` reads as a hero with no strip (and public
  members like `/milestones/` / `/research-panel/` show no personal strip either).
- **Anon-hidden nav entry.** The My Pursuit navbar button and its mobile tab are wrapped in
  `{% if user.is_authenticated %}` — a logged-out visitor has no pursuit to show and the logo already
  reaches `/`, so the entry would be redundant *and* mislabeled (and it wouldn't even highlight,
  since the anon strip is gated off). Anon therefore sees 3 mobile tabs (Browse / Leaderboards /
  Support); the tab bar's `justify-around` inner distributes 3 or 4 evenly, no CSS change needed.
- **No Profile item, and no ownership-aware chrome (both removed 2026-08).** They only ever worked as
  a pair: the dynamic Profile tab needed the viewer's own username, and the chrome swap existed to put
  your own profile under the personal strip *so that tab could be highlighted*. Removing the tab alone
  would have rendered a strip highlighting nothing and naming nothing in the mobile collapse bar. Every
  profile page now carries the same Browse chrome whoever is viewing, and the avatar menu is the single
  route to your own.

## Support hub

`/support/` (`users.views.SupportStorefrontView`) **is** the membership storefront, not a landing
that links to one. Three sections: a **split header** (the statement left; an amount-first purchase
box right — six supporter levels, Backer → Cornerstone, monthly/yearly cycle radios, a mock
leaderboard-row preview showing the viewer's own name wearing the level, and a perks `<dialog>`), a
four-cell **paid band** (supporters / monthly support / months running / ads served), and the
**Credits** (the consent-gated supporter wall, `Profile.show_on_supporter_wall`). The ladder is
PLACEHOLDERS until its Stripe/PayPal SKUs exist (`SUPPORT_TIERS_ARE_PLACEHOLDERS`, forced off in
live mode). `/users/subscribe/` 302s in and its template is deleted.

`/support/roadmap/` shipped 2026-08-22 (`SupportRoadmapView`): forward-only — upcoming features as
icon cards in three certainty tiers (in the works / up next / the wishlist, `ROADMAP_TIERS` +
`ROADMAP_FEATURES` in `users/constants.py`), no dates or counts anywhere, test-enforced. The
storefront carries a roadmap band teasing three features per tier in the same vocabulary.

`/support/membership/` shipped 2026-08 (`SubscriptionManagementView`, moved from
`/users/subscription-management/`): the manage side of the hub -- level-tinted status card
(state via `SubscriptionService.membership_status`: active / past_due / grace / none), billing
facts, tenure, provider-branched actions (Stripe billing portal as a POST action; PayPal cancel
in a dialog), the perk tiles, and the wall opt-out. The old URL is a PERMANENT 302 (never 301 on
a payment-adjacent URL -- it is baked into every sent lifecycle email and stored notification);
the `subscription_management` route NAME moved with the page so every reverse() caller followed.
Staff (or DEBUG) can eyeball every state via `?preview=<active|cancelling|grace|past-due|paypal|
paypal-grace|legacy|none>` -- fabricated context, disarmed controls, real name only.

The view lives in `users.views` rather than `core.views` because **it answers this page's checkout
POST**. The form carries no `action`, so it self-POSTs to whatever URL rendered it; serving the form
here while the handler stayed at `/users/subscribe/` would mean a redirect on a POST, which browsers
turn into a GET with the body dropped. Handler and form must share a URL. For the same reason the old
URL redirects **temporarily** (302): a cached permanent redirect on a payment URL cannot be taken back.

**The Support RAIL is ON (2026-08-22):** Support / Roadmap / Badge Art / My Membership, four
real destinations, which is what makes the reversal of the Leaderboards-rail removal principled
("a rail naming the page you're on is not navigation" held because that hub collapsed to ONE
page; Support grew to four). "Badge Art" names the campaign's content rather than the mechanism.
"My Membership" is `membership_required` (premium_tier truthiness, the same gate as the navbar's
own link) and sits in its own `Yours` group -- non-members and anon viewers see three items,
because a non-member's door is the storefront.
The slugged campaign pages (`fundraiser` / `fundraiser_success` URL names) light the Fundraiser
item via `_URL_NAME_TO_SLUG_OVERRIDES`. The paired `LEADERBOARDS_HUB` comment block in
`hub_subnav.py` was revised with this flip, as both blocks required.

`/support/fundraiser/` (`FundraiserLandingView`, name `support_fundraiser`) is the rail item's
no-args target: a pure resolver that 302s to the latest STARTED campaign's slug page (live
first, else most recent by start date, which covers the ended celebration; a drafted upcoming
campaign is skipped so the public is never bounced home), with a quiet card when no campaign has
ever run. The campaign itself stays at `/fundraiser/<slug>/` -- the one payment-adjacent surface
(processor cancel URLs + sent emails land there), which is why the landing redirects instead of
rendering. Two fundraiser lookups in `fundraiser/models.py`: `get_active_fundraiser()`
(banner_active + live, for the site-wide banner) vs `get_live_fundraiser()` (live window only,
the landing's first choice). Both cache a PK for 60s on their own key.

The hub keeps the name **Support** (settled 2026-08-19; it was flagged as a placeholder). It covers
both halves of what lives here, where "Membership" would name only one of them.

## Sub-nav infrastructure

| File | Purpose |
|------|---------|
| `core/hub_subnav.py` | `HubSubnavConfig` / `HubSubnavItem` dataclasses, `HUB_SUBNAV_CONFIG`, `resolve_hub_subnav(request)`, `build_rendered_items(...)` |
| `plat_pursuit/context_processors.py` | `hub_subnav(request)` — runs every request; builds the template context |
| `templates/partials/hub_subnav.html` | the strip (desktop row + mobile collapse-grid) |
| `templates/partials/navbar.html` | 4 hub buttons + avatar dropdown |
| `templates/partials/mobile_tabbar.html` | 4 bottom tabs (`<lg`) |

- **Config.** `HubSubnavConfig(key, label, icon, prefixes, items)` + `HubSubnavItem(slug, label,
  url_name, icon, auth_required, divider_before)` (frozen dataclasses). `HUB_SUBNAV_CONFIG` holds
  My Pursuit / Browse / Leaderboards / Support.
- **Resolution.** `resolve_hub_subnav(request)`: (1) `_URL_NAME_TO_SLUG_OVERRIDES` — sub-pages whose
  url_name differs from their tab short-circuit here (e.g. `game_detail` → Browse/trophy-lists:
  the whole `/games/<np>/` list family, roadmaps included, lights the list-level catalogue,
  while the concept `game_page` lights Games); (2) the
  **exact `/`** case → My Pursuit + `overview`; (3) **longest-prefix-wins** across every hub's
  prefixes. The bare-root case is an equality check, so `/profiles/...` never falls into it.
- **Context processor.** `hub_subnav()` returns `hub_section`, `hub_subnav_label`/`icon`,
  `hub_subnav_items`, `hub_subnav_active_slug`, and `hub_subnav_active_label` (the current page, for
  the mobile bar). It also applies the My Pursuit anon auth-gate, the ownership-aware profile swap,
  and the dynamic Profile extra. `build_rendered_items` drops `auth_required` items for anon and
  `reverse()`s each url_name (a `NoReverseMatch` skips the item, never 500s).
- **Dynamic items** resolve their own URL (kwargs) before reaching the template; pass them via the
  `extras` tuple (the Profile item is the surviving example). Prefer piggybacking existing cache
  keys over new per-request DB reads.

### The strip: desktop row + mobile collapse-to-grid

- **Desktop (lg+):** a single horizontal-scroll row — hub label + icon, the 6+4 divider
  (`item.divider_before`), pill items. Active pill = filled + primary border + `aria-current`.
- **Mobile (<lg):** a one-line **collapse bar** (`▦ Hub · current-page ▾`) that taps open an
  **absolute overlay grid** (`grid-cols-3`, the 6+4 divider preserved) dropping over the content.
  The panel is `absolute` (z-40, above the hotbar's z-30) so expanding it does not reflow the sticky
  chrome. A delegated `main.js` handler toggles it (open on the bar, close on outside-click or
  Escape → focus returns to the toggle). The collapsed panel uses `visibility:hidden` + `aria-hidden`
  so its links leave the tab order + AT tree; the transitions honor `prefers-reduced-motion`.
- Support has `items=()`, so its whole `<nav>` is short-circuited by the
  `{% if hub_section and hub_subnav_items %}` guard — it renders nothing.

### Sticky chrome

Three pinned top-of-viewport elements (all sticky at every breakpoint): navbar `top-0 z-50` (~64px),
sub-nav `top-16 z-40`, hotbar `top-[7.25rem] z-30` inside a hub family (falls back to `top-[4.5rem]`
on non-hub pages where the sub-nav is hidden), plus the mobile bottom tab bar `bottom-0 z-40`
(`<lg`, ~56px). The Tailwind `top-*` classes are first guesses; **`alignStickyChrome()` in
`main.js`** measures the actual navbar height on load / resize / `fonts.ready` / `hotbar:toggle` and
inline-styles the sub-nav + hotbar `top:` to match, insulating against font/DPI rounding (which can
push the navbar 1-2px off 64px and cause a visible shift). The hotbar template branches on
`hub_section` for its initial fallback offset.

## Gotchas and Pitfalls

- **Longest-prefix-wins + the exact-`/` case are load-bearing.** `/profiles/<u>/` must match Browse,
  not the personal hub's `/`. The bare-root match is an equality check, separate from prefix
  `startswith`.
- **`CloudflareOriginGuardMiddleware` does NOT follow a re-home.** It guards profile pages with a
  hardcoded PATH REGEX, so unlike every `{% url %}`/`reverse()` reference it stays pointed at the old
  path when one moves — silently un-guarding the most scraped page type on the site. Moving any
  guarded surface means editing that regex in the same change (`test_profiles_moved.py` pins it).
  Related: profile paths are unreachable from the Django test client without a `CF-Ray` header,
  because the guard runs before the URL conf.
- **The personal strip is authed-only.** Anon on `/` (or on a public member) gets no strip. The
  gate lives in the context processor (`hub.key == 'my_pursuit' and not is_auth → hub_section None`),
  before any item work.
- **Sub-nav is hidden on non-hub pages** (settings, auth, notifications, `/staff/*`, errors,
  webhook URLs): `hub_section=None` short-circuits the template. Test these.
- **Mobile collapse a11y.** The collapsed panel must be `visibility:hidden` (not just
  `max-height:0`) + `aria-hidden` so its links leave the tab order / AT tree; Escape closes and
  returns focus to the toggle.
- **`build_rendered_items` reverses static items** → static `HubSubnavItem`s cannot have required
  URL kwargs. Kwarg-bearing items go through `extras` with the URL pre-resolved.
- **Don't add a 5th hub.** Four mental modes. A feature that fits none is a signal to reconsider the
  IA, not to add a button.
- **Do NOT inline-style `top:` on `#hotbar-wrapper` from JS** — it overrides the JS-managed sticky
  offset. The collapse animation touches only `#hotbar-container.style.maxHeight` and
  `wrapper.style.marginTop` (the `-8px` nudge that closes the `main` `py-2` gap at rest; sticky
  ignores margins for its own offset, so it's correct in both states).

## Related Docs

- [Navigation](../features/navigation.md): navbar, footer, mobile tab bar, profile tabs
- [My Pursuit Hub](../features/my-pursuit-hub.md) (the Community Hub doc describes a retired page)
- [Fundraiser](../features/fundraiser.md): the badge-art campaign the Support hub houses
- [Template Architecture](../reference/template-architecture.md): base.html, context processors, the hotbar
