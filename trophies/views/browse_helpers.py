"""Shared helpers for browse page views (Games, Genre/Theme Detail, Flagged Games)."""

from datetime import timedelta

from django.db.models import (
    Q, F, Subquery, OuterRef, Value, IntegerField, FloatField, Avg, Case,
    When, Count, OrderBy, Exists,
)
from django.db.models.functions import Coalesce, Lower, Cast
from django.utils import timezone

from trophies.models import (
    Badge, Trophy, UserConceptRating, Stage, ConceptGenre, ConceptTheme,
    ConceptEngine,
)


def get_badge_picker_context(request):
    """Build context dict for the browse badge picker modal.

    Returns picker_badges (list of dicts) and selected_badge_name (str).
    """
    badges = Badge.objects.filter(
        is_live=True, tier=1, series_slug__isnull=False,
    ).exclude(
        series_slug='',
    ).select_related('base_badge').order_by('display_series', 'name')

    picker_badges = []
    for b in badges:
        picker_badges.append({
            'series_slug': b.series_slug,
            'name': b.name,
            'display_series': b.display_series,
            'badge_type': b.badge_type,
            'earned_count': b.earned_count,
            'required_stages': b.required_stages,
            'layers': b.get_badge_layers(),
        })

    selected_slug = request.GET.get('badge_series', '')
    selected_name = ''
    if selected_slug:
        match = next(
            (b for b in picker_badges if b['series_slug'] == selected_slug),
            None,
        )
        if match:
            selected_name = match['display_series'] or match['name']

    return {
        'picker_badges': picker_badges,
        'selected_badge_name': selected_name,
    }


# ---------------------------------------------------------------------------
# Shared Game Browse Filter / Sort Pipeline
# ---------------------------------------------------------------------------

def annotate_ascii_name(qs):
    """Add is_ascii_name annotation for ASCII-first secondary sorting."""
    return qs.annotate(
        is_ascii_name=Case(
            When(title_name__regex=r'^[A-Za-z0-9]', then=Value(0)),
            default=Value(1),
            output_field=IntegerField(),
        ),
    )


_ALPHA_SECONDARY = ['is_ascii_name', Lower('title_name')]


def annotate_community_ratings(qs, concept_ref_path='concept_id'):
    """Add _avg_rating, _avg_difficulty, _avg_fun annotations to any queryset.

    Args:
        qs: Any Django queryset whose model has a path to ``Concept``.
        concept_ref_path: The OuterRef path to the concept_id field.
            - ``'concept_id'`` for Game querysets
            - ``'game__concept_id'`` for ProfileGame querysets
            - ``'trophy__game__concept_id'`` for EarnedTrophy querysets

    Returns:
        The queryset with the three rating annotations added.
    """
    base_ratings = UserConceptRating.objects.filter(
        concept_id=OuterRef(concept_ref_path),
        concept_trophy_group__isnull=True,
    )
    return qs.annotate(
        _avg_rating=Subquery(
            base_ratings.values('concept_id').annotate(
                val=Avg('overall_rating'),
            ).values('val')[:1],
            output_field=FloatField(),
        ),
        _avg_difficulty=Subquery(
            base_ratings.values('concept_id').annotate(
                val=Avg('difficulty'),
            ).values('val')[:1],
            output_field=FloatField(),
        ),
        _avg_fun=Subquery(
            base_ratings.values('concept_id').annotate(
                val=Avg('fun_ranking'),
            ).values('val')[:1],
            output_field=FloatField(),
        ),
    )


