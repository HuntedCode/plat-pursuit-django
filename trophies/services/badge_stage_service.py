"""Generate a badge series' stages from its franchise / collection / developer (`generate_badge_stages`).

The rule a curator would apply by hand: one stage per ORIGIN game, holding every concept of that game (the
original plus its ports, remasters, remakes and editions, which `GameFamily` already folds together). A
stage is "one work" to the badge engine -- any of its games satisfies it, on any platform -- so this is
exactly the shape the engine expects.

Order:
  - Developer badges: by filing studio (the developer first, then its studios A-Z, then games no family
    studio developed; see `developer_family_service.filing_studio`; a plain rename files under the
    studio's current name, a merger keeps each predecessor), then origin title A-Z, so each studio's games
    sit together.
  - Every other badge: origin release date, oldest first (undated last), then origin title A-Z.

The candidates are `badge_coverage_service.series_candidates`, the same set the nightly audit checks, so a
freshly generated badge audits clean. The generator makes no per-game judgement: preview with dry-run, and
exclude unwanted franchise links (`ConceptFranchise.is_excluded`) before writing.

Bundles stay manual. Episodic games (several concepts that only count together) need a ConceptBundle, and
nothing in the data says which concepts are episodes, so a fresh run puts them in their origin's stage as
ordinary concepts, where any ONE of them satisfies the stage.

A series that already has stages is refused unless `append=True`. Appending only adds concepts: it never
renames, renumbers or removes what is there. An uncovered concept whose origin game already has a
stage (a new remaster of a game in stage 4) is ADDED to that stage, preferring a counting stage over a
bonus one. When that stage holds the origin only inside a BUNDLE, nothing is added and the stage is
reported for a curator instead: adding the concept directly would let it satisfy the stage on its own,
turning an all-episodes requirement into any-one-episode. Every other origin game becomes a new stage after
the last one. Appended stages land after the existing ones, so a developer badge's numbers only read in
studio order after a fresh run.
"""

from dataclasses import dataclass, field

from django.db import transaction
from django.db.models import Max, Prefetch

from trophies.models import Concept, Stage
from trophies.services.badge_coverage_service import covered_concept_ids, series_candidates
from trophies.services.developer_family_service import (
    concepts_filing_studio, developer_family_ids, is_developer_badge, studio_filing_map, studio_sort_key,
)

_TITLE_MAX = Stage._meta.get_field('title').max_length


class StagePlanError(Exception):
    """The series cannot be planned as asked (no slug, no subject, or stages exist without append)."""


@dataclass
class PlannedStage:
    """One planned change. A NEW stage has `stage_number` set. An addition to an existing stage has
    `existing` set; with `bundled` True it is NOT written, only reported for a curator (the origin lives in a
    bundle on that stage)."""
    title: str
    concepts: list
    studio: tuple | None = None
    stage_number: int | None = None
    existing: Stage | None = None
    bundled: bool = False


@dataclass
class _OriginGroup:
    key: tuple
    concepts: list = field(default_factory=list)


def _origin_queryset():
    """Concepts with just the fields origin grouping reads (never `raw_response`)."""
    return Concept.objects.select_related('family', 'igdb_match').only(
        'id', 'unified_title', 'release_date',
        'family__id', 'family__igdb_id', 'family__canonical_name', 'family__origin_name',
        'family__origin_first_release_date',
        'igdb_match__id', 'igdb_match__igdb_id', 'igdb_match__igdb_name', 'igdb_match__status',
        'igdb_match__igdb_first_release_date',
    )


def _trusted_match(concept):
    match = getattr(concept, 'igdb_match', None)
    return match if match is not None and match.is_trusted else None


def origin_key(concept):
    """The origin game a concept belongs to.

    The family's topmost IGDB id when it has one, so an unfamilied concept with a trusted match on that same
    id (the origin itself, enriched before its family existed) lands in the same group. Then the family row
    (admin-made families have no IGDB id), then the concept's own trusted IGDB id, then the concept alone.
    An untrusted match is ignored: grouping on a wrong match would fold an unrelated game into a stage.
    """
    family = concept.family
    if family is not None:
        return ('igdb', family.igdb_id) if family.igdb_id else ('family', family.pk)
    match = _trusted_match(concept)
    if match is not None:
        return ('igdb', match.igdb_id)
    return ('concept', concept.pk)


def _concept_title(concept):
    family = concept.family
    match = _trusted_match(concept)
    return (
        ((family.canonical_name or family.origin_name) if family else '')
        or (match.igdb_name if match else '')
        or concept.unified_title
        or ''
    )


def _group_title(group):
    # Prefer a familied concept's name: it is the ORIGIN's name, where a lone concept's name may be a port's.
    ordered = sorted(group.concepts, key=lambda c: (c.family is None, c.pk))
    title = next((t for t in map(_concept_title, ordered) if t), f'Concept {ordered[0].pk}')
    return title[:_TITLE_MAX]


