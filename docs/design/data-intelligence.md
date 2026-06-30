# Data Intelligence: Insight Engine → Drill-Down → Companion

> Status: **VISION / ROADMAP** (aligned 2026-06-30, not built). The flagship premium *value* arc
> for the membership model ([premium-proposal.md](rebuild/premium-proposal.md)). Three phases on
> **one shared spine.** Absorbs and reconciles [platinum-journey.md](platinum-journey.md) as its
> final phase. None of this is in the v1 premium update — each phase is its own dedicated update.

## The vision

PlatPursuit stores an enormous, expensive-to-acquire-and-maintain dataset per user (trophies,
completion, IGDB enrichment, badges, challenges, 120+ derived stats). Today it's presented as
discrete facts. The opportunity: **wield that data better than anyone in the space** — turn it into
*insight*, not just counts.

The product line that keeps this honest (additive, never a squeeze):

- **Profile page (free) = the facts.** What you've done — counts, library, showcases, identity.
- **This arc (premium) = what it *means*.** The "abstract *between* the stats" — the patterns,
  relationships, and trends that tell the story of who you are as a hunter.

Examples of "the abstract between the stats" (insight, not counts): *"you platinum 80% of your RPGs
but bounce off 70% of your shooters"; "your completion rate has climbed every year since 2021";
"your rarest plats cluster in FromSoft titles"; "top-3% for JRPG depth, bottom-20% for genre
breadth"; "at this pace you'll hit 300 plats by spring."* The profile tells you *what*; this arc
tells you *who you are*. Nobody in the space does the second thing well.

## One spine, three phases

These are **not three projects.** They are three *interfaces* onto one engine. Build the spine
once; each interface is a way of consuming it.

### Phase A — The Insight Engine (the spine)

A per-profile intelligence layer: the pre-computed insights, trends, patterns, and affinities.
**Materialized off the request path** (computed after sync / on a schedule, stored), so reading it
is cheap. This is the whale-safety cornerstone (below) and the shared foundation both interfaces
consume. Build this first; it is the thing the other two phases stand on.

### Phase B — The Drill-Down (visual interface)

The My Stats page evolves from a stats *dump* into a genuine drill-down **machine**: slice, filter,
cross-reference, compare (vs your past self, vs the community), trend over time. A power *tool* for
exploring your data — explicitly **not** "more stats behind a wall." Reads the Phase-A materialized
structure. (The current My Stats page stays **as-is** until this dedicated update.)

### Phase C — The Companion (conversational interface) = Platinum Journey

The same engine, *spoken* instead of charted: a named companion that "knows your journey,"
surfaces the insights conversationally, and helps plan next pursuits. This **is** the
[Platinum Journey](platinum-journey.md) "patient companion" — confirmed (2026-06-30) as the
conversational phase of *this* arc, not a separate idea. Its "Context Builder" is this insight
engine.

## Whale-safety (the cornerstone)

The May 2026 OOM saga was caused by computing aggregates in Python, live, on the request path, over
whale-sized rowsets (250k+ trophies). This arc must not repeat it:

- **Heavy insight is pre-computed / materialized** (Phase A), not built live per request. Reading
  is cheap.
- Any genuinely live slice is **DB-aggregated and bounded** (the `.values().annotate()` rule).
- **Premium-gating is also the cost governor.** The expensive computation only ever runs for the
  *paying subset*, structurally capping cost to the people funding it. The gate that makes it
  premium is the gate that makes it safe.

**Reconciliation note for Platinum Journey:** that doc sketches a live, in-memory Context Builder
with a 5–15 min cache. For the *heavy* "abstract between stats" insights that approach is **not
whale-safe**; those should come from the Phase-A materialized layer instead. Light, fresh context
(time since last visit, recent events) can still be built live. Net: the Journey's Context Builder
becomes a thin reader over the materialized insight engine plus a little live state.

## Premium positioning

Both interfaces (drill-down, companion) are premium **value**, **included** in the membership (no
separate tier — keep it simple). They are the deferred flagships that give the membership room to
grow over time. See [premium-proposal.md](rebuild/premium-proposal.md).

## Sequencing

Each phase is its own dedicated update, built with proper care (per the rebuild cadence: build to a
locked baseline, don't rush the ambitious stuff). Natural order: **A (engine) → B (drill-down) → C
(companion)**, since each builds on the prior. None are in the v1 premium positioning update.

## Related

- [premium-proposal.md](rebuild/premium-proposal.md) — the membership model this arc anchors
- [platinum-journey.md](platinum-journey.md) — Phase C, the full companion design
- [stats-page.md](stats-page.md) — the current My Stats page (the Phase-B starting point)
- [gamification-vision.md](gamification-vision.md) — a future data source the engine will read