def apply_game_browse_filters(qs, form, sort_val=''):
    """Apply all GameSearchForm filters to a Game queryset.

    Args:
        qs: Base Game queryset (may already be filtered by tag/category).
        form: A *valid* GameSearchForm instance.
        sort_val: The selected sort value (needed to decide whether
                  rating annotations are required for sorting).

    Returns:
        (qs, annotations_applied): The filtered queryset and a set of strings
        indicating which annotation groups were applied (e.g. ``{'ratings'}``).
    """
    annotations_applied = set()

    qs = annotate_ascii_name(qs)

    # --- Text search ---
    query = form.cleaned_data.get('query')
    if query:
        from trophies.util_modules.roman_numerals import expand_numeral_query
        query_variants = expand_numeral_query(query)
        q_filter = Q()
        for variant in query_variants:
            q_filter |= Q(title_name__icontains=variant)
        qs = qs.filter(q_filter)

    # --- Platform / Region / Letter ---
    platforms = form.cleaned_data.get('platform')
    if platforms:
        qs = qs.for_platform(platforms)
    regions = form.cleaned_data.get('regions')
    if regions:
        qs = qs.for_region(regions)
    letter = form.cleaned_data.get('letter')
    if letter:
        if letter == '0-9':
            qs = qs.filter(title_name__regex=r'^[0-9]')
        else:
            qs = qs.filter(title_name__istartswith=letter)

    # --- Quick filters ---
    # Each multi-relation filter below uses Exists() instead of
    # .filter(...).distinct(). Stacking .distinct() over chained joins on
    # large datasets explodes the planner; Exists short-circuits at the
    # first matching row and keeps the outer cardinality at one row per
    # Game.
    if form.cleaned_data.get('show_only_platinum'):
        qs = qs.filter(Exists(
            Trophy.objects.filter(game=OuterRef('pk'), trophy_type='platinum')
        ))
    if form.cleaned_data.get('filter_shovelware'):
        qs = qs.exclude(shovelware_status__in=['auto_flagged', 'manually_flagged'])

    badge_series = form.cleaned_data.get('badge_series')
    if badge_series:
        live_slugs = Badge.objects.filter(
            is_live=True,
        ).values_list('series_slug', flat=True)
        qs = qs.filter(Exists(
            Stage.objects.filter(
                concepts=OuterRef('concept_id'),
                series_slug=badge_series,
                series_slug__in=live_slugs,
            )
        ))
    elif form.cleaned_data.get('in_badge'):
        live_slugs = Badge.objects.filter(
            is_live=True,
        ).values_list('series_slug', flat=True)
        qs = qs.filter(Exists(
            Stage.objects.filter(
                concepts=OuterRef('concept_id'),
                series_slug__in=live_slugs,
            )
        ))

    # --- Community flag filters (hide wins on conflict) ---
    if form.cleaned_data.get('hide_delisted'):
        qs = qs.filter(is_delisted=False)
    elif form.cleaned_data.get('show_delisted'):
        qs = qs.filter(is_delisted=True)
    if form.cleaned_data.get('hide_unobtainable'):
        qs = qs.filter(is_obtainable=True)
    elif form.cleaned_data.get('show_unobtainable'):
        qs = qs.filter(is_obtainable=False)
    if form.cleaned_data.get('hide_online'):
        qs = qs.filter(has_online_trophies=False)
    elif form.cleaned_data.get('show_online'):
        qs = qs.filter(has_online_trophies=True)
    if form.cleaned_data.get('hide_buggy'):
        qs = qs.filter(has_buggy_trophies=False)
    elif form.cleaned_data.get('show_buggy'):
        qs = qs.filter(has_buggy_trophies=True)

    # --- Community rating filters (dual-range sliders) ---
    rating_min = form.cleaned_data.get('rating_min') or 0
    rating_max = form.cleaned_data.get('rating_max') or 5
    diff_min = form.cleaned_data.get('difficulty_min') or 1
    diff_max = form.cleaned_data.get('difficulty_max') or 10
    fun_lo = form.cleaned_data.get('fun_min') or 1
    fun_hi = form.cleaned_data.get('fun_max') or 10
    has_rating_filter = (
        rating_min > 0 or rating_max < 5
        or diff_min > 1 or diff_max < 10
        or fun_lo > 1 or fun_hi < 10
    )
    needs_rating_annotation = has_rating_filter or sort_val in (
        'rating', 'rating_inv', 'difficulty', 'difficulty_inv', 'fun', 'fun_inv',
    )

    if needs_rating_annotation:
        qs = annotate_community_ratings(qs, concept_ref_path='concept_id')
        annotations_applied.add('ratings')

        if rating_min > 0:
            qs = qs.filter(_avg_rating__gte=float(rating_min))
        if rating_max < 5:
            qs = qs.filter(_avg_rating__lte=float(rating_max))
        if diff_min > 1:
            qs = qs.filter(_avg_difficulty__gte=float(diff_min))
        if diff_max < 10:
            qs = qs.filter(_avg_difficulty__lte=float(diff_max))
        if fun_lo > 1:
            qs = qs.filter(_avg_fun__gte=float(fun_lo))
        if fun_hi < 10:
            qs = qs.filter(_avg_fun__lte=float(fun_hi))

    # --- Time-to-beat filters (dual-range sliders, in hours) ---
    igdb_lo = form.cleaned_data.get('igdb_time_min') or 0
    igdb_hi = form.cleaned_data.get('igdb_time_max') or 1000
    if igdb_lo > 0 or igdb_hi < 1000:
        # Only apply time filtering against trusted matches — pending/rejected
        # matches still have time_to_beat populated but the values haven't
        # been reviewed, so they shouldn't drive the browse filter.
        time_q = Q(
            concept__igdb_match__time_to_beat_completely__isnull=False,
            concept__igdb_match__status__in=('accepted', 'auto_accepted'),
        )
        if igdb_lo > 0:
            time_q &= Q(concept__igdb_match__time_to_beat_completely__gte=int(igdb_lo) * 3600)
        if igdb_hi < 1000:
            time_q &= Q(concept__igdb_match__time_to_beat_completely__lte=int(igdb_hi) * 3600)
        qs = qs.filter(time_q)

    comm_lo = form.cleaned_data.get('community_time_min') or 0
    comm_hi = form.cleaned_data.get('community_time_max') or 1000
    if comm_lo > 0 or comm_hi < 1000:
        avg_hours_sq = UserConceptRating.objects.filter(
            concept_id=OuterRef('concept_id'),
            concept_trophy_group__isnull=True,
        ).values('concept_id').annotate(
            val=Avg('hours_to_platinum'),
        ).values('val')[:1]
        qs = qs.annotate(
            _community_hours=Subquery(avg_hours_sq, output_field=FloatField()),
        )
        annotations_applied.add('community_hours')
        hours_q = Q(_community_hours__isnull=False)
        if comm_lo > 0:
            hours_q &= Q(_community_hours__gte=float(comm_lo))
        if comm_hi < 1000:
            hours_q &= Q(_community_hours__lte=float(comm_hi))
        qs = qs.filter(hours_q)

    # --- Genre / Theme / Engine filters (Exists, see note above) ---
    genres = form.cleaned_data.get('genres')
    if genres:
        qs = qs.filter(Exists(
            ConceptGenre.objects.filter(
                concept_id=OuterRef('concept_id'), genre_id__in=genres,
            )
        ))
    themes = form.cleaned_data.get('themes')
    if themes:
        qs = qs.filter(Exists(
            ConceptTheme.objects.filter(
                concept_id=OuterRef('concept_id'), theme_id__in=themes,
            )
        ))
    engine = form.cleaned_data.get('engine')
    if engine:
        qs = qs.filter(Exists(
            ConceptEngine.objects.filter(
                concept_id=OuterRef('concept_id'), engine_id=engine,
            )
        ))

    return qs, annotations_applied


