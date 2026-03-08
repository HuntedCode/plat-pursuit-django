# PlatPursuit Gamification System — Design Document

## Context
PlatPursuit has gamification infrastructure already in place (`StatType`, `StageStatValue`, `ProfileGamification`) that isn't being fully utilized. The goal is to design a **premium-only** RPG-style system that sits on top of the existing badge XP system without changing badge XP at all.

**Key constraint:** Badge XP is universal (all users) and must remain untouched. The gamification layer is additive and premium-only.

---

## What Already Exists (in code)

### Active systems:
- **Badge XP** — 250 XP/stage (Bronze/Gold), 75 XP/stage (Silver/Plat), +3,000 XP per badge earned
- **`ProfileGamification`** — Denormalized `total_badge_xp`, `series_badge_xp`, `total_badges_earned`
- **`UserBadgeProgress`** — Per-badge completed_concepts tracking
- **Signals** in `trophies/signals.py` — Auto-update `ProfileGamification` on badge progress/earn/revoke
- **`bulk_gamification_update()`** in `trophies/services/xp_service.py` — Context manager for batching updates during sync
- **Leaderboards** in `trophies/services/leaderboard_service.py` — Earners, progress, XP leaderboards

### Built but unused:
- **`StatType`** model — Only `badge_xp` record exists. Has `icon`, `color`, `display_order` fields
- **`StageStatValue`** model — Per-stage, per-stat, per-tier values (bronze/silver/gold/plat). Zero data populated
- **`StageStatValueAdmin`** — Admin UI registered and ready

### Key files:
- `trophies/models.py` — ProfileGamification (L1001), StatType (L1036), StageStatValue (L1061), Stage (L1108)
- `trophies/services/xp_service.py` — XP calculation, bulk update context manager
- `trophies/signals.py` — Gamification signal handlers
- `trophies/util_modules/constants.py` — XP constants (BRONZE_STAGE_XP=250, etc.)

---

## The Vision: "Hunter Profile"

### Dual Leveling System

#### Character Level (Badge XP → Level — ALL users, premium upsell)
- Derived from existing `total_badge_xp` on `ProfileGamification`
- XP thresholds define levels (curve TBD)
- **Visible to all users** as a taste of the system → upsell to premium for full Hunter Profile
- **Does not change how badge XP is calculated** — purely a display layer
- Acts as the hook: "You're Level 23... unlock your full Hunter Profile to see your P.L.A.T.I.N.U.M. stats"

#### Profession Level (Job XP sum — premium only)
- Sum of all individual job levels
- Example: Level 25 Driver + Level 10 Detective = Profession Level 35
- Runescape "total level" concept
- Premium-exclusive

---

### System 1: P.L.A.T.I.N.U.M. Stats (8 Primary Stats) — Premium Only

| Stat | Slug | Thematic Fit |
|------|------|-------------|
| **P**ower | `power` | Raw strength, combat-heavy |
| **L**uck | `luck` | RNG, rare finds, gacha |
| **A**gility | `agility` | Speed, reflexes, action |
| **T**oughness | `toughness` | Endurance, grinding, survival |
| **I**ntelligence | `intelligence` | Puzzle, strategy, knowledge |
| **N**avigation | `navigation` | Exploration, open-world, collectibles |
| **U**tility | `utility` | Versatility, jack-of-all-trades |
| **M**agic | `magic` | Fantasy/supernatural |

**How it works:**
- Admin assigns **1-3 stats** per stage via `StageStatValue` (model already exists)
- Points scale by tier (bronze_value, silver_value, gold_value, platinum_value fields exist)
- Stats accumulate as users complete stages
- Displayed as a **radar chart** on the Hunter Profile page

**Existing infrastructure:**
- `StatType` model → create 8 records for P.L.A.T.I.N.U.M.
- `StageStatValue` model → already has per-tier values, just needs data
- `StageStatValueAdmin` → admin UI already registered

---

### System 2: Jobs (25 Professions) — Premium Only

| # | Job | Theme |
|---|-----|-------|
| 1 | Driver | Racing/Driving/Vehicles |
| 2 | Detective | Puzzle Solving |
| 3 | Athlete | Platforming |
| 4 | Thief | Stealth |
| 5 | Professional | RPG |
| 6 | Mercenary | Action/Combat |
| 7 | Boxer | Fighting |
| 8 | Hacker | Tech |
| 9 | Swashbuckler | Pirate/Sea |
| 10 | Dungeoneer | MMOs |
| 11 | Strategist | Strategy |
| 12 | Friend | Co-Op |
| 13 | Model Citizen | Community |
| 14 | Explorer | Open World |
| 15 | Marksman | Shooter |
| 16 | Spirit Healer | Demon/Horror |
| 17 | Architect | Building |
| 18 | Survivalist | Survival |
| 19 | Curator | Collect-a-thon |
| 20 | Archivist | Story |
| 21 | Spell Caster | Fantasy |
| 22 | Competitor | Competitive |
| 23 | Musician | Rhythm |
| 24 | Casual | Casual |
| 25 | Scientist | Sci-Fi |

**How it works:**
- Admin assigns **2 jobs** per stage
- Completing a stage grants **flat XP** to both assigned jobs (amount TBD)
- Each job has its own level (XP thresholds TBD)
- Sum of all job levels = **Profession Level**

---

### Profile Display
- **Dedicated Hunter Profile page** — full RPG treatment with radar chart, job grid, both level numbers
- Surface key elements on other pages (profile cards, badge detail, share cards)
- Free users see Character Level + locked/teaser Hunter Profile → premium upsell

---

## Decisions Made

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Badge XP changes | None | Universal system stays untouched |
| Premium scope | Stats + Jobs + Profession Level are premium-only | Character Level visible to all as upsell hook |
| Stat assignment | Manual by admin, 1-3 per stage | Full creative control over game feel |
| Job assignment | Manual by admin, 2 per stage | Mirrors stats approach |
| Job XP method | Flat per stage (for now) | Simple, predictable. Open to tier-scaling later |
| Level cap | Uncapped for now | Capping at 99 is desirable but needs a non-gimmicky engagement XP system first |
| Stat count | 8 (P.L.A.T.I.N.U.M.) | On-brand acronym, good radar chart shape |
| Job count | 25 | Comprehensive genre coverage |
| Profile location | Dedicated page + bits on other pages | Full RPG treatment deserves its own page |
| Social/sharing | Open system, not walled off | PlatPursuit is community-first — gamification should encourage sharing (social media, Discord, friends) just like the rest of the site |

---

## Core Design Principles

### Community-First / Open System
The gamification system must NOT be a closed-off or isolated feature. PlatPursuit already encourages users to share achievements on social media, with friends, and in the Discord community. The gamification layer should be consistent with this philosophy:
- Stats, levels, and job progress should be **shareable** (share cards, social media, Discord bots)
- Explorer profiles / Hunter Profiles should be **publicly viewable** (not hidden behind premium for viewing — only earning is premium)
- Progression milestones should be **celebration-worthy** and easy to share
- The system should feel like a natural extension of the existing community, not a separate walled garden
- Design social features (comparing stats, leaderboards, sharing) with openness in mind from the start, even if implementation is deferred to post-MVP

---

## Open Design Threads

### 1. Engagement XP (the "level 99 enabler")
The big open question: can we find **non-gimmicky** ways for users to earn job XP through platform engagement (not just trophy-based stage completion)?

**Ideas to explore:**
- Writing comments on game pages tagged with relevant jobs → trickle XP to those jobs
- Creating/curating checklists → Archivist/Curator XP
- Rating games → small XP to the game's associated job tags
- Community contributions (helping others, guides) → Model Citizen XP
- Completing checklist items → XP to the checklist game's jobs

**The challenge:** Must feel natural and rewarding, not like a daily chore list or engagement manipulation. Needs more thought before committing.

### 2. Class/Archetype System
- Auto-derived from highest stat? Highest job? Both?
- User-selected "main class" or always auto-calculated?
- Could be a v2 feature layered on top once stats/jobs are live

### 3. Leveling Curves
- Character Level: What XP thresholds? Linear, exponential, or custom curve?
- Job levels: Same question. Should early levels come fast and later ones be harder?
- Need to analyze actual badge XP distribution across users to design a meaningful curve

### 4. Job XP Amounts
- How much flat XP per stage? Needs to feel proportional to the effort of completing a stage
- Should different badge types (series vs collection vs megamix) grant different job XP?

### 5. Admin Workflow at Scale
- Potentially hundreds of stages need stat + job assignments
- Bulk assignment tools or management commands would save time
- Could pre-populate based on game genres/tags then let admin refine

### 6. Data Models (detailed)
New models needed:
- `Job` — name, slug, icon, description, color
- `StageJob` — M2M through model (stage, job) or just M2M field on Stage
- `UserJobProgress` — profile, job, total_xp, level (denormalized)
- `DiscoveryStar` — profile, star_type, source_type, source_id, position_x/y, color, label, earned_at, icon_slug
- `UserConstellation` — profile, name, description, stars (M2M to DiscoveryStar), edges (JSONField), created_at
- Extend `ProfileGamification` with stat totals (8 integer fields) + profession_level

Reuse existing:
- `StatType` → 8 new records
- `StageStatValue` → populate with data
- Signal pattern from `trophies/signals.py`
- Bulk update pattern from `xp_service.py`

---

## Expanded Feature Set

### Unified Space Explorer Theme

**PlatPursuit is a colony of space explorers.** Each user is an explorer charting the stars.

#### Thematic Mapping
| Concept | Space Theme Name | Description |
|---------|-----------------|-------------|
| PlatPursuit community | **The Colony** | Home base of all explorers |
| Users/members | **Explorers** | Space-faring adventurers |
| Trophies/games | **Stars** | Points of light to discover |
| Badge series | **Constellations** | Star patterns to chart |
| Badge progress | **Star Chart** | Map of your explored space |
| Hunter Profile | **Explorer's Logbook** | Your personal expedition record |
| Character Level | **Explorer Rank** | Overall experience/standing |
| Profession Level | **Skill Mastery** | Combined specialization depth |
| Store | **Colony Exchange** / **Stellar Market** | Where explorers trade |
| Quests | **Missions** / **Expeditions** | Tasks assigned to explorers |

#### Job Space Context
All 25 jobs map naturally to colony roles:
- Driver → Pilot, vehicle specialist
- Detective → Mystery solver, anomaly investigator
- Mercenary → Combat specialist, colony defender
- Hacker → Digital warfare, alien tech breaker
- Explorer → Pathfinder, first contact specialist
- Scientist → Research, alien biology
- Spell Caster → Cosmic energy, void magic, psionics
- Spirit Healer → Paranormal phenomena, void entities
- Architect → Habitat builder, station engineer
- (all 25 map naturally — every colony needs all roles)

---

### Currency: Stellar Marks with Trophy Denominations

**4 denominations** mirroring trophy types (like WoW gold/silver/copper):

| Denomination | Symbol | Base Value | Theme |
|-------------|--------|------------|-------|
| **Bronze Mark** | 🥉 | 1 | Common, small rewards |
| **Silver Mark** | 🥈 | 10 | = 10 Bronze |
| **Gold Mark** | 🥇 | 100 | = 10 Silver = 100 Bronze |
| **Platinum Mark** | 💠 | 1,000 | = 10 Gold = 1,000 Bronze |

**Display:** "2P 5G" = 2 Platinum + 5 Gold = 2,500 base
**Backend:** Single integer (Bronze Marks). Denominations are display-only.
**Psychology:** "Earned a Platinum Mark!" feels like earning a platinum trophy.

#### Earning in Denominations
| Source | Reward |
|--------|--------|
| Easy daily quest | 15-25 Bronze Marks |
| Medium daily quest | 3-5 Silver Marks |
| Hard daily quest | 1 Gold Mark |
| Weekly quest | 2-3 Gold Marks |
| Badge earned (Bronze) | 5 Gold Marks |
| Badge earned (Platinum) | 2 Platinum Marks |
| Epic quest | 5-20 Platinum Marks |

