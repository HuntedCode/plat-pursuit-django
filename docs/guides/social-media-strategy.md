# Social Media & Growth Strategy

A comprehensive, actionable social media playbook for Platinum Pursuit. This document is designed for a solo developer with 5-10 hours per week to invest in community growth across multiple platforms. Every recommendation maps back to features and data assets that already exist in the platform, minimizing creation overhead and maximizing what the system can produce for you.

**Philosophy**: Trophy hunters are driven by bragging rights, rarity, completion satisfaction, community recognition, and friendly competition. Every piece of content should tap into at least one of those motivations. We are not a corporate brand. We are indie, passionate, and part of this community ourselves. The tone should always reflect that.

---

## Table of Contents

1. [Platform Strategy](#1-platform-strategy)
2. [Content Pillars](#2-content-pillars)
3. [Content Calendar Framework](#3-content-calendar-framework)
4. [Leveraging Existing Features](#4-leveraging-existing-features)
5. [Community Building](#5-community-building)
6. [Growth Tactics](#6-growth-tactics)
7. [User-Generated Content](#7-user-generated-content)
8. [Metrics and KPIs](#8-metrics-and-kpis)
9. [Automation Opportunities](#9-automation-opportunities)
10. [Technical Growth Features](#10-technical-growth-features)
11. [Brand Voice and Guidelines](#11-brand-voice-and-guidelines)
12. [Quick-Start Priorities](#12-quick-start-priorities)
13. [Gotchas and Pitfalls](#gotchas-and-pitfalls)

---

## 1. Platform Strategy

### Platform Priority Ranking

| Priority | Platform | Time Allocation | Why |
|----------|----------|----------------|-----|
| 1 | **X/Twitter** (@platpursuit) | 35% (~2-3.5 hrs/wk) | Trophy hunting's real-time conversation hub. Share cards render perfectly via Twitter Cards. OG meta tags already configured in `base.html`. Highest potential for organic discovery through retweets and quote-tweets of user share cards. |
| 2 | **Discord** (discord.gg/platpursuit) | 25% (~1.5-2.5 hrs/wk) | Deepest engagement per user. Discord webhook integration already exists for badge/milestone notifications. This is where power users live and where retention happens. |
| 3 | **Reddit** (r/Trophies, r/PS5, etc.) | 20% (~1-2 hrs/wk) | Trophy hunting's largest discussion community. r/Trophies has 200k+ members. Reddit rewards genuine participation, not promotion. High-quality data-driven posts can drive significant traffic. |
| 4 | **YouTube** (@PlatPursuit) | 15% (~1-1.5 hrs/wk) | Long-form content for tutorials, feature showcases, and monthly recap highlights. Lower posting frequency but higher content longevity. Each video lives forever as a discoverable resource. |
| 5 | **TikTok/Instagram Reels** | 5% (~0.5 hrs/wk) | Portrait share cards (1080x1350) already optimized for these platforms. Experimental, low-effort repurposing of existing content. Focus here only after the first four platforms have consistent cadence. |

### Platform-Specific Content Approaches

**X/Twitter**: Fast, punchy, data-driven. Trophy hunting hot takes, community milestones, share card showcases, game launch reactions, polls. Use threads for longer narratives (e.g., "The 10 rarest platinums our community earned this month"). Engage with #PlatinumTrophy, #TrophyHunting, #PS5 hashtags. Retweet and quote-tweet user share cards frequently.

**Discord**: Deep engagement. Channels for challenge coordination, platinum celebrations, feature requests, bug reports, and off-topic gaming chat. Weekly events (leaderboard check-ins, challenge spotlights). Role-based access already exists through the badge/milestone system. Discord is the place for real conversations, not broadcasts.

**Reddit**: Value-first participation. Never post just links. Create data-driven posts ("We analyzed 50,000 platinums earned last month: here's what we found"), helpful guides, and participate genuinely in discussions. Share PlatPursuit as a tool within context, never as a hard sell.

**YouTube**: Monthly recap showcase videos, feature walkthroughs, challenge guide series, "how to get started" tutorials. Screen recordings with voiceover. Shorts for quick platinum celebration clips or "did you know" facts from platform data.

**TikTok/Instagram**: Repurposed portrait share cards, short clips of Monthly Recap slide animations, quick stat graphics. These platforms reward visual impact and brevity.

### Posting Frequency Targets

| Platform | Target Frequency | Content Mix |
|----------|-----------------|-------------|
| X/Twitter | 5-7 posts/week | 2 original data posts, 2 community retweets/spotlights, 1 poll/engagement, 1-2 feature highlights |
| Discord | Daily presence | Respond to messages, 2-3 proactive conversation starters per week, 1 weekly event |
| Reddit | 2-3 posts/week | 1 data/insight post, 1-2 genuine comment contributions on existing threads |
| YouTube | 2-4 videos/month | 1 monthly recap highlight, 1 feature tutorial, 1-2 shorts |
| TikTok/IG | 2-3 posts/week (if active) | Repurposed share cards and stat graphics |

---

## 2. Content Pillars

### Pillar 1: "Trophy Data Drops" (Data-Driven Insights)

**What**: Interesting statistics, trends, and insights pulled directly from the platform's data.

**Content types**: Infographics, stat posts, thread breakdowns, polls with data reveals.

**Data sources**:
- `compute_community_stats()` in `core/services/stats.py`: Total profiles, trophies earned, platinums earned, games tracked, badge XP, weekly deltas
- Monthly Recap aggregated data: rarest trophies, most active days, time-of-day analysis, persona detection (early_bird/night_owl), streaks
- Leaderboard data from `update_leaderboards` cache: XP rankings, badge earners, per-series progress
- Challenge completion rates: how many people complete A-Z (26/26), Calendar (365/365), Genre challenges

**Example posts**:
- "Our community earned 12,847 platinums last week. The rarest? [Game] at 0.3% earn rate. The most popular? [Game] with 47 new platinums."
- "Only 3 hunters in our community have completed the Platinum Calendar Challenge (365 unique days!). Think you can join them?"
- "Night Owl vs. Early Bird: 62% of our community earns their trophies after 9 PM. Which camp are you in? [Poll]"
- "The 5 badge series with the fewest earners. Are you brave enough? [Thread]"

**Posting rhythm**: 2-3 times per week.

### Pillar 2: "Community Spotlight" (User Celebrations)

**What**: Highlighting community achievements, milestones, and share cards.

**Content types**: Retweets/reposts of user share cards, milestone callouts, leaderboard movement, badge celebrations.

**Data sources**:
- User-generated share cards (landscape 1200x630 for X/Twitter, portrait 1080x1350 for Instagram)
- Badge/milestone notifications: new badge tier awards, milestone achievements (35+ milestone types tracked)
- Challenge completions: A-Z, Calendar, Genre completion events
- Leaderboard shifts: new top 10 entrants, XP milestones

**Example posts**:
- "[Retweet user's platinum share card] Congrats to @user on Platinum #150! That Elden Ring card is gorgeous."
- "NEW Hall of Famer! @user just completed their A-Z Platinum Challenge, covering every letter from A (Astro Bot) to Z (Zombie Army 4). Absolute legend."
- "Badge Alert: @user just earned the Platinum tier God of War badge. That's every game in the series platinumed AND 100%'d. Respect."

**Posting rhythm**: 2-3 times per week (reactive to community activity).

### Pillar 3: "Feature Showcases" (Product Education)

**What**: Showing off platform features in action. Tutorials, walkthroughs, tips, and "did you know" moments.

**Content types**: Screen recordings, screenshot series, video tutorials, tip threads.

**Feature sources**:
- Share card system: My Shareables page, theme selection, download flow, landscape vs. portrait formats
- Challenge systems: A-Z wizard, Calendar auto-backfill, Genre subgenre tracker
- Monthly Recap: Spotify Wrapped-style slide presentation, quizzes, confetti, theme selection
- Badge system: series exploration, tier progression, XP tracking, leaderboards
- Gamification: P.L.A.T.I.N.U.M. stats, milestones, titles
- Community features: guides/checklists, comments, voting, game lists

**Example posts**:
- "Did you know you can download your platinum share cards in two formats? Landscape for Twitter/Discord, Portrait for Instagram. Here's how: [screenshot thread]"
- "The Genre Challenge tracks 32 subgenres across your platinums. How many have you found? [Video walkthrough]"
- "[YouTube] Setting up your A-Z Platinum Challenge in 5 minutes. From creation to your first assignment."

**Posting rhythm**: 1-2 times per week.

### Pillar 4: "The Hunt" (Gaming Industry & Trophy Talk)

**What**: Commentary on new game releases, PlayStation events, trophy list reveals, and trophy hunting culture.

**Content types**: Hot takes, predictions, reaction threads, game launch content.

**Content sources**:
- New game releases with platinum trophies
- PlayStation State of Play / showcase events
- Trophy list reveals for upcoming games
- Industry news relevant to trophy hunters (PS Plus catalog changes, game updates adding trophies)
- Community debates (hardest platinums, most underrated games, "is this game worth the plat?")

**Example posts**:
- "New trophy list just dropped for [Game]. 47 trophies including a platinum. First impressions: this looks like a 40-hour commitment. Who's going for it?"
- "[State of Play reaction thread] 3 new games announced with platinums. Here's what trophy hunters need to know."
- "Hot take: [Game] has the most satisfying platinum trophy on PS5. The trophy icon design, the rarity, the journey. What's yours?"

**Posting rhythm**: 1-2 times per week (event-driven).

### Pillar 5: "Behind the Curtain" (Dev Diary / Indie Story)

**What**: Behind-the-scenes content about building PlatPursuit. Development updates, design decisions, technical challenges, roadmap teases.

**Content types**: Dev diary posts, screenshot previews, "before and after" design comparisons, roadmap updates, personal reflections.

**Example posts**:
- "Spent the weekend redesigning the Monthly Recap share cards. Here's the before and after. Small details matter."
- "Building a trophy tracking platform as a solo dev: the story of how PlatPursuit started and where it's going. [Thread]"
- "Sneak peek: the Community Hub is coming. Reviews and ratings for every game, powered by the trophy hunting community. [Preview screenshot]"

**Posting rhythm**: 1 time per week.

### Pillar 6: "Just For Fun" (Community Entertainment)

**What**: Lighthearted, engagement-first content. Memes, polls, debates, trivia, challenges.

**Content types**: Polls, "this or that" posts, trivia questions, community challenges, memes, Easter eggs.

**Example posts**:
- "Poll: Would you rather platinum a game with one ultra-rare missable trophy or a game with 200 collectibles?"
- "Trophy Trivia: What percentage of PS5 owners have earned at least one platinum trophy? Drop your guess, we'll reveal the answer tomorrow."
- "Name a game that DESERVES a platinum trophy but doesn't have one. Wrong answers only."

**Posting rhythm**: 1-2 times per week.

---

## 3. Content Calendar Framework

### Weekly Rhythm

| Day | Primary Content | Platform Focus | Pillar |
|-----|----------------|----------------|--------|
| Monday | "Monday Milestones": Community spotlight of weekend achievements | X/Twitter, Discord | Spotlight |
| Tuesday | Data Drop or Feature Showcase | X/Twitter, Reddit | Data / Feature |
| Wednesday | Gaming industry / trophy talk | X/Twitter | The Hunt |
| Thursday | Community engagement (poll, debate, trivia) | X/Twitter, Discord | Just For Fun |
| Friday | "Friday Flex": User share card retweet marathon + community challenge kick-off | X/Twitter, Discord | Spotlight / Fun |
| Saturday | Dev diary or behind-the-scenes content | X/Twitter | Behind the Curtain |
| Sunday | Content prep day: batch creation for the coming week, respond to community | Discord (casual presence) | Internal |

### Monthly Rhythm

| Timing | Content | Details |
|--------|---------|---------|
| 1st-3rd | Monthly Recap push | Tease the recap ("Your February recap is ready!"), share example cards, encourage sharing. Aligns with `generate_monthly_recaps` cron (3rd at 00:05 UTC) and `send_monthly_recap_emails` (3rd at 06:00 UTC). |
| 1st week | "Month in Review" data post | Aggregate community stats from the previous month. Total platinums, most popular games, leaderboard shifts, new badge earners. |
| 2nd week | Feature deep-dive | Pick one feature for a YouTube tutorial + Twitter thread combo. Rotate through: Challenges, Badges, Recaps, Share Cards, Guides, Game Lists. |
| 3rd week | Community event | Discord event, Reddit data post, or Twitter challenge (e.g., "Share your rarest platinum this week using #PlatPursuitRare"). |
| 4th week | Roadmap/dev update + next month teaser | What shipped this month, what's coming next. Build anticipation. |

### Quarterly Themes

| Quarter | Theme | Special Content |
|---------|-------|-----------------|
| Q1 (Jan-Mar) | "New Year, New Plats" | Year-in-review recap data, New Year's resolutions challenge, "Complete your A-Z before summer" push |
| Q2 (Apr-Jun) | "Summer of Platinums" | E3/Summer Game Fest coverage, new game trophy list reactions, challenge season kickoff |
| Q3 (Jul-Sep) | "The Backlog Buster" | Focus on completing games, Calendar Challenge awareness, badge progress push |
| Q4 (Oct-Dec) | "Trophy Season" | Holiday game releases, Black Friday sale trophy recommendations, "Year Wrapped" anticipation, December recap hype |

### Batching Strategy for Efficiency

**Sunday Prep Session (2-3 hours)**:
1. Pull community stats data from the platform (15 min)
2. Screenshot/generate 3-4 share cards or data visualizations (20 min)
3. Draft 5-7 social posts for the week (45 min)
4. Schedule posts using scheduling tool (15 min)
5. Review Discord for anything needing response (15 min)
6. Draft 1 Reddit post if scheduled (20 min)
7. Buffer time for unexpected content opportunities (15 min)

**Daily Check-ins (15-20 min)**:
- Morning: Check notifications, respond to mentions, retweet user share cards
- Evening: Quick Discord check, respond to comments

### Event-Based Content Opportunities

| Event | Content Play | Lead Time |
|-------|-------------|-----------|
| PlayStation State of Play | Live-tweet reactions, follow-up trophy analysis post | Day-of + next day |
| Major game launch | Trophy list breakdown, difficulty estimate, "who's going for it?" poll | Launch day |
| PS Plus monthly games | "Which PS Plus game has the best platinum?" analysis | Announcement day |
| Game awards season | "Most Platinumed Game of the Year" community vote | December |
| PSN sales | "Best platinums under $20" recommendation threads | Sale period |
| PlatPursuit feature launch | Announcement thread, demo video, Discord event | 1 week before |
| Community milestones | "We just hit 1,000 profiles!" celebration posts | Day-of |
| Badge series launch | Showcase post, earner race, Discord announcement | Launch day |

---

## 4. Leveraging Existing Features

### Share Card System (Highest Social ROI)

The Playwright-based share card system is PlatPursuit's most powerful social media asset. Every card generated is a potential social media post with built-in branding.

**Card types and best platforms**:

| Card Type | Best Platform | Social Play |
|-----------|--------------|-------------|
| Platinum Share Card (landscape, 1200x630) | X/Twitter, Discord | "Just platted [Game]!" moment. OG tags already configured. |
| Platinum Share Card (portrait, 1080x1350) | Instagram, TikTok | Instagram stories/posts, TikTok screenshots |
| Monthly Recap Card | All platforms | "My [Month] wrapped!" viral loop. Monthly event content. |
| A-Z Challenge Card (26-slot grid) | X/Twitter, Reddit | Progress updates, completion celebrations |
| Calendar Challenge Card (365-day grid) | X/Twitter, Reddit | Visual progress, milestone celebrations |
| Genre Challenge Card | X/Twitter, Discord | Genre diversity showcase |

**Viral loop**:
1. User earns a platinum or completes a challenge
2. System generates a notification (already built)
3. User visits My Shareables (`/my-shareables/`) and downloads card
4. User posts card to social media with PlatPursuit branding baked in
5. Their followers see the branded card, some visit PlatPursuit
6. New users create accounts, earn their own platinums, generate their own cards
7. Cycle repeats

**Amplification tactics**:
- Retweet/repost every user-shared card you see (builds goodwill and encourages more sharing)
- Create a weekly "Best Cards" showcase post featuring 3-4 community share cards
- Pin a guide on how to download and share cards (My Shareables page walkthrough)

### Monthly Recaps as a Viral Growth Mechanism

Monthly Recaps are PlatPursuit's "Spotify Wrapped" moment, happening every month instead of once a year. This is an enormous advantage.

**Monthly Recap Campaign (1st-5th of each month)**:

| Day | Action |
|-----|--------|
| Day 1 | Teaser post: "Your [Previous Month] Recap is being prepared..." |
| Day 3 (recap emails go out at 06:00 UTC) | Main push: "Your [Month] Recap is live! How many platinums did you earn? Share your card!" |
| Day 3-4 | Retweet/share every user recap card you see |
| Day 4-5 | Data summary: "Community Recap: [Month] by the numbers" (aggregate recap data into a community-level summary) |

**Content from Recap Data** (fields from `MonthlyRecap` model):
- Persona detection: "62% of our community were Night Owls in February. Which are you?"
- Rarest trophy: "The rarest trophy earned by our community last month was [Trophy] from [Game] (0.1% earn rate)"
- Activity patterns: "Tuesday was the most active trophy hunting day in February. What's your peak day?"
- Streak data: "The longest trophy earning streak last month was 23 days straight!"
- Comparison data: "45% of our community beat their previous month's platinum count"

### Challenge Completions as Shareable Moments

**A-Z Challenge**:
- Progress milestones: "13/26 letters done! The halfway point!" (share card with half-filled grid)
- Letter reveal: "Just assigned [Game] to the letter Z. The hardest letter to fill!"
- Completion: "26/26! A-Z COMPLETE!" (share card showing full grid)
- Community data: "The most popular letter to fill first is 'A'. The hardest to fill? 'X' and 'Q'."

**Calendar Challenge**:
- Monthly milestones: "I've covered all 31 days of January! 11 more months to go..."
- Season completion: "Spring is complete! 92 calendar days filled!"
- Fun facts: "March 17 is the most-filled calendar day in our community (St. Patrick's Day gaming sessions, apparently)"

**Genre Challenge**:
- Genre completion: "Just platted my Horror genre game! The scariest platinum I've ever earned."
- Diversity showcase: "16 genres, 16 completely different games. The Genre Challenge shows your range."

### Leaderboard Updates as Competitive Content

Leaderboards recompute every 6 hours (`update_leaderboards` cron) and are cached in Redis. Four leaderboard types provide regular competitive content:

- **Weekly XP Race**: "This week's XP Leaderboard top 5. @user1 climbed 3 spots with their Resident Evil badge progress!"
- **Series Spotlights**: "The God of War badge has 47 earners. Can you make it 48?"
- **Progress Races**: "The closest badge race right now: @user1 and @user2 are tied at 8/12 stages on the Souls series badge."
- **Community XP Milestones**: "Our community has earned over 1,000,000 total Badge XP! That's a lot of platinums."

### Badge Achievements as Milestone Content

The badge system (4 tiers per series, XP tracking, 3,000 XP completion bonus) provides natural milestone moments:

- New badge earners (Discord webhook notifications already fire)
- First earner of a new badge series
- Tier progression celebrations (Bronze to Silver to Gold to Platinum)
- "Badge of the Week" feature: highlight one badge series, its stages, and how many people have earned it

### Community Stats as Content Fuel

`compute_community_stats()` (in `core/services/stats.py`, refreshed hourly via cron) calculates total profiles, trophies earned, games tracked, platinums earned, badge series count, total Badge XP, and weekly deltas.

**Content formula**: "[Big number] + [interesting comparison or trend] + [call to action]"
- "Our community has earned 247,891 trophies. Are you contributing to the count?"
- "142 new profiles joined this week. Welcome to the hunt!"

---

## 5. Community Building

### Discord Strategy

#### Recommended Channel Structure

**Information**:
- `#announcements`: Feature launches, maintenance notices, major updates
- `#changelog`: Detailed changelog entries for each deployment
- `#rules`: Community guidelines

**Discussion**:
- `#general`: Open conversation
- `#trophy-talk`: Trophy hunting discussion, tips, questions
- `#platinum-flex`: Post your platinum share cards here (the "brag zone")
- `#challenge-corner`: A-Z, Calendar, and Genre challenge discussion and coordination
- `#badge-hunters`: Badge series discussion, strategy, progress sharing
- `#game-recommendations`: "What should I plat next?" discussions
- `#feature-requests`: Direct community input on PlatPursuit development

**Event/Bot**:
- `#bot-feed`: Automated notifications (badge awards, milestones, via existing Discord webhook integration)
- `#weekly-spotlight`: Curated weekly community highlights

#### Discord Events (Weekly)

| Day | Event | Description |
|-----|-------|-------------|
| Monday | "Weekend Wins" | Members share what they platinumed over the weekend |
| Wednesday | "What Are You Hunting?" | Mid-week check-in on current trophy targets |
| Friday | "Friday Challenge" | Weekly mini-challenge (e.g., "Earn a trophy in a genre you've never played") |

#### Discord Engagement Tactics
- **Role-based recognition**: Already built. Badge tier roles, milestone roles, and premium roles are auto-assigned via the Discord bot integration. Users with rare roles become visible ambassadors.
- **React to everything**: When someone posts a platinum card, react with relevant emoji. Small acknowledgments drive repeat engagement.
- **Seed conversations**: Don't wait for members to talk. Post a question or topic at least once per day.
- **Voice channels**: Consider occasional "trophy hunting together" voice sessions for community bonding.

### Reddit Strategy

#### Target Subreddits

| Subreddit | Size | Strategy | Posting Frequency |
|-----------|------|----------|-------------------|
| r/Trophies | 200k+ | Primary community. Data posts, helpful comments, occasional tool mentions. | 1-2 posts/week |
| r/PS5 | 4M+ | Broader audience. Only post truly exceptional content. | 1-2 posts/month |
| r/PS4 | 5M+ | Same as r/PS5. Cross-post where appropriate. | 1-2 posts/month |
| r/PlayStationTrophies | Smaller | More niche, more receptive to tool promotion. | 1 post/week |
| r/IndieGaming or r/SideProject | Various | Dev diary content and indie story sharing. | Monthly |

#### Reddit Content Guidelines
- **Never**: Post a bare link with "check out my site"
- **Always**: Lead with value. Data, insights, or helpful content first. PlatPursuit mention as context, not pitch.
- **Best format**: Image posts with data visualizations, text posts with analysis, comments on existing threads offering genuinely helpful information
- **Self-promotion rule**: Follow each subreddit's guidelines (usually 10:1 ratio of community participation to self-promotion). Spend time commenting on other people's posts before sharing your own content.

#### Example Reddit Posts
- r/Trophies: "I built a tool that tracks trophy hunting stats. Here are the most interesting patterns from 50,000 platinums." [Data analysis with screenshots]
- r/Trophies: "I created an A-Z Platinum Challenge tracker. Pick one game per letter and try to plat them all. Here's what the community's attempts look like." [Challenge grid screenshots]
- r/PS5: "Analysis: The average PS5 platinum takes 32 hours according to our community's data. Here are the fastest and slowest." [Data post]

### Turning Users into Advocates

**The Advocacy Ladder**:

| Level | User Behavior | How to Encourage |
|-------|--------------|------------------|
| 1. Visitor | Browses the site | SEO, social discovery, Reddit posts |
| 2. Member | Creates account, links PSN | Smooth onboarding, immediate value (badge progress visible) |
| 3. Engaged | Uses features regularly | Feature quality, notifications, email recaps |
| 4. Sharer | Downloads and posts share cards | Make sharing easy (My Shareables hub), celebrate shared cards |
| 5. Advocate | Recommends PlatPursuit to others | Community recognition, spotlights, Discord roles |
| 6. Champion | Creates content about PlatPursuit | Feature in announcements, early access to features, direct communication |

**Key conversion points**:
- Level 2 to 3: Monthly Recap email drives return visits (already sent on 3rd of each month)
- Level 3 to 4: Make share cards impossible to resist by ensuring they look stunning. Theme system already offers gradient and game art backgrounds.
- Level 4 to 5: Publicly celebrate sharers. Retweet, spotlight, thank them by name.
- Level 5 to 6: Invite top advocates to a "beta tester" or "community advisor" Discord role. Give early access to features and ask for feedback.

### Community Events and Competitions

| Event | Cadence | Description |
|-------|---------|-------------|
| "Platinum Race" | Monthly | Pick a game, race to platinum it. First to complete and share their card wins recognition. |
| "Badge Blitz" | Quarterly | Focus on one badge series for a month. Community progress tracker on Discord. |
| "A-Z Sprint" | Bi-annual | Community-wide A-Z challenge push. Who can fill the most letters in a month? |
| "Rarest Plat of the Month" | Monthly | Community votes on the most impressive platinum earned that month. |
| "Calendar Countdown" | December | Holiday-themed push to fill December calendar days. |

---

## 6. Growth Tactics

### Organic Growth Strategies

**SEO and Discoverability**:
- Existing sitemap (`sitemap.xml`) includes static pages, game pages (up to 5,000), and profile pages (up to 5,000)
- OG meta tags on all pages drive rich link previews when shared
- Public pages (game details, profiles, badge details, challenges, leaderboards, guides) are all indexable
- **Action items**:
  - Ensure game detail pages have rich, unique meta descriptions (currently using the generic fallback)
  - Add structured data (JSON-LD) for game pages to improve Google rich results
  - Create landing pages for high-traffic search terms: "best PS5 platinum trophies", "trophy hunting tracker", "PlayStation completion tracker"

**Content Flywheel**:

```
Platform Data --> Social Content --> New Users --> More Data --> Better Content --> More Users
```

Each new user who links their PSN profile adds to the community stats, making data posts more interesting, which attracts more users. This is the core growth loop.

### Cross-Promotion Between Platforms

| Source | Target | Method |
|--------|--------|--------|
| X/Twitter | Discord | "Join the conversation in our Discord" CTA on milestone posts |
| Discord | X/Twitter | "Share your platinum card on Twitter with #PlatPursuit" prompts |
| Reddit | Platform | Genuine "I built this" context in helpful data posts |
| YouTube | All | Video descriptions with links to platform, Discord, and social accounts |
| Email (Monthly Recap) | Social | "Share your recap on social media" CTA in recap emails |
| Platform (My Shareables) | Social | Share cards inherently cross-promote when posted anywhere |

### Collaboration Opportunities

**Trophy Hunting Content Creators** (YouTube/Twitch):
- Reach out to mid-tier trophy hunting YouTubers (5k-50k subscribers) with a genuine offer: "We built this tool and think your community would find it useful. Would you like to try it?"
- Offer to generate their badge progress or challenge data as a video topic
- Provide early access to new features for content creation
- Do NOT pay for promotion. The indie story and genuine utility are more compelling.

**Target creator profiles**:
- Trophy hunting YouTubers who make guide content
- PlayStation-focused Twitch streamers who track completions
- Trophy hunting podcast hosts
- Active r/Trophies contributors with large followings

**Community Partnerships**:
- PSNProfiles community: Many trophy hunters use both tools. Position PlatPursuit as complementary (badge gamification, challenges, share cards) rather than competitive.
- TrueTrophies community: Same approach.
- Trophy hunting Discord servers: Cross-promote events, not the platform itself.

### Referral and Word-of-Mouth

**Built-in virality** (already exists):
- Share cards with PlatPursuit branding baked into every card downloaded
- Public profile pages that can be linked anywhere
- Public challenge pages viewable by non-members
- Public leaderboards that drive competitive sharing

**Potential future features** (see [Technical Growth Features](#10-technical-growth-features)):
- "Invite a friend" system with tracking
- "Compare with friend" feature requiring both accounts
- Embeddable profile widgets for forums/websites

---

## 7. User-Generated Content

### Encouraging Users to Share

**Friction reduction**: The My Shareables page (`/my-shareables/`) centralizes all downloadable cards. The single biggest UGC driver is making sure users know this page exists.

**Prompts at key moments**:
- After a platinum sync: Notification already exists. Ensure it links to the share card.
- After challenge completion: Notification exists. Link to share card.
- Monthly Recap: Email already sent. Include "Share your recap" CTA.
- Badge earned: Notification exists. Could include a share prompt.

**Social proof loop**: When users see others sharing cards, they want to share their own. Every retweet of a user's card encourages 2-3 more people to share theirs.

### Reposting Strategy

| Platform | Approach |
|----------|----------|
| X/Twitter | Quote-tweet with a brief congratulatory comment. Vary the text to feel genuine, not automated. |
| Discord | Share in `#platinum-flex` or relevant channel with a congratulatory message. Pin exceptional ones. |
| Instagram | Repost to Stories with credit. Use the original image (portrait format already optimized). |

**Guidelines**:
- Always credit the original poster
- Add a brief personal comment (not just "Congrats!")
- Rotate which users you spotlight (don't always feature the same people)
- Prioritize rare/impressive achievements over common ones (drives aspiration)

### Community Spotlights

**Weekly Spotlight Format** (X/Twitter thread or Discord post):

```
This week's Community Spotlight:

[Trophy icon] @user1 earned Platinum #200 with [Game]! That's commitment.
[Badge icon] @user2 completed the Souls Series badge at Platinum tier. Every. Single. Game.
[Challenge icon] @user3 is at 24/26 on their A-Z Challenge. Just Q and X to go!
[Calendar icon] @user4 filled 47 new calendar days this month. On pace for a full year!

Want to be featured? Share your achievements with #PlatPursuit!
```

### Hashtag Strategy

| Hashtag | Usage | Platform |
|---------|-------|----------|
| `#PlatPursuit` | Primary brand hashtag. Use on everything. Encourage users to use it. | All |
| `#PlatinumTrophy` | Existing community hashtag. Join the broader conversation. | X/Twitter |
| `#TrophyHunting` | Existing community hashtag. High volume. | X/Twitter, Instagram |
| `#PS5` / `#PS4` | Platform hashtags for discoverability. | X/Twitter, Instagram |
| `#PlatPursuitRecap` | Monthly recap specific. Drive a monthly sharing wave. | X/Twitter, Instagram |
| `#PlatPursuitChallenge` | Challenge completions and progress. | X/Twitter |
| `#PlatPursuitSpotlight` | Community spotlight features. | X/Twitter |

**Hashtag rules**:
- X/Twitter: 2-3 hashtags per post maximum. More feels spammy.
- Instagram: Up to 10-15 in first comment (not caption).
- Discord: No hashtags needed.
- Reddit: No hashtags. They don't work on Reddit.

---

## 8. Metrics and KPIs

### Platform-Specific Metrics

| Platform | Primary KPI | Secondary KPIs | How to Track |
|----------|-------------|----------------|-------------|
| X/Twitter | Follower growth rate | Impressions, engagement rate, link clicks, retweets | Twitter Analytics (built-in) |
| Discord | Active members (weekly) | Message count, event participation, new joins | Discord Server Insights |
| Reddit | Post upvotes + comments | Profile karma from trophy subreddits, traffic referrals | Reddit post stats + PlatPursuit analytics |
| YouTube | Subscriber growth | Watch time, click-through rate, video views | YouTube Studio |
| Platform | New user signups | DAU/WAU, share card downloads, challenge creations | Custom first-party analytics (already built) |

### Growth Benchmarks

#### First 3 Months (Foundation Phase)

| Metric | Target | Notes |
|--------|--------|-------|
| X/Twitter followers | +100-200 | Focus on consistent posting, not follower count |
| Discord members | +50-100 active | Active = messaged in last 7 days |
| Reddit karma from trophy subs | +500 | Indicates genuine community participation |
| YouTube subscribers | +50-100 | If producing regular content |
| Weekly share cards posted by users | 5-10 | Leading indicator of product-market fit |
| New platform signups/week | Track baseline | Establish baseline, then measure growth rate |

#### 6 Month Targets

| Metric | Target |
|--------|--------|
| X/Twitter followers | 500-1,000 |
| Discord active members | 100-200 |
| Monthly share cards shared on social | 30-50 |
| Monthly new user signups | 2x baseline |
| YouTube video library | 12-20 videos |

#### 12 Month Targets

| Metric | Target |
|--------|--------|
| X/Twitter followers | 1,500-3,000 |
| Discord active members | 300-500 |
| Monthly share cards shared on social | 100+ |
| Monthly new user signups | 5x baseline |
| User-generated content posts (unprompted) | Weekly occurrence |
| Content creator partnerships | 3-5 active relationships |

### Measuring ROI on Time Invested

**Time tracking formula**: Log hours spent on social media weekly. Compare against:
- New user signups attributable to social (use UTM parameters on links)
- Share card downloads (tracked via site events like `recap_image_download`)
- Discord join rate
- Direct mentions/DMs received

**Efficiency metric**: New users per hour of social media work. If this declines, reassess platform allocation.

**Leading indicators** (predict future growth):
- Engagement rate on posts (likes + replies + retweets / impressions)
- Share card download count (indicates users are creating social content)
- Monthly Recap email open rate (indicates email-to-social pipeline health)
- Discord message volume (indicates community health)

**Lagging indicators** (confirm past effort):
- New user signups
- Total platform profiles
- Organic search traffic to public pages

### Analytics Tools

| Tool | Purpose | Cost |
|------|---------|------|
| Twitter/X Analytics | Built-in post performance metrics | Free |
| Discord Server Insights | Member activity, retention, channel usage | Free (requires Community server) |
| YouTube Studio | Video performance, subscriber analytics | Free |
| PlatPursuit analytics | Page views, site events, session tracking | Already built |
| Bitly or UTM links | Track click-throughs from specific social posts | Free tier available |
| Buffer or Typefully | Post scheduling and basic analytics | Free tier available |

---

## 9. Automation Opportunities

### What Can Be Automated Now (Existing Infrastructure)

| Automation | Implementation | Effort |
|-----------|---------------|--------|
| **Discord auto-announcements** | Discord webhook system already sends badge/milestone notifications. Extend to post weekly community stat summaries to a `#weekly-stats` channel. | Low: new management command + cron entry |
| **Monthly Recap social reminder** | Add a step to `send_monthly_recap_emails` that also posts a "Recaps are live!" message to Discord via existing webhooks. | Low: few lines in existing command |
| **Community stats export** | New management command that outputs formatted community stats (from `compute_community_stats()`) as social-ready text. Copy-paste into scheduling tool. | Low: new management command |
| **Share card tracking** | Site events already track `recap_share_generate` and `recap_image_download`. Add similar tracking for all card types to measure sharing behavior. | Low: add event tracking calls |

### Recommended Scheduling Tools

| Tool | Best For | Cost | Notes |
|------|----------|------|-------|
| **Buffer** | X/Twitter scheduling, simple analytics | Free (3 channels, 10 posts/channel) | Best free option for getting started |
| **Typefully** | X/Twitter threads, scheduling, analytics | Free tier available | Better for thread-heavy strategy |
| **Later** | Instagram/TikTok scheduling | Free tier | Best if expanding to visual platforms |
| **Discord Scheduled Events** | Discord events | Free (built-in) | Use for weekly community events |

---

## 10. Technical Growth Features

Technical features that would amplify the social strategy. Each has an impact rating and rough effort estimate for planning purposes.

### Dynamic OG Images (Very High Impact)

**What**: Generate branded Open Graph images dynamically for game detail pages, profile pages, and badge pages using the existing Playwright rendering pipeline, so that when anyone shares a link to a game or profile, the preview shows a branded visual card instead of the generic PlatPursuit logo.

**Current state**: `base.html` falls back to the generic platform logo for `og:image` on most pages. Template blocks (`og_image`, `twitter_image`) exist for per-page overrides but few pages use them.

**Impact**: Very High. Every shared link becomes branded content. No user action required: just copying a URL produces a visual card in the preview. This is passive, permanent, effortless social media.

**Approach**: Extend the `PlaywrightRendererService` (already battle-tested for share cards) to generate OG images. Cache them aggressively (game data changes infrequently). Serve via a new endpoint or as static cached files. Override `og_image` block in game detail, profile detail, and badge detail templates.

**Key files**: `core/services/playwright_renderer.py`, `templates/base.html`, relevant detail templates.

**Effort**: Medium. The rendering pipeline exists. Needs new card templates, a caching strategy, and template block overrides. Roughly 2-3 days of focused development.

### "Share to Twitter" Button (High Impact)

**What**: One-click share button on My Shareables and notification pages that opens a pre-filled tweet with the share card attached using Twitter Web Intents.

**Current state**: Users must download the card, then manually compose a tweet and attach the image. This is the biggest friction point in the viral loop.

**Impact**: High. Removes the most significant barrier between "generated a card" and "posted it to social media." Could double or triple the share-through rate.

**Approach**: Use Twitter Web Intents API (`https://twitter.com/intent/tweet?text=...&url=...`) to open a pre-filled tweet. The card image would need to be attached manually (Twitter Intents don't support image attachment), but the pre-filled text and URL get users 80% of the way there. Could also include a "Copy to clipboard" button for the pre-written caption.

**Key files**: `templates/trophies/my_shareables.html`, notification templates, `static/js/` share handling.

**Effort**: Low. Twitter Web Intents is a simple URL construction. Could be done in an afternoon.

### Public Community Stats Page (High Impact)

**What**: A public `/stats/` or `/community/` page showing live community aggregates: total profiles, trophies earned, trending games, most popular badges, recent completions. Evergreen, linkable, SEO-friendly.

**Current state**: `compute_community_stats()` in `core/services/stats.py` already calculates this data and caches it hourly. It's only surfaced on the homepage in a compact stats bar.

**Impact**: High. Creates an evergreen content source for social posts and Reddit threads. "Check out our live community stats page" is a natural link to share. Also excellent for SEO.

**Approach**: New view + template. Data is already computed and cached. Mostly a frontend/design task.

**Key files**: `core/services/stats.py`, new view in `core/views.py` or `trophies/views/`, new template.

**Effort**: Low-Medium. The backend data exists. Needs a view, a template, and some visual design. 1-2 days.

### Trending Games Feed (Medium-High Impact)

**What**: Public page or API showing which games are being platinumed most this week, using the existing weighted trending formula (60% players engaged + 40% trophies earned, 7-day rolling window).

**Current state**: The `compute_top_games()` function exists and runs as part of the daily homepage cache. It excludes shovelware and biases toward fresh activity. But the data is only used on the homepage in a "Featured Games" section.

**Impact**: Medium-High. "What's Trending on PlatPursuit This Week" is a natural weekly social post. A public page makes it linkable.

**Approach**: New public view that surfaces the trending computation. Could be part of the community stats page or standalone.

**Key files**: `core/services/stats.py` (or similar), homepage service functions.

**Effort**: Low. The computation exists. Needs a view and a template. Half a day to a day.

### Embeddable Profile Cards (Medium Impact)

**What**: HTML embed code or image URL that users can paste into forum signatures, personal websites, Discord bios, or blog sidebars. Shows platinum count, current badge progress, avatar, etc.

**Current state**: Not implemented. Profiles are public pages but there's no embed widget.

**Impact**: Medium. Passive brand exposure wherever users participate online. Every forum post with an embedded PlatPursuit card is a micro-advertisement.

**Approach**: New API endpoint that returns a small HTML snippet or a dynamically rendered PNG (reusing Playwright pipeline). Include an "Embed your profile" section on the profile settings page with copy-paste code.

**Key files**: New API endpoint, new small card template, profile settings template.

**Effort**: Medium. Needs a new rendering template, an API endpoint, and embed code generation UI. 2-3 days.

### Monthly Recap Email Social CTA (High Impact)

**What**: Add a prominent "Share your recap on X/Twitter" button to the monthly recap email template, using Twitter Web Intents to pre-fill a tweet.

**Current state**: The recap email (`templates/emails/monthly_recap.html`) exists and is sent to all users with trophy activity on the 3rd of each month. It does not currently include a social sharing call-to-action.

**Impact**: High. The email already reaches every active user at the perfect moment (when their recap is fresh and exciting). Adding a share button converts email opens into social posts with near-zero friction.

**Approach**: Add a styled button/link to the email template pointing to a Twitter Web Intent URL with pre-filled text like "Check out my [Month] recap on @platpursuit! #PlatPursuitRecap [link to recap page]".

**Key files**: `templates/emails/monthly_recap.html`.

**Effort**: Very Low. A single HTML button added to an existing template. Could be done in under an hour.

### RSS/Atom Feed (Low-Medium Impact)

**What**: Feed of community achievements, new badge series, challenge completions. Can be consumed by social media scheduling tools, IFTTT, or Zapier for automated cross-posting.

**Current state**: Not implemented. Django has a built-in syndication framework that makes this straightforward.

**Impact**: Low-Medium. Bridges platform data to automation tools. Useful if you want to set up IFTTT rules like "When new RSS item, post to Twitter."

**Approach**: Use Django's `django.contrib.syndication` framework. Create feeds for community milestones, new badge earners, etc.

**Key files**: New `feeds.py` in core or trophies app.

**Effort**: Low. Django's syndication framework handles most of the work. Half a day.

### Implementation Priority

Ordered by impact-to-effort ratio:

1. **Monthly Recap Email Social CTA** (very low effort, high impact): do this first
2. **"Share to Twitter" Button** (low effort, high impact): do this second
3. **Public Community Stats Page** (low-medium effort, high impact): natural next step
4. **Trending Games Feed** (low effort, medium-high impact): can combine with stats page
5. **Dynamic OG Images** (medium effort, very high impact): biggest long-term payoff
6. **RSS/Atom Feed** (low effort, low-medium impact): quick automation win
7. **Embeddable Profile Cards** (medium effort, medium impact): nice-to-have

---

## 11. Brand Voice and Guidelines

### The Platinum Pursuit Personality

PlatPursuit is a trophy hunter who also happens to build tools. We speak as a fellow hunter, not as a company talking to customers.

**Core personality traits**:
- **Passionate**: We genuinely care about trophy hunting and gaming. This comes through in everything.
- **Knowledgeable**: We have the data and share it generously. We're the friend who always knows the stats.
- **Encouraging**: We celebrate every platinum, not just the rare ones. Every hunter's journey matters.
- **Playful**: We crack jokes, use gaming references, and don't take ourselves too seriously.
- **Indie-proud**: We're one person building something cool. That's a strength, not a weakness.

### Tone Examples

**Good** (matches the Platinum Pursuit Standard):
- "47 platinums in one month. You absolute legend. Your February recap is going to be something else."
- "We just shipped the Genre Challenge. 16 genres, 32 subgenres, infinite bragging rights. Go explore."
- "Fun fact from our data: the most popular day to earn a platinum trophy is Saturday (shocking, we know)."
- "The Souls badge now has 12 earners. Every single one of them earned it the hard way. No shortcuts in Lordran."

**Bad** (too corporate):
- "We are pleased to announce the launch of our new Genre Challenge feature, designed to enhance your trophy hunting experience."
- "Platinum Pursuit offers a comprehensive suite of tracking tools for PlayStation trophy enthusiasts."

**Bad** (too casual/unprofessional):
- "omg this feature is SO fire no cap fr fr"
- "lol we broke the site again sorry"
- "like and subscribe!!! smash that follow button!!!"

### Do's and Don'ts

| Do | Don't |
|----|-------|
| Celebrate community achievements enthusiastically | Be generic ("congrats!" with nothing else) |
| Share specific, interesting data points | Post vague "check out our site" content |
| Acknowledge when something doesn't work and fix it fast | Ignore complaints or bug reports on social |
| Use first person ("I built", "We shipped") | Use third person ("Platinum Pursuit is pleased to...") |
| Reference specific games, trophies, and community moments | Be so generic that any trophy site could have posted it |
| Maintain consistent posting cadence | Go silent for weeks, then spam posts |
| Credit and thank community members by name | Take credit for community achievements |
| Share development struggles honestly (the indie story) | Pretend to be bigger than you are |
| Use humor and gaming culture references | Force memes or trends that don't fit |
| Write in clear, direct language | Use jargon, corporate speak, or buzzwords |
| Use colons and periods for sentence breaks | Use em dashes (per project style guide) |

### Visual Consistency Guidelines

**Color palette**: Match the platform's existing gradient themes. The `GRADIENT_THEMES` dictionary in `trophies/themes.py` defines the official color system. Share cards, social graphics, and profile images should use these same gradients.

**Typography**: Poppins (headings) and Inter (body text), matching the fonts embedded in share cards.

**Logo usage**: Always use the official logo from `static/images/`. The share cards demonstrate the right balance: visible branding without overwhelming the content.

**Image standards**: Follow the project's conventions:
- `object-cover` for game/trophy icons (square aspect ratio)
- `object-contain` for badges (transparent backgrounds)
- Never stretch or distort images

**Social profile consistency** across all platforms:
- Same logo as profile picture
- Consistent bio format: "Track your PlayStation trophies, earn badges, conquer challenges. Built by a trophy hunter, for trophy hunters. [link]"
- Share card example or community stats as header/banner image

---

## 12. Quick-Start Priorities

### Week 1: Foundation

| Day | Action | Time | Platform |
|-----|--------|------|----------|
| Day 1 | Audit and update all social profiles (bio, profile pic, header, links) | 1 hour | All |
| Day 1 | Create a Bitly or UTM link for the homepage to track social referrals | 15 min | Internal |
| Day 2 | Write and post an "origin story" thread: why you built PlatPursuit | 1 hour | X/Twitter |
| Day 2 | Share the thread in Discord with a personal note | 15 min | Discord |
| Day 3 | Create your first "Trophy Data Drop" post using community stats | 45 min | X/Twitter |
| Day 3 | Set up Discord channel structure (if not already done) | 30 min | Discord |
| Day 4 | Download 3-4 share cards from your own profile, post them as examples | 30 min | X/Twitter |
| Day 5 | Write your first Reddit post for r/Trophies (data-driven, value-first) | 1 hour | Reddit |
| Day 6 | Set up a scheduling tool (Buffer free tier) and schedule 3 posts for next week | 30 min | Internal |
| Day 7 | Respond to any engagement from the week's posts. Note what worked. | 30 min | All |

**Total Week 1 time**: ~6 hours

### First Month: Cadence Building

**Goals**:
- Establish consistent posting rhythm (minimum 4 posts/week on X/Twitter)
- Post at least 2 times on Reddit (r/Trophies)
- Host 1 Discord event or challenge
- Run the first Monthly Recap social push (beginning of next month)
- Identify 3-5 active community members for spotlight posts

**Weekly checklist**:
- [ ] 4-5 X/Twitter posts (mix of pillars)
- [ ] 1-2 community card retweets
- [ ] 1 Reddit post or substantial comment contribution
- [ ] Daily Discord check-in (even if just 5 minutes)
- [ ] Sunday content prep session (2 hours)

**Experiments to try**:
- Different post times (morning vs. evening) to find your audience's active hours
- Polls vs. data posts vs. share card showcases (see which get most engagement)
- Long threads vs. single-image posts
- Tagging vs. not tagging community members in spotlights

### 90-Day Roadmap

| Phase | Weeks | Focus | Key Milestones |
|-------|-------|-------|---------------|
| **Foundation** | 1-2 | Profile setup, first posts, cadence establishment | All profiles updated, 10+ posts published, scheduling tool set up |
| **Consistency** | 3-6 | Regular posting rhythm, community engagement, Reddit participation | 4-5 posts/week sustained, first Reddit post with 50+ upvotes, 10+ Discord active members |
| **Growth Levers** | 7-10 | Monthly Recap campaigns, share card encouragement, first YouTube video | First viral recap push, 5+ user-shared cards, 1 YouTube video published |
| **Amplification** | 11-13 | Creator outreach, community events, content refinement | First creator partnership, first Discord event with 10+ participants, content pillar mix optimized based on data |

### Avoiding Burnout

**The 5-10 hour budget is a ceiling, not a floor.** Protect your time:

- **Batch ruthlessly**: Sunday prep session covers 70% of the week's content. Daily check-ins are 15 minutes max.
- **Repurpose everything**: A single data insight becomes a tweet, a Discord post, and a Reddit comment. One share card example becomes 3 platform posts.
- **Skip days guilt-free**: Missing a Wednesday post never killed a brand. Consistency matters more than perfection.
- **Automate what you can**: Use the scheduling tool. Don't post in real-time unless it's reactive content.
- **Measure and cut**: After 90 days, if a platform isn't producing results, drop it. Focus on what works.
- **Development comes first**: PlatPursuit's best social media strategy is building a great product. If you have to choose between coding a new feature and writing a tweet, the feature wins every time. Great products generate their own word-of-mouth.

---

## Gotchas and Pitfalls

- **Reddit self-promotion rules**: Most gaming subreddits enforce a 10:1 participation ratio. If you post a link to PlatPursuit, you need 10 other genuine comments/posts in that subreddit first. Violating this gets you shadow-banned.
- **Twitter/X API instability**: The X/Twitter API landscape changes frequently. Any automation built against it may break. Prefer manual posting with scheduling tools over full API automation until the platform stabilizes.
- **Discord notification fatigue**: The webhook system already sends badge and milestone notifications. Adding too many automated messages risks turning the server into a notification dump. Keep automated posts to 1-2 per day maximum.
- **Share card branding balance**: Ensure the PlatPursuit logo/URL on share cards remains visible but not obnoxious. Users share cards because they look good. If branding overwhelms the content, sharing drops.
- **Seasonal variance**: Trophy hunting activity peaks during holiday sales (November-January) and around major game launches. Social engagement will naturally dip during quieter periods. This is normal, not a failure.
- **Data accuracy in social posts**: Always verify stats before posting. A wrong number gets screenshotted and shared as "this site has bad data." Use `compute_community_stats()` output directly rather than manual counting.
- **Monthly Recap timing**: Recaps generate on the 3rd at 00:05 UTC and emails send at 06:00 UTC. Don't push "your recap is ready!" content before this time. Users will visit, find nothing, and be frustrated.
- **Platform-specific image formats**: Landscape (1200x630) for X/Twitter and Discord. Portrait (1080x1350) for Instagram and TikTok. The share card system supports both, but you need to download the right format for the right platform.
- **Engagement bait backfire**: Trophy hunting communities are savvy. Overly clickbaity posts ("You won't BELIEVE this platinum!") will get called out. Keep the tone genuine and data-backed.

## Related Docs

- [Share Images](../features/share-images.md): Playwright rendering, share card types, format specifications
- [Monthly Recap](../features/monthly-recap.md): Recap generation, email delivery, share card generation
- [Challenge Systems](../features/challenge-systems.md): A-Z, Calendar, Genre challenge mechanics
- [Badge System](../architecture/badge-system.md): Badge series, tiers, XP, leaderboards
- [Notification System](../architecture/notification-system.md): Discord webhooks, notification types
- [Homepage Services](../reference/homepage-services.md): Community stats, featured content, caching
- [Cron Jobs](cron-jobs.md): Scheduled tasks for data freshness
