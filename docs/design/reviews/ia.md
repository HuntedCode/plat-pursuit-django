# Review: Information architecture

How the whole site hangs together now that every slice is reviewed: the navbar, the mobile tab bar, the hub
sub-nav rails, the avatar menu, the footer, site search and the breadcrumb trails, and whether each page lights
the hub and rail item a hunter would look for. Reviewed 2026-10-27. Method: [README.md](README.md).

**Where:** the chrome on 12 hub pages at phone (375), tablet (768), the owner's laptop (1280 x 633) and desktop
(1440), signed out on **https://platpursuit.com** and signed in on **dev** with the owner's own session; 27 detail
pages signed in and signed out on dev for the hub, rail item and trail each one shows; every URL pattern resolved
through `core.hub_subnav.resolve_hub_subnav` (249 routes) for the pages no hub claims.

**Headline:** the structure holds. Five hubs, each sorting by one question (mine / find / rank / everyone /
support), and the chrome that carries them works at every width: the rail fits at 1280, the phone sheet groups
its items, the active hub and item light on every main page. What drifted is everything **copied by hand from
that structure**: the footer still describes the site of August, site search knows four of the seven things you
can look up, the breadcrumb trails take four different shapes inside one hub, and the IA doc describes hubs that
were rebuilt since. None of it is visible on the page you are on; all of it is visible to somebody lost.

## Findings

| ID | Sev | Finding | Where | Status |
|----|-----|---------|-------|--------|
| IA-01 | Medium | **The footer is a sitemap of a different site.** Jobs is in no column. Badges is filed under My Pursuit (the page lights Browse). Community lists Game Lists and Hunters, not Challenges or the Hall of Fame; My Pursuit lists neither My Lists nor My Challenges. Leaderboards links "Badge Leaderboards", a page that ranks trophies, badge points and Career XP. Signed out, the My Pursuit column offers "Contracts", which is Career and sends a visitor to sign in. Informational links (About, What's New, How badges work, Contact) sit under "Legal". The footer is hand-copied from the hub config and has drifted from it before: its own comment records Hunters filed under the wrong heading for a commit | `templates/partials/footer.html` | open |
| IA-02 | Medium | **Site search finds four of the seven things it could.** The navbar box ("Search games, badges, hunters...") suggests Games, Badges, Franchises and Hunters. A Job, a Game List or a Challenge run can only be found from its own browse page, so a hunter has to know which page to search first; Companies and Genres the same | `trophies/views/sync_views.py` (`SiteSuggestView`), `static/js/navbar-search.js` | open |
| IA-03 | Low | **Breadcrumb trails take four shapes inside My Pursuit.** "Home / Career", "Home / Collection", "Home / Plat Cards" and "Home / Rate My Games"; but "Home / My Pursuit / Titles" and "Home / My Pursuit / My Challenges"; "Home / Game Lists / My Lists" while the chrome lights My Pursuit; and "My Challenges / Optimizer" with no Home. The middle "My Pursuit" crumb leads to the lobby on Titles (via the `/my-pursuit/` redirect) and to Career on My Challenges. Browse uses one shape throughout (Home / rail item / page) | `trophies/views/title_views.py`, `challenges/views.py`, `challenges/optimizer_views.py`, `gamelists/views.py` | open |
| IA-04 | Low | **Pages inside a hub that light no rail item:** the Challenge Optimizer (My Challenges), How badges work (Badges) and a visitor's view of a hunter's day page (Hunters). The rail renders with nothing current, so a phone's collapsed bar reads just the hub name. The `job_detail` failure the hub config's own comments warn about. (The trophy list leaderboard and the challenge calendar day also light nothing, but both are fragments a page fetches, not pages) | `core/hub_subnav.py` (`_URL_NAME_TO_SLUG_OVERRIDES`) | open |
| IA-05 | Low | **Community's nav button lands on the rail's second item.** The rail reads Game Lists, Hunters, Challenges, Hall of Fame; the button and tab open Hunters. Every other hub's button opens its first item. The navbar comment says it points at Hunters "until there is a second item"; there are four | `templates/partials/navbar.html`, `mobile_tabbar.html`, `core/hub_subnav.py` | open |
| IA-06 | Low | **"Support Us / Support".** The Support Us hub's first rail item, the storefront, is still called Support, an open naming question since 2026-09 (the hub, the footer and the doc all note it) | `core/hub_subnav.py`, `footer.html` | open |
| IA-07 | Medium | **The IA doc describes a site that is gone.** `docs/architecture/ia-and-subnav.md` says Community is "NOT YET BUILT" and runs `items=()`; lists My Pursuit's strip as "Overview / Collection / Career / Milestones / Titles / Plat Cards / Recap / Profile" (no Overview, no Profile, no My Lists or My Challenges); names `/lab/` and `/research-panel/`; says every profile carries Browse chrome (a visitor gets Community, the owner My Pursuit); says signed-out phones see Browse / Leaderboards / Support (they see Browse / Leaderboards / Community). It is the doc a future change to the nav starts from | `docs/architecture/ia-and-subnav.md` | open |

## Owner decisions (2026-10-27)

- **Search** also finds Jobs and public Game Lists. Not challenge runs (most share a title like "A-Z Challenge";
  searching the hunter finds them), not Companies or Genres.
- **The storefront's rail item is "Supporter Levels"**, which names what it sells and reads cleanly beside My
  Membership. Closes the 2026-09 open question.
- **Community's rail puts Hunters first**, so the nav button opens the rail's first item like every other hub.

## Checked, not findings

- **The chrome fits.** My Pursuit's nine-item rail fits on one row at 1280 with no More menu; the phone sheet
  groups every rail; the tab bar runs 3 / 4 / 4 / 5 items as designed. No overflow at any width.
- **Every main page lights its hub and item**, signed in and out, including the pages whose hub depends on who
  is looking (a game list, a challenge run and a profile: Community for a visitor, My Pursuit for the owner).
- **Support Us is off the phone tab bar** on purpose (five tabs do not fit at 375); the avatar menu carries it
  for everyone, and the Premium pill for linked non-members.
- **`/search/` has no links**, but it is the target of the homepage `SearchAction` schema, so it stays.
- **Your own profile lights My Pursuit with no item,** by the 2026-08 decision that the avatar menu is the one
  route to it (no Profile rail item).
- **Pages outside every hub** (Settings, About, Contact, Privacy, Terms, What's New, the auth pages) carry no rail
  and no trail, which is right: they belong to no hub.
- **The lobby** is reachable from the wordmark only, as the 2026-08 IA decided (Daily loop reviewed it).

## Candidate lessons

- **Anything copied by hand from the hub config drifts**: the footer, the trails and the doc each did,
  independently. Where a surface mirrors the config, generate it from the config. First sighting as a rule;
  the footer's own comment is the earlier one.
