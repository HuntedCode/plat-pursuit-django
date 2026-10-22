# Page Principles

Seven rules for what a page shows and in what order. They sit between the strategy
([product-identity.md](product-identity.md): why the product exists) and the look
([visual-identity.md](visual-identity.md), [design-system.md](../reference/design-system.md): how a page is drawn).
Neither of those answers "what belongs on this page, and what leads?", and the fresh-eyes reviews
([reviews/](reviews/README.md)) kept finding pages where nothing led.

Agreed with the owner on 2026-10-22, after three review slices. Every new page and every change to an existing
one is held to them, and the reviews judge against them.

## Why these exist

The site has a lot of good systems: badges, contracts, jobs, ratings, roadmaps, rarity, leaderboards, stats.
Each one was given a place on every surface it touches, at the same weight as everything else. The result is
pages where everything is shown and nothing leads: a browse card with eight rows, two of them saying "No";
a game page that lists the publisher above the fold but not that the hunter platinumed it; four catalogue
statistics in front of the results on every browse page. None of it is wrong on its own. Together it is a lot
to take in, every visit.

The fix is not fewer features. It is a default order: **the hunter first, then the page's one job, then depth
for whoever wants it.**

## The rules

### 1. The hunter is the focal point (signed in)

A signed-in hunter came for their own hunt. Every page answers "what does this mean for me?" before "what is
this?": their status, their progress, their next step lead, and catalogue facts follow.

- Game page: "You platinumed this, March 2024" or "45%, 12 trophies to go" in the hero, before the developer.
- Browse cards: whether the hunter owns, is playing, or has platinumed the game is the card's clearest mark,
  not a faint line under the art.
- A list of hunters (a leaderboard): the hunter's own row is findable without scrolling.
- The franchise page already does this ("Your progress" beside each game); that is the pattern.

Signed out, the same page minus the personal layer, and it must still stand on its own: visitors and search
engines see it. The personal layer **replaces** generic content where they overlap ("You platinumed this" can
take the slot "1,240 players" had); it does not stack on top of it.

### 2. One job per page, and it leads

Every page can say its job in one sentence ("find a game", "read a game", "claim what you earned"). The first
screen at 375px shows that job working: results on a browse page, the chosen view on Career, the hunter's
status on a game page. The header earns its height; anything that pushes the job below the first screen has to
be worth more than the job.

### 3. Show what is there, never what is missing

Absence is silence. No "No badges", no "Not on the Job Board", no tile of zeros. A row appears when it has
something to say. The one exception is an empty state that offers an action ("No games match. Clear filters",
"Be the first to rate this").

### 4. Depth on demand

Hunters love the extras: the stats, the rarity maths, the breakdowns. They stay, all of them. They just stop
being always on screen. Every surface keeps its job in front and its depth one tap or one scroll behind it:

- **A card previews.** It answers "is this the one I want?" and nothing more: art, title, the hunter's mark
  (rule 1), and at most two facts that help choose. Everything else lives on the detail page, one tap away.
  When a card grows a row, ask whether that fact helps someone choose from a grid of 30.
- **A page leads with its job, and keeps the rest below it or behind a tap**: a tab, an expanding section, a
  "More stats" row, a modal. The game page's About tab is the pattern: the facts are all there, and none of
  them stand between the hunter and their trophies.
- **Depth is findable.** Hiding something means putting it where a curious hunter looks next, with a label
  that says what is there. "Behind a tap" never means "gone".

### 5. A number counts what its label says, and agrees with the page

Every number on a screen counts the same thing as its label and the content beside it ("15,824 games" above a
grid of games, not trophy lists). The same fact shown in two places shows the same number (a game's players on
its card and on its page). Site-wide scale numbers ("40,125 trophy lists tracked") belong on the landing and
the heartbeat, not in the header of every page.

### 6. Defaults are visible

Any default that shapes what is shown (a platform scope, a sort, a hidden category) is stated where the results
are, or does not exist. A hunter must never get a different answer to the same question because of a setting
they cannot see.

### 7. Adding means replacing

A new element on an existing page names what it replaces or demotes. This is the rule that stops the drift
back: every system is good enough to deserve a place, so "it would be nice here too" is never the test. "It
beats what is here now" is.

## Checklist

For a new page, a change, or a review pass:

- [ ] Signed in: does the first screen say where the hunter stands on this page's subject?
- [ ] What is this page's job, in one sentence? Does the 375px first screen show it working?
- [ ] Is any row, tile or chip saying that something is absent?
- [ ] Does each card carry only what helps choose it, and does the page keep its extras behind its job?
- [ ] Does every number count what its label says, and match its twin elsewhere?
- [ ] Is every default that filters or orders the content visible next to the content?
- [ ] For anything added: what did it replace?

## Gotchas and Pitfalls

- **"Hunter first" is not "the hunter's numbers everywhere."** A wall of the hunter's own stats is as noisy as
  a wall of the catalogue's. Rule 1 picks the one personal fact that matters on this page.
- **Personal marks on a grid must be one bounded query.** Fetch the hunter's status for the ~30 visible cards in
  one query keyed by their ids, never per card, and never by loading the hunter's library (whales own 10,000+
  games; see "Per-User Querysets Must DB-Aggregate" in the project CLAUDE.md).
- **The signed-out page must stay cacheable and complete.** The personal layer renders only for signed-in
  requests; nothing personal may leak into a cached anonymous render, and search engines must get the whole
  page without it.
- **Removing a row is a design decision, not cleanup.** Moving a fact off a card means it must be findable on the
  detail page. Check it is there before deleting it from the card.
- **These rules order a page; they do not decide which pages exist.** That is information architecture, a
  separate review after the journey slices.