def apply_game_browse_sort(qs, sort_val, annotations_applied=None):
    """Apply sort ordering to a Game queryset.

    Args:
        qs: Filtered Game queryset (must already have ``is_ascii_name``
            annotation from ``apply_game_browse_filters`` or
            ``annotate_ascii_name``).
        sort_val: The sort key from ``GameSearchForm.cleaned_data['sort']``.
        annotations_applied: Set of annotation group names already on
            the queryset (from ``apply_game_browse_filters``).

    Returns:
        (qs, order_by_list): Queryset (possibly with new annotations)
        and the list to pass to ``.order_by()``.
    """
    if annotations_applied is None:
        annotations_applied = set()

    order = list(_ALPHA_SECONDARY)

    # --- Popularity ---
    if sort_val == 'played':
        order = ['-played_count'] + _ALPHA_SECONDARY
    elif sort_val == 'played_inv':
        order = ['played_count'] + _ALPHA_SECONDARY

    # --- Platinum counts (conditional annotation) ---
    elif sort_val in ('plat_earned', 'plat_earned_inv', 'plat_rate', 'plat_rate_inv'):
        platinums_earned = Subquery(
            Trophy.objects.filter(
                game=OuterRef('pk'), trophy_type='platinum',
            ).values('earned_count')[:1],
        )
        platinums_rate = Subquery(
            Trophy.objects.filter(
                game=OuterRef('pk'), trophy_type='platinum',
            ).values('earn_rate')[:1],
        )
        qs = qs.annotate(
            platinums_earned_count=Coalesce(
                platinums_earned, Value(0), output_field=IntegerField(),
            ),
            platinums_earn_rate=Coalesce(
                platinums_rate, Value(0.0), output_field=FloatField(),
            ),
        )
        if sort_val == 'plat_earned':
            order = ['-platinums_earned_count'] + _ALPHA_SECONDARY
        elif sort_val == 'plat_earned_inv':
            order = ['platinums_earned_count'] + _ALPHA_SECONDARY
        elif sort_val == 'plat_rate':
            order = ['-platinums_earn_rate'] + _ALPHA_SECONDARY
        elif sort_val == 'plat_rate_inv':
            order = ['platinums_earn_rate'] + _ALPHA_SECONDARY

    # --- Community ratings ---
    elif sort_val == 'rating' and 'ratings' in annotations_applied:
        qs = qs.filter(_avg_rating__isnull=False)
        order = ['-_avg_rating'] + _ALPHA_SECONDARY
    elif sort_val == 'rating_inv' and 'ratings' in annotations_applied:
        qs = qs.filter(_avg_rating__isnull=False)
        order = ['_avg_rating'] + _ALPHA_SECONDARY
    elif sort_val == 'difficulty' and 'ratings' in annotations_applied:
        qs = qs.filter(_avg_difficulty__isnull=False)
        order = ['-_avg_difficulty'] + _ALPHA_SECONDARY
    elif sort_val == 'difficulty_inv' and 'ratings' in annotations_applied:
        qs = qs.filter(_avg_difficulty__isnull=False)
        order = ['_avg_difficulty'] + _ALPHA_SECONDARY
    elif sort_val == 'fun' and 'ratings' in annotations_applied:
        qs = qs.filter(_avg_fun__isnull=False)
        order = ['-_avg_fun'] + _ALPHA_SECONDARY
    elif sort_val == 'fun_inv' and 'ratings' in annotations_applied:
        qs = qs.filter(_avg_fun__isnull=False)
        order = ['_avg_fun'] + _ALPHA_SECONDARY

    # --- Trophy count (sum of defined_trophies JSONField) ---
    elif sort_val in ('trophy_count', 'trophy_count_inv'):
        qs = qs.annotate(
            _total_trophy_count=(
                Coalesce(Cast(F('defined_trophies__bronze'), IntegerField()), Value(0))
                + Coalesce(Cast(F('defined_trophies__silver'), IntegerField()), Value(0))
                + Coalesce(Cast(F('defined_trophies__gold'), IntegerField()), Value(0))
                + Coalesce(Cast(F('defined_trophies__platinum'), IntegerField()), Value(0))
            ),
        )
        if sort_val == 'trophy_count':
            order = ['-_total_trophy_count'] + _ALPHA_SECONDARY
        else:
            order = ['_total_trophy_count'] + _ALPHA_SECONDARY

    # --- Time-to-beat (IGDB completion time, nulls last). Gate on
    # is_trusted so untrusted matches drop to the end of the sort rather
    # than mingling with reviewed times.
    elif sort_val in ('time_to_beat', 'time_to_beat_inv'):
        qs = qs.annotate(
            _time_to_beat=Case(
                When(
                    concept__igdb_match__status__in=('accepted', 'auto_accepted'),
                    then=F('concept__igdb_match__time_to_beat_completely'),
                ),
                default=None,
                output_field=IntegerField(),
            ),
        )
        if sort_val == 'time_to_beat':
            order = [
                OrderBy(F('_time_to_beat'), nulls_last=True),
            ] + _ALPHA_SECONDARY
        else:
            order = [
                OrderBy(F('_time_to_beat'), descending=True, nulls_last=True),
            ] + _ALPHA_SECONDARY

    # --- Trending (recent trophy activity in last 30 days) ---
    elif sort_val == 'trending':
        thirty_days_ago = timezone.now() - timedelta(days=30)
        qs = qs.annotate(
            _trending_count=Count(
                'played_by',
                filter=Q(played_by__most_recent_trophy_date__gte=thirty_days_ago),
            ),
        )
        order = ['-_trending_count', '-played_count'] + _ALPHA_SECONDARY

    # --- Release date (from Concept, nulls last) ---
    elif sort_val in ('release_date', 'release_date_inv'):
        qs = qs.annotate(
            _release_date=F('concept__release_date'),
        )
        if sort_val == 'release_date':
            order = [
                OrderBy(F('_release_date'), descending=True, nulls_last=True),
            ] + _ALPHA_SECONDARY
        else:
            order = [
                OrderBy(F('_release_date'), nulls_last=True),
            ] + _ALPHA_SECONDARY

    # --- Date added to site ---
    elif sort_val == 'newest':
        order = ['-created_at'] + _ALPHA_SECONDARY
    elif sort_val == 'oldest':
        order = ['created_at'] + _ALPHA_SECONDARY

    return qs, order
