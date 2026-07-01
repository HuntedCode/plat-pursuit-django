"""The Pursuer Card: the cross-surface identity signature (home hero, profile header, share).

Grounds identity in what a hunter actually cares about + would screenshot: platinum count,
their standout platinums in real cover art (toggleable Rarest / Recent), completion/rarity,
rank as standing, and -- where a platinum's game is a curated Contract -- the elements that
platinum levels. The premium is craft on real content; this builder assembles the real data.
Reuses the Lab identity + the trophy snapshot; the platinum reads are bounded slices (whale-
safe) and the contract elements are resolved in one batched query.
"""
import logging

from trophies.services import dashboard_service, lab_service

logger = logging.getLogger(__name__)


def _contract_elements_for_concepts(concept_ids):
    """Map concept_id -> [element display dicts] for concepts whose game is a Contract member
    (the one-home ContractMembership). Batched: one query for all showcase concepts."""
    if not concept_ids:
        return {}
    from trophies.models import ContractMembership
    from trophies.services.element_render import SYMBOLS
    out = {}
    memberships = (
        ContractMembership.objects.filter(concept_id__in=concept_ids)
        .select_related('contract').prefetch_related('contract__jobs')
    )
    for m in memberships:
        out[m.concept_id] = [
            {'symbol': SYMBOLS.get(j.slug, (j.name or '')[:2]), 'disc_slug': j.discipline, 'name': j.name}
            for j in m.contract.jobs.all()
        ]
    return out


def _platinums(profile, limit, *, recent):
    """A showcase slice of the profile's platinums -- ordered by recency or rarity -- each with
    cover art and (if the game is a Contract) the elements that platinum levels."""
    from trophies.models import EarnedTrophy
    qs = (
        EarnedTrophy.objects
        .filter(profile=profile, trophy__trophy_type='platinum', earned=True)
        .select_related('trophy__game__concept', 'trophy__game__concept__igdb_match')
        .defer('trophy__game__concept__igdb_match__raw_response')
    )
    if recent:
        qs = qs.filter(earned_date_time__isnull=False).order_by('-earned_date_time')
    else:
        qs = qs.filter(trophy__trophy_earn_rate__isnull=False).order_by('trophy__trophy_earn_rate')
    rows = list(qs[:limit])

    concept_ids = [g.concept_id for et in rows
                   if (g := et.trophy.game) is not None and g.concept_id is not None]
    elements_by_concept = _contract_elements_for_concepts(concept_ids)

    showcase = []
    for et in rows:
        game = et.trophy.game
        concept = getattr(game, 'concept', None) if game else None
        showcase.append({
            'game_name': concept.unified_title if concept else (game.title_name if game else 'Unknown'),
            'cover_url': game.display_image_url if game else '',
            'earn_rate': et.trophy.trophy_earn_rate,
            'np_communication_id': game.np_communication_id if game else None,
            'elements': elements_by_concept.get(concept.id if concept else None, []),
        })
    return showcase


def build_pursuer_card(profile, *, lab_ctx=None, showcase_limit=5):
    """Assemble the Pursuer Card for `profile`.

    `lab_ctx` (the full Lab context) may be passed in to avoid a second Lab build when the
    caller already has one (the Home). Returns identity + headline stats + the family makeup
    (the 5 disciplines, from the DNA-ring data) + a toggleable platinum showcase ({rarest,
    recent}). Returns None when the Lab build yields no usable identity (degraded) so the
    surface hides the card.
    """
    if lab_ctx is None:
        lab_ctx = lab_service.build_lab_context(profile)
    hero = (lab_ctx or {}).get('hero') or {}
    if not hero.get('pursuer_rank'):
        return None
    snap = dashboard_service.provide_trophy_snapshot(profile)
    rarest = _platinums(profile, showcase_limit, recent=False)
    recent = _platinums(profile, showcase_limit, recent=True)
    # The 5 disciplines (from the DNA-ring data) + a bar relative to your strongest family,
    # so the band reads as "what you're made of" at a glance.
    families = hero.get('ring') or []
    if families:
        strongest = max((f.get('avg') or 0 for f in families), default=0) or 1
        families = [{**f, 'bar_pct': round((f.get('avg') or 0) / strongest * 100)} for f in families]
    return {
        'name': hero.get('pursuer_name'),
        'avatar_url': hero.get('avatar_url'),
        'rank': hero.get('pursuer_rank'),          # {key, label, ...} -- key drives the chrome
        'level': hero.get('pursuer_level'),
        'active_title': hero.get('active_title'),
        'platinums': snap.get('total_plats', 0),
        'avg_completion': snap.get('avg_progress'),
        'total_trophies': snap.get('total_earned', 0),
        'families': families,                       # [{label, slug, avg, bar_pct, ...}] -- 5 disciplines
        'rarest_pct': rarest[0]['earn_rate'] if rarest else None,
        'showcase': {'rarest': rarest, 'recent': recent},
        # Epoch of the last sync, so the forge can fire on the next card view after a sync that
        # completed while the user was away/elsewhere (client compares vs the last it forged for).
        'synced_at': int(profile.last_synced.timestamp()) if getattr(profile, 'last_synced', None) else 0,
    }
