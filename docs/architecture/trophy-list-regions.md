# Trophy-List Regions

How a trophy list gets its region chips (NA, EU, JP, AS, KR, CN) and its `is_regional` flag, what is known to be wrong with that today, and the read-only report that measures it. Hunters read these chips to pick the right list when one game has several on the same platform, so a wrong chip sends them to the wrong list.

**Status (2026-10-23):** the audit command exists. The fix (correct the signal, rebuild regions from title IDs, recompute the flag on the right unit) is planned but **not built yet**. Everything under "How it works today" describes the live behavior, bugs included.

## How it works today

1. During sync, each PS4/PS5 **title ID** a hunter has played is mapped to its trophy list via `trophy_titles_for_title` and appended to `Game.title_ids` (`token_keeper.py`, title-stats walk).
2. `sync_title_id` asks the US storefront for that title's details (and, only when the US answer is sparse, the JP, HK and KR storefronts in turn). `detect_region_from_details` reads the 2-letter prefix of `defaultProduct.id` (`UP`, `EP`, ...) and `Game.add_region` appends the matching code to `Game.region`. If `TitleID.region` was `IP`, the detected prefix is also written back to the TitleID row.
3. If no prefix is found, it falls back to `TitleID.region`, loaded from the andshrew PlayStation-Titles dataset by `populate_title_ids`.
4. Lists with no usable concept details and no concept get a region from the script of their title (`detect_asian_language`: Japanese, Korean, Chinese characters), and that path sets `is_regional=True` directly (so does the one-off `populate_region_asian_titles` command).
5. `Concept.check_and_mark_regional()` sets `is_regional=True` on every unlocked (`region_lock=False`) list of a concept that has 2+ lists on the same PS4/PS5 platform (lists sharing a title ID count as one).
6. Templates show region chips only when `is_regional` is set (`partials/region_badges.html`, game page hero, list switcher, game cards).

## Known problems (verified 2026-10-23)

| # | Problem | Effect |
|---|---|---|
| 1 | PSN's concept details describe the game as the **storefront asked** sells it. Asking US about an EU, JP or HK title ID returns a `UP` product. Step 2 then outranks the dataset's correct region. | EU/JP/Asia lists pick up NA. Sibling lists both read "NA". |
| 2 | `KP` (Korea) sits in `JP_REGION_CODES`. `KR` never occurs as a prefix. | Korean lists read JP. |
| 3 | `IP` is both our "unknown" placeholder and Sony's real worldwide prefix, and sits in `NA_REGION_CODES`. 9,717 dataset title IDs have no region. | Unknown becomes NA. |
| 4 | `add_region` only appends; `check_and_mark_regional` only sets True, and only runs in `sync_title_id`. | Nothing ever clears: splits, re-anchors, `absorb()` and admin fixes leave stale chips. |
| 5 | The regional check runs inside one Concept, but a Concept also requires a matching trophy fingerprint. | Regional lists with any trophy difference (Chinese Astro Bot) live in an `X-N` sibling concept and are never flagged. |
| 6 | Title IDs only arrive for PS4/PS5, and the check loops PS4/PS5 only. | PS3/Vita stacks are flagged only via the title-script fallback. |

## The regional unit

Candidate siblings are lists of the **same IGDB version on the same console**: the raw `IGDBMatch.igdb_id` of their concept, trusted matches only (`IGDBMatch.TRUSTED_STATUSES`). Trophy setups do not have to match. Lists with no trusted match fall back to their Concept.