#### Store Pricing
| Rarity | Base Price | In Marks |
|--------|-----------|----------|
| Common | ~500 | 5G |
| Uncommon | ~1,500 | 1P 5G |
| Rare | ~5,000 | 5P |
| Epic | ~15,000 | 15P |
| Legendary | ~50,000 | 50P |

---

### PSN Avatar + Customizable Frame System (Premium Only)

**Core concept:** Users already have a PSN avatar they've chosen — we enhance it with unlockable frames, effects, and badges that reflect their gamification progression. No custom artwork needed; everything is CSS/SVG.

#### How It Works
- PSN avatar (`<img>`) sits at the center
- CSS/SVG layers wrap around it: frame shape, border style, glow effects, corner badges
- Progression unlocks new frame options → users customize their framed avatar
- The framed avatar appears everywhere: comments, leaderboards, profile cards, share images

#### Frame Layer System
| Order | Layer | Implementation | Examples |
|-------|-------|---------------|---------|
| 1 | Background Aura | CSS `box-shadow` / radial gradient | Stat-colored glow, pulsing energy |
| 2 | Frame Shape | CSS `clip-path` / SVG `<clipPath>` | Circle, hexagon, diamond, shield, star |
| 3 | Frame Border | CSS `border` / SVG `<path>` with stroke | Solid, gradient, animated shimmer |
| 4 | PSN Avatar | `<img>` tag, clipped to frame shape | User's existing PSN profile picture |
| 5 | Corner Badges | Absolutely positioned SVG icons | Level badge, top job icon, streak flame |
| 6 | Nameplate | CSS styled `<div>` below avatar | Title, level, custom colors |

#### Frame Shapes (Unlockable)
| Shape | Unlock | CSS/SVG |
|-------|--------|---------|
| Circle | Default (free) | `border-radius: 50%` |
| Rounded Square | Character Level 5 | `border-radius: 12px` |
| Hexagon | Character Level 15 | `clip-path: polygon(...)` |
| Diamond | Character Level 30 | `clip-path: polygon(50% 0%, 100% 50%, 50% 100%, 0% 50%)` |
| Shield | Reach Toughness 100 | SVG `<path>` |
| Star | Complete any badge series | SVG `<path>` |
| Octagon | Reach Profession Level 50 | `clip-path: polygon(...)` |

#### Frame Border Styles (Unlockable / Purchasable)
| Style | Rarity | Implementation |
|-------|--------|---------------|
| Thin solid (Steel) | Common | `border: 2px solid #8b9bb4` |
| Medium solid (colored) | Common | `border: 3px solid {stat_color}` |
| Double line | Uncommon | `border: double 4px` |
| Gradient sweep | Uncommon | CSS `border-image: linear-gradient(...)` |
| Animated pulse | Rare | CSS `@keyframes` border-color cycle |
| Animated shimmer | Epic | CSS `@keyframes` with moving gradient |
| Animated particle ring | Legendary | Canvas particle system around frame |

#### Aura Effects (Stat-Themed, Unlock at Thresholds)
| Stat | Effect | CSS Implementation | Thresholds |
|------|--------|-------------------|------------|
| Power | Red energy glow | `box-shadow: 0 0 Npx #e43b44` | 100/250/500/1000 (increasing spread) |
| Luck | Golden shimmer | Animated `box-shadow` with gold gradient | 100/250/500/1000 |
| Agility | Speed lines | Pseudo-elements with CSS motion blur | 100/250/500/1000 |
| Toughness | Armor plating glow | Copper `box-shadow` with hard edges | 100/250/500/1000 |
| Intelligence | Data stream | SVG animated dashes around frame | 100/250/500/1000 |
| Navigation | Orbital ring | CSS animated rotating border | 100/250/500/1000 |
| Utility | Multi-tool halo | Multiple `box-shadow` layers | 100/250/500/1000 |
| Magic | Void wisps | CSS animated gradient with purple glow | 100/250/500/1000 |

Users with multiple high stats get blended auras (CSS multiple `box-shadow` values).

#### Corner Badges
| Position | Content | Implementation |
|----------|---------|---------------|
| Bottom-right | Character Level number | SVG circle with number overlay |
| Top-left | Top Job icon | Library SVG icon in job's color |
| Top-right | Streak flame (if active) | CSS animated flame gradient |
| Bottom-left | Premium indicator | Small star/diamond SVG |

#### Nameplate / Title Display
Below the framed avatar:
- **Title**: Unlocked via jobs, epic quests, mastery milestones (e.g., "Legendary Detective")
- **Level display**: "Lv. 47 Explorer"
- **Profession Level**: Smaller text showing combined job mastery
- Customizable nameplate colors (from currency store)
- Animated text effects at higher rarities (shimmer, glow)

#### Display Sizes
| Context | Avatar Size | Frame Padding | Total Size |
|---------|-------------|--------------|------------|
| Explorer's Logbook (hero) | 128x128 | 16px | ~160x160 |
| Profile card / sidebar | 80x80 | 8px | ~96x96 |
| Comment avatar | 40x40 | 4px | ~48x48 |
| Leaderboard row | 36x36 | 4px | ~44x44 |
| Share images / OG tags | 128x128 | 16px | ~160x160 |

#### Server-Side Rendering (Share Images / OG Tags)
- Pillow composites: PSN avatar image + frame overlay + badges → single PNG
- Frame shapes rendered as Pillow polygon masks
- Border effects rendered as Pillow draws
- Aura effects simplified to solid glow for static images

#### Frame Customization Data Model (Conceptual)
```
FrameCosmetic:
  - name, slug, description
  - category (shape/border/aura/nameplate)
  - rarity (common/uncommon/rare/epic/legendary)
  - css_class or svg_data (the visual definition)
  - unlock_type (level/stat/job/quest/store)
  - unlock_requirement (JSON: {type: "character_level", value: 15})
  - mark_cost (if store-purchasable)
  - is_default

UserFrameConfig:
  - profile (FK, OneToOne)
  - equipped_shape (FK to FrameCosmetic, category=shape)
  - equipped_border (FK to FrameCosmetic, category=border)
  - equipped_aura (FK to FrameCosmetic, category=aura)
  - equipped_nameplate_style (FK to FrameCosmetic, category=nameplate)
  - custom_title (CharField — selected from unlocked titles)
  - created_at, updated_at
```

---

### Color Palette (Design System)

Colors from the original space explorer palette, repurposed for CSS/SVG use. No longer a pixel art constraint — now a design system for consistent theming.

**Primary Accent Colors:**
```
Red:      #8b1a2b → #e43b44 → #f77676 → #ffc0c0
Blue:     #0a3d6b → #0099db → #5cc8f5 → #b0e8ff
Green:    #1a5c2e → #3e8948 → #63c74d → #a8e86b
Orange:   #9b4a00 → #f77622 → #feae34 → #fee761
Purple:   #3d1c54 → #7b3fa0 → #b55088 → #e090c0
```

**Metallic / Tech:**
```
Steel:    #4a5568 → #8b9bb4 → #c0cbdc
Gold:     #8b6914 → #d4a017 → #fedc5a
Copper:   #6b3a1a → #b07040 → #d4a070
```

**Space / Glow Effects:**
```
Cyan Glow:    #0a8ea0 → #2ce8f5 → #b0f8ff
Void Purple:  #3a1850 → #6b2fa0 → #b070e8
Neon Green:   #1a6b30 → #40e850 → #90ff90
Hot Pink:     #8b1040 → #ff0066 → #ff80b0
Star White:   #c8c8e0 → #e8e8ff → #ffffff
```

**Background / Deep Space:**
```
#0a0a14 → #141428 → #1a1a3c → #2e1a3c
```

**Tier ↔ Color Mapping:**
- Bronze → Copper/Orange ramp
- Silver → Steel ramp
- Gold → Gold ramp
- Platinum → Cyan Glow / Star White ramp

**Usage:** These colors are used as CSS custom properties (`--color-power: #e43b44`), SVG fill/stroke values, and Canvas draw colors. They ensure visual consistency across all gamification UI.

---

### Art Direction: SVG/CSS/Canvas (Zero Custom Artwork)

**Key decision:** All visual elements are built with SVG, HTML/CSS, and Canvas. No pixel art, no PNGs, no commissioned artwork. Everything ships with code alone.

**Aesthetic:** Clean sci-fi dashboard / starship HUD — geometric, sharp, data-driven. Fits the space explorer theme naturally.

#### Icon System — Library-Based SVGs

**Source:** Existing open-source icon library (Lucide, Heroicons, or Phosphor Icons — MIT licensed). Consistent stroke style, huge selection, infinitely scalable.

Icons are colored via CSS using the color assignments below. Stored as inline SVG or referenced via `<use>` for cacheability.

**P.L.A.T.I.N.U.M. Stat Icons (8):**
| Stat | Library Icon Name (Lucide) | Color | Hex |
|------|---------------------------|-------|-----|
| Power | `sword` / `zap` | Red | `#e43b44` |
| Luck | `clover` / `dice-5` | Gold | `#d4a017` |
| Agility | `wind` / `feather` | Green | `#63c74d` |
| Toughness | `shield` / `mountain` | Copper | `#b07040` |
| Intelligence | `brain` / `lightbulb` | Blue | `#0099db` |
| Navigation | `compass` / `map` | Orange | `#f77622` |
| Utility | `wrench` / `settings` | Steel | `#8b9bb4` |
| Magic | `sparkles` / `wand` | Void Purple | `#6b2fa0` |

**Job Icons (25):**
| # | Job | Library Icon | Color | Hex |
|---|-----|-------------|-------|-----|
| 1 | Driver | `steering-wheel` / `car` | Orange | `#f77622` |
| 2 | Detective | `search` / `magnifying-glass` | Blue | `#0099db` |
| 3 | Athlete | `footprints` / `medal` | Green | `#63c74d` |
| 4 | Thief | `eye-off` / `mask` | Copper | `#b07040` |
| 5 | Professional | `swords` / `shield` | Gold | `#d4a017` |
| 6 | Mercenary | `crosshair` / `target` | Red | `#e43b44` |
| 7 | Boxer | `hand-fist` / `dumbbell` | Red | `#e43b44` |
| 8 | Hacker | `terminal` / `code` | Cyan Glow | `#2ce8f5` |
| 9 | Swashbuckler | `anchor` / `ship` | Blue | `#0099db` |
| 10 | Dungeoneer | `key-round` / `door-open` | Void Purple | `#6b2fa0` |
| 11 | Strategist | `crown` / `layout-grid` | Blue | `#0099db` |
| 12 | Friend | `users` / `handshake` | Green | `#63c74d` |
| 13 | Model Citizen | `heart` / `star` | Gold | `#d4a017` |
| 14 | Explorer | `telescope` / `binoculars` | Orange | `#f77622` |
| 15 | Marksman | `crosshair` / `target` | Red | `#e43b44` |
| 16 | Spirit Healer | `ghost` / `flame` | Void Purple | `#6b2fa0` |
| 17 | Architect | `hammer` / `building` | Steel | `#8b9bb4` |
| 18 | Survivalist | `campfire` / `tree-pine` | Green | `#63c74d` |
| 19 | Curator | `gem` / `gallery-horizontal` | Gold | `#d4a017` |
| 20 | Archivist | `scroll` / `book-open` | Cyan Glow | `#2ce8f5` |
| 21 | Spell Caster | `wand-sparkles` / `sparkles` | Void Purple | `#6b2fa0` |
| 22 | Competitor | `trophy` / `award` | Red | `#e43b44` |
| 23 | Musician | `music` / `guitar` | Orange | `#f77622` |
| 24 | Casual | `gamepad-2` / `sofa` | Steel | `#8b9bb4` |
| 25 | Scientist | `flask-conical` / `atom` | Cyan Glow | `#2ce8f5` |

