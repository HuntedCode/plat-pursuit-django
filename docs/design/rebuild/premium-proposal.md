# Premium = Membership: Direction

> Status: **DIRECTION** (aligned 2026-06-30, not yet built). Supersedes the earlier "gate flex
> + depth" draft of this doc. Companion to [data-intelligence.md](../data-intelligence.md) (the
> flagship value roadmap) and [platinum-journey.md](../platinum-journey.md). Billing plumbing
> lives in `docs/features/subscription-lifecycle.md` + `docs/architecture/payment-webhooks.md` —
> this doc is about what premium IS, not how it bills.

## The thesis

**Premium is a membership, not a paywall.** People pay to *support* PlatPursuit and to *belong*,
not to unlock a crippled product. Any upside revenue comes from making membership appealing,
never from walling off functionality.

This is grounded in evidence, not hope: the badge-artwork fundraiser has already raised ~$1,000
from people who simply wanted to fund something they believe in (and got great badge art back).
That is empirical proof this community supports out of love. Premium-as-membership extends a
behaviour that already works.

The trade we are **consciously** making: a support-led model has a lower revenue ceiling than
aggressive gating. We accept that. Brand integrity and an earnest community are worth more to
PlatPursuit than squeezing; the upside ("do even more cool things later") comes from scale and
appeal, not walls.

## What stays free (the floor is already whole)

The free product is already complete, and that is load-bearing for the whole model: the **profile
page** gives every user their facts — stats, library, showcases, identity. Premium never removes
anything from it. This is what makes "premium adds value, never detracts" *true* rather than
aspirational: there is nothing to take away.

Free also keeps the entire gamification spine (Pursuer Card, The Lab, Elements, badges, titles,
Research Panel). Earning and identity are free. Premium is purely additive.

## The membership, in four buckets

| Bucket | What | In v1? |
|---|---|---|
| **Convenience** | Faster sync, higher limits (lists / grid) | Yes (exists) |
| **Community** | Discord roles / bonuses | Yes (exists) |
| **Recognition** | A supporter **flair marker** across the site (leaderboards / profile / comments): a fixed "I support PlatPursuit" signal — NOT customization | Yes (the one light new thing) |
| **Value** | The deep, additive features that reward support | Roadmap (deferred) |

## v1 is a positioning play, on purpose

Honest scoping: **both** value flagships (the My Stats drill-down and customization) are deferred.
So v1 premium ships **no net-new killer feature**. v1 is: reframe what exists as a membership,
build a storefront that tells the story, ship the supporter flair, and publish the roadmap.

For a support-led membership this is the right launch shape — you sell *belonging + a trajectory*
and deliver the big features with care over time. We enter v1 knowing its job is **positioning**,
not a feature drop. The deferred features become the visible roadmap: the reasons people keep
supporting and new people join.

## The roadmap (deliver with care, communicate with humility)

**Internal rule: document the direction with conviction; communicate it publicly with humility.**
Anything user-facing (the storefront, any shown roadmap) stays soft — themes and "we're
exploring," never dates or promises. The direction has shifted meaningfully even in early
development; the docs hold the conviction so the public copy never has to over-commit.

Deferred value features, each its own future update:

1. **Data Intelligence arc** — the flagship premium value. One spine, three phases: a per-profile
   insight engine → the My Stats **drill-down** ("the abstract *between* the stats") → the
   conversational **companion** (Platinum Journey). Full design:
   [data-intelligence.md](../data-intelligence.md). NOTE: the current My Stats page stays **as-is**
   for v1; the drill-down is a dedicated future update.
2. **Customization / cosmetics** — Pursuer Card backdrops + finishes, theming, binder skins: the
   big "flair" surface, tied to the gamification **currency + questing** update. A major growth
   lever, deliberately later.
3. **Parking lot** (mentioned, not yet specced): new / updated challenges, a revamped Recap tool.
   Capture separately as they firm up.

## Guardrails

- **Free stays genuinely whole.** The profile-page floor is the proof the membership adds rather
  than detracts. Never gate basic facts.
- **Flair never outshines *earned* status.** Supporter flair is a *separate visual language* ("I
  support PlatPursuit") from earned achievement (rank chrome, etc.). A bought marker must never
  read as "better hunter." Ties to the visual-identity principle *neon earned by state, not bought.*
- **Whale-safe always.** The data-intelligence features especially: pre-compute insight off the
  request path; premium-gating naturally bounds the heavy compute to the paying subset (the gate
  is also the cost governor). See [data-intelligence.md](../data-intelligence.md).
- **Internal conviction, public humility** (see roadmap rule above).

## Tiers

Keep the existing 4 tiers (`ad_free` / `premium_monthly` / `premium_yearly` / `supporter`); **no
new tier.** The membership framing sits on top of the existing plumbing. The data-intelligence
features are **included** in premium — they do not justify a separate higher tier; keep it simple.

## Placement

A "Premium" / "Membership" **storefront** page (an elevation of `/users/subscribe/`) that tells
the story and sells belonging, plus the flair shown **in-context** where it lives. NOT a siloed,
gated feature-hub tab (those get skipped; in-context converts at the moment of desire).

**Where it lives (IA, decided 2026-06-30):** the storefront's home is a new top-level **Support**
hub, sharing it with the always-on badge-art **fundraiser** — the two "support us" asks are one
coherent story. Premium *features* stay in the personal hub with locked previews that deep-link to
Support (moment-of-desire conversion). See [ia-and-subnav.md](../../architecture/ia-and-subnav.md)
§"Planned evolution".

## Gotchas & pitfalls

- **Don't accidentally re-gate something already free during the rebuild.** Prefer gating *net-new*
  value over taking away what shipped free. The profile floor is sacrosanct.
- **The storefront communicates the roadmap publicly** — keep it soft (no dates / promises) per the
  internal-conviction / public-humility rule.
- **v1 leans entirely on positioning.** With no new feature, the storefront + story + flair have to
  carry it. If v1 feels hollow, the fix is better *storytelling*, not a rushed feature.
