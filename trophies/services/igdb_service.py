import logging
import re
import time
import unicodedata
from datetime import datetime, timezone as dt_timezone
from difflib import SequenceMatcher

import requests
from django.conf import settings
from django.core.cache import cache
from django.db import IntegrityError
from django.utils.text import slugify

from trophies.models import Company, ConceptCompany, IGDBMatch
from trophies.util_modules.cache import redis_client

logger = logging.getLogger('psn_api')

# PlayStation platform IDs in IGDB
PS_PLATFORM_IDS = (7, 8, 9, 38, 46, 48, 165, 167, 390)
PS_PLATFORM_FILTER = ','.join(str(p) for p in PS_PLATFORM_IDS)

# IGDB platform ID -> human-readable name (PlayStation + common others)
IGDB_PLATFORM_NAMES = {
    7: 'PS1', 8: 'PS2', 9: 'PS3', 38: 'PSP', 46: 'Vita',
    48: 'PS4', 165: 'PSVR', 167: 'PS5', 390: 'PSVR2',
    6: 'PC', 14: 'Mac', 3: 'Linux',
    49: 'Xbox One', 169: 'Xbox Series',
    130: 'Switch', 41: 'Wii U',
    34: 'Android', 39: 'iOS',
}

# PlatPursuit platform string -> IGDB platform ID (reverse of IGDB_PLATFORM_NAMES)
PLAT_TO_IGDB_ID = {
    'PS1': 7, 'PS2': 8, 'PS3': 9, 'PSP': 38, 'PSVITA': 46,
    'PS4': 48, 'PSVR': 165, 'PS5': 167, 'PSVR2': 390,
}

# IGDB external game category for PlayStation Store
PLAYSTATION_STORE_CATEGORY = 36

# Website category mapping for external_urls
WEBSITE_CATEGORIES = {
    1: 'official',
    2: 'wikia',
    3: 'wikipedia',
    4: 'facebook',
    5: 'twitter',
    6: 'twitch',
    8: 'instagram',
    9: 'youtube',
    13: 'steam',
    14: 'reddit',
    15: 'itch',
    16: 'epicgames',
    17: 'gog',
    18: 'discord',
}

# Fields we request from IGDB for each game.
#
# Deprecated-but-retained for transition (IGDB still returns them on older
# entries, omits them on newer): `category`, `status`, `external_games.category`.
# Their replacements (`game_type`, `game_status`, `external_games.game_release_format`)
# are reference types, so we expand with .* to get the full {id, name} object.
GAME_FIELDS = (
    'name, slug, summary, storyline, url, '
    'category, game_type.*, status, game_status.*, '
    'first_release_date, rating, aggregated_rating, '
    'cover.image_id, '
    'genres.name, genres.slug, '
    'themes.name, themes.slug, '
    'keywords.name, keywords.slug, '
    'game_modes.name, player_perspectives.name, '
    'game_engines.name, game_engines.slug, '
    'game_engines.description, '
    'game_engines.logo.image_id, '
    'game_engines.companies, '
    'involved_companies.company.name, '
    'involved_companies.company.slug, '
    'involved_companies.company.description, '
    'involved_companies.company.country, '
    'involved_companies.company.logo.image_id, '
    'involved_companies.company.parent.id, '
    'involved_companies.company.parent.name, '
    'involved_companies.company.parent.slug, '
    'involved_companies.company.company_size, '
    'involved_companies.company.start_date, '
    'involved_companies.company.change_date, '
    'involved_companies.company.changed_company_id, '
    'involved_companies.developer, '
    'involved_companies.publisher, '
    'involved_companies.porting, '
    'involved_companies.supporting, '
    'franchise.id, franchise.name, '
    'franchises.id, franchises.name, collections.id, collections.name, '
    'alternative_names.name, alternative_names.comment, '
    'external_games.uid, external_games.category, '
    'external_games.external_game_source.*, external_games.game_release_format.*, '
    'websites.url, websites.category, '
    'platforms, '
    'release_dates.date, release_dates.platform, release_dates.region, '
    'similar_games, '
    'bundles, '
    'parent_game.id, parent_game.name, parent_game.slug, '
    'version_parent.id, version_parent.name, version_parent.slug, '
    'version_title'
)


# Pattern to detect likely DLC/addon entries by name
_DLC_NAME_RE = re.compile(
    r' - .+(?:'
    r'Skins?\b|Skins?\s+Pack|'
    r'Pack\b|'
    r'DLC\b|'
    r'Season\s+Pass|'
    r'Steelbook|'
    r'Costumes?\b|Outfits?\b|'
    r'Weapons?\b|Items?\b|'
    r'Maps?\b|Levels?\b|'
    r'Bonus\b|Pre-Order\b'
    r')',
    re.IGNORECASE,
)


