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
from django.core.paginator import Paginator
from django.db.models import (
    Avg, Case, CharField, Count, DateTimeField, Exists, F, IntegerField, OuterRef,
    Q, Subquery, Sum, Value, When,
)
from django.db.models.functions import Coalesce

from trophies.models import (
    Contract, EarnedContract, Game, IGDBMatch, Job, ProfileGame, ProfileJobXP, Trophy,
)
from trophies.services import job_render
from trophies.services.job_render import DISCIPLINE_LABELS


def _member_gate(prefix='concept__'):
    """The membership GATE (no igdb_id match): an ANCHORED + trusted-matched concept, reached via
    `prefix` (e.g. 'game__concept__' or 'concept__'). Pair with a concrete `..igdb_id` /
    `..igdb_id__in` filter, or use _member_at_igdb for the OuterRef-correlated Exists/Subquery form."""
    return {
        f'{prefix}anchor_migration_completed_at__isnull': False,
        f'{prefix}igdb_match__status__in': IGDBMatch.TRUSTED_STATUSES,
    }


def _member_at_igdb(prefix):
    """Filter kwargs matching a member of the contract at OuterRef('igdb_id'): the gate (above)
    PLUS the raw igdb_id equalling the outer Contract's igdb_id. Replaces the old membership joins."""
    return {**_member_gate(prefix), f'{prefix}igdb_match__igdb_id': OuterRef('igdb_id')}
from trophies.util_modules.constants import (
    ALL_PLATFORMS, CONTRACT_PLATINUM_FRAC, CONTRACT_XP_TOTAL, MODERN_PLATFORMS,
)

CONTRACTS_PER_PAGE = 24

# Chip labels for the smart empty-state suggestion ("drop <label> to see N").
STATUS_LABELS = {
    'available': 'Not Started',
    'pursuing': 'In Progress',
    'claimable': 'Ready to Claim',
    'accepted': 'Claimed',
}


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

def discipline_levels(profile):
    """{discipline_slug: avg job level} for the viewer -- feeds the relevance sort. Cheap grouped
    aggregate (<=5 rows). Untouched disciplines are absent -> default 0 (weakest -> most relevant)."""
    if profile is None:
        return {}
    return {
        r['job__discipline']: r['avg']
        for r in ProfileJobXP.objects.filter(profile=profile)
        .values('job__discipline').annotate(avg=Avg('level'))
    }


def job_roster():
    """The 25-job roster grouped by discipline (slug/icon/name only, no per-user data) for the
    card's 5x5 job map. User-independent, so a page-render doesn't need the full career context."""
    by_disc = {}
    for job in Job.objects.all().order_by('display_order'):
        by_disc.setdefault(job.discipline, []).append(job)
    return [
        {'slug': slug, 'label': label,
         'jobs': [{'slug': j.slug, 'icon': j.icon, 'name': j.name} for j in by_disc.get(slug, [])]}
        for slug, label in DISCIPLINE_LABELS.items()
    ]


def _disc_rankings(disc_levels):
    """(relevance, strength) weight maps by discipline. `relevance` weights your WEAKEST disciplines
    highest (the 'relevant to you' sort -- grow your breadth); `strength` weights your STRONGEST
    highest (the 'keep pushing' sort -- double down on what you're good at). `disc_levels` is
    {slug: avg_level}; unranked disciplines default to 0 (weakest)."""
    slugs = list(DISCIPLINE_LABELS)
    ranked = sorted(slugs, key=lambda s: (disc_levels or {}).get(s, 0))     # weakest -> strongest
    relevance = {s: len(ranked) - i for i, s in enumerate(ranked)}          # weakest -> highest weight
    strength = {s: i + 1 for i, s in enumerate(ranked)}                     # strongest -> highest weight
    return relevance, strength


