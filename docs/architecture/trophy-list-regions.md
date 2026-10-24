# Trophy-List Regions

How a trophy list gets its region chips (NA, EU, JP, AS, KR, CN) and its `is_regional` flag, where that data comes from, and the commands that build, walk and audit it. Hunters read these chips to pick the right list when one game has several on the same platform, so a wrong chip sends them to the wrong list.

**Status (2026-10-23):** rebuilt. Regions come from which storefronts sell each title ID (the store walk), labels and flags are recomputed rather than appended, and the flag is judged per IGDB version by the regional rule. The walk fills in over time on the bulk queue; until it reaches a title ID, that title ID's label is its dataset prefix and the flag does not act on it.

## How it works

1. During sync, each PS4/PS5 **title ID** a hunter has played is mapped to its trophy list via `trophy_titles_for_title` and appended to `Game.title_ids` (`token_keeper.py`, title-stats walk).
2. `sync_title_id` resolves the concept, then calls `region_service.after_title_id_synced`: it rebuilds that list and every list in its regional unit, and, if the title ID has never been walked, queues its **store walk** (`resolve_title_id_region`, profile-less and uncounted, so the hunter's sync never waits on it).
3. The store walk (always on the BULK queue, below every hunter's sync) asks all six storefronts (US, GB, JP, HK, KR, CN) whether they sell that exact title ID, saves the set on `TitleID.store_regions` / `store_checked_at`, and rebuilds every list carrying it. All or nothing: a failed call writes nothing.
4. `rebuild` writes, for each list not region-locked: the **label** (`Game.region`) = the union of its title IDs' regions (when they name none and every title ID is walked, the script of its title; while any is unwalked, the stored label stays), and the **flag** (`is_regional`) = the regional rule's verdict. It runs scoped (sync, the walk) and catalogue-wide nightly (`rebuild_regions` in `nightly`), which catches concept merges, re-anchors and IGDB match changes that move lists between units. Writes are conditional on the exact stored value it loaded and on the list still being unlocked, and the counts it returns are rows actually written.
5. Lists PSN gave no details for and that have no concept still get a region from the script of their title (`detect_asian_language`) via `apply_script_region`, which also flags them.
6. Templates show region chips only when `is_regional` is set (`partials/region_badges.html`, game page hero, list switcher, game cards).

## Region sources

A title ID is a product SKU. What it means for regions:

| TitleID state | Label (`Game.region`) | Verdict (the flag) |
|---|---|---|
| Walked, sold somewhere | the stores that sell it | the stores that sell it |
| Walked, sold nowhere (delisted, relisted under a newer ID) | dataset prefix | dataset prefix |
| Not walked yet | dataset prefix (provisional) | nothing (the flag waits) |
| Not walked, prefix not from the dataset (`region_source` blank) | nothing | nothing |

A list whose title IDs name no region keeps its stored label until **every** title ID is walked, exactly as an unjudged list keeps its flag: with any still unwalked, "names no region" only means "not known yet". So a stored NA that came from an `IP` title ID stays until the walk answers for it, and the window between the migration and the next `populate_title_ids` (when no prefix is trusted yet) cannot wipe labels. A legacy-shaped label (a bare string, blanks, repeats) is rewritten once as a clean list even when its regions do not change, since every chip loop iterates it.

Store codes: US=NA, GB=EU, JP=JP, HK=AS, KR=KR, CN=CN (`STOREFRONTS` in `util_modules/constants.py`). TW and SG answer exactly like HK, so HK speaks for AS. CN sells its own title IDs (none of the western or HK ones).

Prefix table (`PREFIX_REGIONS`): `UP/UB/US/UT` NA, `EP/EB` EU, `JP/JA/JB` JP, `HP/HA/HB/HT` AS, `KP` KR, `CN` CN. `IP` and empty name no region. Only `populate_title_ids` (the andshrew dataset) writes a trusted prefix (`region_source='dataset'`); sync used to write the US store's answer into `TitleID.region`, so a prefix from anywhere else is ignored.

Why the store set and not the prefix (probed 2026-10-23): worldwide products keep ONE prefix in every store (Black Myth: Wukong is `HP` in all five, The Exit 8 and Death Stranding 2 `JP`, LEGO Horizon Adventures `EP`), while region-split products appear only in their own stores (Ghost of Tsushima's EU ID only in GB; FFXV's EU ID only in GB; Hollow Knight's EU ID in GB, JP, HK and KR but not the US). So one title ID can mean several regions, and a prefix names the publishing account, not where it is sold.

## The regional unit

Candidate siblings are lists of the **same IGDB version on the same console**: the raw `IGDBMatch.igdb_id` of their concept, trusted matches only (`IGDBMatch.TRUSTED_STATUSES`). Trophy setups do not have to match. Lists with no trusted match fall back to their Concept.

- **Consoles only** (`PLATFORM_PRIORITY_ORDER`). `PSVR`, `PSVR2` and `PSPC` are tags that ride along on a console list, so a PS4/PSVR list and a PS5/PSVR list of one game are NOT siblings (No Man's Sky, RE7 and Hitman 3 were, in the first baseline). A list with only tags is grouped on its tags.
- **Products, not lists.** Lists that share a title ID are one product carrying two trophy lists, so they are not each other's siblings.

## The regional rule

Being a candidate sibling is not enough. The 2026-10-23 prod baseline showed same-region siblings everywhere: Dead by Daylight splits one game's trophies across several EU/NA lists, Fall Guys got a new list at its free-to-play relaunch, Call of Duty HQ carries several PS5 lists. What separates a regional split is the **regions**, read from the verdict column above (never from the stored label, which is what is being judged).

| Verdict | When | Reason code |
|---|---|---|
| Regional | another product in its unit covers regions that do **not overlap** its own (Astro Bot AS/EU/JP/NA beside Astro Bot CN) | `disjoint_sibling` |
| Unjudged (flag left as is) | it has no region data | `no_region_data` |
| Regional | its regions are Asian only (JP/AS/KR/CN), with or without a sibling | `asia_only` |
| Unjudged (flag left as is) | a sibling has no region data, so a disjoint sibling cannot be ruled out | `sibling_without_region_data` |
| Not regional | every sibling's regions overlap its own (split list, reissue, relaunch) | `overlapping_siblings` |
| Not regional | no sibling at all | `global` |

Checked top to bottom. "Its regions" are its product's: every list sharing a title ID with it, so one storefront product cannot get two verdicts. A region-locked list counts with its admin-set region, since that is the truth its siblings are judged against. The two owner decisions (2026-10-23): an Asia-only list is regional to its region even alone, and a list that cannot be judged keeps whatever flag it has (most of PS3/Vita, which have no title IDs). The rule is `region_service.regional_verdicts`; `rebuild` and `audit_regions` both call it.

## File Map

| File | Purpose |
|------|---------|
| `trophies/services/region_service.py` | Title-ID regions, the regional unit and rule, `rebuild` / `refresh_units`, the store walk, and the audit's `scan` |
| `trophies/token_keeper.py` | `_job_sync_title_id` (calls `after_title_id_synced`), `_job_resolve_title_id_region` (the walk's PSN calls), the `region_walk_refill` dispatch |
| `trophies/models.py` | `Game.region` / `is_regional` / `region_lock`; `TitleID.region` / `region_source` / `store_regions` / `store_checked_at` |
| `trophies/util_modules/constants.py` | `REGIONS` (display order), `PREFIX_REGIONS`, `STOREFRONTS` |
| `core/management/commands/populate_title_ids.py` | Loads the andshrew dataset into `TitleID` (`region_source='dataset'`) |
| `core/management/commands/nightly.py` | Runs `rebuild_regions`, then `resolve_title_regions`, nightly |
| `templates/partials/region_badges.html` | The badge-page region chips |

## Management Commands

| Command | Purpose | Typical Usage |
|---------|---------|---------------|
| `resolve_title_regions` | Queue the store walk for every title ID on a list, most-played first, on the bulk queue. A refill chain keeps the queue short and stops itself when a batch walks nothing (PSN outage) or everything left is cooling down. Runs nightly too (a no-op once all is walked), so a stopped walk resumes and title IDs sync never queued get walked. One chain at a time. | `--status`, `--limit 50` (one trial batch, no chain), `--batch N` (default 500) |
| `rebuild_regions` | Rewrite every list's label and flag from the rules. Nightly. | `python manage.py rebuild_regions --dry-run` |
| `audit_regions` | Read-only. Buckets every list by what is wrong with its region or flag, with most-played samples, siblings, coverage and walk progress. | `python manage.py audit_regions`, `--bucket korea_as_jp`, `--platform PS5`, `--sample N` |
| `populate_title_ids` | Refresh `TitleID` from the andshrew dataset. Never touches the store fields. | `python manage.py populate_title_ids` |

**Audit buckets:** `wrong_region` (stored has a code its title IDs do not support), `missing_region` (stored only lacks one), `korea_as_jp`, `na_from_unknown` (NA, with title IDs that name no known region), `regional_missed` / `regional_stale` (the flag disagrees with the regional rule, tallied by reason code), `regional_no_region`. A list can be in several. The `shown` column counts lists whose chips actually render (flagged regional, with a stored region). Each sample names the siblings its verdict compared against (up to four, disjoint ones first and marked `*`; `[admin]` marks a region-locked sibling standing on its admin-set label), and each title ID shows its prefix and, once walked, the stores that sell it. The "Store walk" lines count distinct title IDs on lists across the whole catalogue (ignoring `--platform`) and how many are walked. Region-locked lists are skipped and left out of the percentages, but still count as siblings.

## Gotchas and Pitfalls

- **The concept response describes the storefront you asked.** `defaultProduct.id` is the asked store's product for the whole concept, usually not the title ID you asked about. Only a product ID containing the exact title ID (`sells_title_id`) says anything about that title ID.
- **Even an exact match's prefix is not a region.** It names the publishing account (a worldwide `HP` product is sold in the US store too). The region is WHICH stores carry the exact title ID.
- **A store that does not sell a title ID answers normally**, with no products. Absence is an answer; only a raised error is a failed lookup, and the walk then writes nothing.
- **Writes are conditional.** `rebuild` loads, computes, then writes only where the list is still unlocked and still holds the value it loaded (grouped `UPDATE ... WHERE region = <loaded>`). An admin who locks a list mid-rebuild, or a walk that rebuilt it since, wins.
- **One walk chain at a time.** `region_walk:chain` (2h, refreshed by every refill and by every walk job the chain queued, so a batch that takes hours to drain keeps it; taken with `nx` and released if the first refill cannot be queued) makes a second `resolve_title_regions` refuse. `redis_admin --flush-token-keeper` clears both `region_walk:*` keys with the queues. The chain stops when a batch walks nothing (measured on the WALKED count, which sync adding new title IDs cannot move backwards) or when every remaining title ID is cooling down after a failed lookup (`region_walk:queued:{title_id}`, 24h, deleted on success). Both stops are logged as warnings; the nightly `resolve_title_regions` resumes it, or re-run the command by hand.
- **The flag waits for the walk; the label does not.** Acting on an unwalked prefix would flag worldwide products from Asian publishers as Asia-only. A list's chips can therefore change twice: once at the first rebuild (label from the prefix), again when the walk reaches it.
- **Store walk jobs are profile-less and uncounted** (`skip_counter=True`). A counted job would make a hunter's `sync_complete` wait on store lookups, and a `profile_id=None` counter would put "None" in `active_profiles`. The worker does not re-queue profile-less jobs after a DB lock or outage; the walk's queued key lapses (24h) and the next nightly `resolve_title_regions` retries it.
- **Platform comes from the title ID when the TitleID's is a tag.** An auto-created TitleID copies the list's first platform, which can be `PSVR`; the catalog only takes PS4/PS5, so `platform_for` falls back to the ID's prefix (CUSA = PS4, PPSA = PS5).
- **`title_ids__contains` has no GIN index.** Each walk job finds the lists carrying its title ID with a sequential scan of Game. Fine at catalogue size; add an index if the walk ever becomes hot.
- **`_execute_api_call` with `profile=None`** must not touch the profile on success (it used to set the history-public flag unguarded, which raised after PSN had answered).
- **`IP` is not NA.** It is Sony's worldwide/system prefix and the old "unknown" placeholder. It names no region.
- **Old relisted title IDs match no storefront** (Watch Dogs CUSA00015/29): the store now sells newer IDs. They fall back to the dataset prefix.
- **Not `GameFamily`, not the `X`/`X-N` family.** Both group other versions (The Last of Us original, Remastered and Part I share canonical 1009), so either would flag a remaster as a regional version.
- **Scoped rebuilds must cover whole units.** A verdict depends on every sibling, so `refresh_units` expands the lists it is given to their full units first. Calling `rebuild` on a partial unit judges a list against only some of its siblings.
- **Flags can lag a day after a merge.** Sync and the walk only rebuild the units they touch; a concept merge or IGDB re-anchor is picked up by the nightly `rebuild_regions`.
- **A sibling is not a regional version.** Same-region siblings (split lists, relaunches) read as not regional, and `disambiguate_entries` keeps their switcher chips apart.
- **Lone does not mean global for Asia.** A list from Asian regions only is regional by itself (owner decision), so it keeps its JP/AS chip with no sibling.
- **Querying another storefront** is just the `country`/`language` parameters on the same catalog call, with the same NPSSO. Responses come back localized (name, description, rating), so a non-US response must never overwrite Concept fields (the existing `used_fallback_region` gate in `sync_title_id`). The walk reads only product IDs.

## Related Docs

- [Token Keeper & Sync Pipeline](token-keeper.md): `sync_title_id`, the title-stats walk that attaches title IDs, and the walk job
- [Concept Model](concept-model.md): `TitleID`, concepts and fingerprints
- [IGDB Integration](igdb-integration.md): trusted matches and `X-N` sibling concepts
- [Cron Jobs](../guides/cron-jobs.md): the nightly chain
