# Platinum Journey, Design Document

> **Status:** Vision document. Not implemented. Shelved until after the current site redesign branch ships as its own headline update.

## Context

PlatPursuit collects an enormous amount of personal trophy hunting data per user: completed games, in-progress games, ratings, reviews, playtime, badge progress, genre/theme/engine affinities (via IGDB enrichment), challenge participation, and 120+ derived stats. Today this data is presented as discrete views (dashboard modules, the stats page, the badge grid, etc.) but nothing **synthesizes** it into a personalized, forward-looking experience.

**Platinum Journey** is the synthesis layer. It is a premium feature centered on a named **companion character** who has "been thinking about you while you were away." The companion knows everything PlatPursuit knows about the user, has opinions about their habits, and helps them plan and pursue their next trophies, badges, and goals. It is intended to be the **flagship premium feature** of a future major release.

This is not a recommendation engine wrapped in a UI. It is a relationship surface, designed to feel alive without ever being intrusive.

---

## Core Philosophy: The Patient Companion

The defining metaphor:

> **A patient, knowledgeable friend who has been thinking about you while you were away.**

Two principles flow from this metaphor and resolve what would otherwise be a tension between "alive" and "user-controlled":

1. **The companion is always alive in the background**, observing, noticing patterns, forming opinions. Memory and presence are what make it feel real.
2. **The companion never acts autonomously.** It does not push notifications, it does not write to other systems, it does not nag. When the user visits the page, the companion has things ready to share. When the user leaves, it goes back to thinking.

This is the difference between a friend and a chatbot. A friend remembers your last conversation. A friend has opinions but waits to be asked. A friend celebrates your wins without asking you to react to them. A chatbot interrupts.

### What this rules out