def annotated_contracts(profile, disc_levels=None):
    """Live Contracts annotated IN SQL with the viewer's per-contract status/progress and a
    relevance score -- so the board filters/sorts/paginates in the database, never by iterating
    the catalog. Read-only (reads existing EarnedContract rows + a ProfileGame aggregate)."""
    weights, strengths = _disc_rankings(disc_levels)
    if profile is not None:
        member_pg = ProfileGame.objects.filter(
            profile=profile,
            **_member_at_igdb('game__concept__'),
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

    def _disc_score(weight_map):   # per-contract max discipline weight across its jobs (0 if jobless)
        return Coalesce(Subquery(
            Job.objects.filter(contracts=OuterRef('pk')).annotate(
                w=Case(*[When(discipline=s, then=Value(w)) for s, w in weight_map.items()],
                       default=Value(0), output_field=IntegerField())
            ).order_by('-w').values('w')[:1]
        ), 0)

    job_count = Coalesce(Subquery(
        Job.objects.filter(contracts=OuterRef('pk')).values('contracts')
        .annotate(c=Count('pk')).values('c')[:1]
    ), 0)

    return (
        Contract.objects.filter(is_live=True)
        .annotate(
            has_jobs=Exists(Job.objects.filter(contracts=OuterRef('pk'))),   # jobless -> awards nothing
            # Do the member games DEFINE a platinum? (mirrors contract_service._has_platinum) -- drives
            # the card's tier split; games with no plat pay the full T at 100% instead.
            defines_plat=Exists(Trophy.objects.filter(
                trophy_type='platinum', **_member_at_igdb('game__concept__'))),
            max_progress=max_progress, any_plat=any_plat,
            plat_reached=plat_reached, plat_accepted=plat_accepted,
            full_reached=full_reached, full_accepted=full_accepted,
            relevance=_disc_score(weights),      # weakest-discipline weight ("relevant to you")
            strength=_disc_score(strengths),     # strongest-discipline weight ("keep pushing")
            job_count=job_count,                 # number of jobs the contract levels
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
# "Keep pushing" mirrors relevance but orders the available pool by your STRONGEST disciplines.
_PUSHING = ('status_order', '-sort_progress', '-strength', '-created_at', 'name')
_SORTS = {
    'relevance': _ORDER,
    'pushing': _PUSHING,
    'jobs': ('-job_count', '-created_at', 'name'),
    'fewest': ('job_count', '-created_at', 'name'),
    'progress': ('-max_progress', 'status_order', 'name'),
    'newest': ('-created_at', 'name'),
    'name': ('name',),
}


def _platform_exists(platforms):
    """A contract with any member game on one of `platforms`, as an EXISTS subquery rather than an
    M2M join through memberships->concept->games. The join multiplied contract rows (one per member
    game x platform), which forced a DISTINCT and made the planner seq-scan the whole game table
    re-evaluating the JSONB filter (~170ms on the default board). EXISTS evaluates once per contract
    and hits the title_platform GIN index via `?|` (has_any_keys)."""
    return Exists(Game.objects.filter(
        title_platform__has_any_keys=list(platforms),
        **_member_at_igdb('concept__'),
    ))


def _filter_contracts(qs, q='', status='', disciplines=None, jobs=None, platforms=None):
    if status and status != 'all':
        qs = qs.filter(status=status)
    # Jobs + disciplines are ANDed: "driver + slayer" = a contract that levels BOTH. Each chained
    # .filter() on the jobs M2M is a separate join (AND); a single __in would be OR (any).
    for slug in (jobs or ()):
        qs = qs.filter(jobs__slug=slug)
    for disc in (disciplines or ()):
        if disc and disc != 'all':
            qs = qs.filter(jobs__discipline=disc)
    if platforms:                             # any member game on a selected platform (EXISTS, not a join)
        qs = qs.filter(_platform_exists(platforms))
    if q:
        # Member-game-title search: a member game is derived (no membership join), so match it as an
        # annotated Exists over the igdb path rather than a relational join.
        game_name_match = Exists(Game.objects.filter(
            title_name__icontains=q, **_member_at_igdb('concept__')))
        qs = qs.annotate(_game_name_match=game_name_match).filter(
            Q(name__icontains=q)
            | Q(_game_name_match=True)
            | Q(jobs__name__icontains=q)
        )
    return qs.distinct()


def _card_prefetch(qs):
    # Member games are igdb-derived (no membership relation to prefetch); _member_games queries them
    # per card. Jobs stay prefetched (the card's primary payload).
    return qs.prefetch_related('jobs')


def _order_member_games(games):
    """Member games newest-first: dated (by concept release, descending) then undated trailing."""
    dated = [g for g in games if g.concept.release_date]
    return (sorted(dated, key=lambda g: g.concept.release_date, reverse=True)
            + [g for g in games if not g.concept.release_date])


def _member_games(contract):
    """One contract's member games newest-first. Igdb-derived: the contract's member concepts
    (anchored + trusted at its igdb_id) resolved to their games. For a PAGE of contracts use
    _member_games_by_igdb (one query) instead of this per-contract lookup."""
    member_ids = contract.member_concept_ids()
    if not member_ids:
        return []
    games = list(
        Game.objects.filter(concept_id__in=member_ids)
        .select_related('concept', 'concept__igdb_match')
        .defer('concept__igdb_match__raw_response')
    )
    return _order_member_games(games)


def _member_games_by_igdb(contracts):
    """Batch: {igdb_id: [member games newest-first]} for a whole page of contracts in ONE query,
    so the board doesn't re-run _member_games (2 queries) per card. Null-igdb (episodic) contracts
    contribute no key -- they have no igdb-derived members, same as the per-contract path."""
    igdb_ids = {c.igdb_id for c in contracts if c.igdb_id is not None}
    if not igdb_ids:
        return {}
    games = (
        Game.objects.filter(
            concept__igdb_match__igdb_id__in=igdb_ids,
            **_member_gate(),   # anchored + trusted (concrete ids supplied above, no OuterRef)
        )
        .select_related('concept', 'concept__igdb_match')
        .defer('concept__igdb_match__raw_response')
    )
    by_igdb = {}
    for g in games:
        by_igdb.setdefault(g.concept.igdb_match.igdb_id, []).append(g)
    return {gid: _order_member_games(gl) for gid, gl in by_igdb.items()}


def project_card(c, member_games=None):
    """Card display dict for one annotated+prefetched Contract. No per-game progress -- that
    lives in the lazily loaded modal. `member_games` may be pre-resolved by the batch board path;
    when None (single-card callers) it falls back to the per-contract query."""
    jobs = list(c.jobs.all())
    elements = [job_render.job_atom(j) for j in jobs]
    n = len(jobs) or 1
    games = _member_games(c) if member_games is None else member_games
    first_concept = games[0].concept if games else None
    family_gradient, family_color = _family_styles(elements)
    status = c.status
    progress = 100 if status in ('claimable', 'accepted') else (c.max_progress if status == 'pursuing' else 0)
    t = c.xp_total_override or CONTRACT_XP_TOTAL
    # Tier split for the card strip. Plat-bearing contracts pay CONTRACT_PLATINUM_FRAC of T on the
    # platinum tier and the rest at 100%; contracts whose games have no plat pay the full T at 100%.
    has_plat = bool(getattr(c, 'defines_plat', False))
    plat_xp = round(t * CONTRACT_PLATINUM_FRAC) if has_plat else 0
    bonus_xp = t - plat_xp   # the "at 100%" amount (== T when there's no plat tier)
    # Per-tier bar fills (drawn like the circle's progress). Plat bar creeps with the member game's
    # completion and snaps full when the platinum is earned; the 100% bar stays locked until the plat
    # is done, then creeps to 100. No-plat contracts have a single bar creeping straight to 100%.
    plat_reached = bool(getattr(c, 'plat_reached', None))
    full_reached = bool(getattr(c, 'full_reached', None))
    mp = getattr(c, 'max_progress', 0) or 0
    if has_plat:
        plat_fill = 100 if plat_reached else mp
        full_fill = 100 if full_reached else (mp if plat_reached else 0)
    else:
        plat_fill = 0
        full_fill = 100 if full_reached else mp
    # Which tier(s) are claimable right now (reached but not yet accepted) -- labels the Claim button.
    plat_accepted = bool(getattr(c, 'plat_accepted', None))
    full_accepted = bool(getattr(c, 'full_accepted', None))
    claim_plat = has_plat and plat_reached and not plat_accepted
    claim_full = full_reached and not full_accepted
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
        'has_plat': has_plat,
        'plat_xp': plat_xp,
        'bonus_xp': bonus_xp,
        'plat_fill': plat_fill,
        'full_fill': full_fill,
        'claim_plat': claim_plat,
        'claim_full': claim_full,
        'status': status,
        'progress': progress,
    }


def contracts_page(profile, disc_levels=None, page=1, q='', status='', disciplines=None,
                   jobs=None, platforms=None, sort='relevance'):
    """One paginated, filtered, sorted page of card dicts + metadata. `disciplines`/`jobs` are lists
    ANDed together (a contract must level every one). `platforms` defaults to current-gen (PS5/PS4);
    pass an explicit list to include legacy/VR, or [] for all platforms."""
    if platforms is None:
        platforms = list(MODERN_PLATFORMS)
    qs = _filter_contracts(annotated_contracts(profile, disc_levels),
                           q=q, status=status, disciplines=disciplines, jobs=jobs, platforms=platforms)
    qs = _card_prefetch(qs.order_by(*_SORTS.get(sort, _ORDER)))
    paginator = Paginator(qs, CONTRACTS_PER_PAGE)
    if page > paginator.num_pages:   # past the end -> empty, so infinite scroll stops (get_page clamps)
        return {'contracts': [], 'page': page, 'has_next': False, 'total': paginator.count}
    page_obj = paginator.get_page(page)
    page_contracts = list(page_obj)
    games_by_igdb = _member_games_by_igdb(page_contracts)   # one query for the whole page's members
    return {
        'contracts': [project_card(c, games_by_igdb.get(c.igdb_id, [])) for c in page_contracts],
        'page': page_obj.number,
        'has_next': page_obj.has_next(),
        'total': paginator.count,
    }


def claimable_count(profile):
    """Cheap DB count of claimable contracts (for the 'Claim all' button)."""
    return annotated_contracts(profile).filter(status='claimable').count()


def claimable_summary(profile):
    """{count, total_xp} across ALL claimable contracts (the pending-rewards rail), independent of
    the board's paging/filters, via one DB aggregate."""
    agg = (annotated_contracts(profile).filter(status='claimable')
           .aggregate(count=Count('id'), xp=Sum('xp_eff')))
    return {'count': agg['count'] or 0, 'total_xp': agg['xp'] or 0}


def board_facets(profile, disc_levels=None, q='', status='', disciplines=None, jobs=None, platforms=None):
    """Facet counts for the toolbar chips. Each dimension counts the catalog filtered by the OTHER
    active filters (so picking PS5 doesn't zero out PS4's count, and status counts reflect your
    current discipline/platform view). Cheap + whale-safe: the live-Contract catalog is bounded and
    curated (never the user's library), so these are a few small aggregates."""
    if platforms is None:                     # match contracts_page: absent -> current-gen, so the
        platforms = list(MODERN_PLATFORMS)    # status counts agree with the board's default total
    base = annotated_contracts(profile, disc_levels)
    # Status chips: ignore the status filter, respect discipline/job/platform/search. One filtered
    # aggregate (a GROUP BY would be split by the board's relevance/strength annotations).
    s_qs = _filter_contracts(base, q=q, disciplines=disciplines, jobs=jobs, platforms=platforms)
    status_counts = s_qs.aggregate(
        available=Count('id', filter=Q(status='available'), distinct=True),
        pursuing=Count('id', filter=Q(status='pursuing'), distinct=True),
        claimable=Count('id', filter=Q(status='claimable'), distinct=True),
        accepted=Count('id', filter=Q(status='accepted'), distinct=True),
        all=Count('id', distinct=True),
    )
    # Platform chips: ignore the platform filter, respect status/discipline/job/search -- so a legacy
    # platform shows its true total even while the board is defaulted to current-gen.
    p_base = _filter_contracts(base, q=q, status=status, disciplines=disciplines, jobs=jobs)
    platform_counts = {
        p: p_base.filter(_platform_exists([p])).distinct().count()
        for p in ALL_PLATFORMS
    }
    # Discipline + job popovers: REFINEMENT counts. Jobs/disciplines are ANDed, so unlike the OR-based
    # platform chips these respect the FULL current filter (including the other selected jobs/disciplines)
    # -- each count is "how many of your current results also level this job", so it narrows as you pick.
    # Counted from the Job side (no board annotations there, so the GROUP BY isn't split). `dj_ids` stays
    # a subquery.
    dj_ids = _filter_contracts(base, q=q, status=status, disciplines=disciplines, jobs=jobs,
                               platforms=platforms).values('id')
    member_jobs = Job.objects.filter(contracts__in=dj_ids)
    discipline_counts = dict(member_jobs.values('discipline')
                             .annotate(c=Count('contracts', distinct=True)).values_list('discipline', 'c'))
    job_counts = dict(member_jobs.values('slug')
                      .annotate(c=Count('contracts', distinct=True)).values_list('slug', 'c'))
    return {'status': status_counts, 'platform': platform_counts,
            'discipline': discipline_counts, 'job': job_counts}


def suggest_relaxation(profile, disc_levels=None, q='', status='', disciplines=None, jobs=None, platforms=None):
    """When a filter combo returns nothing, find the single active filter whose removal yields the most
    results, so the empty state can say 'drop <label> to see N'. Returns {kind, value, label, count} or
    None. Only the removable dimensions are considered; ties break toward the biggest result set."""
    disciplines, jobs = list(disciplines or []), list(jobs or [])
    if platforms is None:                     # default board = current-gen (matches contracts_page), so the
        platforms = list(MODERN_PLATFORMS)    # other-filter candidate counts reflect the current-gen board
    base = annotated_contracts(profile, disc_levels)

    def count(**over):
        f = {'q': q, 'status': status, 'disciplines': disciplines, 'jobs': jobs, 'platforms': platforms}
        f.update(over)
        return _filter_contracts(base, **f).count()

    candidates = []
    job_names = dict(Job.objects.filter(slug__in=jobs).values_list('slug', 'name')) if jobs else {}
    for j in jobs:
        candidates.append(('job', j, job_names.get(j, j), count(jobs=[x for x in jobs if x != j])))
    for d in disciplines:
        candidates.append(('discipline', d, DISCIPLINE_LABELS.get(d, d), count(disciplines=[x for x in disciplines if x != d])))
    if status and status != 'all':
        candidates.append(('status', status, STATUS_LABELS.get(status, status), count(status='')))
    # Widen platforms to ALL: since we defaulted to current-gen above, this offers to reveal legacy/VR
    # contracts. The client mirrors this by lighting every platform chip.
    if set(platforms) != set(ALL_PLATFORMS):
        candidates.append(('platform', '', 'platform filter', count(platforms=list(ALL_PLATFORMS))))
    if q:
        candidates.append(('q', '', 'search', count(q='')))
    best = max((c for c in candidates if c[3] > 0), key=lambda c: c[3], default=None)
    return {'kind': best[0], 'value': best[1], 'label': best[2], 'count': best[3]} if best else None


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