**Currency Icons (4):**
| Denomination | Icon | Color | Hex |
|-------------|------|-------|-----|
| Bronze Mark | `circle-dot` (coin shape) | Copper | `#b07040` |
| Silver Mark | `circle-dot` | Steel | `#8b9bb4` |
| Gold Mark | `circle-dot` | Gold | `#d4a017` |
| Platinum Mark | `diamond` | Cyan Glow | `#2ce8f5` |

**Quest Icons (6 tier + 6 category):**
| Icon | Library Icon | Color |
|------|-------------|-------|
| Daily quest | `sun` | `#63c74d` |
| Weekly quest | `calendar` | `#0099db` |
| Epic quest | `crown` | `#d4a017` |
| Quest complete | `check-circle` | `#63c74d` |
| Quest expired | `hourglass` | `#4a5568` |
| Reroll | `refresh-cw` | `#8b9bb4` |
| Cat: Trophy | `trophy` | `#d4a017` |
| Cat: Badge | `award` | `#6b2fa0` |
| Cat: Checklist | `clipboard-check` | `#63c74d` |
| Cat: Community | `message-circle` | `#0099db` |
| Cat: Exploration | `eye` | `#f77622` |
| Cat: Meta | `settings` | `#8b9bb4` |

**UI/System Icons (14):**
| Icon | Library Icon | Color |
|------|-------------|-------|
| Explorer Rank | `star` | `#2ce8f5` |
| Profession Level | `layers` | `#d4a017` |
| Streak flame | `flame` | `#f77622` |
| Streak Shield | `shield` | `#0099db` |
| Login bonus | `gift` | `#d4a017` |
| Store | `shopping-bag` | `#f77622` |
| Lock (premium) | `lock` | `#4a5568` |
| Milestone 25% | `star` (25% fill) | `#b07040` |
| Milestone 50% | `star` (50% fill) | `#8b9bb4` |
| Milestone 75% | `star` (75% fill) | `#d4a017` |
| Milestone 100% | `star` (full) | `#2ce8f5` |
| Completionist | `sparkles` | `#ff0066` |
| Notification | `bell` | `#d4a017` |
| XP bar | CSS gradient | `#2ce8f5` |

#### Icon Display Sizes (CSS, scalable)
| Context | Size |
|---------|------|
| Radar chart axis labels | 28px |
| Explorer's Logbook stat/job row | 24px |
| Stage reward preview | 20px |
| Quest reward display | 20px |
| Leaderboard column header | 20px |
| Admin panel / tooltips | 16px |
| Store prices / wallet | 20-24px |

#### Icon Implementation
```html
<!-- Inline SVG with color from CSS custom property -->
<svg class="gamification-icon" style="color: var(--color-power)">
  <use href="#icon-sword" />
</svg>
```

```css
.gamification-icon {
  width: 24px;
  height: 24px;
  stroke: currentColor;
  stroke-width: 2;
  fill: none;
}

/* Size variants */
.gamification-icon--sm { width: 16px; height: 16px; }
.gamification-icon--lg { width: 28px; height: 28px; }
```

#### Rarity System — CSS Borders
| Rarity | Border Style | CSS |
|--------|-------------|-----|
| Common | Solid steel | `border: 2px solid #8b9bb4` |
| Uncommon | Solid green | `border: 2px solid #3e8948` |
| Rare | Gradient blue | `border-image: linear-gradient(135deg, #0a3d6b, #5cc8f5) 1` |
| Epic | Animated purple | `@keyframes` gradient rotation with purple ramp |
| Legendary | Animated gold shimmer | `@keyframes` with gold ramp + glow `box-shadow` |

#### Constellation Map — SVG/Canvas Rendering
All map elements rendered programmatically, no image assets:
| Element | Implementation |
|---------|---------------|
| Completed star | SVG `<circle>` + CSS `box-shadow` glow in tier color |
| In-progress star | SVG `<circle>` + CSS `@keyframes` pulse animation |
| Locked star | SVG `<circle>` in `#3a3a5c` at 40% opacity |
| Optional star (Stage 0) | SVG `<polygon>` (different shape) in `#40e850` |
| Connection lines | SVG `<line>` or `<path>`, dashed for locked |
| Milestone nodes | Larger SVG `<circle>` with double border |
| Background | CSS radial gradient (deep space colors) |

#### XP Bar — CSS Only
```css
.xp-bar {
  height: 8px;
  background: #141428;
  border: 1px solid #3a3a5c;
  border-radius: 4px;
  overflow: hidden;
}
.xp-bar__fill {
  height: 100%;
  background: linear-gradient(90deg, #0a8ea0, #2ce8f5);
  transition: width 0.5s ease;
}
```

#### Color Assignments — Master Reference

All colors below reference ramps from the Master Color Palette section. "Primary hex" is the base shade used for flat fills, UI accents, and the `color` field on model records.

**P.L.A.T.I.N.U.M. Stat Colors:**
| Stat | Color Ramp | Primary Hex | Full Ramp (shadow → base → light → highlight) |
|------|-----------|-------------|-----------------------------------------------|
| Power | Red | `#e43b44` | `#8b1a2b → #e43b44 → #f77676 → #ffc0c0` |
| Luck | Gold | `#d4a017` | `#8b6914 → #d4a017 → #fedc5a` |
| Agility | Green | `#63c74d` | `#1a5c2e → #3e8948 → #63c74d → #a8e86b` |
| Toughness | Copper | `#b07040` | `#6b3a1a → #b07040 → #d4a070` |
| Intelligence | Blue | `#0099db` | `#0a3d6b → #0099db → #5cc8f5 → #b0e8ff` |
| Navigation | Orange | `#f77622` | `#9b4a00 → #f77622 → #feae34 → #fee761` |
| Utility | Steel | `#8b9bb4` | `#4a5568 → #8b9bb4 → #c0cbdc` |
| Magic | Void Purple | `#6b2fa0` | `#3a1850 → #6b2fa0 → #b070e8` |

These colors are used for: stat SVG icons, radar chart segments, stat bars, avatar frame auras (CSS `box-shadow`), and any UI element referencing a specific stat. Aura effects at stat thresholds (100/250/500/1000) use progressively more of the ramp — see "Aura Effects" table in the PSN Avatar + Frame System section.

**Job Icon Colors (all 25):**
Jobs are grouped by color family — the icon silhouette provides differentiation within a group.
| # | Job | Color Family | Primary Hex | Rationale |
|---|-----|-------------|-------------|-----------|
| 1 | Driver | Orange | `#f77622` | Speed, vehicles, flames |
| 2 | Detective | Blue | `#0099db` | Cerebral, analytical |
| 3 | Athlete | Green | `#63c74d` | Physical, outdoor, active |
| 4 | Thief | Copper | `#b07040` | Shadows, leather, stealth |
| 5 | Professional | Gold | `#d4a017` | RPG prestige, heroic |
| 6 | Mercenary | Red | `#e43b44` | Combat, danger, blood |
| 7 | Boxer | Red | `#e43b44` | Fighting, intensity |
| 8 | Hacker | Cyan Glow | `#2ce8f5` | Tech, digital, neon |
| 9 | Swashbuckler | Blue | `#0099db` | Ocean, sea, adventure |
| 10 | Dungeoneer | Void Purple | `#6b2fa0` | Dark depths, mystery |
| 11 | Strategist | Blue | `#0099db` | Cerebral, calculated |
| 12 | Friend | Green | `#63c74d` | Warmth, cooperation, growth |
| 13 | Model Citizen | Gold | `#d4a017` | Community, prestige, trust |
| 14 | Explorer | Orange | `#f77622` | Discovery, adventure, warmth |
| 15 | Marksman | Red | `#e43b44` | Precision, danger, crosshairs |
| 16 | Spirit Healer | Void Purple | `#6b2fa0` | Supernatural, eerie |
| 17 | Architect | Steel | `#8b9bb4` | Metal, construction, neutral |
| 18 | Survivalist | Green | `#63c74d` | Nature, wilderness, endurance |
| 19 | Curator | Gold | `#d4a017` | Treasure, collection, value |
| 20 | Archivist | Cyan Glow | `#2ce8f5` | Data, records, digital |
| 21 | Spell Caster | Void Purple | `#6b2fa0` | Magic, arcane, cosmic |
| 22 | Competitor | Red | `#e43b44` | Competition, intensity |
| 23 | Musician | Orange | `#f77622` | Creative, expressive, vibrant |
| 24 | Casual | Steel | `#8b9bb4` | Relaxed, neutral, chill |
| 25 | Scientist | Cyan Glow | `#2ce8f5` | Science, technology, futuristic |

**Currency Denomination Colors:**
| Denomination | Color Ramp | Primary Hex |
|-------------|-----------|-------------|
| Bronze Mark | Copper | `#b07040` |
| Silver Mark | Steel | `#8b9bb4` |
| Gold Mark | Gold | `#d4a017` |
| Platinum Mark | Cyan Glow | `#2ce8f5` |

**Quest Tier Colors:**
| Tier | Color Ramp | Primary Hex |
|------|-----------|-------------|
| Daily | Green | `#63c74d` |
| Weekly | Blue | `#0099db` |
| Epic | Gold | `#d4a017` |

**Quest Category Colors:**
| Category | Color Ramp | Primary Hex |
|----------|-----------|-------------|
| Trophy | Gold | `#d4a017` |
| Badge Progress | Void Purple | `#6b2fa0` |
| Checklist | Green | `#63c74d` |
| Community | Blue | `#0099db` |
| Exploration | Orange | `#f77622` |
| Meta/Engagement | Steel | `#8b9bb4` |

**Rarity Colors:**
| Rarity | Color Ramp | Primary Hex |
|--------|-----------|-------------|
| Common | Steel | `#8b9bb4` |
| Uncommon | Green | `#3e8948` |
| Rare | Blue | `#0099db` |
| Epic | Void Purple | `#6b2fa0` |
| Legendary | Gold | `#d4a017` |

**Constellation Map Colors:**
| Element | Color | Hex | Notes |
|---------|-------|-----|-------|
| Completed star | Star White | `#ffffff` | + tier-colored glow aura |
| In-progress star | Cyan Glow | `#2ce8f5` | Pulsing, 50-75% opacity |
| Locked star | Neutral | `#3a3a5c` | Dim, barely visible |
| Optional star (Stage 0) | Neon Green | `#40e850` | Distinct from main path |
| Connection line (active) | Star White | `#e8e8ff` | 40% opacity |
| Connection line (locked) | Deep Space | `#1a1a3c` | 30% opacity |
| Milestone 25% | Copper | `#b07040` | Bronze-tier feel |
| Milestone 50% | Steel | `#8b9bb4` | Silver-tier feel |
| Milestone 75% | Gold | `#d4a017` | Gold-tier feel |
| Milestone 100% | Cyan Glow | `#2ce8f5` | Platinum-tier feel |
| Completionist | Hot Pink | `#ff0066` | Unique, stands out |
| Map background | Deep Space | `#0a0a14` | Darkest palette color |

**Tier-Colored Glows on Constellation Stars:**
When a completed star shows its tier glow, the aura color matches the trophy tier:
| Tier Completed | Glow Color | Hex |
|---------------|-----------|-----|
| Bronze only | Copper | `#d4a070` (light copper) |
| Silver | Steel | `#c0cbdc` (light steel) |
| Gold | Gold | `#fedc5a` (light gold) |
| Platinum | Cyan Glow | `#b0f8ff` (light cyan) |
| All tiers | Star White | `#ffffff` (pure white, brightest) |

