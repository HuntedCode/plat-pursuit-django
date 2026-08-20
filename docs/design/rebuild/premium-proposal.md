# Premium = Membership: Direction

> Status: **DIRECTION** (aligned 2026-06-30, not yet built). Supersedes the earlier "gate flex
> + depth" draft of this doc. Companion to [data-intelligence.md](../data-intelligence.md) (the
> flagship roadmap arc -- for everyone, per the amendment below) and
> [platinum-journey.md](../platinum-journey.md). Billing plumbing
> lives in `docs/features/subscription-lifecycle.md` + `docs/architecture/payment-webhooks.md` —
> this doc is about what premium IS, not how it bills.
>
> **Amended 2026-08-19** -- see "Premium is a dial, not a door" below. The gated-flagship framing that
> survived in the Value bucket and in the deferred-roadmap items is **retired**: nothing is locked,
> ever. Recorded because the earlier text would otherwise steer future work back toward walls.

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

Free also keeps the entire gamification spine (Pursuer Card, Career, jobs + contracts, badges, titles,
Research Panel). Earning and identity are free. Premium is purely additive.

*(Naming corrected 2026-08-19: this line said "The Lab" and "Elements", both of which were reverted --
`/my-pursuit/lab/` is a permanent redirect to `career`, and the periodic-table metaphor went back to
jobs and contracts. See `project_elements_periodic_reframe`.)*

## Premium is a dial, not a door

**Nothing is gated. Ever.** Premium never grants access to something a free user cannot reach. It
gives *more* of what they already have: longer history, higher limits, more frequent refreshes, a
bigger allowance. The membership turns a dial that is already on for everybody.

This is stricter than the "purely additive" language above, and deliberately so. "Additive" still
permits a future flagship sitting behind a wall. This does not.

**The build already works this way.** The principle describes existing behaviour rather than proposing
new: every perk that exists today is quantitative (anytime sync is a rate limit; list and grid caps
are counts), and the rebuild *deleted* the Monthly Recap's premium gate from five places, opening it
to every logged-in hunter for all history. There is no door in the product right now.

**The cost, stated once.** This doc already accepted a lower revenue ceiling than gating would give.
This lowers it again, by removing even the *option* of a future flagship carrying the pitch. The
consequence to hold onto: the story, the roadmap and the sense of belonging do **all** of the work,
permanently, with no feature ever arriving to help. That is a far higher bar than a normal pricing
page has to clear, which is why the storefront's *writing* is the deliverable, not its layout.

## The membership, in four buckets

| Bucket | What | In v1? |
|---|---|---|
| **Convenience** | Faster sync, higher limits (lists / grid) | Yes (exists) |
| **Community** | Discord roles / bonuses | Yes (exists) |
| **Recognition** | A supporter **flair marker** across the site (leaderboards / profile / comments): a fixed "I support PlatPursuit" signal — NOT customization | Yes (the one light new thing) |
| **Value** | **More of what everyone already gets**: longer history, more frequent recomputation, a bigger currency allowance. Never a feature a free user cannot reach | Dial-only (see above) |

## v1 is a positioning play, on purpose

Honest scoping: both of the big roadmap items (the My Stats drill-down and customization) are
deferred -- and under the dial principle neither would have been premium-only even once they land. So
v1 premium ships **no net-new killer feature**, and no later version ships one either. v1 is: reframe what exists as a membership,
build a storefront that tells the story, ship the supporter flair, and publish the roadmap.

For a support-led membership this is the right launch shape — you sell *belonging + a trajectory*
and deliver the big features with care over time. We enter v1 knowing its job is **positioning**,
not a feature drop. The deferred features become the visible roadmap: the reasons people keep
supporting and new people join.

**The v1 perk lineup (decided 2026-08-19):**

| Perk | State |
|---|---|
| Anytime syncing | Exists (a rate-limit dial) |
| Discord: the supporter role **and the room where we work out what's next** | Role sync already ships -- `trophies/services/discord_roles.py` grants off `premium_tier`, with a downgrade hook. Sold as **one** perk rather than two, so the Discord dependency is visible before payment instead of discovered after: the say *is* the Discord perk |
| Supporter flair | Built on the star that already exists (`.pp-hcard--supporter` + `.pp-hcard__supp`, shipped in the Browse Hunters pass). Promoting an existing treatment site-wide, not inventing a visual language |
| Early access / beta lane | The gate is already wired (`trophies/mixins.py` -> `beta_access_required`) and currently unused for this |
| Permanent supporter credit | "PlatPursuit Supporter". Doubles as the graceful answer to what *lapsing* feels like |

Roadmap input runs **in Discord**, not as a built voting feature. A roadmap is steered by conversation
("why do you want that?") rather than by vote counts, and the role plumbing already exists; building
ballots, dedup and abuse handling to do the job worse would be the wrong trade.

