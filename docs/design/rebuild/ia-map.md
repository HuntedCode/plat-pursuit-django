# IA Map — Living Document

> **How to use this.** The rebuild's information architecture was built **bottom-up**: pages first,
> with the structure recorded here as it solidified rather than designed in full up front. The rebuild
> is complete (2026-09), so the map below describes the shipped IA, not a plan.
>
> **It stays living.** For every page added from here on, add a row with its URL, what it links **out**
> to, and what links **in** to it. Thirty seconds per page; it is the defense against disconnected
> islands. The bottom-up rule still applies to any new cluster — build **destinations first, hubs last**,
> so a hub surfaces real things instead of guesses.
>
> This supersedes the legacy `docs/architecture/ia-and-subnav.md` (the 4-hub model) for
> rebuild work. The strategic frame is `docs/design/product-identity.md`.

## Stable skeleton (settled, not in question)

The expensive-to-change structure is decided and stable. Do not re-litigate it per page.

- **3 hubs**: **Pursuit** (`/`, the spine + home), **Browse** (`/games/`), **Community** (`/community/`).
- **Standalone utilities**: Stats (`/stats/` — hidden for 1.0, redirects to Home; out of scope for the site-wide rebuild, returns later as its own tool),
  Shareables (`/shareables/`), Recap (`/recap/`).
- **Navbar (signed-in)**: `[Logo -> Pursuit home] [Browse] [Community] [My Profile]   [bell] [avatar]`. There is no separate "Pursuit" button; the logo is the Pursuit home link.
- **URL convention**: flat top-level URLs for Pursuit sub-pages (no `/pursuit/` prefix; `/` is its home). Legacy paths 301 via the reverse-name redirect strategy.
- **Sub-nav**: config-driven (`core/hub_subnav.py:HUB_SUBNAV_CONFIG`); add items as pages ship.

## Sections

### Pursuit (`/`) — the spine
Home of the Pursuer, the two rails (**Badges** and **Contracts/Elements** = leveling), and identity. Badges now split into two surfaces: the **public catalog** (Browse `/badges/` — the per-series Series view + the per-tier medallion Gallery) and the **personal album** (the engaged-scoped Collection under Pursuit). The catalog is discovery; the album is "what you hold." **The Pursuit home IS the "where you're at" overview** (Pursuer hero + Lab snapshot + active Projects + recent badges, with quick links into each destination). There is **no separate Logbook page** — that role folds into `/` (see resolution below).

| Page | URL | Status | Links OUT to | Linked IN from |
|------|-----|--------|--------------|----------------|
| **Pursuit home** | `/` | identified; **build LAST** (the "where you're at" hub; absorbs the old Logbook role) | The Lab, Research Panel, Badges, Titles, Browse, Community, Stats | navbar logo, redirects |
| **The Lab** | `/my-pursuit/lab/` (flat `/lab/` per the URL sweep TBD) | **built** (the converted Logbook page: Pursuer hero + element experience — periodic table / radar / element detail) | element detail (in-page modal), Research Panel | Pursuit home (Lab snapshot), sub-nav |
| **Research Panel** | `/my-pursuit/research-panel/` | **built** (baseline; accept endpoint live) | Project/game detail, accept endpoint | Pursuit home, sub-nav |
| **Collection** | `/my-pursuit/collection/` (flat `/collection/` per the URL sweep) | **built** (the badge album / Binder Surface; the Pursuit "Badges" rail) | badge detail (Browse) | Pursuit home, sub-nav |
| **Milestones** | `/milestones/` | exists (legacy, to rebuild) | — | sub-nav |
| **Titles** | `/titles/` | exists (legacy, to rebuild) | Pursuit home | Pursuit home, sub-nav |

> **Conversion note:** the Logbook page **was converted directly into The Lab** (`/my-pursuit/logbook/` 301s to `/my-pursuit/lab/`) — the Logbook was always the element-identity page wearing a placeholder name. There is no separate Logbook. The "where you're at" overview role is a **fresh concern for the Pursuit home (`/`)**, assembled last, which previews + links to The Lab, Research Panel, and Badges.

Deferred within Pursuit: **Star Chart**, **Quests** (Phase 2/3).

### Browse (`/games/`) — discovery
Existing hub. Games, trophies, **badges (catalog `/badges/` + detail `/badges/<series_slug>/`)**, companies, franchises, genres, themes, engines, recently-added, flagged. Badges re-homed here 2026-06-23 (re-homed `BadgeListView`/`BadgeDetailView`) — the public find/search catalog; the personal album is the Pursuit Collection. Sub-nav "Badges" item added; `/my-pursuit/badges/*` 301s to `/badges/*`.

### Community (`/community/`) — social
Existing hub, unchanged. Reviews, profiles, challenges, lists, leaderboards.

## Resolved IA questions

- **Logbook vs The Lab** (resolved 2026-06-16) — **The Lab is its own destination page.** The "Logbook" was really the personal overview hub, which is the **Pursuit home (`/`)** — so there is no separate Logbook page; that role folds into `/`. This *removes* a page rather than adding one: destinations (Lab, Research Panel, Badge album) are their own pages, and `/` is the single "where you're at" hub that previews + links to them.
- **Research Panel home** (resolved 2026-06-16) — its **own page** (`/my-pursuit/research-panel/`), a Pursuit sub-nav slot; linked in from the Pursuit home.
- **Badge catalog vs Collection** (resolved 2026-06-23) — badges live in TWO surfaces, mirroring games. The public **catalog** (find/search: `/badges/` + `/badges/<series_slug>/`) is a **Browse** page (re-homed `BadgeListView`/`BadgeDetailView`); the personal **Collection** album stays in **Pursuit**. This supersedes "Badges = collection at `/badges/`" — `/badges/` is now the Browse catalog, the Collection is built (currently `/my-pursuit/collection/`). The list is a far better find/search surface than the album, and being public it fixes anonymous discovery. See `product-identity.md` IA amendment + memory `project_badge_pages_collection_vs_list`.

## IA questions — both resolved

- **URL convention sweep** — RESOLVED. The flat top-level Pursuit URLs shipped as one sweep: the old
  `/my-pursuit/*` and `/dashboard/*` paths 301-redirect to them, reverse names did not move, and bare
  `/my-pursuit/` and `/dashboard/` redirect to `/`. New pages use flat URLs. See
  [ia-and-subnav.md](../../architecture/ia-and-subnav.md).
- **Near-term sub-nav shape** — RESOLVED by shipping: the sub-nav is config-driven from
  `core/hub_subnav.py`, and the live items are what that file lists.