**UI Element Colors:**
| Element | Color | Hex |
|---------|-------|-----|
| XP bar fill | Cyan Glow | `#2ce8f5` |
| XP bar background | Deep Space | `#141428` |
| XP bar border | Neutral | `#3a3a5c` |
| Streak flame (active) | Orange → Gold | `#f77622` → `#d4a017` |
| Streak flame (inactive/broken) | Steel (dim) | `#4a5568` |
| Streak Shield | Blue | `#0099db` |
| Login bonus | Gold | `#d4a017` |
| Store icon | Orange | `#f77622` |
| Lock (premium) | Steel (dark) | `#4a5568` |
| Notification bell | Gold | `#d4a017` |
| Level-up celebration | Cyan Glow burst | `#2ce8f5` → `#b0f8ff` |
| Job level-up | Job's assigned color | (varies per job) |
| Stat milestone | Stat's assigned color | (varies per stat) |
| Mastery milestone | Milestone tier color | (Copper/Steel/Gold/Cyan per threshold) |

#### Radar Chart Rendering Specifications

| Property | Value | Notes |
|----------|-------|-------|
| Background shape | Octagon (8 sides, one per stat) | Matches 8 P.L.A.T.I.N.U.M. stats |
| Background fill | `#0a0a14` (Deep Space) at 80% opacity | Dark, spacey feel |
| Grid lines | `#1a1a3c` at 50% opacity | 3-4 concentric rings |
| Grid line style | 1px solid | Subtle but visible |
| Axis lines | `#3a3a5c` at 60% opacity | From center to each vertex |
| Data fill | Stat colors at 25% opacity, blended | Semi-transparent colored region |
| Data border | `#e8e8ff` (Star White) at 80%, 2px | Bright outline of the data shape |
| Data points | Stat's primary color, 6px circles | Dots at each axis value |
| Axis labels | Stat SVG icon (28px) | Positioned outside the chart |
| Stat value text | `#e8e8f0` (lightest neutral), 12px | Below each icon |
| Chart size | 280x280px on Explorer's Logbook | Scales down for profile cards |
| Implementation | Chart.js with radar type OR custom Canvas | Chart.js preferred for interactivity |

**Radar chart color blending:** When stats overlap in the filled region, each axis sector takes on its stat's color at low opacity. The overall effect is a multicolored gem shape — not a single flat color.

#### CSS Custom Properties (Design Tokens)

All gamification colors defined as CSS variables for consistent theming:

```css
:root {
  /* Stats */
  --color-power: #e43b44;
  --color-luck: #d4a017;
  --color-agility: #63c74d;
  --color-toughness: #b07040;
  --color-intelligence: #0099db;
  --color-navigation: #f77622;
  --color-utility: #8b9bb4;
  --color-magic: #6b2fa0;

  /* Rarity */
  --rarity-common: #8b9bb4;
  --rarity-uncommon: #3e8948;
  --rarity-rare: #0099db;
  --rarity-epic: #6b2fa0;
  --rarity-legendary: #d4a017;

  /* Currency */
  --currency-bronze: #b07040;
  --currency-silver: #8b9bb4;
  --currency-gold: #d4a017;
  --currency-platinum: #2ce8f5;

  /* Space / UI */
  --deep-space-1: #0a0a14;
  --deep-space-2: #141428;
  --deep-space-3: #1a1a3c;
  --neutral-dark: #3a3a5c;
  --neutral-mid: #6b6b8d;
  --neutral-light: #b0b0c8;
  --star-white: #e8e8f0;
  --cyan-glow: #2ce8f5;
}
```

#### Static File Structure (Minimal)

No PNG asset directories needed. All icons come from the icon library. Only static files are:

```
static/
├── css/
│   └── gamification.css         # All gamification styles, CSS vars, animations
├── js/
│   ├── gamification/
│   │   ├── radar-chart.js       # Chart.js radar chart for stats
│   │   ├── constellation-map.js # Canvas/SVG star chart renderer
│   │   ├── frame-customizer.js  # Avatar frame preview/equip UI
│   │   └── quest-tracker.js     # Quest progress UI
│   └── icons/
│       └── gamification-icons.svg  # SVG sprite sheet (all icons as <symbol>)
└── images/gamification/
    └── (empty — no art assets needed)
```

**SVG sprite sheet pattern:**
```html
<!-- Loaded once in base template, hidden -->
<svg style="display:none">
  <symbol id="icon-sword" viewBox="0 0 24 24">
    <path d="..." stroke="currentColor" fill="none" />
  </symbol>
  <symbol id="icon-shield" viewBox="0 0 24 24">
    <path d="..." stroke="currentColor" fill="none" />
  </symbol>
  <!-- ... all icons -->
</svg>

<!-- Usage anywhere -->
<svg class="gamification-icon" style="color: var(--color-power)">
  <use href="#icon-sword" />
</svg>
```

---

### Explorer's Logbook (formerly "Hunter Profile")

Dedicated page for each premium explorer. Two visual centerpieces: the **framed PSN avatar** and the **star chart**.

