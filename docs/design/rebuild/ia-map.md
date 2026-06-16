# IA Map (Rebuild) — Living Document

> **How to use this.** The rebuild's information architecture is being built **bottom-up**:
> we build pages, and the structure is recorded here as it solidifies (not designed in full
> up front). For every page we build, add a row with its URL, what it links **out** to, and
> what links **in** to it. Thirty seconds per page; it is the defense against disconnected
> islands. Build **destinations first, hubs last** — the hub pages (especially Pursuit home)
> are assembled once their destinations exist, so they surface real things instead of guesses.
>
> This supersedes the legacy `docs/architecture/ia-and-subnav.md` (the 4-hub model) for
> rebuild work. The strategic frame is `docs/design/product-identity.md`.

## Stable skeleton (settled, not in question)

The expensive-to-change structure is decided and stable. Do not re-litigate it per page.

- **3 hubs**: **Pursuit** (`/`, the spine + home), **Browse** (`/games/`), **Community** (`/community/`).
- **Standalone utilities**: Stats (`/stats/`), Shareables (`/shareables/`), Recap (`/recap/`).
- **Navbar (signed-in)**: `[Logo -> Pursuit home] [Browse] [Community] [My Profile]   [bell] [avatar]`. There is no separate "Pursuit" button; the logo is the Pursuit home link.
- **URL convention**: flat top-level URLs for Pursuit sub-pages (no `/pursuit/` prefix; `/` is its home). Legacy paths 301 via the reverse-name redirect strategy.
- **Sub-nav**: config-driven (`core/hub_subnav.py:HUB_SUBNAV_CONFIG`); add items as pages ship.

## Sections

### Pursuit (`/`) — the spine
Home of the Pursuer, the two rails (**Badges** = collection, **Contracts/Elements** = leveling), and identity. **The Pursuit home IS the "where you're at" overview** (Pursuer hero + Lab snapshot + active Projects + recent badges, with quick links into each destination). There is **no separate Logbook page** — that role folds into `/` (see resolution below).

| Page | URL | Status | Links OUT to | Linked IN from |
|------|-----|--------|--------------|----------------|
| **Pursuit home** | `/` | identified; **build LAST** (the "where you're at" hub; absorbs the old Logbook role) | The Lab, Research Panel, Badges, Titles, Browse, Community, Stats | navbar logo, redirects |
| **The Lab** | `/my-pursuit/lab/` (flat `/lab/` per the URL sweep TBD) | **built** (the converted Logbook page: Pursuer hero + element experience — periodic table / radar / element detail) | element detail (in-page modal), Research Panel | Pursuit home (Lab snapshot), sub-nav |
| **Research Panel** | `/my-pursuit/research-panel/` | **built** (baseline; accept endpoint live) | Project/game detail, accept endpoint | Pursuit home, sub-nav |
| **Badges** | `/badges/` | not started | badge detail | Pursuit home, sub-nav |
| **Milestones** | `/milestones/` | exists (legacy, to rebuild) | — | sub-nav |
| **Titles** | `/titles/` | exists (legacy, to rebuild) | Pursuit home | Pursuit home, sub-nav |

> **Conversion note:** the Logbook page **was converted directly into The Lab** (`/my-pursuit/logbook/` 301s to `/my-pursuit/lab/`) — the Logbook was always the element-identity page wearing a placeholder name. There is no separate Logbook. The "where you're at" overview role is a **fresh concern for the Pursuit home (`/`)**, assembled last, which previews + links to The Lab, Research Panel, and Badges.

Deferred within Pursuit: **Star Chart**, **Quests** (Phase 2/3).

### Browse (`/games/`) — discovery
Existing hub, unchanged by the gamification shift. Games, trophies, companies, franchises, genres, themes, engines, recently-added, flagged.

### Community (`/community/`) — social
Existing hub, unchanged. Reviews, profiles, challenges, lists, leaderboards.

## Resolved IA questions

- **Logbook vs The Lab** (resolved 2026-06-16) — **The Lab is its own destination page.** The "Logbook" was really the personal overview hub, which is the **Pursuit home (`/`)** — so there is no separate Logbook page; that role folds into `/`. This *removes* a page rather than adding one: destinations (Lab, Research Panel, Badge album) are their own pages, and `/` is the single "where you're at" hub that previews + links to them.
- **Research Panel home** (resolved 2026-06-16) — its **own page** (`/my-pursuit/research-panel/`), a Pursuit sub-nav slot; linked in from the Pursuit home.

## Open IA questions (resolve as we build)

- **URL convention sweep** — the stable skeleton calls for flat top-level Pursuit URLs (`/lab/`, `/badges/`), but pages are currently shipping under `/my-pursuit/*` (logbook, research-panel). Do the flat-URL migration as one sweep (with reverse-name 301s) rather than per page; new pages use `/my-pursuit/*` until then for sibling consistency.
- **Near-term sub-nav shape** — which items show in Phase 1 vs deferred.
