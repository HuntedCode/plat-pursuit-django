# Jobs Catalog — IGDB Genre/Theme Taxonomy Data & Design

> **Purpose.** Re-derive the gamification **Job catalog** from PlatPursuit's *actual*
> IGDB enrichment (genres + themes), replacing the legacy pre-IGDB 25-job list.
> This is the open "job catalog redesign" thread from `docs/design/gamification-plan.md`
> (jobs = auto-derived play-style specializations; group into 4–8 categories;
> XP ticks per-trophy / per-stage / per-badge-tier). Treat the old jobs as a loose
> reference, not a constraint — the data drives this.

## Data source

`python manage.py report_concept_taxonomy`, run on **prod 2026-06-11**.

**Scope (all three hold):** anchored (`anchor_migration_completed_at` set) + non-shovelware
(≥1 game with `shovelware_status` clean/manually_cleared) + developer-attributed
(`ConceptCompany.is_developer` or `is_porting`).

**Totals:** 11,677 concepts · 23 genres · 22 themes · 1,370 distinct genre-sets · 870 theme-sets.
Gaps: 50 with no genre, 1,061 with no theme.

## Genre marginals (% of the 11,677)

| % | Genre | | % | Genre |
|---|---|---|---|---|
| 52.3 | Adventure | | 6.6 | Fighting |
| 40.6 | Indie | | 6.5 | Racing |
| 22.8 | Arcade | | 4.8 | Visual Novel |
| 22.6 | Role-playing (RPG) | | 3.4 | Turn-based strategy |
| 20.8 | Simulator | | 3.4 | Point-and-click |
| 20.3 | Puzzle | | 3.0 | Card & Board Game |
| 19.8 | Shooter | | 3.0 | Music |
| 16.0 | Strategy | | 2.9 | Tactical |
| 15.8 | Platform | | 1.2 | Real Time Strategy |
| 9.2 | Sport | | 0.8 | Quiz/Trivia |
| 7.0 | Hack & slash/Beat 'em up | | 0.4 | Pinball |
| | | | 0.1 | MOBA |

## Theme marginals (% of the 11,677)

| % | Theme | | % | Theme |
|---|---|---|---|---|
| 64.8 | Action | | 4.9 | Historical |
| 21.3 | Fantasy | | 4.2 | Drama |
| 16.5 | Science fiction | | 3.6 | Sandbox |
| 9.7 | Comedy | | 3.4 | Stealth |
| 9.7 | Horror | | 3.1 | Warfare |
| 7.6 | Kids | | 2.8 | Romance |
| 5.8 | Survival | | 2.6 | Non-fiction |
| 5.8 | Mystery | | 2.5 | Thriller |
| 5.8 | Open world | | 2.0 | Educational |
| 5.4 | Party | | 1.9 | Business |
| | | | 0.4 | Erotic · 0.2 4X |

## Top genre combinations (of 1,370)

Adventure+RPG (346) · Arcade+Shooter (322) · Shooter (296) · Adventure+Indie+Puzzle (281) ·
Adventure (235) · Adventure+Indie (208) · Adventure+Indie+Platform+Puzzle (203) ·
Adventure+Shooter (196) · Sport (171) · Adventure+Platform (170) · Simulator+Sport (161) ·
Arcade (159) · Adventure+Indie+Platform (158) · RPG (156) · Adventure+Visual Novel (155) ·
Adventure+Puzzle (152) · Adventure+Indie+RPG (146) · Arcade+Indie+Shooter (129) …

## Top genre × theme co-occurrence (of 450)

Adventure×Action (4,237) · Indie×Action (3,245) · Arcade×Action (2,276) · Shooter×Action (2,229) ·
Adventure×Fantasy (1,793) · RPG×Action (1,777) · Platform×Action (1,674) · RPG×Fantasy (1,344) ·
Puzzle×Action (1,236) · Indie×Fantasy (1,040) · Adventure×Sci-fi (1,035) · Simulator×Action (1,019) ·
Strategy×Action (912) · Adventure×Horror (905) · Shooter×Sci-fi (867) · Hack&slash×Action (806) …

## Key insights for catalog design

1. **"Action" theme is near-universal (65%)** — it's noise as a job signal; most games are
   "Action". Themes are mostly *flavor*, not primary job axes. Exceptions worth using:
   Fantasy, Sci-fi, Horror, Survival, Mystery, Stealth, Open world (distinctive enough).
2. **"Adventure" (52%) and "Indie" (41%) are too broad to be distinct jobs.** Indie is a
   production-scale tag, not a play-style — *not a job*. Adventure is a catch-all; candidate
   for a baseline "Adventurer" everyone levels, or excluded as a job-definer.
3. **The job spine is the mid-tier genres** (good population, clear identity): RPG, Shooter,
   Simulator, Puzzle, Strategy, Platform, Arcade, Sport, Fighting, Racing, Hack&slash,
   Visual Novel, Music, Card & Board.
4. **Rare genres must merge** into a parent job (too few games to level on their own):
   Turn-based strategy / Real-time strategy / Tactical / MOBA → Strategy; Point-and-click →
   Adventure/Detective; Pinball / Quiz → Arcade or a "Party/Casual" job.
5. **Most games are multi-genre** (avg ~1.5–2). Cleanest rule: a game contributes XP to *all*
   its matching jobs (multi-job), so "Action RPG Shooter" levels Roleplayer + Gunslinger.
6. **Modes not yet pulled.** The plan also names IGDB *modes* (single/multi/co-op) as a job
   axis. Not in this dataset; a future `report_concept_taxonomy` extension if we want
   mode-based jobs (e.g. Co-op Partner, Competitor).

## Straw-man catalog (DRAFT — for brainstorming, not locked)

Evocative names (per the plan's Driver/Detective style), grouped into candidate categories.
Detection = "has this genre/theme". ~22 jobs:

- **Combat:** Gunslinger (Shooter) · Brawler (Fighting) · Slayer (Hack & slash/Beat 'em up)
- **Exploration:** Adventurer (Adventure — baseline?) · Acrobat (Platform) · Survivalist (Survival theme) · Wanderer (Open world theme)
- **Mind:** Puzzler (Puzzle) · Tactician (Strategy + TBS/RTS/Tactical/MOBA) · Detective (Mystery theme + Point-and-click)
- **Story:** Roleplayer (RPG) · Storyteller (Visual Novel) · (Dramatist? Romance/Drama themes)
- **Simulation:** Tycoon (Simulator + Business theme) · Architect (Sandbox theme)
- **Speed & Sport:** Driver (Racing) · Athlete (Sport) · Maestro (Music/rhythm)
- **Casual/Arcade:** Arcader (Arcade) · Card Shark (Card & Board) · Party Host (Party theme + Quiz/Pinball)
- **Flavor:** Horror Survivor (Horror theme)

## Open design decisions

1. Jobs primarily genre-driven, themes as flavor/secondary — or a handful of theme-jobs (Horror, Survival)?
2. Broad genres: make "Adventurer" a baseline job, or exclude Adventure/Indie as job-definers?
3. Rare-genre merges: confirm the parent mappings (TBS/RTS/MOBA/Tactical → Tactician, etc.).
4. Multi-job XP (game → all matching jobs) vs a single "primary" job per game?
5. The 4–8 categories for the radar (Combat / Exploration / Mind / Story / Simulation / Sport / Casual …).
6. Job count target (~20–24 here) and naming convention (evocative class names).
7. Pull IGDB *modes* too (extend the command) for mode-based jobs — now or later?