- **Consoles only** (`PLATFORM_PRIORITY_ORDER`). `PSVR`, `PSVR2` and `PSPC` are tags that ride along on a console list, so a PS4/PSVR list and a PS5/PSVR list of one game are NOT siblings (No Man's Sky, RE7 and Hitman 3 were, in the first baseline). A list with only tags is grouped on its tags.
- **Products, not lists.** Lists that share a title ID are one product carrying two trophy lists, so they are not each other's siblings.

## The regional rule

Being a candidate sibling is not enough. The 2026-10-23 prod baseline showed same-region siblings everywhere: Dead by Daylight splits one game's trophies across several EU/NA lists, Fall Guys got a new list at its free-to-play relaunch, Call of Duty HQ carries several PS5 lists. What separates a regional split is the **regions**, read from title IDs (never from the stored label, which is what is being judged).

| Verdict | When | Reason code |
|---|---|---|
| Regional | another product in its unit covers regions that do **not overlap** its own (Astro Bot AS/EU/JP/NA beside Astro Bot CN) | `disjoint_sibling` |
| Unjudged (flag left as is) | it has no region data | `no_region_data` |
| Regional | its regions are Asian only (JP/AS/KR/CN), with or without a sibling | `asia_only` |
| Unjudged (flag left as is) | a sibling has no region data, so a disjoint sibling cannot be ruled out | `sibling_without_region_data` |
| Not regional | every sibling's regions overlap its own (split list, reissue, relaunch) | `overlapping_siblings` |
| Not regional | no sibling at all | `global` |

Checked top to bottom. "Its regions" are its product's: every list sharing a title ID with it, so one storefront product cannot get two verdicts. A region-locked list counts with its admin-set region, since that is the truth its siblings are judged against. The two owner decisions (2026-10-23): an Asia-only list is regional to its region even alone, and a list that cannot be judged keeps whatever flag it has today (most of PS3/Vita, until a follow-up finds their regions). The rule is `region_service.regional_verdicts`, which the rebuild reuses.

## Region sources, by trust

1. **Which storefronts sell the exact title ID.** A product ID embeds its title ID (`EP9000-PPSA03208_00-...`), so each store (US, GB, JP, HK, KR) can be asked whether it sells THAT title ID. The set of stores that do is the title ID's regions: US=NA, GB=EU, JP=JP, HK=AS, KR=KR. Not built yet; the rebuild adds it.
2. **The content-ID prefix** (`TitleID.region`, from the andshrew dataset), only when no store sells the title ID (delisted or relisted IDs). It is a guess: the prefix names the account the product is published under, not where it is sold.
3. Unknown. Never NA by default.

Probed 2026-10-23: worldwide products keep ONE prefix in every store (Black Myth: Wukong is `HP` in all five, The Exit 8 and Death Stranding 2 `JP`, LEGO Horizon Adventures `EP`), while region-split products appear only in their own stores (Ghost of Tsushima's EU ID only in GB; FFXV's EU ID only in GB; Hollow Knight's EU ID in GB, JP, HK and KR but not the US). So one title ID can mean several regions, and the prefix alone mislabels worldwide products from Asian and European publishers. Until the store walk runs, the audit judges by prefix, so its `asia_only` count is inflated by those worldwide products.

Prefix table: `UP/UB/US/UT` NA, `EP/EB` EU, `JP/JA/JB` JP, `HP/HA/HB/HT` AS, `KP` KR, `CN` CN. `IP` and empty name no region. The corrected table lives in `trophies/services/region_service.py` (`PREFIX_REGIONS`); sync still uses the old lists in `util_modules/constants.py` until the fix lands.

## File Map

| File | Purpose |
|------|---------|
| `trophies/services/region_service.py` | Corrected region table, regional unit, and the audit's bucket rules (`scan`) |
| `trophies/management/commands/audit_regions.py` | Read-only report over `scan()` |
| `trophies/util_modules/region.py` | `detect_region_from_details` (the storefront-prefix read, problem 1) |
| `trophies/util_modules/constants.py` | `*_REGION_CODES` lists sync uses today (problems 2 and 3) |
| `trophies/models.py` | `Game.region` / `is_regional` / `region_lock`, `Game.add_region`, `Concept.check_and_mark_regional`, `TitleID` |
| `trophies/token_keeper.py` | `_job_sync_title_id`: where regions are written |
| `core/management/commands/populate_title_ids.py` | Loads the andshrew dataset into `TitleID` |

## Management Commands

| Command | Purpose | Typical Usage |
|---------|---------|---------------|
| `audit_regions` | Read-only. Buckets every list by what is wrong with its region or flag, with most-played samples, a per-platform coverage table, and a `region_lock` count. Run before and after any region fix. | `python manage.py audit_regions`, `--bucket korea_as_jp`, `--platform PS5`, `--sample N` |
| `populate_title_ids` | Refresh `TitleID` from the andshrew dataset | `python manage.py populate_title_ids` |

**Audit buckets:** `wrong_region` (stored has a code its title IDs do not support), `missing_region` (stored only lacks one), `korea_as_jp`, `na_from_unknown` (NA, with title IDs that name no known region), `regional_missed` / `regional_stale` (the flag disagrees with the regional rule, tallied by reason code), `regional_no_region`. A list can be in several. The `shown` column counts lists whose chips actually render (flagged regional, with a stored region); a wrong label on an unflagged list is never seen. A "Regional verdicts" table shows how the rule splits the catalogue. Each sample also names the siblings its verdict compared against (up to four, disjoint ones first and marked `*`, with the product regions the rule used; `[admin]` marks a region-locked sibling standing on its admin-set label). A "Store walk size" line counts the distinct title IDs on lists across the whole catalogue (it ignores `--platform`), which sizes the store walk (about five catalog calls each). Region-locked lists are skipped and left out of the percentages, but still count as another list of their game when judging siblings (unlike `check_and_mark_regional`, which ignores them).

**Run order on prod:** `populate_title_ids` first (it resets the dataset's TitleID rows that sync overwrote), then `audit_regions`.

## Gotchas and Pitfalls

- **The concept response describes the storefront you asked.** `defaultProduct.id` is the asked store's product for the whole concept, usually not the title ID you asked about. Only a product ID containing the exact title ID says anything about that title ID.
- **Even an exact match's prefix is not a region.** It names the publishing account (a worldwide `HP` product is sold in the US store too). The region is WHICH stores carry the exact title ID, never the prefix of what they return.
- **`IP` is not NA.** It is Sony's worldwide/system prefix and our own "unknown" placeholder. Treat both as no region.
- **Old relisted title IDs match no storefront** (Watch Dogs CUSA00015/29): the store now sells newer IDs. Those stay unknown.
- **Not `GameFamily`, not the `X`/`X-N` family.** Both group other versions (The Last of Us original, Remastered and Part I share canonical 1009), so either would flag a remaster as a regional version.
- **The audit's blind spot:** sync overwrites every TitleID stored as `IP` with the US store's answer. That covers title IDs missing from the dataset (auto-created as `IP`) and the ~9.7k dataset rows with no region (also stored as `IP`). Those rows hold the same wrong NA as their lists, so the audit cannot see those lists as wrong. `populate_title_ids` resets the dataset rows only. Telling the rest apart needs provenance on `TitleID`, which the fix adds.
- **`partials/region_badges.html` renders anything that is not NA, EU or JP as "AS"**, so a KR or CN list shows an AS chip there. The fix must update it when it starts storing KR.
- **A sibling is not a regional version.** Two lists of one game on one console are regional only when their regions do not overlap. Same-region siblings (split lists, relaunches) read as not regional, and `disambiguate_entries` keeps their switcher chips apart.
- **Lone does not mean global for Asia.** A list from Asian regions only is regional by itself (owner decision), so it keeps its JP/AS chip with no sibling.
- **Querying another storefront** is just the `country`/`language` parameters on the same catalog call, with the same NPSSO. Responses come back localized (name, description, rating), so a non-US response must never overwrite Concept fields (the existing `used_fallback_region` gate in `sync_title_id`).

## Related Docs

- [Token Keeper & Sync Pipeline](token-keeper.md): `sync_title_id` and the title-stats walk that attach title IDs
- [Concept Model](concept-model.md): `TitleID`, `check_and_mark_regional`, concepts and fingerprints
- [IGDB Integration](igdb-integration.md): trusted matches and `X-N` sibling concepts
