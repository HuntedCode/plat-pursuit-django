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

from django.db.models import Max, Prefetch

from trophies.models import Contract, ContractMembership, EarnedContract, ProfileGame
from trophies.services import element_render
from trophies.util_modules.constants import CONTRACT_XP_TOTAL

logger = logging.getLogger(__name__)


def _project_status(ec, max_progress):
    """(status, progress%) for a Project, read-only:
      available  -- never reached, untouched
      pursuing   -- in progress (a member game has some completion), not yet reached
      claimable  -- a tier is reached but not accepted (the glowing Accept moment)
      accepted   -- the reward has been banked
    `ec` is the existing EarnedContract (or None if never reached); `max_progress` is the
    viewer's best completion across the member games.
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
    if max_progress > 0:
        return 'pursuing', max_progress
    return 'available', 0


def _build_projects(profile):
    # Cover-art OOM rule: select_related the concept + its IGDB match, defer the ~30KB
    # raw_response blob that cover templates never read.
    member_qs = (
        ContractMembership.objects
        .select_related('concept', 'concept__igdb_match')
        .defer('concept__igdb_match__raw_response')
    )
    contracts = list(
        Contract.objects.filter(is_live=True)
        .prefetch_related('jobs', Prefetch('memberships', queryset=member_qs))
        .order_by('name')
    )
    if not contracts:
        return []

    member_concept_ids = {m.concept_id for c in contracts for m in c.memberships.all()}

    earned, progress_by_concept = {}, {}
    if profile is not None and member_concept_ids:
        earned = {
            ec.contract_id: ec
            for ec in EarnedContract.objects.filter(profile=profile, contract__in=contracts)
        }
        # One bounded aggregate: the viewer's best completion per member concept.
        progress_by_concept = dict(
            ProfileGame.objects
            .filter(profile=profile, game__concept_id__in=member_concept_ids)
            .values('game__concept_id')
            .annotate(mp=Max('progress'))
            .values_list('game__concept_id', 'mp')
        )

    projects = []
    for contract in contracts:
        jobs = list(contract.jobs.all())
        if not jobs:
            continue  # a Project with no elements awards nothing -- hide it
        concepts = [m.concept for m in contract.memberships.all()]
        elements = [element_render.job_atom(j) for j in jobs]
        n = len(jobs)
        t = contract.xp_total_override or CONTRACT_XP_TOTAL

        games = [{
            'title': c.unified_title or contract.name,
            'cover_url': c.cover_url,
            'progress': progress_by_concept.get(c.id, 0),
        } for c in concepts]
        max_progress = max((g['progress'] for g in games), default=0)
        status, progress = _project_status(earned.get(contract.id), max_progress)

        projects.append({
            'name': (concepts[0].unified_title if concepts else '') or contract.name,
            'slug': contract.slug,
            'games': games,            # the focal point: the member games (variants)
            'multi_game': len(games) > 1,
            'elements': elements,      # what you level
            'compound': element_render.build_compound(elements, zlib.crc32(contract.slug.encode())),
            'xp_total': t,
            'xp_each': t // n,
            'status': status,
            'progress': progress,
        })
    return projects


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
