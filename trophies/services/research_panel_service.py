"""Research Panel page context builder.

The Research Panel (`/my-pursuit/research-panel/`) lists live Contracts as "Projects" to
pursue. Following the badge-stage model, each Project foregrounds its GAMES (the member
Concepts) -- cover + title + the viewer's per-game completion -- as the main draw; the
elements it levels, the fixed-T XP reward, and the Compound are the supporting "what you
get for it".

Read-only + whale-safe. Status is derived from EXISTING `EarnedContract` rows (created by
the sync's `mark_contract_reached`, never on this render path) plus a single bounded
`ProfileGame` progress aggregate. The member-concept set is the curated live-Contract pool
(bounded), never the user's whole library, so there is no whale-OOM risk.
"""
import logging
import zlib

from django.db.models import Prefetch

from trophies.models import Contract, ContractMembership, EarnedContract, Game, ProfileGame
from trophies.services import element_render
from trophies.util_modules.constants import CONTRACT_XP_TOTAL

logger = logging.getLogger(__name__)


def _project_status(ec, max_progress, any_plat):
    """(status, progress%) for a Project, read-only:
      available  -- never reached, untouched
      pursuing   -- in progress (a member game has some completion), not yet reached
      claimable  -- a tier is reached but not accepted (the glowing Accept moment)
      accepted   -- the reward has been banked
    `ec` is the existing EarnedContract (or None if never reached); `max_progress` is the
    viewer's best completion across the member games; `any_plat` is True if any member
    game's platinum is earned.
    """
    if ec is not None:
        claimable = bool(
            (ec.platinum_reached_at and not ec.platinum_accepted_at)
            or (ec.full_reached_at and not ec.full_accepted_at)
        )
        if claimable:
            return 'claimable', 100
        if ec.platinum_accepted_at or ec.full_accepted_at:
            return 'accepted', 100
    if max_progress >= 100 or any_plat:
        # A game is done but sync hasn't stamped the reach yet -- still "pursuing"
        # on this read-only render; the next sync flips it to claimable.
        return 'pursuing', 100
    if max_progress > 0:
        return 'pursuing', max_progress
    return 'available', 0


def _build_projects(profile):
    # Each member Concept's GAMES are the focal point (the badge-stage model: show exactly
    # what's available). Prefetch concept.games; cover-art OOM rule -> select_related the
    # game's concept + IGDB match and defer the ~30KB raw_response blob cover templates
    # never read.
    games_qs = (
        Game.objects
        .select_related('concept', 'concept__igdb_match')
        .defer('concept__igdb_match__raw_response')
    )
    member_qs = (
        ContractMembership.objects
        .select_related('concept')
        .prefetch_related(Prefetch('concept__games', queryset=games_qs))
    )
    contracts = list(
        Contract.objects.filter(is_live=True)
        .prefetch_related('jobs', Prefetch('memberships', queryset=member_qs))
        .order_by('name')
    )
    if not contracts:
        return []

    # Flatten every member game across every live Project (curated, bounded pool -> safe to
    # iterate; this is NOT the user's whole library) for one bulk ProfileGame lookup.
    games_by_contract = {}
    all_games = []
    for contract in contracts:
        cg = []
        for m in contract.memberships.all():
            cg.extend(m.concept.games.all())
        games_by_contract[contract.id] = cg
        all_games.extend(cg)

    earned, pg_by_game = {}, {}
    if profile is not None:
        earned = {
            ec.contract_id: ec
            for ec in EarnedContract.objects.filter(profile=profile, contract__in=contracts)
        }
        if all_games:
            pg_by_game = {
                pg.game_id: pg
                for pg in ProfileGame.objects.filter(profile=profile, game__in=all_games)
            }

    projects = []
    for contract in contracts:
        jobs = list(contract.jobs.all())
        if not jobs:
            continue  # a Project with no elements awards nothing -- hide it
        contract_games = games_by_contract[contract.id]
        elements = [element_render.job_atom(j) for j in jobs]
        n = len(jobs)
        t = contract.xp_total_override or CONTRACT_XP_TOTAL

        # Per-game entries matching the badge-stage card contract
        # (badge_detail_items.html expects game / pgame / ratings / has_guide).
        game_entries = [{
            'game': g,
            'profile_game': pg_by_game.get(g.id),
            'community_ratings': None,  # kept lean on the list view; add if needed
            'has_guide': bool(g.concept.guide_slug),
        } for g in contract_games]

        max_progress = max(
            (e['profile_game'].progress for e in game_entries if e['profile_game']),
            default=0,
        )
        any_plat = any(e['profile_game'] and e['profile_game'].has_plat for e in game_entries)
        status, progress = _project_status(earned.get(contract.id), max_progress, any_plat)

        first_concept = contract_games[0].concept if contract_games else None
        family_gradient, family_color = _family_styles(elements)
        projects.append({
            'name': (first_concept.unified_title if first_concept else '') or contract.name,
            'slug': contract.slug,
            'games': game_entries,         # the focal point: every game that satisfies it
            'game_count': len(game_entries),
            'elements': elements,          # what you level
            'compound': element_render.build_compound(elements, zlib.crc32(contract.slug.encode())),
            'family_gradient': family_gradient,  # CSS for the family accent bar (gradient if multi-family)
            'family_color': family_color,        # dominant family color var, for the card tint/glow
            'xp_total': t,
            'xp_each': t // n,
            'status': status,
            'progress': progress,
            'completed': max_progress >= 100 or any_plat,
        })
    return projects


def _family_styles(elements):
    """(family_gradient, family_color) CSS for a Project's element families. The accent
    bar runs a top-to-bottom gradient across the distinct families (solid if one); the
    dominant (first) family drives the card's subtle tint + glow. Values are built only
    from the controlled family slug enum (combat/exploration/mind/heart/finesse), never
    user input, so they are safe to inline in a style attribute."""
    fams = []
    for el in elements:
        if el['disc_slug'] not in fams:
            fams.append(el['disc_slug'])
    if not fams:
        return 'var(--pp-border)', 'var(--pp-border)'
    color = f"var(--disc-{fams[0]})"
    if len(fams) == 1:
        return color, color
    stops = ', '.join(f"var(--disc-{f})" for f in fams)
    return f"linear-gradient(180deg, {stops})", color


def build_research_panel_context(profile):
    """Assemble the Research Panel context. Read-only + whale-safe (see module docstring)."""
    context = {}
    try:
        projects = _build_projects(profile)
    except Exception:
        logger.exception("Research Panel projects build failed for profile %s", getattr(profile, 'id', '?'))
        projects = []
    context['projects'] = projects
    context['claimable_count'] = sum(1 for p in projects if p['status'] == 'claimable')
    return context
