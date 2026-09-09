# What's New

**Status:** shipped 2026-09
**Code:** `core/whats_new.py` (`Entry.safe_link_url` is the render-time link guard), `core/sitemaps.py`, `core/views.py` (`HomeView`, `WhatsNewView`), `api/user_settings_views.py`, `templates/trophies/partials/home/_whats_new.html`, `templates/trophies/partials/home/_home_modal_gate.html`, `templates/pages/whats_new.html`
**Tests:** `tests/engine/test_whats_new.py`

A one-shot modal on the Home lobby announcing the newest thing we shipped, plus a public archive at
`/whats-new/` holding every entry.

> **Not the old one.** A `core/services/whats_new.py` existed once, feeding a "What's New" module on the
> dashboard: a live feed of recently added GAMES. It went with the dashboard in the 2026-08 cutover and
> is documented as deleted in [homepage-services](../reference/homepage-services.md). This is an
> announcement channel about the SITE, unrelated to it and sharing only the name.

---

## Adding an entry

Put it at the **top** of `ENTRIES` in `core/whats_new.py` with a new `id`. That is the whole procedure.

```python
Entry(
    id='2026-10-something',            # never reuse an id; it IS the seen marker
    published=date(2026, 10, 1),
    title='What shipped',
    beats=(('Label', 'A sentence.'), ('Label', 'Another.')),
    link_label='Take a look',          # optional
    link_url='/somewhere/',            # optional, must be a path we own
)
```

The modal shows **only the newest** entry a hunter has not dismissed. Older ones live on the archive
page, which the modal links to.

## Why the content is code

A feature ships in a deploy, so the sentence announcing it ships in the same deploy, reviewable in the
same PR as the thing it describes. A wrong claim about a feature gets caught by whoever reviews that
feature. The alternative (a model + a staff CRUD) buys publishing without a deploy and costs a table, a
migration, two screens, an audit-log entry and a preview door.

The one thing it cannot do: announce something on a day you are not deploying.

## What a deploy does and does not do

| You do | What users see |
|---|---|
| Deploy anything, no entry added | Nothing. No modal for anyone. |
| Deploy and add one entry | Only hunters who have not seen that id, once each. |
| Deploy five more times | Nothing new. Same entry, already seen. |
| Fix a typo in an existing entry | Nothing. Same id, still seen. |
| Delete an entry someone had dismissed | They see the new newest one. One extra modal, never a stuck one. |

## The "seen" marker

`CustomUser.ui_flags['whats_new_seen']` holds the **id of the newest entry the hunter dismissed**, written
through the quick-settings API's `whats_new_seen` branch.

An **id, not a boolean.** A boolean would make the first entry the last one anybody ever saw, which is
the difference between an announcement channel and a one-time notice.

Its own API branch rather than a value on `ui_flag`, because that branch is documented as sticky
booleans for one-shot education flags and this is a moving marker. Sharing it would have left one of the
two behaviours undocumented in the place someone reads to learn what the flag means.

The submitted id is **validated against the shipped entries**. An arbitrary string would match no entry
ever, permanently suppressing the modal for that account with nothing in the UI able to undo it.

## Who is due one

`whats_new.is_due(user)` — the user is authenticated, an entry exists, and its id is not the one stored
on them. The view adds one more condition the function deliberately does not know about: the whole block
lives inside `state == 'synced'`, so a signed-in hunter with no linked PSN never reaches it.

**New accounts are included, deliberately.** An earlier cut skipped anyone who signed up after the entry
was published, on the reasoning that a feature they have always had cannot be new to them. That is true
about the *feature* and wrong about the *message*: what a new hunter takes from the notice is that the
site is actively being built and worked on, which is worth more to them than the literal accuracy of the
word "new". The date on the entry is what keeps it honest — they can see it landed before they arrived.

## Dates

Every entry carries a `published` date, and it is rendered in **both** places: beside the eyebrow in the
modal, and on each entry in the archive. Both use `<time datetime="YYYY-MM-DD">`, so the human-readable
form can change without breaking anything that reads the page.

It is not decoration. It is what makes "new" a claim a reader can check rather than one they take on
trust, and it is what lets the rule above be as simple as it is.

## How people reach it