#### Page Layout
- **Explorer Card**: Framed PSN avatar (with equipped frame shape, border, aura), name, title, Explorer Rank
- **Star Chart**: Full interactive constellation map of all started badge series (the user's unique progression fingerprint — see below)
- **Core Systems Scan**: P.L.A.T.I.N.U.M. 8-stat radar chart (Chart.js)
- **Crew Manifest**: 25-job grid with levels (specializations)
- **Explorer Rank**: Character Level with XP bar
- **Skill Mastery**: Profession Level (sum of job levels)
- **Mission Log**: Recent activity (quests, stages, levels)
- **Customization Showcase**: Equipped frame, unlocked titles, earned cosmetics
- **Expedition Stats**: Lifetime numbers (stars charted, constellations mastered, marks earned)

#### Star Chart — The User's Identity Map

The star chart is the user's unique progression fingerprint. Each user's constellation pattern is different based on which badge series and stages they've completed.

**Data mapping:**
- Each **badge series** (e.g., "Resident Evil", "God of War") = one **constellation**
- Each **stage** within a series = one **star** in the constellation
- **Stage 0** (optional) = a distinct diamond-shaped star, connected with a dashed line
- A user's chart only shows series they have progress in — it grows over time

**Why it's unique per user:**
1. Different users have progress in different series → different constellations appear
2. Different completion levels → different stars glow (dim vs bright)
3. Different tier completions → different glow colors (copper, steel, gold, cyan, white)
4. The overall pattern/density tells a story: someone deep in RPGs has different constellations than a shooter fan

##### Constellation Shape Generation (Algorithmic)

Each series gets a unique, deterministic shape generated from its `series_slug`:

1. **Hash the slug** → deterministic seed number (e.g., `hash("resident-evil")` → `0x7A3F...`)
2. **Generate N points** in a bounded area using a seeded PRNG (one point per stage)
3. **Connect via Minimum Spanning Tree** — ensures every star is reachable with minimum total edge length
4. **Add 1-2 extra edges** — seeded random picks to create triangles/loops (looks more like a real constellation)
5. **Stage 0** connects to a random main-path star with a dashed line
6. **Normalize** all positions to a consistent bounding box

**Result:** Same slug always produces the same shape. Different slugs produce different shapes. Zero admin work.

```
Series A (5 stages):        Series B (8 stages):        Series C (3 stages):
    ★                          ★───★                       ★
   / \                        / \   \                     / \
  ★───★                      ★   ★   ★                   ★───★
     / \                      \ /   /
    ★   ◇                      ★───★
                                \
                                 ◇
```

##### Full Chart Layout (Force-Directed)

When showing ALL of a user's constellations on one canvas:

1. Each constellation = a node with a bounding box
2. **Repulsion force** between all constellation centers (they push apart, avoid overlap)
3. **Attraction force** toward canvas center (keeps them clustered, not flying off-screen)
4. **Run simulation** for N iterations until positions stabilize
5. **Result:** Organic, non-overlapping placement that fills the space naturally

```
┌──────────────────────────────────────────────────────┐
│                    ·  ·  ·                            │
│      ★═══★═══★                    ·                   │
│     ║         ║           ★───★                       │
│     ★    RE   ★          / GoW \                      │
│      ║       ║          ★───★───★                     │
│       ★═══★══★                                        │
│                                                       │
│         ·                  ★                           │
│              ★───★───★    /FF\                         │
│              │  Unch  │  ★    ★                        │
│              ★───★───★    \  /                         │
│   ·                        ★            ·              │
│                                                       │
│        ★──★                                           │
│       / TLoU\           ·        ·                    │
│      ★      ★                                         │
│       \    /                   ·                       │
│        ★──★                                           │
│                    ·                                   │
└──────────────────────────────────────────────────────┘
```

##### Star Rendering

| Star State | Visual | Canvas Implementation |
|------------|--------|----------------------|
| Not started | Tiny dim dot, 3px radius | `ctx.arc()` + `fillStyle: #3a3a5c` at 40% opacity |
| In progress | Medium pulsing dot, 5px | `ctx.arc()` with `#2ce8f5`, animated opacity (CSS or requestAnimationFrame) |
| Completed (Bronze only) | Bright dot + copper glow, 6px | `ctx.arc()` + radial gradient `#d4a070` |
| Completed (Silver) | Bright dot + steel glow, 6px | `ctx.arc()` + radial gradient `#c0cbdc` |
| Completed (Gold) | Bright dot + gold glow, 7px | `ctx.arc()` + radial gradient `#fedc5a` |
| Completed (Platinum) | Bright dot + cyan glow, 8px | `ctx.arc()` + radial gradient `#b0f8ff` |
| Completed (All tiers) | Brightest + white glow, 8px | `ctx.arc()` + radial gradient `#ffffff` |
| Stage 0 (optional) | Diamond shape (rotated square) | `ctx` path with 45° rotation |

**Completion = "highest tier completed for this stage"**. A stage is "completed" for a tier when the user has earned all concepts in that stage that apply to the tier (based on `required_tiers`).

##### Connection Line Rendering

| Connection State | Visual |
|-----------------|--------|
| Both stars completed | Bright line (`#e8e8ff`, 60% opacity, 1.5px width) |
| One star completed | Medium line (`#e8e8ff`, 30% opacity, 1px) |
| Neither completed | Faint line (`#1a1a3c`, 20% opacity, 0.5px) |
| To Stage 0 | Dashed line (brightness matches state) |

##### Background

- Deep space radial gradient: `#0a0a14` center → `#141428` edges
- 100-200 random tiny dots (1-2px, `#3a3a5c` at 20-40% opacity) — ambient decorative "stars"
- Optional: subtle nebula effect via overlapping radial gradients with purple/blue at very low opacity

##### Interactivity (Canvas-based)

| Action | Result |
|--------|--------|
| **Hover a star** | Tooltip: stage name, game count, tier completion indicators (4 dots colored/grey) |
| **Click a star** | Expand panel: list of games in that stage, with links to game detail pages |
| **Hover a constellation** | Highlight entire constellation, show series name + overall progress % |
| **Click constellation label** | Navigate to badge detail page for that series |
| **Scroll wheel** | Zoom in/out (for users with many constellations) |
| **Click + drag** | Pan the chart |

##### Data Required from Backend

The view passes this as JSON context:

```python
star_chart_data = {
    # Layer 2: Badge constellations
    'series': [
        {
            'series_slug': 'resident-evil',
            'display_name': 'Resident Evil',
            'stages': [
                {
                    'stage_number': 0,
                    'title': 'Bonus',
                    'is_optional': True,
                    'completion': {1: True, 2: False, 3: False, 4: False},
                    'concepts_total': 3,
                    'concepts_completed': 1,
                },
                {
                    'stage_number': 1,
                    'title': 'Origins',
                    'is_optional': False,
                    'completion': {1: True, 2: True, 3: True, 4: True},
                    'concepts_total': 2,
                    'concepts_completed': 2,
                },
                # ... more stages
            ],
            'badge_progress': {
                1: {'earned': True, 'progress': 100},
                2: {'earned': True, 'progress': 100},
                3: {'earned': False, 'progress': 60},
                4: {'earned': False, 'progress': 40},
            },
            # Layer 1: Stat nebulae — dominant stats for this series
            'dominant_stats': [
                {'slug': 'toughness', 'color': '#b07040', 'total_points': 850},
                {'slug': 'intelligence', 'color': '#0099db', 'total_points': 400},
            ],
            # Layer 3: Job assignments per stage
            'stage_jobs': {
                1: ['mercenary', 'survivalist'],
                2: ['mercenary', 'detective'],
                # stage_number: [job_slug, job_slug]
            },
        },
        # ... more series
    ],

    # Layer 3: Job levels (for pathway brightness)
    'job_levels': {
        'mercenary': 25,
        'detective': 12,
        'survivalist': 8,
        # ...
    },

    # Layer 4: Discovery stars
    'discovery_stars': [
        {
            'id': 42,
            'star_type': 'named',
            'label': 'Mercenary Lv 25',
            'x': 0.65,
            'y': 0.32,
            'color': '#e43b44',
            'icon_slug': 'crosshair',
            'earned_at': '2025-03-15',
        },
        # ...
    ],

    # Layer 5: User-plotted constellations
    'user_constellations': [
        {
            'id': 7,
            'name': 'The Hunter',
            'stars': [42, 55, 61],  # discovery star IDs
            'edges': [[42, 55], [55, 61], [61, 42]],
        },
    ],
}
```

Serialized as `JSON.parse()` in a `<script>` tag, consumed by the `StarChart` class.

##### Implementation Architecture

```
constellation-map.js
├── class StarChart
│   ├── constructor(canvas, userData)
│   ├── layoutConstellations()       // Force-directed placement
│   ├── render()                      // Main draw loop (calls layers in order)
│   ├── drawBackground()             // Layer 0: Deep space + ambient stars
│   ├── drawStatNebulae()            // Layer 1: Colored gas clouds per constellation
│   ├── drawConstellation(c)         // Layer 2: Single badge constellation
│   ├── drawJobPathways()            // Layer 3: Cross-constellation job arcs
│   ├── drawDiscoveryStars()         // Layer 4: Bonus earned stars
│   ├── drawUserConstellations()     // Layer 5: User-drawn connections
│   ├── drawOverlays()              // Layer 6: Tooltips, labels
│   ├── handleHover(x, y)           // Detect all layers
│   ├── handleClick(x, y)           // Detect all layers
│   └── handleZoomPan()             // Scroll + drag
├── class Constellation
│   ├── constructor(seriesSlug, stages, progress)
│   ├── generatePoints(seed)         // Deterministic star positions
│   ├── computeEdges()               // MST + extra connections
│   ├── getStarState(stageNumber)    // Completion color/size lookup
│   └── getDominantStats()          // Sum StageStatValues for nebula
├── class DiscoveryStar
│   ├── constructor(data)
│   ├── render(ctx)
│   └── containsPoint(x, y)
├── class UserConstellation
│   ├── constructor(data, discoveryStars)
│   ├── render(ctx)
│   └── renderLabel(ctx)
├── class ConstellationDrawer        // Drawing mode UI
│   ├── enter()
│   ├── selectStar(star)
│   ├── connectStars(a, b)
│   ├── undo()
│   ├── preview()
│   └── save()
└── function seededRandom(seed)      // Deterministic PRNG
```

##### Display Contexts

| Context | Canvas Size | Features |
|---------|-------------|----------|
| Explorer's Logbook (main) | 800x500px | Full interactivity: hover, click, zoom, pan |
| Profile card (viewing others) | 200x120px | Static mini-render, no interactivity |
| Share image (Pillow server-side) | 800x500px | Static PNG, user name + stats overlay |
| Badge detail page | 400x300px | Single constellation highlighted, others dimmed |

##### Sharing

"Share your Star Chart" button:
1. Client sends request to server
2. Server renders star chart via Pillow (replicates Canvas logic in Python)
3. Overlays user name, Explorer Rank, total stats at bottom
4. Returns PNG for download / social sharing
5. Same infrastructure as existing recap share cards

The star chart is genuinely unique per user — it's like Spotify Wrapped for trophy hunting.

##### Layered Star Chart: Stats, Jobs, and Discovery

The star chart evolves from a single-layer badge map into a multi-layered living canvas. Each layer is independent, renders on top of the previous, and can ship separately. No layer modifies the core badge constellation logic.

**Rendering Pipeline (7 layers, back to front):**
```
Layer 0: Background          — Static starfield, deep space gradient
Layer 1: Stat Nebulae        — Colored gas clouds showing stat dominance
Layer 2: Badge Constellations — Existing core (series = constellation, stage = star)
Layer 3: Job Pathways         — Cross-constellation threads connecting shared jobs
Layer 4: Discovery Stars      — Bonus stars earned through engagement/quests/mini-games
Layer 5: User Constellations  — User-drawn connections between their discovery stars
Layer 6: Interactive Overlays — Hover tooltips, click panels, labels
```

###### Layer 1: Stat Nebulae (P.L.A.T.I.N.U.M. Visual Integration)

Each badge constellation sits in a region of space that glows with the colors of its dominant stats. A cluster of combat-heavy series creates a red Power nebula; puzzle series create a blue Intelligence nebula. As users complete stages, the nebulae intensify where they're strong.

**How it works:**
1. Each stage has `StageStatValue` records assigning 1-3 stats with per-tier point values
2. For each constellation, sum the stat points across all its stages → dominant stat(s)
3. Render a radial gradient behind the constellation using the dominant stat's color at low opacity
4. Intensity scales with completion: 0% complete = barely visible, 100% = vibrant

| Completion | Nebula Opacity | Spread |
|------------|---------------|--------|
| 0-25% | 3-5% opacity | Tight (1.2x constellation bounds) |
| 25-50% | 8-12% opacity | Medium (1.5x bounds) |
| 50-75% | 15-20% opacity | Wide (1.8x bounds) |
| 75-100% | 25-30% opacity | Full (2x bounds) |

**Multi-stat blending:** If a constellation has 2+ dominant stats, their colors blend via overlapping radial gradients. Each stat's gradient is positioned slightly offset toward its "home region" on the chart.

```javascript
drawStatNebula(constellation) {
    const stats = constellation.getDominantStats(); // from StageStatValue
    const completion = constellation.getCompletionPercent();
    const opacity = 0.03 + (completion * 0.27); // 3% to 30%
    const spread = constellation.bounds.scale(1.2 + completion * 0.8);

    for (const stat of stats) {
        const gradient = ctx.createRadialGradient(
            constellation.center.x, constellation.center.y, 0,
            constellation.center.x, constellation.center.y, spread
        );
        gradient.addColorStop(0, `${stat.color}${Math.round(opacity * 255).toString(16)}`);
        gradient.addColorStop(1, 'transparent');
        ctx.fillStyle = gradient;
        ctx.fillRect(/* bounds */);
    }
}
```

**Data source:** Reads existing `StageStatValue` records — **zero new data needed**.

**User experience:** Your chart literally glows in the colors of your playstyle. A combat-focused player's chart burns red/orange. An explorer's chart glows green/orange. A balanced player gets a full rainbow.

###### Layer 3: Job Pathways (Job System Visual Integration)

Jobs are assigned 2-per-stage. When a user completes stages across multiple series that share a job, faint pathway lines connect those stars across constellations — "trade routes" between related game worlds.

**How it works:**
1. Each stage has 2 assigned jobs (via `StageJob` model)
2. Find all pairs of completed stars that share a job across different constellations
3. Draw curved connection lines between them, colored by the shared job
4. Higher job level = brighter/thicker pathways

| Job Level | Pathway Opacity | Width | Style |
|-----------|----------------|-------|-------|
| 1-5 | 8% | 0.5px | Faint dotted |
| 6-15 | 15% | 1px | Dotted |
| 16-30 | 25% | 1.5px | Dashed |
| 31-50 | 35% | 2px | Solid |
| 50+ | 45% | 2.5px | Solid + faint glow |

**Line rendering:**
- Bezier curves (not straight lines) — arcs through space between constellations
- Control points placed perpendicular to the midpoint to avoid crossing through other constellations
- Lines use the job's color with the opacity from the table above
- Where multiple job pathways overlap, they fan out slightly (offset by 3-5px)

**Performance:** Only draw pathways for the user's top 5 jobs, or jobs above level 5. Others hidden but toggleable via UI filter.

**User experience:** A specialist sees a few thick, bright threads — focused expertise. A generalist has many faint threads — a web of connections. Hovering a pathway highlights all connected stars and shows the job name + level.

###### Layer 4: Discovery Stars (User-Driven Star Plotting)

Discovery stars are bonus stars that exist in the dark space between constellations. They're earned through platform engagement — quests, job milestones, stat thresholds, mini-games. They represent non-trophy achievements.

| Source | Star Type | Visual | Positioning |
|--------|-----------|--------|------------|
| Complete a daily quest | **Wandering Star** | Small (3px), dim, job-colored | Near relevant job pathway |
| Job level milestone (Lv 10/25/50) | **Named Star** | Medium (6px), job icon overlay, bright | Near densest cluster of that job's pathways |
| Stat threshold (100/250/500/1000) | **Anchor Star** | Large (8px), stat-colored, pulsing glow | In center of that stat's nebula region |
| Win a mini-game | **Shooting Star** | Small (3px), white, faint sparkle | Random open space |
| Complete an epic quest | **Nova** | Large (10px), multi-pointed, bright white + color ring | Prominent open space, burst animation |
| 30-day streak | **Eternal Flame** | Medium (6px), animated orange flicker | Near chart center |

**Positioning algorithm:**
1. Find "open space" on the chart (areas not occupied by constellations or other discovery stars)
2. For job/stat-tied stars, bias toward the relevant nebula region or pathway cluster
3. Enforce minimum 20px distance between discovery stars to prevent clumping
4. Position is determined once on earn and stored permanently

**Data model:**
```
DiscoveryStar:
  - profile (FK to Profile)
  - star_type (choices: wandering/named/anchor/shooting/nova/eternal)
  - source_type (choices: quest/job_milestone/stat_threshold/mini_game/epic_quest/streak)
  - source_id (nullable, identifier for the specific quest/job/stat that triggered it)
  - position_x, position_y (float, 0-1 normalized canvas coordinates)
  - color (hex, derived from source job/stat)
  - label (CharField, e.g., "Mercenary Lv 25" or "Power 500")
  - earned_at (DateTimeField)
  - icon_slug (nullable, for named/anchor stars that show an icon)
```

A very active user might accumulate 100+ discovery stars over months — the dark space fills with personal achievements. No two users' patterns ever match.

###### Layer 5: User-Plotted Constellations (Creative Expression)

Once users have 3+ discovery stars in proximity, they can draw lines between them to create their own named constellations — creative expression on their star chart.

**How it works:**
1. Enter "constellation drawing mode" (toggle button)
2. Click discovery stars to select them (minimum 3, maximum 12)
3. Draw lines between selected stars (click A, then B to connect)
4. Name the constellation + optional description
5. Save — permanent on your chart
6. Other users viewing your chart see your custom constellations (dimmed)

**Constraints:**
- Only discovery stars can be used (not badge constellation stars)
- Stars must be within a proximity threshold (prevents spanning the entire chart)
- Maximum 10 custom constellations per user
- Each star can belong to at most 2 custom constellations
- Lines visually curve around badge constellations

| Element | Style |
|---------|-------|
| Custom constellation lines | 1px solid, average color of connected stars, 40% opacity |
| Selected star highlight | 2px bright ring during drawing mode |
| Constellation label | User's name, 10px, at centroid, 50% opacity |
| Other users viewing | Lines at 15% opacity, label at 30% opacity |

**Drawing UI:** Point-and-click on canvas, "Undo last line" button, preview before saving. Mobile: tap-to-select, connect button.

**Data model:**
```
UserConstellation:
  - profile (FK to Profile)
  - name (CharField, max 30)
  - description (CharField, max 100, optional)
  - stars (M2M to DiscoveryStar)
  - edges (JSONField, list of [star_id_a, star_id_b] pairs)
  - created_at (DateTimeField)
```

###### Updated Hover/Click Interactions

| Target | Hover | Click |
|--------|-------|-------|
| Badge star (Layer 2) | Stage name, games, tier dots | Expand: game list with links |
| Stat nebula (Layer 1) | Stat name + total points | Toggle: show/hide this stat's nebula |
| Job pathway (Layer 3) | Job name + level, connected series | Highlight: all stars with this job |
| Discovery star (Layer 4) | Label + earn date + source | Select in drawing mode |
| User constellation (Layer 5) | Name + creation date | Edit in drawing mode |

###### Layer Shipping Strategy

| Phase | Layers | What Ships |
|-------|--------|-----------|
| MVP | 0, 2, 6 | Background + badge constellations + hover/click |
| v1.1 | + 1 | Stat nebulae (needs StageStatValue data populated) |
| v1.2 | + 3 | Job pathways (needs StageJob model + data) |
| v2.0 | + 4 | Discovery stars (needs quest system or job milestones) |
| v2.x | + 5 | User constellation drawing (creative endgame) |

Each layer is independent and can ship separately. The MVP star chart works with just layers 0+2+6.

---

### Quest / Challenge System (Premium Only)

#### Quest Tiers

| Tier | Count | Refresh | Duration | Purpose |
|------|-------|---------|----------|---------|
| Daily | 3 | Every 24h | 1 day | Quick engagement, daily habit |
| Weekly | 3-5 | Every Monday | 7 days | Medium commitment, guides play |
| Epic | Unlimited | Never (one-time) | Permanent | Long-term goals, milestone rewards |

**Critical rule:** Quest progress only counts actions taken AFTER quest assignment (no retroactive completion).

#### Quest Template System
Quests are generated from templates with randomized variables for variety:
- `"Earn a {trophy_type} trophy in a {series_name} game"` — picks from user's in-progress series
- `"Complete a stage that grants {stat_name} points"` — rotates through stats
- Variables are personalized per user based on their active games/series

#### Quest Categories (6 types)

**1. Trophy Quests** (requires gameplay):
- "Earn a {type} trophy in any game"
- "Earn a trophy in a {series_name} game"
- "Earn {n} trophies today"

**2. Badge Progress Quests** (requires gameplay, higher commitment):
- "Complete a stage in the {series_name} series"
- "Make progress in {n} different badge series this week"

**3. Checklist Quests** (on-platform, no gameplay):
- "Check off {n} items on any checklist"
- "Complete a full section of a checklist"

**4. Community Quests** (social actions):
- "Leave a comment on a game page"
- "Rate {n} games"
- "Upvote {n} comments"

**5. Exploration Quests** (browsing):
- "Visit a badge page you haven't viewed before"
- "Browse the trophy list for a new game"

**6. Meta/Engagement Quests** (system-level):
- "Log in for {n} consecutive days"
- "Complete all 3 daily quests"
- "Reach level {n} in any job"

#### Smart Quest Generation Rules
1. **Never generate impossible quests** — don't reference series the user has zero games in
2. **Favor in-progress work** — bias toward series the user is close to completing
3. **Ensure variety** — no 3 quests from same category
4. **Match difficulty to engagement** — active users get harder quests with better rewards
5. **Seasonal/thematic** — October = Spirit Healer quests, launch weeks = new game quests

#### Anti-Gaming Rules
- Can't complete quests by undoing actions (uncheck/recheck items)
- Rate-limit community quests (no spamming meaningless comments)
- Trophy quests require trophies earned AFTER quest acceptance

#### Daily Quest Difficulty Slots
- Slot 1: Easy (30 seconds — rate a game, upvote a comment)
- Slot 2: Medium (few minutes — leave a comment, checklist items)
- Slot 3: Hard (requires gameplay — earn a trophy, complete a stage)

#### Quest Rewards

**Daily:**
- Easy: 15-25 PlatCoins + 10 job XP
- Medium: 30-50 PlatCoins + 25 job XP
- Hard: 75-100 PlatCoins + 50 job XP
- Daily completion bonus (all 3): +50 PlatCoins

**Weekly:**
- 150-300 PlatCoins + 100-200 job XP per quest
- Weekly completion bonus: +200 PlatCoins + weekly cosmetic token

**Epic:**
- 500-2,000 PlatCoins + exclusive cosmetic/title + large job XP
- One-time only, never repeats

#### Reroll Mechanic
- 1 free reroll per day
- Additional rerolls cost PlatCoins (25-50)
- Creates a natural coin sink

#### Epic Quest Examples

**Progression:**
- "Reach Character Level 10/25/50/100"
- "Reach Profession Level 50/100/250"
- "Max a single job to level 50"
- "Have all 25 jobs at level 5+"

**Collection:**
- "Earn badges from 5/10/15 different series"
- "Complete all 4 tiers of any badge series"

**Engagement:**
- "Complete 100 daily quests"
- "Maintain a 30-day login streak"
- "Earn 10,000 PlatCoins total"

**Stat:**
- "Reach 500 in any P.L.A.T.I.N.U.M. stat"
- "Have at least 100 in every stat" (well-rounded)

#### Quest Completion Detection (Technical)
Signal-based, extending existing pattern in `trophies/signals.py`:
- Trophy earned → check active trophy quests
- Badge progress updated → check badge progress quests
- Comment created → check community quests
- Checklist item toggled → check checklist quests
- Page view logged → check exploration quests

Each signal handler checks: "Does this user have an active quest that this action satisfies?"

#### Quest Data Models (Conceptual)

```
QuestTemplate:
  - slug, name, description_template
  - tier (daily/weekly/epic)
  - category (trophy/badge/checklist/community/exploration/meta)
  - action_type (what triggers progress)
  - target_count
  - variable_config (JSON)
  - reward_config (JSON: {platcoins, job_xp, cosmetic_id, ...})
  - difficulty (easy/medium/hard)
  - weight (selection probability)
  - prerequisite (min character level, job level, etc.)
  - is_active

UserQuest:
  - profile (FK), quest_template (FK)
  - tier, generated_variables (JSON), display_text
  - current_progress, target_progress
  - status (active/completed/expired/rerolled)
  - reward_claimed (bool)
  - generated_at, completed_at, expires_at
```

**Quest rewards are the primary source of engagement XP** — this solves the "level 99 enabler" problem.

---

### Currency / Store System (Premium Only)

#### Earning Sources & Rates

**Daily Recurring:**
| Source | Amount | Weekly Total |
|--------|--------|--------------|
| Daily Quest (Easy) | 15-25 | 105-175 |
| Daily Quest (Medium) | 30-50 | 210-350 |
| Daily Quest (Hard) | 75-100 | 525-700 |
| Daily Completion Bonus | 50 | 350 |
| Login Streak | 10-50/day | 70-350 |
| **Subtotal** | | **1,260-1,925** |

**Weekly Recurring:**
| Source | Amount | Weekly Total |
|--------|--------|--------------|
| Weekly Quests (3-5) | 150-300 each | 450-1,500 |
| Weekly Completion Bonus | 200 | 200 |
| **Subtotal** | | **650-1,700** |

**Variable (depends on gameplay):**
| Source | Amount | Notes |
|--------|--------|-------|
| Badge Earned | 500 (Bronze), 750 (Silver), 1000 (Gold), 2000 (Plat) | Major milestone |
| Job Level Up | 50-200 (scales with level) | Higher level = more |
| Epic Quest | 500-2,000 | One-time only |
| Mini-games | 10-50 | Future feature |

**Estimated Weekly Income by Player Type:**
| Player Type | Weekly Income |
|-------------|--------------|
| Casual (2-3 days) | 300-600 |
| Regular (5-6 days) | 1,500-2,500 |
| Dedicated (daily) | 3,000-5,000 |
| Hardcore (all content) | 4,000-7,000+ |

#### Store Pricing Tiers

| Rarity | Price | Time to Earn (Regular) |
|--------|-------|----------------------|
| Common | 200-500 | 1-2 days |
| Uncommon | 750-1,500 | 3-7 days |
| Rare | 2,000-5,000 | 1-3 weeks |
| Epic | 7,500-15,000 | 3-8 weeks |
| Legendary | 20,000-50,000 | 2-6 months |
| Exclusive | N/A | Quest/achievement only |

#### Coin Sinks (Preventing Inflation)

**Active sinks:**
- Quest rerolls (25-50 per reroll after free daily one)
- Cosmetic purchases
- Streak Shields (2,000 each — protects 1 missed login day, can stockpile up to 3)
- Cosmetic re-coloring (500 to change outfit colors)

**One-time sinks:**
- Profile effects: animated borders (5,000), custom backgrounds (10,000), nameplates (3,000)
- Title customization: add adjectives/prefixes (2,000) — "Legendary Detective"
- Character slots: additional saved looks (8,000 per slot)

**Social sinks:**
- Gift coins to other users (transfers, doesn't create)
- Community fund: donate to site-wide goals ("Community reaches 1M → unlock new feature")

**Seasonal sinks:**
- Limited-time store items at premium prices (creates urgency)
- Seasonal cosmetic sets (available 2 weeks, then gone)

#### Anti-Inflation Mechanisms
1. Regular new cosmetic content to maintain demand
2. Seasonal/limited items with FOMO urgency
3. Prestige items (50,000+ coins = months of dedication)
4. Community fund (pure coin destruction with social benefit)
5. **No coin decay** — feels too punishing, avoided

#### Admin Economy Controls
- Quest reward multipliers (global and per-tier)
- Store pricing adjustments
- Bonus coin events ("Double coins this weekend!")
- Toggle earning sources on/off
- Economy dashboard: total coins in circulation, daily mint/burn, avg player wealth

---

### Daily Streaks / Weekly Incentives

#### Login Streak Curve
| Day | Bonus | Cumulative |
|-----|-------|------------|
| 1-2 | 10 | 20 |
| 3-4 | 15 | 50 |
| 5-6 | 20 | 90 |
| 7 | 50 (weekly!) | 140 |
| 8-13 | 15 | 230 |
| 14 | 75 (2-week!) | 305 |
| 15-29 | 20 | 605 |
| 30 | 200 (monthly!) | 805 |
| 31+ | 25/day + 50 every 7th | ~200/week |

#### Streak Break Policy
- Missing 1 day = streak resets to 0
- **Streak Shield** (consumable, 2,000 PlatCoins): protects streak for 1 missed day
  - Can stockpile up to 3 shields
  - Creates meaningful coin sink AND makes long streaks feel like an investment

#### Weekly Activity Recap
- "This week: +450 stat points, +2 job levels, 5/5 daily quests" → bonus coins
- Visual summary on Hunter Profile page

---

### Mini-Games: "The Arcade" (Premium Only)

**One mini-game per Job (25 total).** Each game is a small, daily-engagement experience tied to a specific job, built with Phaser 3 on an HTML5 Canvas. Arcade hub visible to all users; playing requires premium.

**Full design document:** `C:\Users\Jlowe\.claude\plans\valiant-toasting-allen.md`

**4 Game Archetypes:**
- **Competitive**: Daily leaderboards, skill-based (Driver, Marksman, Boxer, Survivalist, Explorer)
- **Cooperative**: Community works toward shared goals (Friend, Model Citizen)
- **Progression-based**: Persistent state, daily tasks, resource-gated growth (Architect, Curator, Hacker)
- **Single Player / Seasonal**: New curated content daily, individual completion (Detective, Archivist, Strategist)

**First game: "Stellar Circuit" (Driver job)** — Top-down space racer with procedurally generated circuit tracks, dual leaderboards (daily + all-time), ghost racing (own + other players), neon vector/geometric + space-faring aesthetic.

**Shared architecture (new `minigames` Django app):** Game sessions, daily challenges (seed-based deterministic generation), dual leaderboard system, reward distribution, anti-cheat validation, streak tracking.

**Mini-games serve dual purpose:** fun engagement AND non-gimmicky XP/PlatCoin source.

#### Game Concepts by Job

##### 1. Driver: "Stellar Circuit" (Competitive)
See full design: `C:\Users\Jlowe\.claude\plans\valiant-toasting-allen.md`

Top-down space racer. Procedurally generated circuit tracks in space with neon vector aesthetic. 3-lap time trials, ghost racing (own best + other players' replays), boost pads, obstacles. Dual leaderboards (daily + all-time). Seed-based track generation ensures everyone races the same daily track.

##### 2. Detective: "Starside Precinct" (Single Player / Seasonal)

**Concept**: You're a detective aboard a space station. Each day, a new procedurally generated crime case (theft, sabotage, smuggling, espionage) unfolds across three investigative stages, culminating in a final deduction. Cases within the same week share a hidden conspiracy thread that attentive players can piece together for bonus rewards.

**Daily Case Flow (3 Stages)**:

**Stage 1: "Crime Scene" (Evidence Collection)**
Investigate a station module (cargo bay, docking ring, lab, quarters, bridge). A visual layout with interactive hotspots. Click to examine areas and collect evidence items. Some are genuine clues, some are red herrings. You're building your evidence inventory for the interrogation.
- Scoring: speed + evidence efficiency (key items found vs. red herrings collected)

**Stage 2: "Interrogation" (Statement Analysis + Social Deduction)**
Armed with your evidence, interview 3-4 suspects. Each gives 2-3 statements built from templates. Cross-reference statements against your evidence and each other's alibis to spot contradictions. Present the right evidence to break a lie.

Social deduction layer: suspects have relationships with each other (allies, rivals, strangers). Allies cover for each other, rivals throw each other under the bus. You get a limited number of "investigate relationship" actions to reveal relationship types between suspect pairs, adding strategic decision-making about where to spend your investigations.
- Scoring: contradictions spotted on first try + strategic use of relationship investigations

**Stage 3: "Case Closed" (Final Deduction)**
Lock in your answers to the key questions: Who? Motive? Method? Partial credit available for getting some right. Bonus points for identifying accomplices or secondary details.
- Scoring: deduction correctness (partial credit)

**Meta Layer: Weekly Conspiracy Arc**
Each daily case is standalone, but over the course of a week, subtle connections emerge between cases. A suspect from Monday's case appears as a witness on Thursday. A stolen item resurfaces. A recurring location ties cases together. Players who notice these threads and solve the overarching "conspiracy" at the end of the week earn a significant bonus. This rewards attentive repeat players without adding daily complexity.
- End-of-week "Conspiracy Board" where players piece together the overarching thread
- Bonus rewards for cracking the weekly case

**Scoring**:
- Daily composite: evidence efficiency + interrogation accuracy + deduction correctness + time
- Weekly: conspiracy solve (big bonus)
- Both feed into daily and all-time leaderboards

**Procedural Generation (Solution-First Approach)**:

Each daily case starts by generating the answer, then builds everything backward:

1. **Generate the crime**: Pick from crime templates (smuggling, sabotage, theft, assault, espionage). Assign a motive category (greed, revenge, cover-up, ideology).
2. **Generate the cast**: Pull 4-5 characters from an archetype pool (station engineer, cargo pilot, diplomat, security officer, medic, merchant, scientist). Each gets a procedurally assigned name, role, and personality trait. One is designated the culprit.
3. **Generate relationships**: Each suspect pair gets a relationship type (ally, rival, stranger, secret history). Constrained by the solution: the culprit's ally will cover for them, their rival might over-accuse. A rules engine ensures the relationship web is solvable but not obvious.
4. **Generate evidence**: The solution dictates which evidence items exist (weapon, cargo manifest with discrepancy, security log placing someone at the wrong location). Red herrings are generated separately and scattered in. Each piece composed from templates: "[Item type] found in [location] showing [detail relevant to suspect X]."
5. **Generate statements**: Each suspect gets 2-3 statements from templates with variable slots. The culprit's statements contain exactly 1-2 lies that contradict specific evidence. Allies echo the culprit's alibi. Rivals may exaggerate suspicion. Template system ensures statements are always logically consistent with the solution.
6. **Weekly conspiracy seeding**: At the start of each week, a meta-solution is generated (e.g., "Suspect archetype X is running a smuggling ring"). Each daily case seeds 1-2 subtle connections: a name in a manifest, a repeated location, evidence that doesn't quite fit the daily case but points to something bigger. Breadcrumbs are baked into daily generation but flagged internally so the weekly solve can be validated.
7. **Validation**: Before publishing, the algorithm verifies the case is solvable with certainty (no guessing). Given the evidence and statements, there must be exactly one logical conclusion.

**Procedural Visuals (Fully Programmatic)**:

**Crime Scenes (Station Modules)**:
- Each room type (cargo bay, med lab, bridge, quarters, docking ring) is a tile-based layout using geometric shapes
- Walls are neon-outlined rectangles/polygons on a dark background
- Furniture/objects are simple iconic shapes: rectangles for crates, circles for tanks, triangles for consoles
- Evidence hotspots get a subtle pulsing glow effect (same particle system approach as Stellar Circuit)
- Seed determines room type + object placement + evidence locations, but each room type has a recognizable template (cargo bays always "feel" like cargo bays)

**Character Portraits**:
- Stylized silhouettes with distinguishing features: helmet shape, shoulder outline, accessory (visor, scarf, tool belt)
- Built from composable SVG-like parts: head shape + body shape + 1-2 accessories + color accent
- Each archetype has a distinct silhouette base, color-tinted with the Detective job's blue theme
- Relationship lines drawn between portraits during interrogation: solid for allies, jagged for rivals, dotted for strangers
- Contradiction spotted: portrait gets a red glitch effect (screen-shake + color shift)

**Interrogation UI**:
- Statement text in "terminal readout" style (monospace, typing animation)
- Evidence items displayed as small neon-bordered cards, draggable to present
- Relationship investigation shown as "scanning" animation over the connection line between two portraits

**Conspiracy Board (Weekly)**:
- Corkboard aesthetic in neon: dark background, evidence "pins" as glowing nodes, connection strings as animated neon lines
- Photos/evidence from each daily case appear as small cards pinned to the board
- Players drag strings between nodes to propose connections; correct ones lock in with a satisfying glow pulse

**Performance**: All visuals are Canvas 2D or simple DOM elements (no heavy 3D). Room layouts are positioned rectangles with border glow. Character portraits are layered sprite compositions. Particle effects reuse Stellar Circuit's system.

##### 3. Athlete: "Zero-G Games" (Competitive)

**Concept**: The space station's rec deck. A collection of sports micro-games played against AI opponents with a persistent MMR skill rating system (inspired by Wii Sports). Each sport has its own independent rating, and the AI scales on a continuous difficulty curve calibrated to your skill. The core engagement loop is the climb itself: start by stomping easy opponents, gradually face tougher competition, and chase personal rating milestones.

**Core Loop**:
- Each session, you're matched against an AI opponent calibrated to your current rating for that sport
- Win: rating increases (gains scale inversely with current rating, so early climbs are fast)
- Lose: rating decreases (smaller penalty than gains to keep it encouraging)
- Start at 0, uncapped ceiling
- The AI gets meaningfully harder as you climb: faster reactions, better aim, fewer mistakes, more aggressive strategy

**Sport Rotation**: 3-4 sports available at any time, cycling on a weekly or bi-weekly basis from a pool of ~8-10 total sports. Keeps things fresh without overwhelming scope.

**Sport Pool** (all space station rec deck themed):
- **Zero-G Pong**: Classic pong but the ball curves through gravity wells placed on the field. Higher-rated AI reads curves better, places shots more precisely
- **Asteroid Bowling**: Roll a charge down a lane with asteroid obstacles and shifting gravity. AI bowls cleaner lines at higher ratings
- **Solar Disc**: Frisbee/air hockey hybrid. Throw a disc into the opponent's goal in a zero-G arena with bumpers and boost pads. AI positioning and shot placement scales up
- **Meteor Boxing**: Timing-based boxing. Read the opponent's tells, dodge, counter-punch. Higher-rated AI has shorter tells, mixes up patterns more
- **Orbit Archery**: Hit targets on spinning rings at varying distances. Wind/drift compensation required. AI accuracy scales with rating
- **Sprint Relay**: Rhythm/timing race through a corridor. Hit boost pads on the beat. AI timing precision scales up
- **Grav-Ball**: Tiny 1v1 soccer/basketball in a zero-G chamber. Simple controls (move + shoot). AI decision-making gets sharper

**AI Difficulty Scaling**:
Continuous curve rather than discrete difficulty levels. At rating 0 the AI is sluggish and makes frequent mistakes. At 2500+ the AI is near-perfect but still beatable with skill. Tuned per sport since each has different levers:
- Reaction time (pong, boxing, disc)
- Accuracy/precision (archery, bowling)
- Decision quality and positioning (grav-ball, disc)
- Pattern complexity and tell duration (boxing)
- Timing precision (sprint relay)

**MMR Milestones**:
| Rating | Tier Name | Reward |
|--------|-----------|--------|
| 500 | Rookie | Sport-themed cosmetic (common) |
| 1000 | Contender | Sport-themed cosmetic (uncommon) |
| 1500 | Challenger | Sport-themed title |
| 2000 | Champion | Sport-themed cosmetic (rare) |
| 2500+ | Legend | Animated cosmetic (epic) |

**Daily Challenge**: Each day, one sport is featured with a specific scenario (e.g., "Beat a 1500-rated opponent at Zero-G Pong with double gravity wells"). Completing the daily challenge earns bonus XP/PlatCoins regardless of the player's own rating.

**Daily Leaderboard**: Shows "highest rating achieved today" so even veterans who are already high-rated have a daily competitive goal (push your peak even higher).

**Scoring**:
- Primary metric: MMR rating per sport (persistent)
- Daily leaderboard: peak rating achieved that day
- All-time leaderboard: highest rating ever reached per sport

**Procedural Generation**:
Each match is inherently unique through the AI's adaptive behavior. Arena layouts (gravity well positions, bumper placements, target configurations) are seed-randomized daily for additional variety. No two sessions play the same way because the AI responds to your rating and the randomized arena parameters.

**Procedural Visuals (Fully Programmatic)**:
- Arenas are simple geometric layouts: neon-bordered rectangular/circular play fields on a dark background
- Sports equipment (paddles, balls, discs, targets) are basic geometric shapes with glow effects
- AI opponents rendered as stylized silhouettes or geometric avatars, color-coded by difficulty tier
- Gravity wells visualized as subtle radial distortion effects or concentric ring animations
- Score/rating displays use the terminal readout style consistent with other mini-games
- All fits the neon vector aesthetic: glowing edges, particle trails on moving objects, boost pad effects
- Zero custom art assets required

##### 4. Thief: "Salvage Run" (Competitive)

**Concept**: Board procedurally generated derelict ships drifting in the void to extract valuable salvage, while evading the ship's still-active automated security systems (patrol bots, sentry turrets, hunter drones). A top-down stealth game with AI-driven enemies, resource management, and risk/reward decision-making across three stages.

**Multi-Stage Structure**:

**Stage 1: "Scan" (Recon)**
Before boarding, you receive a partial scan of the ship's layout. The general floor plan is visible but not all details. You choose:
- **Entry point**: Multiple airlocks available, each with tradeoffs (closer to high-value salvage but heavier security, or safer entry but longer route)
- **Loadout**: Limited tools from a pool (EMP charges, noise decoys, cloaking bursts, override keys). Can't take everything, so you plan based on what the scan reveals
- The scan shows some bot patrol routes but not all, adding uncertainty

**Stage 2: "Infiltration" (The Run)**
Top-down stealth gameplay on the ship. Move through corridors and rooms, avoiding security bots. Find salvage containers scattered throughout (some visible from the scan, some hidden). Each piece of salvage has a value and a weight with limited carry capacity, creating constant risk/reward decisions: push deeper into the heavily guarded engine room for the high-value haul, or play it safe with easier pickups near your entry?

Tools counter specific threats:
- **EMP charge**: Disables a bot temporarily
- **Noise decoy**: Draws patrols away from your position
- **Cloaking burst**: Brief invisibility window
- **Override key**: Opens locked/sealed doors
- All have limited uses, forcing strategic choices about when and where to spend them

**Stage 3: "Extraction" (Get Out)**
Once you've grabbed what you want (or things have gotten too hot), reach an extraction point. The ship's security awareness is now elevated based on how much noise you made during infiltration:
- Clean run: extraction is straightforward, minimal resistance
- Set off alarms: route back is crawling with hunters and locked doors
- Creates a natural consequence system where infiltration performance directly impacts extraction difficulty

**AI Security Bot System**:

**Bot Types**:
| Bot | Behavior | Threat Level |
|-----|----------|-------------|
| **Patrol Bot** | Walks set routes, reacts to sound/sight. If it spots you, pursues and calls for backup | Medium |
| **Sentry Turret** | Stationary, sweeps a cone of vision. Predictable but deadly in the open | Low-Medium |
| **Hunter Drone** | Only activates if an alarm is triggered. Fast, aggressive, patrols the area where you were last seen | High |
| **Lockdown Protocol** | Not a bot but a system response. If too many alarms trigger, ship sections seal off, cutting access to remaining salvage | Environmental |

**Sensory System**:
- **Vision cone**: Configurable angle and range per bot type. Blocked by walls. Wider for sentries, narrower for patrol bots
- **Sound radius**: Player footsteps generate sound. Running = large radius, walking = medium, crouching = small. Opening doors, using tools, and bumping into objects also generate sound
- **Alert propagation**: When one bot detects something, nearby bots enter "investigate" state. If confirmed, all bots in the sector go to "hunt"

**Bot State Machine**:
| State | Behavior | Triggers |
|-------|----------|----------|
| **Patrol** | Follow predetermined route, normal vision/hearing | Default state |
| **Curious** | Pause, look toward sound source, briefly investigate | Heard a noise nearby |
| **Investigate** | Move to last known disturbance, search the area | Alerted by another bot or saw brief movement |
| **Hunt** | Actively pursue player's last known position, sweep nearby rooms | Confirmed visual contact |
| **Return** | Head back to patrol route after investigation timeout | Lost the player, search timer expired |

**Difficulty Scaling** (by ship tier / daily seed):
- Easy ships: fewer bots, wider patrol gaps, slower reaction times, shorter vision cones
- Hard ships: more bots, tighter coverage, faster reactions, hunter drones pre-deployed, lockdown triggers sooner

**Scoring**:
- Total salvage value extracted
- Stealth bonus (no alarms triggered)
- Speed bonus (faster completion)
- Clean extraction bonus (reached exit without being in active pursuit)
- Daily leaderboard from composite score
- All-time leaderboard tracks best composite scores

**Procedural Generation**:
- **Ship layouts**: Generated from modular room templates (bridge, engine room, cargo hold, crew quarters, med bay, airlock corridor) connected by hallways. Room templates are hand-designed but placement, rotation, and connections are procedural
- **Bot patrol routes**: Generated based on ship layout. Patrol bots follow routes that cover key corridors with intentional gaps for the player to exploit. Route density scales with difficulty
- **Salvage placement**: High-value salvage placed in high-security areas (engine room, bridge). Lower-value scattered in accessible areas. Creates natural risk/reward gradient
- **Daily seed**: Same ship layout + bot routes + salvage placement for all players, enabling fair leaderboard comparison

**Procedural Visuals (Fully Programmatic)**:
- Ship interiors rendered as neon-outlined geometric rooms on a dark background (consistent with Starside Precinct's station modules)
- Bot patrol routes shown as faint dotted lines during Scan phase (partially revealed)
- Vision cones rendered as semi-transparent colored triangles: green (patrol), amber (curious/investigate), red (hunt)
- Player character as a small geometric shape with a subtle shadow/stealth trail
- Salvage containers as glowing geometric icons with value indicators
- Alert state visualized through ambient lighting shifts: calm (dark blue), suspicious (amber tint), alarm (red pulse)
- Lockdown doors shown as thick red barriers with warning stripes
- All fits the neon vector/derelict ship aesthetic: flickering lights, damaged panels, exposed wiring as decorative line elements

---

### Mastery Paths — Constellation Map (Premium Only)

**Layout: Constellation Map** — Stages as glowing stars connected by ethereal lines against a dark backdrop, tying into the PlayStation space/star aesthetic.

**Important: Does NOT change badge XP.** Mastery rewards are PlatCoins + cosmetics + titles only.

#### Visual Concept
- Dark background (night sky / space theme)
- Completed stages = bright, glowing stars with tier-colored aura
- In-progress stages = pulsing/dimming star with progress ring
- Locked stages = dim, distant stars showing reward preview on hover
- Lines between stages form constellation patterns as they complete
- Stage 0 (optional) = separate branch/offshoot constellation
- Milestone markers at 25%/50%/75%/100% along the path

#### Node States
| State | Visual | Details |
|-------|--------|---------|
| Completed | Bright glow, checkmark, tier-colored aura | Full details on click |
| In Progress | Pulsing star, progress ring overlay | Shows completion % |
| Locked | Dim star, faded | Shows reward preview as motivation |
| Optional (Stage 0) | Different shape/color, branching path | Side quest visual treatment |

#### Per-Node Information (hover/click)
**Reward Preview:**
- P.L.A.T.I.N.U.M. stat points (from `StageStatValue`) with colored icons
- Job XP grants (from `StageJob`) with job icons
- Visual: stat bars + job badges

**Completion Status:**
- Per-tier completion indicators (which tiers done for this stage)
- Games in stage with progress %
- Trophy count earned vs total

#### Mastery Milestones (separate from badge XP!)
| Threshold | Reward | Type |
|-----------|--------|------|
| 25% stages done | 500 PlatCoins | Currency |
| 50% stages done | Series-themed cosmetic piece | Cosmetic |
| 75% stages done | Series-themed title | Title |
| 100% stages done | "Master of {Series}" title + 2,000 PlatCoins | Title + Currency |
| 100% + all optional | "Completionist" badge for series + animated effect | Exclusive cosmetic |

#### Stage 0 — Side Quest Branch
- Visually branches off the main constellation as a separate arm
- Different color/style (maybe nebula instead of stars?)
- Grants bonus stat points + PlatCoins
- Separates "completed the badge" from "mastered the series"
- Completing side quests is required for the "Completionist" mastery level

#### Integration With Existing Badge Detail Page
- **Free users**: See the regular badge detail page unchanged
- **Premium users**: Constellation map rendered at top of page (above stage list)
- Clicking a star node scrolls to that stage's detail section below
- New partial template: `badge_detail_mastery_path.html`
- JavaScript for: hover tooltips, click-to-scroll, star glow animations, progress rings
- View extended: `BadgeDetailView` passes `stage_stat_values` + `stage_jobs` + `mastery_milestones`

#### Mastery Paths Across the Site
- **Hunter Profile**: Mini constellation views for each started series (compressed)
- **Badge list page**: Mastery % indicator on each badge card
- **Notifications**: "1 stage from mastering Resident Evil!"
- **Weekly recap**: "Progressed on 3 mastery paths this week"

#### Mastery Path Data Model (view context)
```python
mastery_data = [
    {
        'stage': stage_obj,
        'stage_number': 1,
        'title': 'Origins',
        'icon_url': '...',
        'is_optional': False,
        'completion': {1: True, 2: True, 3: False, 4: False},
        'overall_complete': True,
        'stat_rewards': [
            {'name': 'Power', 'slug': 'power', 'icon': '⚔️', 'color': '#FF4444', 'value': 25},
        ],
        'job_rewards': [
            {'name': 'Driver', 'slug': 'driver', 'icon_url': '...', 'xp': 100},
        ],
        'games_count': 3,
        'games_completed': 2,
    }, ...
]
mastery_milestones = {
    25: {'reward': '500 PlatCoins', 'unlocked': True},
    50: {'reward': 'Series Cosmetic', 'unlocked': False},
    75: {'reward': 'Series Title', 'unlocked': False},
    100: {'reward': 'Mastery Badge + 2000 PlatCoins', 'unlocked': False},
}
```

---

## The Complete Gamification Loop

```
PLAY GAMES (earn trophies on PSN)
    ↓
COMPLETE STAGES (badge progress)
    ↓
EARN: Badge XP + P.L.A.T.I.N.U.M. Stats + Job XP
    ↓
LEVEL UP: Character Level + Job Levels + Profession Level
    ↓
UNLOCK: New quests, cosmetics, store items, titles
    ↓
COMPLETE QUESTS (daily/weekly/epic)
    ↓
EARN: PlatCoins + bonus Job XP + quest rewards
    ↓
SPEND: PlatCoins → Character cosmetics, profile flair, titles
    ↓
PLAY MINI-GAMES (for fun + bonus coins/XP)
    ↓
SHOW OFF: Hunter Profile, character avatar, titles, radar chart
    ↓
REPEAT (new quests daily, new stages from game releases)
```

**Key insight:** Trophies are the primary fuel, but quests + mini-games + currency create secondary loops that keep users engaged between trophy earnings. A user who hasn't earned a new trophy in 2 weeks still has reasons to log in.

---

## Feature Scoping (rough tiers)

### MVP (Core System)
- P.L.A.T.I.N.U.M. Stats (8 stats, radar chart via Chart.js)
- Jobs (25 jobs, flat XP per stage, job levels)
- Character Level (badge XP → level, all users)
- Profession Level (sum of job levels, premium)
- Explorer's Logbook page (framed PSN avatar + radar chart + job grid)
- Star chart (constellation map of badge progress — Canvas/SVG)
- SVG icon system (library icons with color assignments)
- Admin tooling for stat/job assignment

### Must Haves (v1.x)
- Quest system (daily/weekly/epic)
- Stellar Marks currency + basic store
- Daily login streaks
- Mastery paths / constellation map on badge detail pages
- Avatar frame customization (shapes, borders, auras — CSS/SVG)

### Nice to Haves (v2)
- Avatar frame cosmetics tied to progression (store purchases, job unlocks)
- Class/archetype auto-assignment
- Profile card integration (show level + top job + mini star chart)

### Future / Stretch
- Mini-games (trivia, bingo, etc.)
- Trading card system
- Social features (compare stats, challenge friends)
- Seasonal events / limited-time quests
- Leaderboards per stat, per job, profession level

---

## Summary

The gamification system creates a comprehensive RPG identity layer for premium users:
- **Character Level** (all users, from badge XP) = "how far you've come" (premium upsell hook)
- **P.L.A.T.I.N.U.M. Stats** (premium) = "what kind of gamer you are"
- **Jobs + Profession Level** (premium) = "what you specialize in"
- **Quests** (premium) = structured engagement that feeds job XP + currency
- **Stellar Marks + Store** (premium) = economy that powers frame/cosmetic customization
- **Framed PSN Avatar** (premium) = customizable visual identity using their existing PSN avatar + unlockable CSS/SVG frames, borders, and aura effects
- **Star Chart** (premium) = unique constellation map showing progression across all badge series — the shareable identity artifact
- **Mini-games** (premium) = fun engagement between trophy earnings

**Art direction:** Full SVG/CSS/Canvas approach — zero custom artwork required. All icons from open-source library (Lucide/Heroicons/Phosphor), all effects via CSS animations, all rendering via Canvas/Chart.js. No art pipeline dependency; everything ships with code alone.

The existing infrastructure (`StatType`, `StageStatValue`, `ProfileGamification`, signals, bulk updates) provides a solid foundation for the MVP. Each subsequent tier builds on the last without requiring the others.
