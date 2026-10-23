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

Lists are regional versions of each other when they belong to the **same IGDB version on the same platform**: the raw `IGDBMatch.igdb_id` of their concept, trusted matches only (`IGDBMatch.TRUSTED_STATUSES`). Trophy setups do not have to match. Lists with no trusted match fall back to their Concept.

Within a unit, lists that share a title ID are one product carrying two trophy lists, so they are not each other's regional siblings.

## Region sources, by trust

1. The dataset region for that title ID (`TitleID.region`).
2. An exact match: a product ID embeds its title ID (`EP9000-PPSA03208_00-...`), and the exact title ID only appears in its home storefront's product list. The storefront that carries it names the region.
3. Unknown. Never NA by default.

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

**Audit buckets:** `dataset_disagrees`, `korea_as_jp`, `na_from_unknown` (NA, with title IDs that name no known region), `regional_missed`, `same_region_siblings`, `regional_stale`, `regional_no_region`. A list can be in several. Region-locked lists are skipped and left out of the percentages, but still count as another list of their game when judging siblings (unlike `check_and_mark_regional`, which ignores them).

**Run order on prod:** `populate_title_ids` first (it resets the dataset's TitleID rows that sync overwrote), then `audit_regions`.

## Gotchas and Pitfalls

- **A product prefix names the storefront, not the title ID.** Never read a region from `defaultProduct.id` without checking that the product ID contains the exact title ID you asked about.
- **`IP` is not NA.** It is Sony's worldwide/system prefix and our own "unknown" placeholder. Treat both as no region.
- **Old relisted title IDs match no storefront** (Watch Dogs CUSA00015/29): the store now sells newer IDs. Those stay unknown.
- **Not `GameFamily`, not the `X`/`X-N` family.** Both group other versions (The Last of Us original, Remastered and Part I share canonical 1009), so either would flag a remaster as a regional version.
- **The audit's blind spot:** sync overwrites every TitleID stored as `IP` with the US store's answer. That covers title IDs missing from the dataset (auto-created as `IP`) and the ~9.7k dataset rows with no region (also stored as `IP`). Those rows hold the same wrong NA as their lists, so the audit cannot see those lists as wrong. `populate_title_ids` resets the dataset rows only. Telling the rest apart needs provenance on `TitleID`, which the fix adds.
- **`partials/region_badges.html` renders anything that is not NA, EU or JP as "AS"**, so a KR or CN list shows an AS chip there. The fix must update it when it starts storing KR.
- **Lone regional lists are not all stale.** A list labeled only JP/AS/KR/CN with no sibling is probably an Asia-only release; the audit counts those separately.
- **Querying another storefront** is just the `country`/`language` parameters on the same catalog call, with the same NPSSO. Responses come back localized (name, description, rating), so a non-US response must never overwrite Concept fields (the existing `used_fallback_region` gate in `sync_title_id`).

## Related Docs

- [Token Keeper & Sync Pipeline](token-keeper.md): `sync_title_id` and the title-stats walk that attach title IDs
- [Concept Model](concept-model.md): `TitleID`, `check_and_mark_regional`, concepts and fingerprints
- [IGDB Integration](igdb-integration.md): trusted matches and `X-N` sibling concepts