- No push notifications "from the companion" (regular PP notifications still work, but they are not the companion's voice).
- No automatic XP grants, badge awards, or stat changes from Journey activity. Journey is a **read-only consumer** of every other system.
- No "you must respond" UI patterns. Every interaction is opt-in.
- No auto-generated multi-page reports the user has to scroll through. Information is offered in conversational chunks.

### What this enables

- The companion can have a rich internal life (observations, opinions, memories) without forcing it on the user.
- The questionnaire problem dissolves: instead of a 30-question wall on first visit, the companion asks 1-2 questions per visit, ranked by usefulness. The "profile" builds gradually over months.
- The architectural rule (read-only, one-way coupling) makes the system safe to extend. Future systems can plug in as new data sources without the Journey ever becoming a bottleneck.

---

## The One-Way Coupling Rule

This is the load-bearing architectural constraint of the system, documented here so it cannot be violated later.

> **Platinum Journey reads from every system in PlatPursuit. It writes to nothing outside its own tables.**

### Concretely

- The Journey can read trophy progress, badge progress, ratings, reviews, IGDB metadata, challenge state, stats, dashboard preferences, and anything else PP knows about the user.
- The Journey can NOT grant XP, award badges, modify stats, advance challenges, mark games complete, or trigger any state change in any other system.
- Other systems do not depend on the Journey existing. Removing the Journey app should not break anything else.

### Why

- **Avoids muddying other systems.** A user platting a game should be rewarded by the badge and stat systems on its own merits, not because it was "on their journey." Coupling the two cheapens both.
- **Future-proofs against new systems.** When the gamification vision (`gamification-vision.md`) ships, the Journey will read from it. When new features land later, the Journey will read from those too. None of them need to know the Journey exists.
- **Keeps the Journey safe to iterate on.** Vision will refine, voice will be rewritten, recommendation logic will swap out. None of that should require migrations in unrelated apps.
- **Reflects the philosophical model.** A friend who tracks your progress is helpful. A friend who unilaterally changes your scorecards is a problem.

### The one exception

Journey-internal state (threads, observations, answers, events) is fully owned by the Journey and writes freely within its own tables. The rule is about **inter-app boundaries**, not internal model writes.

---

## The User Experience

This section describes what the Journey **feels like** rather than how it is implemented. The implementation must serve this experience, not the other way around.

### Entry points

**1. The dashboard module.** A single tile on the dashboard shows the most pressing thing the companion wants to share, in one or two sentences. Click-through goes to the full Journey page. Never autoplays, never demands attention. Free users see a teaser version (see Free Tier section).

**2. Direct navigation.** A top-level link in the navbar (premium users) takes them to `/journey/`.

**3. Profile cross-link.** A small companion presence on the user's own profile page, linking to the journey.

### The Journey page itself

Landing on the page is the moment that has to feel alive. The page is structured as a vertical conversation, not a dashboard.

**Section 1: The Greeting**

A context-aware welcome from the companion that references what has happened since the user's last visit. Rich with personal detail. Examples:

> "Welcome back. You finished Hollow Knight on Tuesday, that's your fourth metroidvania this year. I noticed you rated it five stars. I have some thoughts about that."

> "It's been a quiet week. Your Bloodborne save hasn't moved in 11 days. No judgment, sometimes a game needs to sit. Want to talk about something else?"

> "Two weeks since we talked. Your platinum count went up by three. I'm proud, but also a little suspicious you might be dodging me."

The greeting is generated from a combination of:
- Time since last visit
- Events since last visit (new plats, new badges, new ratings, abandoned saves)
- Long-term observations the companion has been holding
- A rotating bank of voice templates per situation

**Section 2: The Threads (Conversational Entry Points)**

Below the greeting, a small set of invitations. Not a menu of buttons, but framed offerings the companion is making.

- *"Want to talk about what's next?"* (opens recommendations)
- *"I noticed something about your Souls runs. Curious?"* (opens an observation)
- *"I have a question for you, if you have a moment."* (opens a questionnaire trickle)
- *"Your Trophy Hunter badge is closer than you think."* (opens a badge nudge)

The user clicks one (or none) and the conversation continues. Each thread expands inline. Nothing leaves the page unless the user explicitly navigates away.

**Section 3: Active Pursuits**

A living list of things the user has explicitly **accepted** into their journey. Each is a "thread" the companion is helping with. Types:

- **Game pursuits**: a specific game the user is chasing
- **Badge pursuits**: a specific badge the user wants to earn
- **Genre pursuits**: an exploration goal ("try three JRPGs this season")
- **Custom goals**: arbitrary user-stated goals ("plat 12 games this year", "no more shooters until I finish my backlog")
- **Companion suggestions accepted**: anything the companion proposed and the user said yes to

Each pursuit is **user-managed**: pause, retire, complete, swap, edit notes. The companion adds its own notes alongside the user's. This is the "plan" but it grows organically out of conversation, not from a setup wizard.

Importantly, **completing a pursuit grants nothing in other systems**. If the user plats a game on their pursuit list, the badge system rewards them for the plat, not the Journey. The Journey only updates its own internal state.

**Section 4: The Notebook**

A scrollable archive of past observations, conversations, and accepted/retired pursuits. This is the **memory surface** that makes the relationship feel real. Users can revisit "what was the companion saying about me last summer?" Entries are timestamped and grouped by month or season.

The notebook is **infinite**. We accept the database growth and revisit if it ever becomes a real problem.

**Section 5: Settings and Preferences**

Tucked away. Mix slider (more new games vs more finishing started games), mute topics, opt out of negative pattern observations, voice intensity (chatty vs sparse), notification preferences for Journey-relevant events. The companion respects all of these without commenting on them.

### What the page is NOT

- Not a dashboard with widgets and charts
- Not a checklist or task manager
- Not a recommendation feed
- Not a chatbot the user types into (at least not in v1)
- Not a quiz or onboarding flow

It is a **single living conversation surface** that absorbs the chat, the recommendations, the goals, and the memory into one unified experience.

---

## Voice and Identity

### Decision: Named Character (Option A)

The companion will be a **named character** with a distinct visual design, personality, and voice. This is locked in.

**Rationale:**
- The Plat Pursuit charm depends on personality. A neutral assistant would feel sterile and undermine the entire premise.
- A named character is a marketing asset (mascot art, social media voice, potential merch, recurring presence in monthly recap and other features).
- It makes the experience instantly memorable and shareable ("my companion said this about me today").
- It gives writers (you, me, future contributors) a clear voice to write toward.

### Character details: TBD

The specific character is to be designed in collaboration with the artist. Open questions for the artist conversation:

- **Form**: humanoid, creature, abstract, object-based (e.g. a sentient trophy)?
- **Age and energy**: young and excited, old and wise, middle-aged and dry, ageless?
- **Connection to the PP world**: standalone mascot, or tied to existing PP iconography/characters?
- **Visual flexibility**: does the character need expressions/poses for different moods (celebrating, concerned, curious, proud)?
- **Animation potential**: static art only, or simple Lottie/CSS animations on the page?

### Voice guidelines (regardless of final character)

The voice must hit these notes consistently:

- **Warm, never cold.** The companion likes the user and is rooting for them.
- **Knowledgeable, never condescending.** The companion knows trophy hunting deeply and treats the user as a peer.
- **Curious, never invasive.** The companion is interested in the user's preferences but accepts "rather not say" without comment.
- **Humorous, never sarcastic to the point of meanness.** Light teasing is fine. Cruelty is not.
- **Brief, never lecturing.** A few sentences at a time. Multi-paragraph monologues are forbidden.
- **In-world, never breaking the fourth wall.** The companion exists inside PlatPursuit. It does not say things like "as an AI" or "according to your data."

### The "humorous, never therapist" rule

Negative pattern recognition is allowed and encouraged, but it must always stay in trophy hunting territory and never veer into personal/emotional territory. Compare:

- **OK**: "You haven't touched your PSN in three weeks. I assume you've been busy. The trophies will wait."
- **OK**: "Your Bloodborne save has been gathering dust. Want me to suggest something easier on the soul?"
- **NOT OK**: "You seem to have lost motivation lately. Is everything alright?"
- **NOT OK**: "You're playing a lot more games than usual. Is there something on your mind?"

The companion is a hunting buddy, not a therapist. When in doubt, lean **aloof and humorous** rather than concerned.

---

## Premium Model

### Premium users (full experience)

- Full Journey page with all sections active
- Companion remembers everything indefinitely (the notebook is infinite)
- Unlimited active pursuits
- All recommendation types (games, badges, genres, custom goals)
- Questionnaire trickle continues over time
- Companion celebrates plats and badges with personalized messages
- Dashboard module shows real, personalized content

### Free users (the teaser)

The free tier exists to **introduce the companion** and create desire, not to be a watered-down product. The goal: a free user should walk away thinking "I want to keep talking to them."

**The free user experience:**

1. **First visit**: The companion introduces itself with a personalized greeting drawn from the user's existing PP data. This is the "wow, it really knows me" moment. ("Hi. I've been looking through your trophies. You really like 2D platformers, don't you? You've platted seven. I have some opinions about that.")
2. **One free observation**: The companion shares one genuine, useful observation about the user's habits or library. Not a generic one. A real one, drawn from real data.
3. **One free pursuit suggestion**: The companion suggests one specific game or badge the user might want to chase, with the "why this" explanation.
4. **The handshake**: The companion explains that this is just a taste, and that with premium it can keep talking to them, remember their conversations, and help them plan.
5. **Locked sections** with peek previews: The notebook, full pursuits list, and ongoing conversation are visibly present but locked. Hover/click shows a friendly upsell from the companion itself ("There's more I'd love to share. The full me lives here.").

The dashboard module for free users shows the same teaser concept: a friendly hook with a clear premium upgrade path.

### Why this is a strong sales tool

- The free user gets a **real** sample, not a marketing pitch. This builds trust.
- The companion's personality does the selling, not a feature list.
- The "I want to keep talking to them" hook is emotional, not transactional.
- It introduces the companion to the entire user base, which has marketing/social media value beyond the conversion itself.

---

## Data Model Sketch

This is a high-level sketch, not migration-ready. Field names will likely change.

### `JourneyProfile` (one-to-one with `Profile`)

The user's relationship state with the Journey.

- `profile` (OneToOne to Profile)
- `companion_introduced_at` (when first visit happened)
- `last_visit_at`
- `visit_count`
- `mix_preference` (slider value: heavy_new_games <-> heavy_finishing)
- `voice_intensity` (sparse, normal, chatty)
- `negative_observations_enabled` (bool, default true)
- `muted_topics` (JSON list of topic slugs the user does not want raised)
- `learned_facts` (JSON blob of stable preferences inferred or stated, e.g. "dislikes horror", "limited play time on weekdays")
- `questionnaire_state` (JSON tracking which questions have been asked/answered/skipped)

### `JourneyThread`

An active or historical pursuit. The "threads" the user has accepted into their journey.

- `profile` (FK to Profile)
- `thread_type` (enum: game, badge, genre, custom_goal, companion_suggestion)
- `target_object_type` + `target_object_id` (generic FK to Game, Badge, Genre, etc., nullable for custom_goal)
- `title` (display)
- `companion_notes` (the companion's framing of this pursuit)
- `user_notes` (the user's own notes)
- `status` (proposed, active, paused, completed, retired)
- `created_at`
- `accepted_at` (nullable)
- `completed_at` (nullable)
- `priority` (user-orderable)

### `JourneyObservation`

Something the companion noticed and wants to share. Stored persistently so it lives in the notebook.

- `profile` (FK to Profile)
- `observation_type` (enum: pattern, milestone, suggestion, follow_up, celebration, gentle_nudge)
- `topic` (slug, used for muting)
- `template_key` (which voice template was used)
- `context_data` (JSON, the data the observation references)
- `rendered_text` (the final text shown to the user, frozen at generation time so the notebook stays consistent)
- `surfaced_at` (when the companion offered it)
- `user_response` (enum: ignored, dismissed, engaged, accepted_as_thread, nullable)
- `seen` (bool)

### `JourneyQuestion`

The bank of questions the companion can ask the user. These are static, defined in code or admin, not per-user.

- `slug` (unique key)
- `question_text` (templated)
- `question_type` (one_time, periodic, contextual)
- `topic` (slug)
- `triggers` (JSON: when the companion would consider asking this)
- `answer_schema` (JSON: how the answer should be structured)
- `priority`

### `JourneyAnswer`

A user's answer to a question. Some questions are answered once and become permanent facts, others are periodic.

- `profile` (FK to Profile)
- `question` (FK to JourneyQuestion)
- `answer_data` (JSON)
- `answered_at`
- `skipped` (bool, if user opted not to answer)

### `JourneyEvent`

Lightweight audit log of significant moments in the relationship. Used to power "remember when..." references.

- `profile` (FK to Profile)
- `event_type` (visit, thread_accepted, thread_completed, observation_engaged, question_answered, milestone, etc.)
- `payload` (JSON)
- `occurred_at`

### Notes on the data model

- All Journey models live in their own app or a clearly-bounded module to enforce the one-way coupling rule physically, not just by convention.
- Foreign keys to other apps (Game, Badge, Profile) are read-only references. The Journey never modifies those objects.
- The notebook view is built from `JourneyObservation` and `JourneyEvent` joined and sorted by time.
- Storing `rendered_text` on `JourneyObservation` means voice template changes do not retroactively rewrite history. The notebook is a faithful record of what was actually said.

---

## Service Architecture Sketch

Three services, layered. Each has a clear single responsibility, and the boundaries between them are where future ML/LLM enhancements will plug in.

### 1. Context Builder

**Responsibility:** Assemble a rich, read-only snapshot of everything PlatPursuit knows about a user, in a shape the recommendation engine and voice service can consume.

- Aggregates trophy progress, badge progress, ratings, reviews, playtime, IGDB metadata affinities, challenge state, stats, recent activity, abandoned saves, etc.
- Returns a structured `JourneyContext` object (in-memory, not persisted).
- Heavy use of `select_related` / `prefetch_related` to keep query count bounded.
- Cacheable per-user with short TTL (5-15 min) since user state changes during gameplay.

This service is the "knows everything about you" layer. It is purely a reader.

### 2. Recommendation Engine

**Responsibility:** Given a `JourneyContext`, generate candidate observations, suggestions, and questions, ranked by usefulness.

- **v1: Rule-based.** A library of generators, each of which inspects the context and proposes candidates with confidence scores. Examples:
  - "User has rated 4+ stars on N games of genre X" -> suggest more genre X games
  - "User has 80%+ progress on a badge" -> suggest finishing it
  - "User has 5 in-progress games untouched for >30 days" -> offer to discuss them
  - "User has never tried genre Y but has rated genre Z highly, and Y/Z share themes A and B" -> suggest a Y game
- **v2: ML re-ranker.** Once telemetry exists on which suggestions users accept/reject, train a simple model (logistic regression or gradient boosted trees) to re-rank candidates from the rule-based generators. The generators stay; the ML just reorders them.
- **v3: LLM-assisted generation.** Optional far-future enhancement where an LLM proposes novel candidates the rule-based system would miss. Still constrained to the candidate generator interface so it never escapes the system.

Each candidate carries a structured "why this" explanation that the voice service uses to render the final text. **Explainability is mandatory at this layer.** If the engine cannot explain why it surfaced something, it does not surface it.

### 3. Companion Voice Service

**Responsibility:** Wrap raw recommendations in the chosen voice, using templates and the character's personality.

- Maintains a library of voice templates per situation (greeting, observation, suggestion, celebration, question, follow-up, gentle nudge, etc.).
- Selects templates based on context (mood, time since last visit, recent events) and rotates them to avoid repetition.
- Renders final text and stores it on `JourneyObservation.rendered_text` so the notebook is consistent over time.
- **v1: Pure templates** with variable substitution. Maybe 100-200 lines of voice writing across all situations.
- **v2: Template variants per character mood** so the companion's tone shifts based on the user's recent activity (proud, concerned, playful, curious).
- **v3: LLM-rewritten variants** that take a structured payload and rewrite it in the character's voice, fully constrained to the character's tone guide.

Voice is the swappable layer. The character could be redesigned, the templates rewritten, or the rendering upgraded to LLM, all without touching the recommendation engine or the data model.

### Why this layering matters

Each layer has a clean interface:
- Context Builder outputs a `JourneyContext` (data structure)
- Recommendation Engine inputs a context, outputs ranked `Candidate` objects (data structure)
- Voice Service inputs candidates, outputs rendered observations (text + metadata)

This means:
- ML can be added at the engine layer without touching anything else
- Voice can be rewritten without touching the engine
- New data sources plug into the Context Builder without rippling outward
- Tests can mock any layer cleanly

---

## Phased Implementation Strategy

This is the rough phasing for when implementation begins. Each phase is its own release, ideally.

### Phase 1: The Companion Arrives (MVP)

The minimum viable Journey: the relationship exists, the conversation works, the companion has a real voice.

- Character finalized with artist
- Journey app, models, migrations
- Context Builder service (full)
- Recommendation Engine v1 (rule-based, 8-12 generator types)
- Voice Service v1 (template-based, 100-200 voice lines)
- Journey page with greeting, threads, active pursuits, notebook, settings
- Free tier teaser experience
- Dashboard module (premium and free variants)
- Questionnaire bank with 20-30 questions
- Premium gating
- Documentation in `docs/features/platinum-journey.md`

**Goal:** A premium user can land on the page and feel a real companion who knows them. A free user can meet the companion and feel the pull to subscribe.

### Phase 2: Memory Deepens (Refinement)

After Phase 1 is live and we have telemetry on what users accept and reject.

- Expand voice library (more templates, more variation, more situations)
- Expand questionnaire bank
- Add more recommendation generators based on what users find valuable
- Notebook filtering and search
- Pursuit reordering and tagging
- Refined free tier hooks based on conversion data
- Optional: companion mood states based on recent user activity

### Phase 3: ML Re-ranker (Smarter Suggestions)

Once we have several months of accept/reject telemetry.

- Train a simple ranking model (logistic regression or GBT) on the candidate features
- A/B test against the rule-based ranking
- Add it as a post-processing step in the Recommendation Engine
- Recommendation Engine interface unchanged, voice unchanged

### Phase 4: Social Threads (Future)

When PP has more social infrastructure in general.

- Compare the user's profile against friend cohorts ("three of your friends platted this last month")
- Optional: shared journeys or journey-visible-to-friends
- Optional: companion comparing notes on shared games
- All new data sources plug into the Context Builder. No changes to the engine or voice required.

### Phase 5: Deeper Integrations

As future PP systems come online (gamification, etc.), the Context Builder gets new data sources and the Recommendation Engine gets new generators. The Journey itself does not change shape. **One-way coupling rule still applies.**

---

## Open Questions

These are the things still TBD that need to be resolved before or during Phase 1.

1. **Character design.** Form, name, visual style, animation potential. Requires artist collaboration.
2. **Voice tone exact calibration.** The "humorous, aloof, never therapist" line is clear in principle but needs example dialogue to lock in. Some sample greetings, observations, and follow-ups should be written before the voice service is built.
3. **Free tier exact boundaries.** How many free observations? Does the free user keep the same companion forever or does the companion forget them between visits? (Leaning toward: free users always meet a fresh companion who remembers nothing, premium unlocks the relationship.)
4. **Premium price point implications.** Does Journey justify a higher tier than current premium, or is it included in the existing premium? Probably the latter for simplicity, but worth revisiting closer to launch.
5. **Chapters/seasons (optional).** Some users may want their journey to feel episodic ("my JRPG summer", "my horror October"). This could be opt-in chapter framing or skipped entirely. Decide during Phase 2.
6. **Custom goals limits.** Should custom user-stated goals have any structure (deadlines, metrics) or be free-text only? Lean toward free-text in v1, structured in v2.
7. **Notebook compaction strategy.** If the infinite notebook eventually becomes a real database problem, what does compaction look like? Probably summarize-and-archive monthly observations after a year. Defer decision until it's a real problem.
8. **Onboarding length.** First-visit experience for premium: how long should the "let me get to know you" moment be? Too short feels shallow, too long feels like a wizard. Lean toward: one rich greeting + 3-4 starting observations + 1 question + the option to continue or explore.
9. **Question tone bank.** The questionnaire questions need to be written in the companion's voice, not as form fields. This is real writing work.
10. **Telemetry schema for Phase 3.** What exactly do we log on each candidate to feed the future ML re-ranker? Define this in Phase 1 so we have training data ready when Phase 3 starts.
11. **Conversational input (typed text).** Should v1 allow the user to type to the companion at all, or are interactions strictly button/menu-driven? Lean toward button/menu in v1 to keep scope sane and avoid the "now we need an LLM" trap. Revisit later.
12. **Mobile experience.** The conversation surface needs to work at 375px. Design the page mobile-first per the project responsive philosophy.

---

## Gotchas and Pitfalls

- **The "alive but never autonomous" line is fragile.** It will be tempting to add "small" autonomous behaviors (auto-completing pursuits, auto-celebrating plats with notifications, etc.). Resist this. Every autonomous behavior added erodes the trust the design depends on. The companion acts when the user visits, full stop.
- **Voice writing is real work.** Underestimating the writing effort is the most likely way Phase 1 ships feeling lifeless. Budget actual time for it. Consider drafting voice samples before any code is written, so the engineering work is in service of a known voice rather than guessing.
- **Cold start with sparse user data.** New PP users, or users who barely sync, will not give the recommendation engine much to work with. The companion needs to handle this gracefully ("I don't know you yet. Want to tell me about yourself?") rather than making bad recommendations or saying nothing at all.
- **The "creepy line" on negative observations.** Even with the humor/aloof rule, some users will find pattern recognition uncomfortable. The mute-topic and negative-observations-disabled settings are not optional. Test with real users early.
- **Don't let the recommendation engine become opaque.** Every candidate must have a "why this" explanation at the engine layer, or it cannot ship to the voice layer. The user must always be able to understand why the companion suggested something. This is a hard rule, not a nice-to-have.
- **Notebook consistency over time.** Storing `rendered_text` on observations is critical. If voice templates ever rewrite history, the notebook stops feeling like a real memory and starts feeling like a database view. Freeze the text at generation time.
- **Premium gate checks must be cheap.** The page will be visited often. Premium status checks should hit cached values, not query the subscription system on every render.
- **Coupling violations are seductive.** The temptation to "just give the user a tiny XP bonus for completing a journey thread" will be strong. Do not do this. The one-way coupling rule is the entire reason this system stays clean as PP grows. Violating it once opens the door to violating it everywhere.
- **The questionnaire is not a form.** If the questionnaire ever gets implemented as a multi-question form, the design has failed. Questions are individual conversational beats, asked one at a time, in the companion's voice.
- **ML temptation in v1.** It will be tempting to start with ML or LLM generation in Phase 1 because it sounds cooler. Resist. Rule-based v1 is faster to ship, easier to debug, fully explainable, and gives the ML phases real training data instead of synthetic data.

---

## Integration Points

### Reads from (one-way only)

- **Trophy data**: Game, Trophy, UserGameProgress, completion timestamps
- **Concept model**: unified game identities, IGDB enrichment (genres, themes, engines, time-to-beat)
- **Badge system**: badge progress, earned badges, milestones
- **Stats service**: any stat the stats page can compute
- **Ratings and reviews**: UserConceptRating, ConceptReview
- **Challenges**: A-Z, Calendar, Genre challenge state
- **Game families**: deduplication so the companion doesn't suggest three versions of the same game
- **Profile**: avatar, premium status, dashboard preferences
- **Subscription system**: premium gating

### Writes to (Journey-internal only)

- `JourneyProfile`, `JourneyThread`, `JourneyObservation`, `JourneyAnswer`, `JourneyEvent`
- Nothing else. Ever.

---

## Related Docs

- [Stats Page](stats-page.md): the source of truth for the kinds of stats the Context Builder will surface
- [Gamification Vision](gamification-vision.md): a future system the Journey will eventually read from
- [Dashboard Module Catalog](dashboard-module-catalog.md): where the Journey teaser module will live
- [Review Hub](../features/review-hub.md): ratings and reviews are a key signal source
- [Challenge Systems](../features/challenge-systems.md): challenge state is read by the Context Builder
- [IGDB Integration](../architecture/igdb-integration.md): genre/theme/engine metadata for content-based recommendations
- [Subscription Lifecycle](../features/subscription-lifecycle.md): premium gating
