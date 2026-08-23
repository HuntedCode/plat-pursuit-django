"""Context for the anonymous landing (`/`, logged-out).

THE RULE, inherited from the premium-preview incidents: nothing here may run per-user providers
or uncached aggregates on the request path. Every section's data is one of:
  1. the cron-cached site heartbeat (the view already fetches it),
  2. a small cached community read with a bounded builder (the badge showcase),
  3. a cron-rendered artifact (the showcase Profile Card, rendered hourly off the profile named
     by settings.LANDING_SHOWCASE_PSN), or
  4. a literal fixture (the showcase card's fallback -- hand-written constants, zero ORM).

The anon `/` was ~free before this service (0 SQL, a few cache reads) and must stay that way:
a cold cache costs one bounded 6-row badge query, everything else degrades to fixtures or hides.
"""
import logging
from functools import lru_cache

from django.core.cache import cache
from django.template.loader import render_to_string

logger = logging.getLogger(__name__)

BADGE_SHOWCASE_CACHE_KEY = 'landing_badge_showcase_v1'
BADGE_SHOWCASE_TTL = 3600
SHOWCASE_CARD_CACHE_KEY = 'landing_showcase_card_v1'
SHOWCASE_CARD_TTL = 6 * 3600   # refreshed hourly by the cron; survives a few missed runs

#: How many medallions the landing's badge section shows. Six reads as a shelf; each is another
#: image or two the visitor downloads.
BADGE_SHOWCASE_CAP = 6


def _build_badge_showcase():
    """The most-held live badges, one edition per series, as medallion frames.

    Bounded: one ordered query sliced generously, deduped to BADGE_SHOWCASE_CAP series in Python.
    Frames match `home_service._recent_medallions`' minimal shape, consumed by the shared
    components/badge_medallion.html -- the landing shows the REAL objects, not screenshots.
    """
    from trophies.models import GroupBadge
    from trophies.services.badge_detail_service import group_medallion_layers

    rows = (
        GroupBadge.objects.filter(is_live=True)
        .select_related('platform_group', 'series', 'series__submitted_by')
        .order_by('-earned_count', 'id')[:BADGE_SHOWCASE_CAP * 3]
    )
    frames, seen = [], set()
    for gb in rows:
        slug = gb.series.series_slug
        if slug in seen:
            continue
        seen.add(slug)
        tier, layers, is_avatar = group_medallion_layers(gb)
        frames.append({
            'tier': tier,
            'state': 'earned',          # the landing shows them gleaming, as earned objects
            'is_holographic': False,
            'is_avatar': is_avatar,
            'art_layers': layers,
            'series_name': gb.series.name,
            'badge_name': gb.platform_group.name,
        })
        if len(frames) >= BADGE_SHOWCASE_CAP:
            break
    return frames


def _badge_showcase():
    try:
        return cache.get_or_set(BADGE_SHOWCASE_CACHE_KEY, _build_badge_showcase, BADGE_SHOWCASE_TTL)
    except Exception:
        logger.exception("Landing badge showcase build failed")
        # Negative-cache briefly, or a persistent failure re-runs the query on EVERY anon request
        # and quietly voids the module's zero-SQL rule.
        try:
            cache.set(BADGE_SHOWCASE_CACHE_KEY, [], 300)
        except Exception:
            pass
        return []


# ── The showcase Profile Card ─────────────────────────────────────────────────────────────────────