## Includes is not the same as sells

Quantity perks are, ironically, the **most** transactional kind. "10 lists instead of 3" invites the
units-per-dollar arithmetic this whole model is trying to escape, in a way that "early access" and
"the room where we decide what's next" do not.

So the two lists carry different weight on the page. **What premium includes** is honest, complete,
and sits low: a thank-you, not an argument. **What the storefront sells**, up top, is belonging, the
roadmap and the proof. Someone who has already decided to support wants to know what they get; they
should not have to wade through it to find out why we exist.

## The roadmap (deliver with care, communicate with humility)

**Internal rule: document the direction with conviction; communicate it publicly with humility.**
Anything user-facing (the storefront, any shown roadmap) stays soft — themes and "we're
exploring," never dates or promises. The direction has shifted meaningfully even in early
development; the docs hold the conviction so the public copy never has to over-commit.

### The public roadmap page (structure decided 2026-08-19)

`/support/roadmap/` groups by **confidence, not by time**. Timeline shapes (Q1/Q2, Now/Next/Later, a
horizontal track) read as a schedule however hard the copy hedges, and a schedule has to be
conservative or it lies. A confidence-grouped page reads as *direction*, which is allowed to be
expansive. The layout does the disclaiming, so individual lines do not each need a hedge around them.

Three tiers, each a genuinely different *kind* of certainty:

1. **Coming back** -- decided already, just not built: Challenges, My Stats (as an *engine*, not a
   static page), Game Lists, notifications, profile showcases. These are commitments the codebase made
   when 1.0 hid them, so they cost nothing in risk and are the cheapest credibility on the page.
   **Careful: not everything removed is returning.** Profile Timeline was deleted outright, the
   Community Hub retired, and advertising is gone permanently and deliberately. Those must never drift
   onto this list.
2. **Being built** -- whatever is actually in flight. One or two items, never more.
3. **Where we're headed** -- expanded gamification surfaces (quests, streaks), a bigger badge library,
   customization + earnable currency, a mobile app. Ambitious, and explicitly not promised.

Plus a closing **horizon line** for multi-platform (Xbox / Steam / RetroAchievements), stated as
ambition rather than plan. It is the biggest idea available and would be badly undersold as a bullet
among smaller ones -- but it is also not a *feature*: the data model is PSN-shaped throughout
(`np_communication_id`, trophy groups, the platinum as the atom Contracts, badges and Career are all
keyed on), so it means a second identity model and a rethink of what a completion even is.

Two rules that keep it honest:

- **Volume is the promise.** Readers infer pace from list length even when the page carries no dates:
  twenty items reads as "none of this is happening". Hold it near seven plus the horizon line, and cut
  vague entries -- they cost the same credibility as real ones and return nothing.
- **Staleness kills, not unshipped items.** Nobody minds something sitting in tier 3 for two years.
  What kills a roadmap is an item that shipped but still says "coming soon", or one that quietly
  vanishes. Items **move** between tiers or are **removed with a note**, never silently, and the page
  carries a last-updated date.

The **shipped half is load-bearing**, not decoration: 1.0 is ~30 pages rebuilt from scratch, the ad
layer removed entirely, the badge system rebuilt, plus Career, Milestones and Titles -- almost none of
it visible to anyone who was not watching it happen. Ambition sitting under that evidence reads as a
track record being extended; the same ambition alone reads as a wish list. That is also the real answer
to "how ambitious can we be": exactly as ambitious as the proof above it supports.

Deferred features, each its own future update. **These ship for everyone** -- premium turns the dial
on them rather than owning them:

1. **Data Intelligence arc** -- one spine, three phases: a per-profile insight engine -> the My Stats
   **drill-down** ("the abstract *between* the stats") -> the conversational **companion** (Platinum
   Journey). Full design: [data-intelligence.md](../data-intelligence.md). Everyone gets the engine;
   the dial is history depth and recomputation frequency, which is also what keeps the heavy compute
   bounded (see Guardrails). NOTE: the current My Stats page stays **as-is** for v1.
2. **Customization / cosmetics** -- Pursuer Card backdrops + finishes, theming, binder skins, tied to
   the gamification **currency + questing** update. Currency is **earnable by everyone** and the whole
   library is open to it; members get a larger allowance, never exclusive items. Supporter-flavoured
   cosmetics stay legitimate because flair is a *separate* visual language rather than a better tier
   of one (see Guardrails). "Pay to skip the grind" is a poor fit for an audience that volunteers for
   grinds.
3. **Parking lot** (mentioned, not yet specced): new / updated challenges, a revamped Recap tool.
   Capture separately as they firm up.

## Guardrails

