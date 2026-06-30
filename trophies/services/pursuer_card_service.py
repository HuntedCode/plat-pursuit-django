"""The Pursuer Card: the cross-surface identity signature (home hero, profile header, share).

Grounds identity in what a hunter actually cares about + would screenshot: platinum count,
their standout (rarest) platinums in real cover art, completion/rarity, and rank as standing.
The premium is craft on real content, so this builder just assembles real data; the chrome
lives in the component. Reuses the Lab identity (name/avatar/rank/level) + the trophy snapshot;
the only new read is the rarest-platinums showcase (a bounded, ordered slice -- whale-safe).
"""
import logging

from trophies.services import dashboard_service, lab_service

logger = logging.getLogger(__name__)


def _rarest_platinums(profile, limit):
    """The hunter's rarest platinums (lowest global earn-rate) with cover art -- the showcase.
    A bounded sort+slice over the profile's platinum EarnedTrophies (not a whale-wide scan)."""
    from trophies.models import EarnedTrophy
    plats = (
        EarnedTrophy.objects
        .filter(profile=profile, trophy__trophy_type='platinum', earned=True,
                trophy__trophy_earn_rate__isnull=False)
        .select_related('trophy__game__concept', 'trophy__game__concept__igdb_match')
        .defer('trophy__game__concept__igdb_match__raw_response')
        .order_by('trophy__trophy_earn_rate')[:limit]
    )
    showcase = []
    for et in plats:
        game = et.trophy.game
        concept = getattr(game, 'concept', None) if game else None
        showcase.append({
            'game_name': concept.unified_title if concept else (game.title_name if game else 'Unknown'),
            'cover_url': game.display_image_url if game else '',
            'has_cover': bool(game.has_cover_art) if game else False,
            'earn_rate': et.trophy.trophy_earn_rate,
            'np_communication_id': game.np_communication_id if game else None,
        })
    return showcase


def build_pursuer_card(profile, *, hero=None, showcase_limit=5):
    """Assemble the Pursuer Card for `profile`.

    `hero` may be passed in (the Lab hero dict) to avoid a second Lab build when the caller
    already has one (the Home). Returns identity + headline stats + the rarest-platinum
    showcase, all real data; the component handles the rank-tinted chrome.
    """
    if hero is None:
        hero = lab_service.build_lab_context(profile).get('hero') or {}
    snap = dashboard_service.provide_trophy_snapshot(profile)
    showcase = _rarest_platinums(profile, showcase_limit)
    return {
        'name': hero.get('pursuer_name'),
        'avatar_url': hero.get('avatar_url'),
        'rank': hero.get('pursuer_rank'),          # {key, label, ...} -- key drives the chrome
        'level': hero.get('pursuer_level'),
        'active_title': hero.get('active_title'),
        'platinums': snap.get('total_plats', 0),
        'avg_completion': snap.get('avg_progress'),
        'total_trophies': snap.get('total_earned', 0),
        'rarest_pct': showcase[0]['earn_rate'] if showcase else None,
        'showcase': showcase,
    }
