# What's New

**Status:** shipped 2026-09
**Code:** `core/whats_new.py`, `core/views.py` (`HomeView`, `WhatsNewView`), `api/user_settings_views.py`, `templates/trophies/partials/home/_whats_new.html`, `templates/trophies/partials/home/_home_modal_gate.html`, `templates/pages/whats_new.html`
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

`whats_new.is_due(user)` — an entry exists, and its id is not the one stored on the user. That is the
whole rule.

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

## Precedence against the 1.0 greeting

**Never both on one visit.** The 1.0 launch greeting wins; What's New waits for the next visit.

The greeting fires exactly once in an account's lifetime and cannot be deferred to a better moment.
What's New can: its entry stays undismissed, so it is simply due again. Nothing is lost.

Decided in `HomeView.get_context_data`, the one place that can see both. `is_due` deliberately knows
nothing about the greeting.

> Note: the greeting is dormant until `PP_LAUNCH_DATE` is set, so while that is unset this rule is a
> no-op that becomes real the moment the date is armed. See [onboarding](onboarding.md).

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
- **The archive page runs zero queries.** Do not grow it a per-user branch; the moment it reads a profile
  it stops being the cheapest page on the site for no gain a reader can see.

## Related

- [Onboarding](onboarding.md) — the 1.0 greeting and the `ui_flags` one-shot mechanism
- [JS utilities](../reference/js-utilities.md) — `PlatPursuit.DetailModal`