def _group_date(group):
    dates = []
    for concept in group.concepts:
        match = _trusted_match(concept)
        date = (
            (concept.family.origin_first_release_date if concept.family else None)
            or (match.igdb_first_release_date if match else None)
            or concept.release_date
        )
        if date is not None:
            dates.append(date)
    return min(dates) if dates else None


def _existing_stage_by_origin(series_slug):
    """{origin key: (stage, bundled)} for the existing stage an uncovered concept of that origin should join.

    Preference when an origin sits in several stages: a counting stage over a bonus one (which never counts
    toward the badge), a DIRECT member over a bundled one, then the lowest stage number. `bundled` True means the
    chosen stage holds the origin only inside a ConceptBundle, or only as the list of a trophy-group member:
    either way the addition is reported for a curator, never written (writing the concept whole onto a stage
    that counts one of its groups would set two bars for one list, which StageAdminForm refuses).
    """
    stages = (
        Stage.objects.filter(series_slug=series_slug)
        .prefetch_related(
            Prefetch('concepts', queryset=_origin_queryset()),
            Prefetch('concept_bundles__concepts', queryset=_origin_queryset()),
            Prefetch('trophy_groups__concept', queryset=_origin_queryset()),
        )
    )
    best = {}
    for stage in stages:
        members = [(concept, False) for concept in stage.concepts.all()]
        for bundle in stage.concept_bundles.all():
            members.extend((concept, True) for concept in bundle.concepts.all())
        members.extend((ctg.concept, True) for ctg in stage.trophy_groups.all())
        for concept, bundled in members:
            rank = (stage.is_bonus, bundled, stage.stage_number)
            key = origin_key(concept)
            if key not in best or rank < best[key][0]:
                best[key] = (rank, stage, bundled)
    return {key: (stage, bundled) for key, (_, stage, bundled) in best.items()}


def plan_stages(series, append=False):
    """The stages to write for `series`, in order. Writes nothing.

    Raises StagePlanError when the series has no slug or no franchise/collection/developer, or already has
    stages and `append` is False. An empty list means there is nothing to add.
    """
    if not series.series_slug:
        raise StagePlanError(f'Series "{series.name}" has no series_slug, so stages cannot join to it.')
    if not (series.franchise_id or series.collection_id or series.developer_id):
        raise StagePlanError(
            f'Series "{series.name}" has no franchise, collection or developer to build stages from.'
        )
    existing_count = Stage.objects.filter(series_slug=series.series_slug).count()
    if existing_count and not append:
        raise StagePlanError(
            f'Series "{series.series_slug}" already has {existing_count} stage(s). Pass --append to add only '
            f'the games they do not cover yet.'
        )

    candidate_ids, credits = series_candidates(series)
    todo_ids = candidate_ids - covered_concept_ids(series.series_slug)
    groups = {}
    for concept in _origin_queryset().filter(id__in=todo_ids):
        key = origin_key(concept)
        groups.setdefault(key, _OriginGroup(key)).concepts.append(concept)

    existing_by_origin = _existing_stage_by_origin(series.series_slug) if existing_count else {}
    by_studio = is_developer_badge(series)
    filing_map = studio_filing_map(series.developer, developer_family_ids(series.developer)) if by_studio else {}

    additions, new_stages = [], []
    for group in groups.values():
        group.concepts.sort(key=lambda c: ((c.unified_title or '').lower(), c.pk))
        planned = PlannedStage(
            title=_group_title(group),
            concepts=group.concepts,
            studio=(
                concepts_filing_studio([c.pk for c in group.concepts], credits, filing_map, series.developer)
                if by_studio else None
            ),
        )
        if group.key in existing_by_origin:
            planned.existing, planned.bundled = existing_by_origin[group.key]
            additions.append(planned)
        else:
            new_stages.append((group, planned))

    if by_studio:
        def studio_order(gp):
            return (studio_sort_key(gp[1].studio, series.developer), gp[1].title.lower(), str(gp[0].key))
        new_stages.sort(key=studio_order)
    else:
        def chronological(gp):
            date = _group_date(gp[0])
            return (date is None, date.timestamp() if date else 0, gp[1].title.lower(), str(gp[0].key))
        new_stages.sort(key=chronological)

    last = Stage.objects.filter(series_slug=series.series_slug).aggregate(n=Max('stage_number'))['n'] or 0
    for offset, (_, planned) in enumerate(new_stages, start=1):
        planned.stage_number = last + offset

    additions.sort(key=lambda p: (p.existing.stage_number, p.title.lower()))
    return additions + [planned for _, planned in new_stages]


def write_stages(series, plan):
    """Write a `plan_stages` result in ONE transaction. Bundled additions are skipped (reported only).

    A stage is its concepts: a row committed without its `concepts.set()` would be a stage that gates on
    nothing.
    """
    with transaction.atomic():
        for planned in plan:
            if planned.bundled:
                continue
            if planned.existing is not None:
                planned.existing.concepts.add(*planned.concepts)
                continue
            stage = Stage.objects.create(
                series_slug=series.series_slug,
                stage_number=planned.stage_number,
                title=planned.title,
            )
            stage.concepts.set(planned.concepts)