- **Nothing is gated, ever.** Premium is a dial, not a door (see above). The tell is grammatical: an
  *amount* is on-direction, an *object* is not. If a proposal reads "premium users get X" where free
  users get no X at all, it is off-direction -- restate it as an amount, or drop it.
- **Free stays genuinely whole.** The profile-page floor is the proof the membership adds rather
  than detracts. Never gate basic facts.
- **Flair never outshines *earned* status.** Supporter flair is a *separate visual language* ("I
  support PlatPursuit") from earned achievement (rank chrome, etc.). A bought marker must never
  read as "better hunter." Ties to the visual-identity principle *neon earned by state, not bought.*
- **Whale-safe always.** The data-intelligence features especially: pre-compute insight off the
  request path. The membership **dial** (history depth, refresh frequency) does the cost-governing a
  gate used to -- heavy compute stays bounded to the paying subset without anything being walled off,
  so the economics survive the dial-not-door change intact.
  See [data-intelligence.md](../data-intelligence.md).
- **Internal conviction, public humility** (see roadmap rule above).

## Tiers

Keep the existing 3 tiers (`premium_monthly` / `premium_yearly` / `supporter`); **no new tier.** The
membership framing sits on top of the existing plumbing. (There were 4: `ad_free` was retired in
2026-08 with advertising itself. Its removal *strengthens* this proposal rather than thinning it —
"premium removes ads" sold the removal of something we inflicted, which is the opposite of the
support-led framing here, so the pitch now has to stand on genuine value and belonging.) The data-intelligence
features are **included** in premium — they do not justify a separate higher tier; keep it simple.

**Flat rate + gifting are purchase paths, not tiers (lane 2, decided 2026-08-19).** A one-time payment
granting a year of premium, giftable codes, and staff comps all fall out of one missing primitive:
`Profile.is_premium()` (`users/models.py:93`) has exactly two branches -- an active Stripe subscription,
an active PayPal one -- and no notion of "premium until date X". Add that expiry branch and all three
follow. The processors are the easy half (both do one-time payments; Stripe Checkout in `payment` mode
rather than `subscription` mode); the model change is the work. Prefer **codes** over gifting direct to
a named user: a code decouples payment from recipient, so it survives the recipient having no account
yet, and it doubles as the staff comp tool. Sequenced **after** the storefront ships.

## Placement

A "Premium" / "Membership" **storefront** page (an elevation of `/users/subscribe/`) that tells
the story and sells belonging, plus the flair shown **in-context** where it lives. NOT a siloed,
gated feature-hub tab (those get skipped; in-context converts at the moment of desire).

**Where it lives (IA, decided 2026-06-30; shape settled 2026-08-19):** the storefront's home is the
top-level **Support** hub, sharing it with the always-on badge-art **fundraiser** -- the two "support
us" asks are one coherent story.

The **storefront IS the Support landing**: `/support/` is one uninterrupted pitch (story -> three
options -> perks), not a table of contents pointing at the real page. `/users/subscribe/` 301s in. Two
sub-pages get their own addresses and turn the hub's sub-nav strip on for the first time:
`/support/roadmap/` (what comes after 1.0) and `/support/fundraiser/` (the campaign's permanent home,
plus the gallery of already-funded art with its donor credits -- the best proof of the thesis on the
whole site, and that data already works). The hub keeps the name **Support**: it covers both halves,
where "Membership" covers one. See [ia-and-subnav.md](../../architecture/ia-and-subnav.md)
§"Support hub".

**Conversion without locks.** The earlier plan put *locked previews* of premium features in the
personal hub, deep-linking here at the moment of desire. Under the dial principle there is nothing to
lock, so that mechanism is **retired**. Its replacement keeps the instinct and drops the wall: a quiet
contextual line wherever the dial is visible ("this recomputes monthly for you; members get it
weekly"). Still moment-of-desire, but it reads as information rather than a tease.

## Gotchas & pitfalls

- **Don't accidentally re-gate something already free during the rebuild.** Prefer gating *net-new*
  value over taking away what shipped free. The profile floor is sacrosanct.
- **The storefront communicates the roadmap publicly** — keep it soft (no dates / promises) per the
  internal-conviction / public-humility rule.
- **Watch for gates sneaking back in as "premium gets X".** Every re-derivation of this model drifts
  toward a wall, because walls are easier to sell. The tell is grammatical: an amount is fine, an
  object is not.
- **Don't let the roadmap become a premium feature list.** It lives on the storefront, so the gravity
  is real -- a roadmap made mostly of future paid perks reads as a layaway plan, which is precisely
  the transactional frame this model avoids. Most items should be things everyone gets.
- **v1 leans entirely on positioning.** With no new feature, the storefront + story + flair have to
  carry it. If v1 feels hollow, the fix is better *storytelling*, not a rushed feature.