| Route | Who sees it |
|---|---|
| The modal on Home | Hunters with a **linked, synced** profile and an undismissed entry, once each |
| Avatar dot + menu marker | Every signed-in hunter with an unread entry, on **every page** |
| Avatar menu → What's New | Every signed-in hunter, always |
| Footer → What's New | Everybody, signed in or not |

The chrome links are not optional garnish. The modal is **render == armed**: a dismissed notice leaves no
markup on the page at all, so without a door in the chrome a hunter who closed it by reflex has no route
back to what it said, and the archive is a page nothing links to.

### The attention dot

A primary-coloured dot on the avatar, plus a "New" pill on the menu row, driven by
`whats_new_unread` (`plat_pursuit/context_processors.py`).

**It is not redundant with the modal**, which is the objection that kept it out of the first cut. The
modal fires on the **lobby**, for **synced** hunters only. It reaches nobody who lands deep from a
bookmark or a link, nobody signed in without a linked PSN (the whole block sits inside
`state == 'synced'`), and nobody who closes it by reflex having read nothing. The dot is the signal for
exactly those people.

**Zero queries**, and it must stay that way: it runs on every render of every page, including the Django
admin. `ui_flags` rides the user object authentication already loaded, and the entries are a module
constant. Pinned by a `django_assert_num_queries(0)` test.

**The moderation queue outranks it.** Both live in the avatar's top-right corner, and the template
suppresses the dot whenever the queue badge is present — a report backlog is a problem to clear, this is
not. It also uses the primary colour rather than the queue's error red, so red keeps meaning "something
is wrong" rather than "you owe someone something".

**No mobile tab-bar marker.** The avatar has no breakpoint rules — the tab bar replaces the navbar's
*hub buttons*, not the avatar — so the dot already covers phones. A tab-bar version would have needed a
fifth tab for something that is not a hub.

### Reading the archive clears it

Opening `/whats-new/` marks the newest entry seen, which clears the dot **and** retires the modal.

