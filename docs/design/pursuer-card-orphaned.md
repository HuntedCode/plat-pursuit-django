# Orphaned primitives: the Pursuer Card and the Frame

> **Two of the four signature primitives in [visual-identity.md](visual-identity.md) render nowhere in
> production.** The constitution still presents both as live brand vocabulary, which is a trap for exactly
> the reuse check a careful reader is supposed to run — it has now cost rework twice on one branch.

## The Pursuer Card: give it a home or delete it

**Status:** open question. Raised 2026-10-01, after the Hall of Fame plaque was built against this component
by mistake and the owner caught it.

---

## The question

The Pursuer Card is a complete, audited, tested component that **renders nowhere in production**. It should
either get a real mounting point or be deleted. Leaving it as it is has already cost one piece of work.

## How it got here

| Commit | Date | What happened |
|---|---|---|
| `d739bf2b` | 2026-06-29 | "Pursuer Card component, **mounted as the home hero**" |
| `7d27ccaa` | 2026-06-29 | Rank chrome dialled up + the per-rank design preview added |
| `4a730fd9` | 2026-08-13 | "**make / the lobby**, and Career My Pursuit's landing" — rebuilt `home_service.py`, dropped the card, and added `_disciplines_ring.html` in its place |

It was live for roughly six weeks. Nothing re-mounted it. The lobby rebuild was not a rejection of the card
as such — `/` changed job, from a personal hero to a router — but the card went down with the old shape and
no one re-homed it.

## What is currently inert

| | |
|---|---|
| `trophies/services/pursuer_card_service.py` | called only by the API view below |
| `templates/partials/components/_pursuer_card.html` | included only by the staff design preview |
| `templates/partials/components/_pursuer_card_cover.html` | — |
| `static/css/components/pursuer-card.css` | `@import`ed into the bundle; styles nothing on a user-facing page |
| `static/css/components/pursuer-card-forge.css` | the forge motion signature |
| `static/js/pursuer-card.js` | loaded only by the staff design preview |
| `static/js/pursuer-card-forge.js` | the scan-beam reveal, sparks, new-platinum slot-in |
| `api/pursuer_card_views.py` → `/api/v1/pursuer-card/` | routed and reachable; its only caller is the JS above |
| `/design/pursuer-card-ranks/` | `StaffRequiredMixin`. **The only renderer.** |
| `tests/engine/test_pursuer_card_endpoint.py` | still green, still testing an unmounted component |

The forge is the part worth weighing most: a bespoke motion signature (scan-beam reveal, arcing sparks,
staggered content reveal, number tick-ups, a conveyor slot-in for a newly earned platinum) built from the
Frame's fabrication vocabulary and explicitly **not** from `celebrations.js`. It fires on a real
`syncing → synced` transition and on catch-up. That is the single most elaborate motion piece in the
codebase and it currently cannot fire for anybody.

## Why it matters beyond dead code

**It actively misled this branch.** Building the Hall of Fame plaque, the reuse check searched for "Pursuer
Card" — because `visual-identity.md` named it a primitive and the lane's own notes described it as live on
the home hero — found this component, and modelled a five-tile discipline band on it. The live primitive for
that job, `_disciplines_ring.html`, was one directory away and in use on both the Career hero and the lobby,
with a docstring that says the second host must be *"a scale, not a second implementation, which is what
keeps the two surfaces from drifting"*. The plaque shipped that second implementation, carrying three WCAG
AA failures, a sub-12px label that broke a type-floor guard, and a five-across grid that squeezed to ~37px
at the primary breakpoint. All of it disappeared when the plaque switched to the ring.

An orphaned component that still appears in the design constitution is a trap for exactly the search a
careful reader is supposed to run.

**It also carries a live-by-design defect.** `pursuer_card_service` proportions each discipline bar against
the hunter's *strongest* discipline, which is undefined when all five sit at the level-1 floor and resolves
to 100% — so a hunter with no job XP gets **five full bars**, claiming mastery of everything.
`_pursuer_card.html` gates the markup only on a truthy five-element list, so nothing stops it. Dormant today
because nothing renders it; it would ship the moment the card is re-mounted. Whoever re-homes it fixes that
on the way in. (The ring avoids it structurally by proportioning against the sum.)

## The three options

**1. Re-home it on the profile header.** The lane's own deferral list already names this
("profile header — profile is still legacy; Jeffrey doesn't want a profile redesign yet"), and the profile is
the surface whose job *is* "who you are", which is the card's stated purpose. Cost: the profile redesign that
was deliberately deferred. This is the option that makes the forge mean something again.

**2. Keep it parked, deliberately and visibly.** Lowest effort. Requires only that
`visual-identity.md` keep saying plainly that it is mounted on nothing — which it now does, with the commits
— so the next reuse check is not misled. The risk is that "parked" and "abandoned" look identical after
another six months, and the forge keeps aging against a motion vocabulary that moves.