#: The literal fixture behind the cron-rendered card: a plausible mid-career hunter, every value
#: hand-written. Rank/discipline/tier names are the product's REAL vocabulary (Warden, the five
#: disciplines, the four trophy tiers); games and figures are invented. The ring's dash/offset are
#: precomputed against career_service's circumference (263.89) exactly as the live service does.
def _fixture_card_context():
    from core.services.completion_card_service import DISCIPLINE_COLOURS, TIER_DISPLAY
    from users.services.marks import mark_style

    ring_data = [
        ('combat', 'Combat', 96, 74.07, 0),
        ('exploration', 'Exploration', 78, 60.19, -74.07),
        ('mind', 'Mind', 66, 50.93, -134.26),
        ('heart', 'Heart', 55, 42.44, -185.19),
        ('finesse', 'Finesse', 47, 36.26, -227.63),
    ]
    tier_totals = {'platinum': 87, 'gold': 512, 'silver': 1466, 'bronze': 4417}
    return {
        'username': 'PlatinumPursuer',
        'mark': mark_style('backer'),
        'avatar_image': '',
        'display_title': 'Completionist',
        'total_games': 214,
        'total_plats': 87,
        'total_completes': 102,
        'total_earned': 6482,
        'total_unearned': 1120,
        'trophy_level': 512,
        'avg_progress': 71,
        'tier_counts': [
            {'tier': tier, 'count': tier_totals[tier], 'colour': colour}
            for tier, colour in TIER_DISPLAY
        ],
        'rarest_plat': {'name': 'Sekiro: Shadows Die Twice', 'cover_cached': '', 'cover_url': '',
                        'earn_rate': 1.2, 'earned_at': None},
        'latest_plat': {'name': 'Astro Bot', 'cover_cached': '', 'cover_url': '',
                        'earn_rate': None, 'earned_at': None},
        'pursuer_level': 342,
        'rank_label': 'Warden',
        'ring': [
            {'slug': slug, 'label': label, 'total': total, 'dash': dash, 'offset': offset,
             'colour': DISCIPLINE_COLOURS[slug]}
            for slug, label, total, dash, offset in ring_data
        ],
        'top_job': None,               # a real job name belongs to the catalog; the fixture claims none
        'jobs_played': 19,
        'jobs_total': 25,
        'tiers_earned': 41,
        'career_xp_compact': '1.2M',
        'badges': {
            'earned': 14, 'catalog_total': 126, 'pct': 11, 'holo': 2, 'chasing': 5,
            'closest': None, 'more': 11,
            # The tier backdrop discs alone read as metal coins -- honest sample art with no
            # invented badge subjects.
            'medallions': [
                {'layers_cached': ['/static/images/badges/backdrops/4_backdrop.png'], 'is_avatar': False},
                {'layers_cached': ['/static/images/badges/backdrops/3_backdrop.png'], 'is_avatar': False},
                {'layers_cached': ['/static/images/badges/backdrops/2_backdrop.png'], 'is_avatar': False},
            ],
        },
    }


@lru_cache(maxsize=1)
def _fixture_card_html():
    return render_to_string('shareables/profile_card.html', _fixture_card_context())


def render_showcase_card():
    """Cron-side: render the REAL Profile Card of the profile named by LANDING_SHOWCASE_PSN and
    cache its HTML for the landing. Returns True when a card was cached.

    The landing is a real page on the site origin, so raw URLs resolve themselves -- same
    passthrough the Card tab uses, no ShareImageCache. Never called on the request path.
    """
    from django.conf import settings

    name = (getattr(settings, 'LANDING_SHOWCASE_PSN', '') or '').strip()
    if not name:
        return False
    from core.services import profile_card_service
    from trophies.models import Profile

    profile = Profile.objects.filter(psn_username__iexact=name).first()
    if not profile:
        logger.warning("LANDING_SHOWCASE_PSN %r has no profile; landing keeps the fixture card", name)
        return False
    data = profile_card_service.get_card_data(profile)
    data['avatar_image'] = data['user_avatar_url']
    for m in data['badges']['medallions']:
        m['layers_cached'] = m['layers']
    for key in ('rarest_plat', 'latest_plat'):
        if data.get(key):
            data[key]['cover_cached'] = data[key]['cover_url']
    html = render_to_string('shareables/profile_card.html', data)
    cache.set(SHOWCASE_CARD_CACHE_KEY, html, SHOWCASE_CARD_TTL)
    return True


def build_landing_context():
    """Everything the anon landing needs beyond the heartbeat the view already fetches."""
    try:
        cached = cache.get(SHOWCASE_CARD_CACHE_KEY)
        showcase_card = cached or _fixture_card_html()
    except Exception:
        logger.exception("Landing showcase card resolution failed")
        cached, showcase_card = None, ''
    return {
        'badge_showcase': _badge_showcase(),
        'showcase_card_html': showcase_card,
        'showcase_card_is_sample': not cached,
    }