This reverses the original rule ("reading is not dismissing", which existed so a shared link could not
burn someone's notice). It became untenable the moment the dot shared that marker: a reader who clicks
the dot, reads the page and returns would still have the dot, with no way to clear it by doing the
obvious thing. One marker, and reading is dismissing.

The clear is a **POST from the page**, not a side effect of the GET — this page is public and crawlable,
and a mutating GET is the wrong shape whoever can reach it. It is gated on the unread flag, so an
ordinary re-read costs no request, and it never fires for anonymous readers.

## The archive's spine

The entries are drawn as a vertical timeline: a connector down the left with a node per entry, the
newest one lit.

**A calendar was the other candidate and would have lied.** Entries are sparse and irregular, so a month
grid is thirty cells with two lit, and every empty cell reads as "nothing happens here" — the opposite of
what an announcement page is for. A timeline with two nodes reads as the *start of a record*. Same data,
opposite impression. It also self-scales: identical design at 2 entries or 50, and it works at 375px,
which a right-hand rail does not.

**The nodes carry no text.** Each card prints its own date in full; repeating it on the spine an inch to
the left would be printing it twice.

Drawn in the same idiom as game detail's journey timeline (`.gd-mystats__tl`) — connector from a
`::before`, node pop on a spring curve. **Not extracted into a shared primitive:** that is the only other
vertical timeline on the site (badge detail's is a horizontal first/last strip), so this is the second
consumer and the bar is three. A third is the trigger, and the pair to generalise then is these two.

Motion is the shared `.pp-arrive` section arrival. Its three-path degradation is load-bearing here: the
hidden state is armed pre-paint in `extra_head`, so if the JS bundle fails to load, **nothing is left to
reveal the entries** and the page is a header above an empty column. The boot script removes the arm when
the helper is missing. Pinned by test, because the failure mode is a blank page rather than an unstyled
one.

### Deferred: jump-to navigation

A sticky table of contents was considered and deferred. With a handful of entries it lists titles already
fully on screen, and it is inherently desktop-only, so the page has to be good without it either way. It
earns itself somewhere around 8-10 entries — at which point sticky year markers on the spine are the
cheaper form, reusing the existing sticky-header pattern that already solves the offset under the chrome.

## Precedence against the 1.0 greeting

**Never both on one visit.** The 1.0 launch greeting wins; What's New waits for the next visit.

The greeting fires exactly once in an account's lifetime and cannot be deferred to a better moment.
What's New can: its entry stays undismissed, so it is simply due again. Nothing is lost.

Decided in `HomeView.get_context_data`, the one place that can see both. `is_due` deliberately knows
nothing about the greeting.

> `PP_LAUNCH_DATE` is **armed on prod** (2026-09-01), so this rule is live: anyone who joined before
> that instant and has not dismissed the greeting gets it first, and What's New on their next visit.
> See [onboarding](onboarding.md).

### The 1.0 entry is on the archive, and only there

`2026-09-platpursuit-1-0` records the rebuild at the launch instant. It is **archive-only**, and by
position rather than by a flag: `latest()` is `ENTRIES[0]`, so nothing below the top can ever fire.

That is the point. 1.0 already has its own greeting modal, so an entry that could also pop would show a
hunter the same announcement twice, once in each. If an entry ever needs to be recorded without popping
*while it is the newest*, that is the moment to add a flag for it — there is no such entry today, so
there is no flag.

## The lobby's choreography gate

Home's on-load motion (count-ups, Horizon fills, ring pace) must not play out behind a modal's scrim.
`home-motion.js` waits on `window.ppAfterHomeModal`.

The gate is **armed by the page** (`_home_modal_gate.html`), not by either modal, and this is the part
that is easy to get wrong. It must be armed *synchronously*, before end-of-body scripts run, because
`home-motion.js` is one of those scripts and asks the question the moment it loads — while the modals
are wired through `PlatPursuit.DetailModal`, which lives in end-of-body `utils.js`. A gate armed
alongside the modal wiring would arm after the code waiting on it had already given up.

So: the page arms, the modal reports back through `onSettled`.

It was `ppAfterLaunchWelcome`, owned by the greeting's own inline script, which was correct while that
was the lobby's only modal.

## Gotchas and Pitfalls

- **The ID-scoped `is-closing` rule is mandatory, not styling.** `badge-inspect.css` defines
  `.pp-detail-modal.is-closing` **unscoped**; a clone without its own id in `series-list.css` inherits it
  and dissolves the dialog's chrome while the text inside stays fully opaque. Found by audit twice, on
  two different modals, before the rule was consolidated into one selector list with the warning above it.
- **Every path that ends with no modal on screen must settle the gate** — the dismissal, the auto-open
  skipped because the reader was typing, and the auto-open skipped because this device already dismissed
  it. Miss one and the lobby's numbers sit frozen at their server-rendered values. Those values are
  correct (they are what no-JS readers see), so it fails quietly rather than visibly. A 4-second backstop
  in the gate partial releases it regardless, for the case where the JS never runs at all.
- **A dismissing LINK must still navigate.** Every close control marks the entry seen, and two of them
  are `<a href>`. The controller's click handler cannot blanket-`preventDefault` (that dismisses the
  modal and goes nowhere) and cannot simply let the click through either (an in-flight request is
  cancelled on unload, so the dismissal is lost and the reader meets the same notice again having
  already clicked it). It holds the navigation until the write settles, capped at 600ms so a hung
  request never strands anybody. Modified and middle clicks are left to the browser and only recorded.
- **The archive page must not mark anything seen.** Reading the record is not dismissing the notice; a
  hunter who arrives from a link should still meet the modal on Home.
- **Entry links must stay on-site.** The modal opens itself, unasked, over the page — a link out of it is
  the shape of a phishing lure. Pinned by test.
- **`ENTRIES` order IS the ordering.** `latest()` is `ENTRIES[0]`, not a `max()`, so an entry appended to
  the bottom ships as content nobody is ever due. Pinned by test.
- **The archive VIEW runs zero queries.** The request is not query-free for a signed-in reader — session,
  user, profile and the navbar all cost — and the page is linked from the avatar menu, so that is the
  common case. Anonymous really is Postgres-free (every context processor short-circuits). Do not grow
  the view a per-user branch: the moment it reads a profile it stops being the cheapest page on the site
  for no gain a reader can see.

## Related

- [Onboarding](onboarding.md) — the 1.0 greeting and the `ui_flags` one-shot mechanism
- [JS utilities](../reference/js-utilities.md) — `PlatPursuit.DetailModal`