class IGDBService:
    """Service for matching PlatPursuit Concepts to IGDB game entries
    and enriching them with developer, genre, theme, and metadata."""

    REDIS_TOKEN_KEY = 'igdb_access_token'
    REDIS_RATE_KEY = 'igdb_rate_limit'
    TOKEN_URL = 'https://id.twitch.tv/oauth2/token'
    API_BASE = 'https://api.igdb.com/v4'

    # Max requests per second across all workers
    MAX_REQUESTS_PER_SECOND = 3  # Conservative (IGDB allows 4)

    # -----------------------------------------------------------------------
    # Auth
    # -----------------------------------------------------------------------

    @classmethod
    def _get_access_token(cls):
        """Get IGDB access token, using cached version if available."""
        token = cache.get(cls.REDIS_TOKEN_KEY)
        if token:
            return token

        client_id = settings.IGDB_CLIENT_ID
        client_secret = settings.IGDB_CLIENT_SECRET
        if not client_id or not client_secret:
            raise ValueError('IGDB_CLIENT_ID and IGDB_CLIENT_SECRET must be set in environment')

        response = requests.post(cls.TOKEN_URL, params={
            'client_id': client_id,
            'client_secret': client_secret,
            'grant_type': 'client_credentials',
        }, timeout=15)
        response.raise_for_status()
        data = response.json()

        token = data['access_token']
        expires_in = data.get('expires_in', 5000000)
        # Cache with a 1-hour buffer before actual expiry
        cache.set(cls.REDIS_TOKEN_KEY, token, timeout=max(expires_in - 3600, 3600))
        return token

    # -----------------------------------------------------------------------
    # Low-level API
    # -----------------------------------------------------------------------

    @classmethod
    def _rate_limit(cls):
        """Distributed rate limiter using Redis sliding window.

        Ensures all workers collectively stay under the IGDB rate limit.
        Uses a Redis sorted set with timestamps to track requests across
        all 24 token_keeper workers.
        """
        max_wait = 5.0  # seconds
        waited = 0.0

        while waited < max_wait:
            now = time.time()
            window_start = now - 1.0  # 1-second sliding window

            pipe = redis_client.pipeline()
            # Remove expired entries
            pipe.zremrangebyscore(cls.REDIS_RATE_KEY, 0, window_start)
            # Count requests in current window
            pipe.zcard(cls.REDIS_RATE_KEY)
            results = pipe.execute()
            count = results[1]

            if count < cls.MAX_REQUESTS_PER_SECOND:
                # Slot available, record this request
                redis_client.zadd(cls.REDIS_RATE_KEY, {f'{now}:{id(cls)}': now})
                redis_client.expire(cls.REDIS_RATE_KEY, 5)
                return

            # No slot available, wait and retry
            time.sleep(0.1)
            waited += 0.1

        # Timed out waiting, proceed anyway (better than blocking forever)
        logger.warning('IGDB rate limiter timed out after %.1fs, proceeding', max_wait)

    @classmethod
    def _request(cls, endpoint, query):
        """Make an authenticated request to the IGDB API.

        Args:
            endpoint: API endpoint (e.g. 'games', 'external_games')
            query: Apicalypse query string

        Returns:
            list: Parsed JSON response (list of results)
        """
        cls._rate_limit()
        token = cls._get_access_token()
        url = f'{cls.API_BASE}/{endpoint}'
        headers = {
            'Client-ID': settings.IGDB_CLIENT_ID,
            'Authorization': f'Bearer {token}',
        }
        response = requests.post(url, data=query, headers=headers, timeout=30)

        # Stale token: clear cache and retry once with a fresh token
        if response.status_code == 401:
            logger.warning('IGDB 401 for %s, refreshing access token and retrying', endpoint)
            cache.delete(cls.REDIS_TOKEN_KEY)
            token = cls._get_access_token()
            headers['Authorization'] = f'Bearer {token}'
            cls._rate_limit()
            response = requests.post(url, data=query, headers=headers, timeout=30)

        if response.status_code == 429:
            logger.warning('IGDB rate limit hit, waiting 1 second...')
            time.sleep(1)
            cls._rate_limit()
            response = requests.post(url, data=query, headers=headers, timeout=30)
            if response.status_code == 429:
                logger.error('IGDB still rate limited after retry')

        if not response.ok:
            logger.error(
                'IGDB API error %s for %s: %s',
                response.status_code, endpoint, response.text[:500],
            )
        response.raise_for_status()
        return response.json()

    # -----------------------------------------------------------------------
    # Search
    # -----------------------------------------------------------------------

    @classmethod
    def search_game(cls, title, limit=10, platform_filter=True):
        """Search IGDB for a game by title.

        Args:
            title: Game title to search for
            limit: Max results
            platform_filter: If True, restrict to PlayStation platforms

        Returns:
            list: IGDB game objects with full Tier 1 field expansion
        """
        cleaned = cls._clean_title_for_search(title)
        where = f'where platforms = ({PS_PLATFORM_FILTER}); ' if platform_filter else ''
        query = (
            f'search "{cleaned}"; '
            f'fields {GAME_FIELDS}; '
            f'{where}'
            f'limit {limit};'
        )
        return cls._request('games', query)

    @classmethod
    def search_by_exact_name(cls, title, limit=10):
        """Query IGDB games by exact name match using where clauses.

        Unlike the search endpoint (which uses relevance ranking and buries
        base games under DLC), this tries a strict equality match first,
        then falls back to a wildcard sorted by name length.

        Returns:
            list: IGDB game objects, deduplicated, shortest names first
        """
        seen_ids = set()
        combined = []

        # Prepare a lightly escaped version that preserves colons (unlike _clean_title_for_search)
        # IGDB's where clause needs the actual name characters
        lightly_cleaned = title.strip()
        for ch in ('"', '\\'):
            lightly_cleaned = lightly_cleaned.replace(ch, '')
        lightly_cleaned = lightly_cleaned.lower()

        # First: strict case-insensitive equality with PS platform filter
        query_exact = (
            f'fields {GAME_FIELDS}; '
            f'where name ~ "{lightly_cleaned}" & platforms = ({PS_PLATFORM_FILTER}); '
            f'limit 5;'
        )
        try:
            exact = cls._request('games', query_exact)
            for r in exact:
                if r['id'] not in seen_ids:
                    seen_ids.add(r['id'])
                    combined.append(r)
        except Exception:
            pass

        # Second: wildcard match with PS platform filter, shortest names first
        query_wild = (
            f'fields {GAME_FIELDS}; '
            f'where name ~ *"{lightly_cleaned}"* & platforms = ({PS_PLATFORM_FILTER}); '
            f'sort name asc; '
            f'limit {limit};'
        )
        try:
            wild = cls._request('games', query_wild)
            wild.sort(key=lambda r: len(r.get('name', '')))
            for r in wild:
                if r['id'] not in seen_ids:
                    seen_ids.add(r['id'])
                    combined.append(r)
        except Exception:
            pass

        return combined[:limit]

    @classmethod
    def search_by_external_id(cls, psn_ids):
        """Search IGDB external_games for PlayStation Store IDs.

        Args:
            psn_ids: List of PSN IDs (concept_id, title_ids) to search for

        Returns:
            list: IGDB game IDs that matched, or empty list
        """
        if not psn_ids:
            return []

        uid_filter = ','.join(f'"{uid}"' for uid in psn_ids)
        query = (
            f'fields game; '
            f'where category = {PLAYSTATION_STORE_CATEGORY} & uid = ({uid_filter}); '
            f'limit 10;'
        )
        results = cls._request('external_games', query)
        game_ids = list({r['game'] for r in results if 'game' in r})
        if not game_ids:
            return []

        # Fetch full game details for matched IDs
        id_filter = ','.join(str(gid) for gid in game_ids)
        detail_query = (
            f'fields {GAME_FIELDS}; '
            f'where id = ({id_filter}); '
            f'limit {len(game_ids)};'
        )
        return cls._request('games', detail_query)

    @classmethod
    def search_by_alternative_name(cls, title, limit=5):
        """Search IGDB alternative_names for regional/alternate titles.

        Catches games like "Kurushi" (EU) -> "Intelligent Qube" (US/IGDB),
        or "Sly Raccoon" (EU) -> "Sly Cooper and the Thievius Raccoonus".

        Returns:
            list: IGDB game objects with full Tier 1 field expansion
        """
        cleaned = cls._clean_title_for_search(title)
        # Query alternative_names where the name matches, get the game IDs
        query = (
            f'fields game; '
            f'where name ~ *"{cleaned}"*; '
            f'limit {limit};'
        )
        results = cls._request('alternative_names', query)
        game_ids = list({r['game'] for r in results if 'game' in r})
        if not game_ids:
            return []

        # Fetch full game details
        id_filter = ','.join(str(gid) for gid in game_ids)
        detail_query = (
            f'fields {GAME_FIELDS}; '
            f'where id = ({id_filter}); '
            f'limit {len(game_ids)};'
        )
        return cls._request('games', detail_query)

    @classmethod
    def get_game_details(cls, igdb_id):
        """Fetch full game details by IGDB ID.

        Returns:
            dict or None: Full IGDB game object, or None if not found
        """
        query = (
            f'fields {GAME_FIELDS}; '
            f'where id = {igdb_id}; '
            f'limit 1;'
        )
        results = cls._request('games', query)
        return results[0] if results else None

    @classmethod
    def _fetch_time_to_beat(cls, igdb_id):
        """Fetch time-to-beat data from the separate IGDB endpoint.

        Returns:
            dict: {hastily, normally, completely} in seconds, or empty dict
        """
        try:
            query = (
                f'fields hastily, normally, completely; '
                f'where game_id = {igdb_id}; '
                f'limit 1;'
            )
            results = cls._request('game_time_to_beats', query)
            return results[0] if results else {}
        except Exception:
            logger.debug(f'No time-to-beat data for IGDB game {igdb_id}')
            return {}

    # -----------------------------------------------------------------------
    # Matching
    # -----------------------------------------------------------------------

    @classmethod
    def match_concept(cls, concept):
        """Try to match a Concept to an IGDB game entry.

        Tries in order: family sibling match, external ID, exact name, fuzzy name.

        Returns:
            tuple: (igdb_data, confidence, method) or None if no match found
        """
        # Build list of PSN IDs to try for external matching
        psn_ids = []
        if concept.concept_id and not str(concept.concept_id).startswith('PP_'):
            psn_ids.append(concept.concept_id)
        psn_ids.extend(concept.title_ids or [])

        # Strategy 1: External ID match
        if psn_ids:
            try:
                results = cls.search_by_external_id(psn_ids)
                if results:
                    best = cls._pick_best_match(concept, results, 'external_id')
                    if best:
                        return best
            except Exception:
                logger.exception(f'IGDB external ID search failed for concept {concept.concept_id}')

        title = concept.unified_title
        if not title:
            return None

        # Strategy 2: Fuzzy search (PlayStation-filtered) - handles 90%+ of games
        try:
            results = cls.search_game(title)
        except Exception:
            logger.exception(f'IGDB name search failed for "{title}"')
            return None

        if results:
            cls._log_search_results(title, 'PS-filtered', concept, results)
            best = cls._pick_best_non_dlc(concept, results)
            if best:
                return best

        # Strategy 3: Exact name query (catches base games buried under DLC in fuzzy search)
        try:
            results = cls.search_by_exact_name(title)
        except Exception:
            logger.exception(f'IGDB exact name query failed for "{title}"')
            results = []

        if results:
            cls._log_search_results(title, 'exact-name-query', concept, results)
            best = cls._pick_best_non_dlc(concept, results)
            if best:
                return best
        else:
            logger.info(f'IGDB exact name query for "{title}": 0 results')

        # Strategy 4: Fuzzy search unfiltered (catches PC-first games that came to PS later)
        try:
            results = cls.search_game(title, platform_filter=False)
        except Exception:
            logger.exception(f'IGDB unfiltered search failed for "{title}"')
            return None

        if results:
            cls._log_search_results(title, 'unfiltered', concept, results)
            best = cls._pick_best_non_dlc(concept, results)
            if best:
                igdb_data, confidence, match_method = best
                return (igdb_data, max(confidence - 0.05, settings.IGDB_REVIEW_THRESHOLD), match_method)
        else:
            logger.info(f'IGDB unfiltered search for "{title}": 0 results')

        # Strategy 5: Search alternative names (catches regional title differences, e.g. "Sly Raccoon")
        try:
            results = cls.search_by_alternative_name(title)
        except Exception:
            logger.exception(f'IGDB alt-name search failed for "{title}"')
            return None

        if results:
            cls._log_search_results(title, 'alt-name', concept, results)
            best = cls._pick_best_non_dlc(concept, results)
            if best:
                return best
        else:
            logger.info(f'IGDB alt-name search for "{title}": 0 results')

        # Strategy 6: Truncated title search (series name before colon/dash)
        # "Sly 3: Honour Among Thieves" -> search "Sly 3"
        truncated = cls._extract_series_prefix(title)
        if truncated and truncated != cls._clean_title_for_search(title):
            try:
                results = cls.search_game(truncated)
            except Exception:
                logger.exception(f'IGDB truncated search failed for "{truncated}"')
                return None

            if results:
                cls._log_search_results(title, f'truncated ("{truncated}")', concept, results)
                best = cls._pick_best_non_dlc(concept, results)
                if best:
                    # Cap at pending_review since this is a loose match
                    igdb_data, confidence, match_method = best
                    capped = min(confidence, settings.IGDB_AUTO_ACCEPT_THRESHOLD - 0.01)
                    return (igdb_data, capped, match_method)
            else:
                logger.info(f'IGDB truncated search for "{truncated}": 0 results')

        return None

    @classmethod
    def _log_search_results(cls, title, search_type, concept, results):
        """Log all IGDB search results with their confidence scores."""
        lines = [f'IGDB {search_type} search for "{title}": {len(results)} result(s)']
        for r in results:
            score = cls._calculate_confidence(concept, r, 'fuzzy_name')
            cat = r.get('category', '?')
            lines.append(f'  - "{r.get("name")}" (cat={cat}, score={score:.0%})')
        logger.info('\n'.join(lines))

    @classmethod
    def _pick_best_non_dlc(cls, concept, results):
        """Try to pick the best match, preferring non-DLC results.

        First tries all results with DLC filtered out. If that yields nothing,
        tries again with DLC included (some PSN "games" are legitimately DLC
        with their own trophy lists).
        """
        # Filter out likely DLC entries
        non_dlc = [r for r in results if not _DLC_NAME_RE.search(r.get('name', ''))]

        if non_dlc:
            for method in ('exact_name', 'fuzzy_name'):
                best = cls._pick_best_match(concept, non_dlc, method)
                if best:
                    return best

        # Fall back to all results (DLC included) if no non-DLC match found
        for method in ('exact_name', 'fuzzy_name'):
            best = cls._pick_best_match(concept, results, method)
            if best:
                return best

        return None

    @classmethod
    def _pick_best_match(cls, concept, results, method):
        """Score all results and return the best match with platform overlap.

        Requires at least one PlayStation platform in common between the
        concept's games and the IGDB result. No confidence floor: any
        match with platform overlap is surfaced for review.

        Returns:
            tuple: (igdb_data, confidence, method) or None
        """
        # Get concept's platform IDs for filtering
        concept_plat_ids = set()
        try:
            for game in concept.games.all():
                for plat_str in (game.title_platform or []):
                    igdb_id_for_plat = PLAT_TO_IGDB_ID.get(plat_str)
                    if igdb_id_for_plat:
                        concept_plat_ids.add(igdb_id_for_plat)
        except Exception:
            pass

        scored = []
        for game in results:
            # Require platform overlap (skip VR-only check)
            if concept_plat_ids:
                igdb_plat_ids = set()
                for p in game.get('platforms', []):
                    pid = p if isinstance(p, int) else p.get('id') if isinstance(p, dict) else None
                    if pid:
                        igdb_plat_ids.add(pid)
                if not (concept_plat_ids & igdb_plat_ids):
                    continue

            confidence = cls._calculate_confidence(concept, game, method)
            if confidence > 0:
                scored.append((game, confidence))

        if not scored:
            return None

        scored.sort(key=lambda x: x[1], reverse=True)

        # Ambiguity penalty: if top two are within 0.10, reduce confidence
        best_game, best_confidence = scored[0]
        if len(scored) > 1 and (best_confidence - scored[1][1]) < 0.10:
            best_confidence -= 0.10

        return (best_game, max(best_confidence, 0.01), method)

    @classmethod
    def _calculate_confidence(cls, concept, igdb_game, method):
        """Calculate match confidence between a Concept and an IGDB game.

        Returns:
            float: Confidence score between 0.0 and 1.0
        """
        igdb_name = igdb_game.get('name', '')

        # Normalize titles for comparison
        psn_norm = cls._normalize_title(concept.unified_title)
        igdb_norm = cls._normalize_title(igdb_name)

        # Check against primary name AND all alternative names (regional titles, etc.)
        best_ratio = cls._fuzzy_title_match(concept.unified_title, igdb_name)
        best_name = igdb_name
        for alt in igdb_game.get('alternative_names', []):
            alt_name = alt.get('name', '')
            if not alt_name:
                continue
            alt_ratio = cls._fuzzy_title_match(concept.unified_title, alt_name)
            if alt_ratio > best_ratio:
                best_ratio = alt_ratio
                best_name = alt_name

        # Containment check using the best-matching name
        best_norm = cls._normalize_title(best_name)
        is_contained = (
            len(psn_norm) > 5
            and (psn_norm in best_norm or best_norm in psn_norm)
        )
        # Also check against primary IGDB name for containment
        if not is_contained:
            is_contained = (
                len(psn_norm) > 5
                and (psn_norm in igdb_norm or igdb_norm in psn_norm)
            )

        # Base score by method
        if method == 'external_id':
            base = 0.95
        elif method == 'manual':
            return 1.0
        else:
            ratio = best_ratio

            if method == 'exact_name':
                if ratio >= 0.98 or (is_contained and (igdb_norm == psn_norm or best_norm == psn_norm)):
                    base = 0.85
                else:
                    return 0.0
            else:  # fuzzy_name
                if ratio >= 0.90:
                    base = 0.70
                elif ratio >= 0.80:
                    base = 0.55
                elif is_contained:
                    base = 0.55
                else:
                    return 0.0

            # Boost main games over DLC/bundles/skins when names are similar
            category = igdb_game.get('category', 0)
            if category == 0 and is_contained:
                base += 0.10

        # Modifier: release year proximity
        if concept.release_date and igdb_game.get('first_release_date'):
            try:
                igdb_year = datetime.fromtimestamp(
                    igdb_game['first_release_date'], tz=dt_timezone.utc
                ).year
                concept_year = concept.release_date.year
                if abs(igdb_year - concept_year) <= 1:
                    base += 0.05
            except (ValueError, OSError, AttributeError):
                pass

        # Modifier: publisher name match
        if concept.publisher_name and igdb_game.get('involved_companies'):
            for ic in igdb_game['involved_companies']:
                company = ic.get('company', {})
                if ic.get('publisher') and company.get('name'):
                    pub_ratio = cls._fuzzy_title_match(
                        concept.publisher_name.lower(),
                        company['name'].lower()
                    )
                    if pub_ratio >= 0.80:
                        base += 0.05
                        break

        # Platform overlap is enforced by _pick_best_match (hard requirement).
        # No additional modifier needed here.

        # Modifier: non-main game penalty (DLC/bundles score lower)
        category = igdb_game.get('category', 0)
        if category != 0:
            base -= 0.15

        return max(0.0, min(1.0, base))

    @classmethod
    def _fuzzy_title_match(cls, title1, title2):
        """Calculate string similarity between two titles.

        Normalizes both titles before comparison to handle case differences,
        platform suffixes, and unicode characters.
        """
        if not title1 or not title2:
            return 0.0
        t1 = cls._normalize_title(title1)
        t2 = cls._normalize_title(title2)
        if t1 == t2:
            return 1.0
        return SequenceMatcher(None, t1, t2).ratio()

    # Regex to strip platform suffixes from PSN titles
    _PLATFORM_SUFFIX_RE = re.compile(
        r'\s*[\-–—]?\s*\(?\s*'
        r'(?:PS[345]|PS4\s*&\s*PS5|PlayStation\s*[345V]|PSVR2?|PS\s*Vita)'
        r'\s*\)?\s*$',
        re.IGNORECASE,
    )

    # Regex to strip edition/version suffixes that IGDB won't have
    _EDITION_SUFFIX_RE = re.compile(
        r'\s*[\-–—]?\s*\(?\s*(?:Digital\s+Edition|Deluxe\s+Edition|Gold\s+Edition|'
        r'Game\s+of\s+the\s+Year\s+Edition|GOTY\s+Edition|'
        r'Platinum\s+Edition|Complete\s+Edition|Standard\s+Edition|'
        r'Remastered|HD\s+Remaster|Director.s\s+Cut)\s*\)?\s*$',
        re.IGNORECASE,
    )

    # Regex to strip trailing year in parentheses: "Alone in the Dark 2 (1996)"
    _YEAR_SUFFIX_RE = re.compile(r'\s*\(\d{4}\)\s*$')

    # Brand prefixes that IGDB typically doesn't include
    _BRAND_PREFIX_RE = re.compile(
        r'^(?:Disney[•·]?Pixar\s+|Disney\s+|Marvel.s\s+|LEGO\s+)',
        re.IGNORECASE,
    )

    @classmethod
    def _extract_series_prefix(cls, title):
        """Extract the series name before the first colon or dash separator.

        "Sly 3: Honour Among Thieves" -> "sly 3"
        "Assassin's Creed - Discovery Tour" -> "assassin's creed"
        "The Last of Us Part II" -> None (no separator)

        Returns cleaned prefix suitable for IGDB search, or None if no separator found.
        """
        # Split on separators BEFORE stripping special chars
        for sep in (':', ' - ', ' – ', ' — '):
            if sep in title:
                prefix = title.split(sep)[0].strip()
                if len(prefix) >= 3:
                    return cls._clean_title_for_search(prefix)
        return None

    @classmethod
    def _normalize_title(cls, text):
        """Normalize a game title for comparison (not for search queries).

        Lowercases, strips platform/edition/year suffixes, normalizes unicode.
        Used for confidence scoring, not for IGDB API queries.
        """
        # Normalize unicode (smart quotes, bullets, accents)
        text = unicodedata.normalize('NFKD', text)
        # Remove non-ASCII combining marks but keep base letters
        text = ''.join(c for c in text if not unicodedata.combining(c))
        text = text.lower().strip()
        # Strip suffixes
        text = cls._PLATFORM_SUFFIX_RE.sub('', text)
        text = cls._EDITION_SUFFIX_RE.sub('', text)
        text = cls._YEAR_SUFFIX_RE.sub('', text)
        # Strip brand prefixes
        text = cls._BRAND_PREFIX_RE.sub('', text)
        # Collapse whitespace
        text = re.sub(r'\s+', ' ', text).strip()
        return text

    @classmethod
    def _clean_title_for_search(cls, text):
        """Clean a title for use in IGDB Apicalypse search queries.

        Strips platform suffixes, Apicalypse-breaking characters, and
        unicode noise. IGDB search is fuzzy so this improves match rates.
        """
        # Normalize unicode
        text = unicodedata.normalize('NFKD', text)
        text = ''.join(c for c in text if not unicodedata.combining(c))
        # Strip suffixes
        text = cls._PLATFORM_SUFFIX_RE.sub('', text)
        text = cls._EDITION_SUFFIX_RE.sub('', text)
        text = cls._YEAR_SUFFIX_RE.sub('', text)
        # Strip brand prefixes
        text = cls._BRAND_PREFIX_RE.sub('', text)
        # Remove characters that break Apicalypse parsing
        for ch in (':', ';', '"', '\\', '(', ')', '{', '}', '[', ']', '•', '™', '®', '©'):
            text = text.replace(ch, '')
        # Lowercase: IGDB search handles all-caps poorly
        text = text.lower()
        # Collapse whitespace
        text = re.sub(r'\s+', ' ', text).strip()
        return text

    # -----------------------------------------------------------------------
    # Processing
    # -----------------------------------------------------------------------

    @classmethod
    def process_match(cls, concept, igdb_data, confidence, method):
        """Create or update an IGDBMatch record and apply enrichment if auto-accepted.

        Returns:
            IGDBMatch: The created/updated match record
        """
        if confidence >= settings.IGDB_AUTO_ACCEPT_THRESHOLD:
            status = 'auto_accepted'
        else:
            status = 'pending_review'

        igdb_id = igdb_data['id']

        # Flag if another Concept already has this IGDB game and they're not in the same family
        existing = (
            IGDBMatch.objects
            .filter(igdb_id=igdb_id)
            .exclude(concept=concept)
            .select_related('concept')
            .first()
        )
        if existing:
            other_concept = existing.concept
            if not concept.family_id or concept.family_id != other_concept.family_id:
                logger.info(
                    f'IGDB game {igdb_id} also matched to concept {other_concept.concept_id} '
                    f'("{other_concept.unified_title}"). Creating family proposal.'
                )
                cls._create_family_proposal(concept, other_concept, igdb_id, igdb_data)

        # Fetch time-to-beat from separate endpoint
        ttb = cls._fetch_time_to_beat(igdb_id)
        igdb_data['_time_to_beat'] = ttb

        parsed = cls._parse_game_data(igdb_data)

        igdb_match, created = IGDBMatch.objects.update_or_create(
            concept=concept,
            defaults={
                'igdb_id': igdb_data['id'],
                'igdb_name': igdb_data.get('name', ''),
                'igdb_slug': igdb_data.get('slug', ''),
                'match_confidence': confidence,
                'match_method': method,
                'status': status,
                'raw_response': igdb_data,
                'game_category': parsed['game_category'],
                'igdb_summary': parsed['summary'],
                'igdb_storyline': parsed['storyline'],
                'time_to_beat_hastily': parsed['time_to_beat_hastily'],
                'time_to_beat_normally': parsed['time_to_beat_normally'],
                'time_to_beat_completely': parsed['time_to_beat_completely'],
                'igdb_first_release_date': parsed['first_release_date'],
                'game_engine_name': parsed['game_engine_name'],
                'igdb_cover_image_id': parsed['cover_image_id'],
                'franchise_names': parsed['franchise_names'],
                'similar_game_igdb_ids': parsed['similar_game_igdb_ids'],
                'external_urls': parsed['external_urls'],
                'last_synced_at': datetime.now(dt_timezone.utc),
            },
        )

        if status in ('auto_accepted', 'accepted'):
            cls._apply_enrichment(igdb_match, igdb_data)
            # Re-evaluate shovelware now that the concept has a trusted
            # primary developer. Covers the case where rule 1 flagged the
            # concept before this match existed (e.g. earn rate >= 80% at
            # platinum sync, match arriving later in the pipeline).
            from trophies.services.shovelware_detection_service import ShovelwareDetectionService
            ShovelwareDetectionService.on_igdb_match_trusted(concept)

        return igdb_match

    # IGDB platform ID -> PlatPursuit title_platform string
    IGDB_VR_PLATFORMS = {
        165: 'PSVR',
        390: 'PSVR2',
    }

    @classmethod
    def _apply_enrichment(cls, igdb_match, igdb_data=None):
        """Apply IGDB enrichment data to the Concept and create Company/ConceptCompany records.

        Called on auto_accepted matches and when admin approves pending matches.
        """
        if igdb_data is None:
            igdb_data = igdb_match.raw_response

        concept = igdb_match.concept

        # Create/update Company records and ConceptCompany entries
        cls._create_concept_companies(concept, igdb_data.get('involved_companies', []))

        # Update Concept fields (JSON fields for backward compatibility)
        cls._update_concept_fields(concept, igdb_data)

        # Create normalized Genre/Theme/Engine records
        cls._create_normalized_tags(concept, igdb_data)

        # Create normalized Franchise records
        cls._create_concept_franchises(concept, igdb_data)

        # Add VR platforms to Games that are missing them
        cls._apply_vr_platforms(concept, igdb_data)

    @classmethod
    def _create_concept_companies(cls, concept, involved_companies):
        """Create Company and ConceptCompany records from IGDB involved_companies data."""
        if not involved_companies:
            return

        for ic in involved_companies:
            company_data = ic.get('company')
            if not company_data or not isinstance(company_data, dict) or 'name' not in company_data:
                continue

            company = cls._get_or_create_company(company_data)
            if not company:
                continue

            try:
                ConceptCompany.objects.update_or_create(
                    concept=concept,
                    company=company,
                    defaults={
                        'is_developer': ic.get('developer', False),
                        'is_publisher': ic.get('publisher', False),
                        'is_porting': ic.get('porting', False),
                        'is_supporting': ic.get('supporting', False),
                    },
                )
            except IntegrityError:
                logger.warning(f'Duplicate ConceptCompany for concept={concept.pk}, company={company.pk}')

    @classmethod
    def _get_or_create_company(cls, company_data):
        """Get or create a Company record from IGDB company data.

        Handles the parent company relationship and merger chain.

        Returns:
            Company or None
        """
        igdb_id = company_data.get('id')
        if not igdb_id:
            return None

        # Handle parent company first (if expanded in response)
        parent = None
        parent_data = company_data.get('parent')
        if isinstance(parent_data, dict) and parent_data.get('id'):
            parent, _ = Company.objects.get_or_create(
                igdb_id=parent_data['id'],
                defaults={
                    'name': parent_data.get('name', f'Company {parent_data["id"]}'),
                    'slug': parent_data.get('slug', f'company-{parent_data["id"]}'),
                },
            )

        # Parse start_date from IGDB timestamp
        start_date = None
        raw_start = company_data.get('start_date')
        if raw_start is not None:
            try:
                start_date = datetime.fromtimestamp(raw_start, tz=dt_timezone.utc).date()
            except (ValueError, OSError):
                pass

        # Parse change_date
        change_date = None
        raw_change = company_data.get('change_date')
        if raw_change is not None:
            try:
                change_date = datetime.fromtimestamp(raw_change, tz=dt_timezone.utc).date()
            except (ValueError, OSError):
                pass

        # Logo image ID
        logo_image_id = ''
        logo_data = company_data.get('logo')
        if isinstance(logo_data, dict):
            logo_image_id = logo_data.get('image_id', '')

        defaults = {
            'name': company_data.get('name', f'Company {igdb_id}'),
            'slug': company_data.get('slug', f'company-{igdb_id}'),
            'description': company_data.get('description', ''),
            'country': company_data.get('country'),
            'logo_image_id': logo_image_id,
            'company_size': company_data.get('company_size'),
            'start_date': start_date,
            'change_date': change_date,
        }

        if parent:
            defaults['parent'] = parent

        # Handle changed_company_id (merger/rename pointer)
        changed_id = company_data.get('changed_company_id')
        if changed_id:
            changed_company = Company.objects.filter(igdb_id=changed_id).first()
            if changed_company:
                defaults['changed_company'] = changed_company

        company, created = Company.objects.update_or_create(
            igdb_id=igdb_id,
            defaults=defaults,
        )
        return company

    @classmethod
    def _update_concept_fields(cls, concept, igdb_data):
        """Update Concept's IGDB genre and theme fields from parsed data."""
        genres = [g.get('name', '') for g in igdb_data.get('genres', []) if g.get('name')]
        themes = [t.get('name', '') for t in igdb_data.get('themes', []) if t.get('name')]

        update_fields = []
        if genres and genres != concept.igdb_genres:
            concept.igdb_genres = genres
            update_fields.append('igdb_genres')
        if themes and themes != concept.igdb_themes:
            concept.igdb_themes = themes
            update_fields.append('igdb_themes')

        if update_fields:
            concept.save(update_fields=update_fields)

    @classmethod
    def _create_normalized_tags(cls, concept, igdb_data):
        """Create normalized Genre, Theme, and GameEngine records from IGDB data.

        Parses the genres, themes, and game_engines arrays from igdb_data
        (which contain id, name, slug objects) and creates the corresponding
        model records plus through-model links to the Concept.
        """
        from trophies.models import (
            Genre, Theme, GameEngine, Company,
            ConceptGenre, ConceptTheme, ConceptEngine, EngineCompany,
        )

        # IGDB slugs occasionally contain URL-unsafe characters (e.g. the
        # engine "CTG (Core Technology Group)" yields slug
        # "ctg-(core-technology-group)" which breaks Django's slug URL
        # converter). We always run the IGDB slug through slugify() to
        # normalize — idempotent for already-clean slugs.
        for genre_data in igdb_data.get('genres', []):
            igdb_id = genre_data.get('id')
            name = genre_data.get('name', '')
            slug = slugify(genre_data.get('slug') or name)
            if not igdb_id or not name or not slug:
                continue
            try:
                genre, _ = Genre.objects.get_or_create(
                    igdb_id=igdb_id,
                    defaults={'name': name, 'slug': slug},
                )
            except IntegrityError:
                genre = Genre.objects.filter(slug=slug).first()
                if not genre:
                    continue
            ConceptGenre.objects.get_or_create(concept=concept, genre=genre)

        for theme_data in igdb_data.get('themes', []):
            igdb_id = theme_data.get('id')
            name = theme_data.get('name', '')
            slug = slugify(theme_data.get('slug') or name)
            if not igdb_id or not name or not slug:
                continue
            try:
                theme, _ = Theme.objects.get_or_create(
                    igdb_id=igdb_id,
                    defaults={'name': name, 'slug': slug},
                )
            except IntegrityError:
                theme = Theme.objects.filter(slug=slug).first()
                if not theme:
                    continue
            ConceptTheme.objects.get_or_create(concept=concept, theme=theme)

        # Engines: IGDB conflates runtime engines with dev tools (Sagebrush
        # lists Unity AND Photoshop AND Blender). Only take the first entry —
        # IGDB's ordering puts the real engine first in practice. Admin can
        # override manually for the rare edge cases.
        engines_list = igdb_data.get('game_engines', [])
        engine_data = engines_list[0] if engines_list else None
        if engine_data:
            igdb_id = engine_data.get('id')
            name = engine_data.get('name', '')
            slug = slugify(engine_data.get('slug') or name)
            description = engine_data.get('description') or ''
            logo_data = engine_data.get('logo') or {}
            logo_image_id = logo_data.get('image_id') or ''
            company_ids = engine_data.get('companies') or []

            if igdb_id and name and slug:
                try:
                    engine, created = GameEngine.objects.get_or_create(
                        igdb_id=igdb_id,
                        defaults={
                            'name': name,
                            'slug': slug,
                            'description': description,
                            'logo_image_id': logo_image_id,
                        },
                    )
                except IntegrityError:
                    engine = GameEngine.objects.filter(slug=slug).first()

                if engine:
                    # Backfill description/logo only when empty so we never
                    # clobber admin-curated values.
                    updates = {}
                    if description and not engine.description:
                        updates['description'] = description
                    if logo_image_id and not engine.logo_image_id:
                        updates['logo_image_id'] = logo_image_id
                    if updates:
                        for k, v in updates.items():
                            setattr(engine, k, v)
                        engine.save(update_fields=list(updates.keys()))

                    ConceptEngine.objects.get_or_create(concept=concept, engine=engine)

                    # Engine maker companies (Epic -> Unreal, Unity Tech -> Unity).
                    # IGDB gives us company IDs; we only link companies that
                    # already exist in our DB (enriched via involved_companies
                    # on some game — the usual case for major engines).
                    if company_ids:
                        for company in Company.objects.filter(igdb_id__in=company_ids):
                            EngineCompany.objects.get_or_create(
                                engine=engine, company=company,
                            )

    @classmethod
    def _create_concept_franchises(cls, concept, igdb_data):
        """Create Franchise and ConceptFranchise records from IGDB franchise/collection data.

        IGDB exposes ``franchise`` (singular = primary identity) AND ``franchises``
        (plural = secondary tie-ins). The link to the singular franchise is
        flagged ``is_main=True``; everything else is ``is_main=False``. This
        powers the franchise browse page (only shows franchises that ARE
        someone's main) and the detail page Games / Also Featured split.
        Collections never get is_main set (different IGDB taxonomy entirely).
        """
        from trophies.models import Franchise, ConceptFranchise

        # Determine which franchise should be flagged as main.
        #
        # Precedence (intentional — keep in sync with backfill_franchise_main_flag):
        #   1. First entry of the plural `franchises` array.
        #      The plural array is IGDB's modern field (singular `franchise`
        #      is being phased out per their changelog), and is what IGDB's
        #      own UI surfaces. Within the array, IGDB orders by curator
        #      confidence, so the first entry is the umbrella IP.
        #   2. Fall back to the singular `franchise` field only when the
        #      plural array is empty. Covers older entries that were
        #      curated before the plural field existed.
        #   3. Otherwise no main franchise is set for this concept.
        main_igdb_id = None
        plural = [
            f for f in igdb_data.get('franchises', [])
            if f.get('id') and f.get('name')
        ]
        if plural:
            main_igdb_id = plural[0]['id']
        else:
            singular = igdb_data.get('franchise') or {}
            if singular.get('id'):
                main_igdb_id = singular['id']

        # Build the source list. The singular `franchise` is included as its
        # own source (when present) so a Franchise record gets created for it
        # even when it isn't in the plural array.
        #
        # Dedup key is (igdb_id, source_type) — NOT igdb_id alone — because
        # IGDB franchises and collections live in separate ID namespaces.
        # Franchise id 222 is "NCAA"; collection id 222 is "Army of Two".
        # A bare-id dedup conflates them and corrupts links across the DB.
        singular_obj = igdb_data.get('franchise') or {}
        seen_keys = set()
        sources = []
        if singular_obj:
            sources.append(([singular_obj], 'franchise'))
        sources.append((igdb_data.get('franchises', []), 'franchise'))
        sources.append((igdb_data.get('collections', []), 'collection'))

        for items, source_type in sources:
            for item in items:
                igdb_id = item.get('id')
                name = item.get('name', '')
                key = (igdb_id, source_type)
                if not igdb_id or not name or key in seen_keys:
                    continue
                seen_keys.add(key)
                slug = slugify(name) or f'{source_type}-{igdb_id}'
                try:
                    # The unique constraint on (igdb_id, source_type) makes
                    # this lookup correct — same numeric ID in the franchise
                    # namespace vs. collection namespace gets two distinct rows.
                    franchise, _ = Franchise.objects.get_or_create(
                        igdb_id=igdb_id,
                        source_type=source_type,
                        defaults={'name': name, 'slug': slug},
                    )
                except IntegrityError:
                    # Most likely a slug collision (different IGDB entity with
                    # the same slugified name, e.g. "South Park" exists as
                    # both a franchise and a collection). Could also be a race
                    # condition on the (igdb_id, source_type) constraint.
                    franchise = Franchise.objects.filter(
                        igdb_id=igdb_id, source_type=source_type,
                    ).first()
                    if not franchise:
                        # The (igdb_id, source_type) row doesn't exist yet —
                        # the IntegrityError was a slug collision. Retry with
                        # a disambiguated slug. Do NOT fall through to a bare
                        # slug lookup: that returns the WRONG Franchise row
                        # (e.g. the franchise-type "South Park" when we're
                        # trying to create the collection-type "South Park"),
                        # which then overwrites the correctly-set is_main flag
                        # via the stale-fix logic below.
                        dedup_slug = f'{slug}-{igdb_id}'
                        try:
                            franchise, _ = Franchise.objects.get_or_create(
                                igdb_id=igdb_id,
                                source_type=source_type,
                                defaults={'name': name, 'slug': dedup_slug},
                            )
                        except IntegrityError:
                            franchise = Franchise.objects.filter(
                                igdb_id=igdb_id, source_type=source_type,
                            ).first()
                            if not franchise:
                                continue
                # Only the franchise matching IGDB's primary franchise (per the
                # precedence above) is main. Collections are never main.
                desired_is_main = (
                    source_type == 'franchise' and igdb_id == main_igdb_id
                )
                cf, created = ConceptFranchise.objects.get_or_create(
                    concept=concept,
                    franchise=franchise,
                    defaults={'is_main': desired_is_main},
                )
                # If the row already existed with a stale is_main, fix it.
                if not created and cf.is_main != desired_is_main:
                    cf.is_main = desired_is_main
                    cf.save(update_fields=['is_main'])

    @classmethod
    def _apply_vr_platforms(cls, concept, igdb_data):
        """Add PSVR/PSVR2 to Game.title_platform for games IGDB identifies as VR.

        Sony does not provide VR platform info, so we fill the gap from IGDB.
        Only adds platforms, never removes.
        """
        igdb_platforms = igdb_data.get('platforms', [])
        if not igdb_platforms:
            return

        # Determine which VR platform strings to add
        vr_platforms_to_add = []
        for pid in igdb_platforms:
            # platforms can be ints (IDs) or dicts with 'id' key
            platform_id = pid if isinstance(pid, int) else pid.get('id') if isinstance(pid, dict) else None
            if platform_id in cls.IGDB_VR_PLATFORMS:
                vr_platforms_to_add.append(cls.IGDB_VR_PLATFORMS[platform_id])

        if not vr_platforms_to_add:
            return

        from trophies.models import Game
        for game in concept.games.all():
            added = False
            for vr_platform in vr_platforms_to_add:
                if vr_platform not in (game.title_platform or []):
                    if game.title_platform is None:
                        game.title_platform = []
                    game.title_platform.append(vr_platform)
                    added = True
            if added:
                game.save(update_fields=['title_platform'])
                logger.info(
                    f'Added VR platform(s) {vr_platforms_to_add} to game '
                    f'{game.np_communication_id} "{game.title_name}"'
                )

    @classmethod
    def _check_family_proposals(cls, igdb_match):
        """Check if other concepts share this IGDB game and create family proposals if needed."""
        existing_matches = (
            IGDBMatch.objects
            .filter(igdb_id=igdb_match.igdb_id)
            .exclude(concept=igdb_match.concept)
            .select_related('concept')
        )
        for other_match in existing_matches:
            if not igdb_match.concept.family_id or igdb_match.concept.family_id != other_match.concept.family_id:
                cls._create_family_proposal(
                    igdb_match.concept, other_match.concept,
                    igdb_match.igdb_id, igdb_match.raw_response or {},
                )

    @classmethod
    def _create_family_proposal(cls, concept_a, concept_b, igdb_id, igdb_data):
        """Create a GameFamilyProposal when two Concepts share an IGDB game but aren't in the same family."""
        from trophies.models import GameFamilyProposal

        # Check if a proposal already exists for these two concepts
        existing = GameFamilyProposal.objects.filter(
            status='pending',
            concepts=concept_a,
        ).filter(concepts=concept_b).exists()
        if existing:
            return

        igdb_name = igdb_data.get('name', f'IGDB #{igdb_id}')
        proposal = GameFamilyProposal.objects.create(
            proposed_name=igdb_name,
            confidence=0.95,
            match_reason=f'Both concepts matched to the same IGDB game: "{igdb_name}" (ID {igdb_id})',
            match_signals={
                'source': 'igdb_duplicate_match',
                'igdb_id': igdb_id,
                'igdb_name': igdb_name,
                'concept_a': concept_a.concept_id,
                'concept_b': concept_b.concept_id,
            },
        )
        proposal.concepts.add(concept_a, concept_b)
        logger.info(
            f'Created GameFamilyProposal #{proposal.pk} for "{concept_a.unified_title}" '
            f'and "{concept_b.unified_title}" (IGDB #{igdb_id})'
        )

    @classmethod
    def _parse_game_data(cls, igdb_data):
        """Extract Tier 1 structured data from a raw IGDB game response.

        Returns:
            dict: Parsed fields ready for IGDBMatch creation
        """
        # Time to beat (fetched separately, injected as _time_to_beat)
        ttb_data = igdb_data.get('_time_to_beat', {})

        # First release date
        first_release = None
        raw_date = igdb_data.get('first_release_date')
        if raw_date:
            try:
                first_release = datetime.fromtimestamp(raw_date, tz=dt_timezone.utc)
            except (ValueError, OSError):
                pass

        # Game engine
        engines = igdb_data.get('game_engines', [])
        engine_name = engines[0].get('name', '') if engines else ''

        # Cover image
        cover = igdb_data.get('cover', {}) or {}
        cover_image_id = cover.get('image_id', '')

        # Franchise names (from both franchises and collections)
        franchise_names = []
        franchise_data = []
        for f in igdb_data.get('franchises', []):
            fid = f.get('id')
            name = f.get('name', '')
            if name:
                franchise_names.append(name)
            if fid and name:
                franchise_data.append({'igdb_id': fid, 'name': name, 'source_type': 'franchise'})
        for c in igdb_data.get('collections', []):
            cid = c.get('id')
            name = c.get('name', '')
            if name and name not in franchise_names:
                franchise_names.append(name)
            if cid and name:
                franchise_data.append({'igdb_id': cid, 'name': name, 'source_type': 'collection'})

        # Similar game IDs
        similar_ids = []
        for sg in igdb_data.get('similar_games', []):
            if isinstance(sg, int):
                similar_ids.append(sg)
            elif isinstance(sg, dict) and sg.get('id'):
                similar_ids.append(sg['id'])

        # External URLs from websites
        external_urls = {}
        for w in igdb_data.get('websites', []):
            cat = w.get('category') or w.get('type')
            url = w.get('url', '')
            if cat and url:
                key = WEBSITE_CATEGORIES.get(cat)
                if key:
                    external_urls[key] = url

        return {
            'game_category': igdb_data.get('category'),
            'summary': igdb_data.get('summary', ''),
            'storyline': igdb_data.get('storyline', ''),
            'time_to_beat_hastily': ttb_data.get('hastily'),
            'time_to_beat_normally': ttb_data.get('normally'),
            'time_to_beat_completely': ttb_data.get('completely'),
            'first_release_date': first_release,
            'game_engine_name': engine_name,
            'cover_image_id': cover_image_id,
            'franchise_names': franchise_names,
            'franchise_data': franchise_data,
            'similar_game_igdb_ids': similar_ids,
            'external_urls': external_urls,
        }

    # -----------------------------------------------------------------------
    # Batch operations
    # -----------------------------------------------------------------------

    @classmethod
    def enrich_concept(cls, concept):
        """Match and enrich a single Concept from IGDB.

        Returns:
            IGDBMatch or None: The match record if successful
        """
        result = cls.match_concept(concept)
        if not result:
            return None

        igdb_data, confidence, method = result
        return cls.process_match(concept, igdb_data, confidence, method)

    @classmethod
    def record_no_match(cls, concept):
        """Persist a 'no_match' IGDBMatch row for a concept where matching found nothing.

        Lets the enrich command skip these on subsequent runs while still allowing
        explicit retries via --retry-no-match. Refuses to overwrite an existing
        accepted/pending/rejected match (those carry real data and should not be
        clobbered by a re-check that happens to fail).

        Returns:
            IGDBMatch: The created or updated no_match record, or None if the
            concept already has a non-no_match record (no overwrite performed).
        """
        existing = IGDBMatch.objects.filter(concept=concept).first()
        if existing and existing.status != 'no_match':
            logger.debug(
                f'record_no_match skipped for concept {concept.concept_id}: '
                f'existing match has status={existing.status}'
            )
            return None

        igdb_match, _ = IGDBMatch.objects.update_or_create(
            concept=concept,
            defaults={
                'igdb_id': None,
                'igdb_name': '',
                'igdb_slug': '',
                'match_confidence': None,
                'match_method': '',
                'status': 'no_match',
                'raw_response': {},
                'game_category': None,
                'igdb_summary': '',
                'igdb_storyline': '',
                'time_to_beat_hastily': None,
                'time_to_beat_normally': None,
                'time_to_beat_completely': None,
                'igdb_first_release_date': None,
                'game_engine_name': '',
                'igdb_cover_image_id': '',
                'franchise_names': [],
                'similar_game_igdb_ids': [],
                'external_urls': {},
                'last_synced_at': datetime.now(dt_timezone.utc),
            },
        )
        return igdb_match

    @classmethod
    def refresh_match(cls, igdb_match):
        """Re-fetch IGDB data for an existing match and update all parsed fields.

        Skips the search/matching step entirely since we already have the igdb_id.
        Updates raw_response, all Tier 1 fields, Company records, and Concept fields.

        Returns:
            IGDBMatch: The updated match record, or None if fetch failed
        """
        igdb_data = cls.get_game_details(igdb_match.igdb_id)
        if not igdb_data:
            logger.warning(f'IGDB refresh: game {igdb_match.igdb_id} no longer found')
            return None

        # Fetch time-to-beat separately
        ttb = cls._fetch_time_to_beat(igdb_match.igdb_id)
        igdb_data['_time_to_beat'] = ttb

        parsed = cls._parse_game_data(igdb_data)

        igdb_match.raw_response = igdb_data
        igdb_match.igdb_name = igdb_data.get('name', igdb_match.igdb_name)
        igdb_match.igdb_slug = igdb_data.get('slug', igdb_match.igdb_slug)
        igdb_match.game_category = parsed['game_category']
        igdb_match.igdb_summary = parsed['summary']
        igdb_match.igdb_storyline = parsed['storyline']
        igdb_match.time_to_beat_hastily = parsed['time_to_beat_hastily']
        igdb_match.time_to_beat_normally = parsed['time_to_beat_normally']
        igdb_match.time_to_beat_completely = parsed['time_to_beat_completely']
        igdb_match.igdb_first_release_date = parsed['first_release_date']
        igdb_match.game_engine_name = parsed['game_engine_name']
        igdb_match.igdb_cover_image_id = parsed['cover_image_id']
        igdb_match.franchise_names = parsed['franchise_names']
        igdb_match.similar_game_igdb_ids = parsed['similar_game_igdb_ids']
        igdb_match.external_urls = parsed['external_urls']
        igdb_match.last_synced_at = datetime.now(dt_timezone.utc)
        igdb_match.save()

        # Re-apply enrichment (updates companies + concept fields)
        cls._apply_enrichment(igdb_match, igdb_data)

        return igdb_match

    @classmethod
    def approve_match(cls, igdb_match):
        """Approve a pending IGDBMatch and apply enrichment.

        Called from admin actions when reviewing pending matches.
        """
        if igdb_match.status == 'pending_review':
            igdb_match.status = 'accepted'
            igdb_match.save(update_fields=['status'])
            cls._apply_enrichment(igdb_match)
            from trophies.services.shovelware_detection_service import ShovelwareDetectionService
            ShovelwareDetectionService.on_igdb_match_trusted(igdb_match.concept)

    @classmethod
    def reject_match(cls, igdb_match):
        """Reject an IGDBMatch by deleting it.

        The concept becomes unmatched and eligible for the next default
        enrich_from_igdb run.
        """
        igdb_match.delete()

    @classmethod
    def rematch_concept(cls, concept):
        """Delete existing match and re-run matching for a concept.

        ConceptCompany records are preserved; _apply_enrichment will
        update_or_create them from the new IGDB response.

        Returns:
            IGDBMatch or None
        """
        IGDBMatch.objects.filter(concept=concept).delete()
        return cls.enrich_concept(concept)