**3. Delete it.** The project's own rule is *"Prefer Deletion Over Deprecation: unused code gets deleted, not
wrapped or re-exported."* Taken literally, this qualifies. Against: it is a large, audited, workshop-locked
piece of design work, the design constitution still describes it as a primitive, and the decisions behind it
are recorded rather than obvious — deleting the code throws away the cheap part and keeps the expensive part
(the decisions) only as prose.

## Recommendation

**Option 2 now, option 1 when the profile is redesigned, and a decision point rather than a drift.** The
constitution's correction has removed the trap that actually cost something, and the component is not
costing anything else per day beyond a stylesheet in the bundle. But "parked" should mean a named next
surface, which is the profile header — so the honest version of option 2 is *"parked until the profile
redesign, and that is when it gets re-homed or dropped."*

What should **not** happen is a third surface being built against it by accident. The Hall of Fame plaque
tried; only a browser pass caught it.

## If it is re-homed, fix these on the way in

- The five-full-bars bar scaling (above). Proportion against the sum, as `discipline_ring` does, or gate on
  the hunter having any job XP.
- Check `--pc-tier` against `--rank-*`. The card groups eleven rungs into two hues; `elements.css` defines a
  full per-rank spectrum and both `career.html` and the Hall of Fame plaque read it. The card is the one
  surface out of step, so the same Marshal is violet on the card and olive everywhere else.
- `.pursuer-card__fam-l` and `.pursuer-card__cap` are 9px. No floor guard scans `pursuer-card.css`, which is
  how that value was copied into `challenges.css` and became a red test there.

---

## The Frame is in the same position

Found while removing the plaque's corner diamonds (owner, 2026-10-01: *"I don't like the diamonds on the
corners either, they don't really look great"*). The diamonds are **the Frame's** brand mark, and the Frame
is rendered by **nothing at all**.

An earlier version of this section said "nothing but `templates/design/frame_preview.html`". That is wrong,
and wrong in the direction that weakens the argument: `frame_preview.html` is a **self-contained prototype**
with zero `{% include %}` tags and its own inline CSS and JS, so it does not mount `components/frame.html`
either. Repo-wide, the only mention of `frame.html` outside the file itself is a docstring line in
`badge_medallion.html`. The production Frame partial has no renderer whatsoever.

| | |
|---|---|
| `templates/components/frame.html` | **included by nothing.** The design preview does not include it; it is a self-contained prototype |
| `templates/components/_frame_face_front.html` | included only by `frame.html`, which nothing includes |
| `static/css/components/frame.css` | `@import`ed into the bundle |
| `static/js/frame.js` | **loaded by no template at all**, not even the preview |
| `templates/design/frame_preview.html` | the reference implementation of the Earn Moment (twelve phases), and a prototype independent of the partials above |

**What replaced it is live and thriving.** The [Badge Medallion](../reference/badge-medallion.md) renders in
**19 templates** — the collection gallery and its two modals, badge detail, badge list and its gallery card,
game detail and its hero/header partials, the profile badges tab, the title plate, two recap slides, the
fundraiser and the anonymous landing page — and it draws **no corner notches**. (An earlier version of this
paragraph named five, which understated this doc's own argument.) `visual-identity.md` records the handover for
`/collection/` (2026-07) and says a site-wide Frame → Medallion migration is "under evaluation"; in practice
the migration appears to have completed by attrition, with the Frame keeping its primitive status in the doc
and losing every mount in the code.

**The Earn Moment is the thing to weigh.** `frame_preview.html` is the canonical demonstration of the kit's
motion and particle vocabulary — twelve phases, the welding/scanning language the Brief calls canon, and
explicitly the pattern future earn moments are supposed to draw from. That is a design asset whether or not
the Frame card itself ever renders again, and deleting the Frame would take it with it.

**The question is narrower than the Pursuer Card's**, because the replacement is unambiguous and already
everywhere: does the Frame still exist as a primitive, or is the Badge Medallion the primitive now and the
Frame a historical prototype that happens to host the Earn Moment reference? If the latter, the constitution's
Section 3 needs rewriting around the Medallion, and `frame.js` can go today — it is loaded by nothing.

**The Frame's case for deletion is stronger than the Pursuer Card's on every axis.** The Pursuer Card at
least has a routed API endpoint rendering its partial and a named candidate surface (the profile header). The
Frame's partial has no renderer, no endpoint, and a replacement in active use across the badge surfaces. What
holds it back is one thing only: the Earn Moment reference lives in the preview file, which is independent of
the partial and could be kept on its own.

**Shared recommendation for both:** the cheapest correct move is not deleting code, it is making the
constitution stop describing unmounted components as live vocabulary. That is what actually misled the work.
The code can wait for the profile redesign (Pursuer Card) and the Frame/Medallion decision (Frame).

---

## Related

- [visual-identity.md](visual-identity.md) — the Pursuer Card and Frame primitives, now with real mounting history
- [challenge-systems.md](../features/challenge-systems.md) — "The band that became a ring", the incident
  that raised this
- `trophies/services/job_render.py` — `discipline_ring`, which now owns the arc geometry for all three hosts
  of the live ring
