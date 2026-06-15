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
Home of the Pursuer, the two rails (**Badges** = collection, **Contracts/Elements** = leveling), and identity.

| Page | URL | Status | Links OUT to | Linked IN from |
|------|-----|--------|--------------|----------------|
| **Pursuit home** | `/` | identified; **build LAST** (most connective page) | Badges, Logbook, Research Panel, Browse, Community, Stats | navbar logo, redirects |
| **Logbook** | `/logbook/` | **in design** (first build target) | full Badge gallery, Titles, element detail, (Research Panel?) | Pursuit home, avatar dropdown |
| **Badges** | `/badges/` | not started | badge detail, Logbook | Pursuit home, sub-nav |
| **Research Panel** | TBD | not started | Contract/Project detail, accept endpoint | Pursuit home, Logbook? |
| **Milestones** | `/milestones/` | exists (legacy, to rebuild) | — | sub-nav |
| **Titles** | `/titles/` | exists (legacy, to rebuild) | Logbook | Logbook, sub-nav |

Deferred within Pursuit: **Star Chart**, **Quests** (Phase 2/3).

### Browse (`/games/`) — discovery
Existing hub, unchanged by the gamification shift. Games, trophies, companies, franchises, genres, themes, engines, recently-added, flagged.

### Community (`/community/`) — social
Existing hub, unchanged. Reviews, profiles, challenges, lists, leaderboards.

## Open IA questions (resolve as we build)

- **Logbook vs The Lab** — is "The Lab" the whole identity page, or the elements zone within the Logbook? Decide by feel while building the Logbook.
- **Research Panel home** — its own Pursuit sub-nav slot, a Logbook section, or paired near Badges? Decide when building it.
- **Near-term sub-nav shape** — which items show in Phase 1 vs deferred.
