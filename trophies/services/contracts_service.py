"""Contracts board context builder.

The Contracts board (Career's Contracts tab) lists live Contracts to pursue. Following the
badge-stage model, each contract foregrounds its GAMES (the member Concepts) -- cover + title +
the viewer's per-game completion -- as the main draw; the jobs it levels and the fixed-T XP
reward are the supporting "what you get for it".

Read-only + whale-safe. Status is derived from EXISTING `EarnedContract` rows (created by the
sync's `mark_contract_reached`, never on this render path) plus a single bounded `ProfileGame`
progress aggregate. The member-concept set is the curated live-Contract pool (bounded), never
the user's whole library, so there is no whale-OOM risk.
"""
import logging

from django.core.paginator import Paginator
from django.db.models import (
    Case, CharField, DateTimeField, Exists, F, IntegerField, OuterRef,
    Prefetch, Q, Subquery, Value, When,
)
from django.db.models.functions import Coalesce

from trophies.models import Contract, ContractMembership, EarnedContract, Game, Job, ProfileGame
from trophies.services import job_render
from trophies.services.job_render import DISCIPLINE_LABELS
from trophies.util_modules.constants import CONTRACT_XP_TOTAL, MODERN_PLATFORMS

logger = logging.getLogger(__name__)

CONTRACTS_PER_PAGE = 24


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


def _ring_segments(elements):
    """Per-job arcs for the SVG split-ring: N equal, family-colored segments (the even XP split)
    with a small gap between them. Each is a stroke-dash arc on a `pathLength=100` circle, so `dash`
    is a direct percentage of the circumference and `offset` positions it. Tagged with the job slug
    so the ring and the job grid can cross-highlight. Replaces the old conic-gradient, which was a
    single unsegmented element that couldn't be drawn or hovered arc-by-arc."""
    n = len(elements)
    if not n:
        return []
    gap = 4.0 if n > 1 else 0.0
    seg = 100.0 / n
    out = []
    for i, el in enumerate(elements):
        out.append({
            'slug': el['slug'],
            'disc_slug': el['disc_slug'],
            'dash': round(seg - gap, 3),                     # visible arc length (0-100 scale)
            'offset': round(-(i * seg + gap / 2.0), 3),      # dashoffset positions the arc
        })
    return out


def _build_contracts(profile):
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
        # Front the card with the most recent member game's art (newest release first; undated
        # concepts trail). This also orders the Tier-2 games grid newest-first.
        _dated = [g for g in contract_games if g.concept.release_date]
        contract_games = (sorted(_dated, key=lambda g: g.concept.release_date, reverse=True)
                          + [g for g in contract_games if not g.concept.release_date])
        elements = [job_render.job_atom(j) for j in jobs]
        n = len(jobs)
        t = contract.xp_total_override or CONTRACT_XP_TOTAL

        # Per-game entries consumed by _contract_game.html (the tier-2 modal cards): game / pgame / has_guide.
        game_entries = [{
            'game': g,
            'profile_game': pg_by_game.get(g.id),
            'has_guide': bool(g.concept.guide_slug),
        } for g in contract_games]

        max_progress = max(
            (e['profile_game'].progress for e in game_entries if e['profile_game']),
            default=0,
        )
        # has_plat is a DISPLAY-only heuristic for the pre-EarnedContract fallback (it only
        # nudges an unreached Project to "pursuing 100%"). The authoritative platinum signal
        # is the engine's EarnedTrophy-based reach detection (contract_service._detect_tiers);
        # claimable/accepted status always comes from EarnedContract below, never from this.
        any_plat = any(e['profile_game'] and e['profile_game'].has_plat for e in game_entries)
        status, progress = _project_status(earned.get(contract.id), max_progress, any_plat)

        first_concept = contract_games[0].concept if contract_games else None
        family_gradient, family_color = _family_styles(elements)
        card_name = contract.name or (first_concept.unified_title if first_concept else '')
        # Client-side search/filter fodder for the board toolbar: one lowercased haystack (contract
        # name + member game titles + job names) and the distinct disciplines this Contract levels.
        search_text = ' '.join(filter(None,
            [card_name]
            + [e['game'].title_name for e in game_entries]
            + [el['name'] for el in elements]
        )).lower()
        projects.append({
            'name': card_name,
            'slug': contract.slug,
            'cover_game': contract_games[0] if contract_games else None,  # newest game -> card art
            'games': game_entries,         # the focal point: every game that satisfies it
            'game_count': len(game_entries),
            'search_text': search_text,    # toolbar search haystack
            'discipline_slugs': sorted({el['disc_slug'] for el in elements}),  # toolbar discipline filter
            'elements': elements,          # what you level
            'element_slugs': [el['slug'] for el in elements],  # for the 5x5 job-grid "lit" lookup
            'ring_segments': _ring_segments(elements),  # SVG arcs for the split-ring (even split, per-job)
            'family_gradient': family_gradient,  # CSS for the family accent bar (gradient if multi-family)
            'family_color': family_color,        # dominant family color var, for the hover/glow
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
    dominant (first) family drives the hover/glow. Built only from the controlled family
    slug enum (combat/exploration/mind/heart/finesse), never user input, so they are safe
    to inline in a style attribute."""
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


# ---------------------------------------------------------------------------
# Server-side board: annotate/filter/sort/paginate in the DB so the (eventually
# huge) catalog is never materialized. Status + a "relevant to you" score are
# derived in SQL; the current all-in-Python path above is used only until the
# frontend switches to the paginated endpoints.
# ---------------------------------------------------------------------------

def _disc_weights(disc_levels):
    """Discipline -> relevance weight, weakest (lowest avg level) weighted highest. Drives the
    'relevant to you' order of the untouched pool. `disc_levels` is {slug: avg_level}."""
    slugs = list(DISCIPLINE_LABELS)
    ranked = sorted(slugs, key=lambda s: (disc_levels or {}).get(s, 0))
    return {s: len(ranked) - i for i, s in enumerate(ranked)}   # weakest -> highest weight


def annotated_contracts(profile, disc_levels=None):
    """Live Contracts annotated IN SQL with the viewer's per-contract status/progress and a
    relevance score -- so the board filters/sorts/paginates in the database, never by iterating
    the catalog. Read-only (reads existing EarnedContract rows + a ProfileGame aggregate)."""
    weights = _disc_weights(disc_levels)
    if profile is not None:
        member_pg = ProfileGame.objects.filter(
            profile=profile,
            game__concept__contract_membership__contract=OuterRef('pk'),
        )
        ec = EarnedContract.objects.filter(profile=profile, contract=OuterRef('pk'))
        max_progress = Coalesce(Subquery(member_pg.order_by('-progress').values('progress')[:1]), 0)
        any_plat = Exists(member_pg.filter(has_plat=True))
        plat_reached = Subquery(ec.values('platinum_reached_at')[:1])
        plat_accepted = Subquery(ec.values('platinum_accepted_at')[:1])
        full_reached = Subquery(ec.values('full_reached_at')[:1])
        full_accepted = Subquery(ec.values('full_accepted_at')[:1])
    else:
        max_progress, any_plat = Value(0), Value(False)
        none_dt = Value(None, output_field=DateTimeField())
        plat_reached = plat_accepted = full_reached = full_accepted = none_dt

    relevance = Coalesce(Subquery(
        Job.objects.filter(contracts=OuterRef('pk')).annotate(
            w=Case(*[When(discipline=s, then=Value(w)) for s, w in weights.items()],
                   default=Value(0), output_field=IntegerField())
        ).order_by('-w').values('w')[:1]
    ), 0)

    return (
        Contract.objects.filter(is_live=True)
        .annotate(
            has_jobs=Exists(Job.objects.filter(contracts=OuterRef('pk'))),   # jobless -> awards nothing
            max_progress=max_progress, any_plat=any_plat,
            plat_reached=plat_reached, plat_accepted=plat_accepted,
            full_reached=full_reached, full_accepted=full_accepted,
            relevance=relevance,
            xp_eff=Coalesce('xp_total_override', Value(CONTRACT_XP_TOTAL)),
        )
        .filter(has_jobs=True)
        .annotate(status=Case(
            When(Q(plat_reached__isnull=False, plat_accepted__isnull=True)
                 | Q(full_reached__isnull=False, full_accepted__isnull=True), then=Value('claimable')),
            When(Q(plat_accepted__isnull=False) | Q(full_accepted__isnull=False), then=Value('accepted')),
            When(Q(max_progress__gte=100) | Q(any_plat=True), then=Value('pursuing')),
            When(max_progress__gt=0, then=Value('pursuing')),
            default=Value('available'), output_field=CharField(),
        ))
        .annotate(
            status_order=Case(
                When(status='claimable', then=Value(0)),
                When(status='pursuing', then=Value(1)),
                When(status='available', then=Value(2)),
                default=Value(3), output_field=IntegerField()),
            sort_progress=Case(When(status='pursuing', then=F('max_progress')),
                               default=Value(0), output_field=IntegerField()),
        )
    )


# Relevance order: claimable -> pursuing (closest-to-done) -> available (relevant-to-you) -> claimed.
_ORDER = ('status_order', '-sort_progress', '-relevance', '-xp_eff', '-created_at', 'name')
_SORTS = {
    'relevance': _ORDER,
    'xp': ('-xp_eff', '-created_at', 'name'),
    'progress': ('-max_progress', 'status_order', 'name'),
    'newest': ('-created_at', 'name'),
    'name': ('name',),
}


def _filter_contracts(qs, q='', status='', discipline='', job='', platforms=None):
    if status and status != 'all':
        qs = qs.filter(status=status)
    if job:                                   # specific-job drill-down wins over its discipline
        qs = qs.filter(jobs__slug=job)
    elif discipline and discipline != 'all':
        qs = qs.filter(jobs__discipline=discipline)
    if platforms:                             # any member game on a selected platform (GIN-indexed JSONB)
        plat_q = Q()
        for p in platforms:
            plat_q |= Q(memberships__concept__games__title_platform__contains=[p])
        qs = qs.filter(plat_q)
    if q:
        qs = qs.filter(
            Q(name__icontains=q)
            | Q(memberships__concept__games__title_name__icontains=q)
            | Q(jobs__name__icontains=q)
        )
    return qs.distinct()


def _card_prefetch(qs):
    games_qs = (Game.objects.select_related('concept', 'concept__igdb_match')
                .defer('concept__igdb_match__raw_response'))
    return qs.prefetch_related(
        'jobs',
        Prefetch('memberships', queryset=ContractMembership.objects.select_related('concept')
                 .prefetch_related(Prefetch('concept__games', queryset=games_qs))),
    )


def _member_games(contract):
    """Member games newest-first (dated by concept release, undated trailing)."""
    games = []
    for m in contract.memberships.all():
        games.extend(m.concept.games.all())
    dated = [g for g in games if g.concept.release_date]
    return (sorted(dated, key=lambda g: g.concept.release_date, reverse=True)
            + [g for g in games if not g.concept.release_date])


def project_card(c):
    """Card display dict for one annotated+prefetched Contract. No per-game progress -- that
    lives in the lazily loaded modal."""
    jobs = list(c.jobs.all())
    elements = [job_render.job_atom(j) for j in jobs]
    n = len(jobs) or 1
    games = _member_games(c)
    first_concept = games[0].concept if games else None
    family_gradient, family_color = _family_styles(elements)
    status = c.status
    progress = 100 if status in ('claimable', 'accepted') else (c.max_progress if status == 'pursuing' else 0)
    t = c.xp_total_override or CONTRACT_XP_TOTAL
    return {
        'name': c.name or (first_concept.unified_title if first_concept else ''),
        'slug': c.slug,
        'cover_game': games[0] if games else None,
        'game_count': len(games),
        'elements': elements,
        'element_slugs': [el['slug'] for el in elements],
        'ring_segments': _ring_segments(elements),
        'family_gradient': family_gradient,
        'family_color': family_color,
        'xp_total': t,
        'xp_each': t // n,
        'status': status,
        'progress': progress,
    }


def contracts_page(profile, disc_levels=None, page=1, q='', status='', discipline='',
                   job='', platforms=None, sort='relevance'):
    """One paginated, filtered, sorted page of card dicts + metadata. `platforms` defaults to
    current-gen (PS5/PS4); pass an explicit list to include legacy/VR, or [] for all platforms."""
    if platforms is None:
        platforms = list(MODERN_PLATFORMS)
    qs = _filter_contracts(annotated_contracts(profile, disc_levels),
                           q=q, status=status, discipline=discipline, job=job, platforms=platforms)
    qs = _card_prefetch(qs.order_by(*_SORTS.get(sort, _ORDER)))
    page_obj = Paginator(qs, CONTRACTS_PER_PAGE).get_page(page)
    return {
        'contracts': [project_card(c) for c in page_obj],
        'page': page_obj.number,
        'has_next': page_obj.has_next(),
        'total': page_obj.paginator.count,
    }


def claimable_count(profile):
    """Cheap DB count of claimable contracts (for the 'Claim all' button)."""
    return annotated_contracts(profile).filter(status='claimable').count()


def build_contract_modal(profile, slug):
    """Full modal dict for one Contract: jobs + member games WITH the viewer's per-game progress.
    Powers the lazy-loaded modal endpoint. Returns None if the slug isn't a live contract."""
    try:
        c = _card_prefetch(Contract.objects.filter(is_live=True)).get(slug=slug)
    except Contract.DoesNotExist:
        return None
    jobs = list(c.jobs.all())
    if not jobs:
        return None
    elements = [job_render.job_atom(j) for j in jobs]
    n = len(jobs)
    games = _member_games(c)
    pg_by_game = {}
    if profile is not None and games:
        pg_by_game = {pg.game_id: pg for pg in ProfileGame.objects.filter(profile=profile, game__in=games)}
    game_entries = [{
        'game': g, 'profile_game': pg_by_game.get(g.id), 'has_guide': bool(g.concept.guide_slug),
    } for g in games]
    first_concept = games[0].concept if games else None
    family_gradient, family_color = _family_styles(elements)
    t = c.xp_total_override or CONTRACT_XP_TOTAL
    return {
        'name': c.name or (first_concept.unified_title if first_concept else ''),
        'slug': c.slug,
        'cover_game': games[0] if games else None,
        'games': game_entries,
        'game_count': len(game_entries),
        'elements': elements,
        'family_gradient': family_gradient,
        'family_color': family_color,
        'xp_total': t,
        'xp_each': t // n,
    }


def build_contracts_context(profile):
    """Assemble the Contracts board context. Read-only + whale-safe (see module docstring)."""
    context = {}
    try:
        contracts = _build_contracts(profile)
    except Exception:
        logger.exception("Contracts board build failed for profile %s", getattr(profile, 'id', '?'))
        contracts = []
    context['contracts'] = contracts
    context['claimable_count'] = sum(1 for p in contracts if p['status'] == 'claimable')
    return context
