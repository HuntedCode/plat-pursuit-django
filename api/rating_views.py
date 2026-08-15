"""Rating API views.

The structured game-rating system (difficulty / grindiness / hours /
fun / overall, per concept + optional DLC trophy group) lives here,
fully independent of the text-review system. Reviews were archived in
2026-05; ratings are kept because they're simple, self-contained, and
widely consumed (game detail, dashboard, share cards, milestones).

Endpoints are mounted under `/api/v1/ratings/` (NOT the historical
`/reviews/` prefix the rating endpoints used to share with reviews).

Business logic lives in RatingService + ConceptTrophyGroupService.
"""
import logging

from django.db.models import Max, Sum
from django.db.models.functions import Lower
from django.utils.decorators import method_decorator
from django_ratelimit.decorators import ratelimit
from rest_framework import status
from rest_framework.authentication import SessionAuthentication, TokenAuthentication
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from trophies.models import Concept, ConceptTrophyGroup
from api.utils import safe_bool, safe_int

logger = logging.getLogger('psn_api')


def _get_profile_or_error(request):
    """Return (profile, None) or (None, Response)."""
    profile = getattr(request.user, 'profile', None)
    if not profile:
        return None, Response(
            {'error': 'Linked profile required.'},
            status=status.HTTP_403_FORBIDDEN,
        )
    return profile, None


def _get_concept_and_group(concept_id, group_id_str):
    """Resolve Concept + ConceptTrophyGroup from URL params.

    Shovelware is intentionally NOT gated here: a shovelware platinum is
    still a real platinum, and a "yes this is shovelware, here's how grindy
    it was" rating is useful signal. The Rate My Games wizard hides
    shovelware from its queue by default (opt-in via a toggle), but any
    surface that already knows which game/group to rate (share card prompt,
    game-detail Quick Rate) may submit a shovelware rating freely.

    The base ('default') group is auto-created when missing (a freshly
    synced concept may not have it yet); other groups must already exist.

    Returns:
        (concept, ctg, None) on success
        (None, None, Response) on error
    """
    try:
        concept = Concept.objects.get(id=concept_id)
    except Concept.DoesNotExist:
        return None, None, Response(
            {'error': 'Concept not found.'},
            status=status.HTTP_404_NOT_FOUND,
        )

    if group_id_str == 'default':
        ctg, _ = ConceptTrophyGroup.objects.get_or_create(
            concept=concept,
            trophy_group_id='default',
            defaults={'display_name': 'Base Game', 'sort_order': 0},
        )
    else:
        try:
            ctg = ConceptTrophyGroup.objects.get(
                concept=concept, trophy_group_id=group_id_str,
            )
        except ConceptTrophyGroup.DoesNotExist:
            return None, None, Response(
                {'error': 'Trophy group not found.'},
                status=status.HTTP_404_NOT_FOUND,
            )

    return concept, ctg, None


class GroupRatingView(APIView):
    """Submit or update a rating for a concept trophy group (base game or DLC)."""
    authentication_classes = [SessionAuthentication, TokenAuthentication]
    permission_classes = [IsAuthenticated]

    @method_decorator(ratelimit(key='user', rate='30/m', method='POST', block=True))
    def post(self, request, concept_id, group_id):
        """
        POST /api/v1/ratings/<concept_id>/group/<group_id>/rate/
        Body: {difficulty, grindiness, hours_to_platinum, fun_ranking, overall_rating}
        """
        try:
            profile, err = _get_profile_or_error(request)
            if err:
                return err

            concept, ctg, err = _get_concept_and_group(concept_id, group_id)
            if err:
                return err

            from trophies.services.concept_trophy_group_service import ConceptTrophyGroupService

            can, reason = ConceptTrophyGroupService.can_rate_group(profile, concept, ctg)
            if not can:
                return Response({'error': reason}, status=status.HTTP_403_FORBIDDEN)

            from trophies.models import UserConceptRating
            from trophies.forms import UserConceptRatingForm
            from trophies.services.rating_service import RatingService, concepts_defining_a_platinum

            # Base game (trophy_group_id='default'): concept_trophy_group=None
            # for backward compat. DLC groups store the FK.
            ctg_fk = None if ctg.trophy_group_id == 'default' else ctg

            # Whether the middle option reads "tough plat" or "tough trophies", by the same rule the
            # form's own copy used: a base group whose concept actually defines a platinum. Short-circuits
            # on a DLC pack, so this costs nothing on that half.
            has_plat = ctg_fk is None and concept.id in concepts_defining_a_platinum({concept.id})

            existing_rating = UserConceptRating.objects.filter(
                profile=profile,
                concept=concept,
                concept_trophy_group=ctg_fk,
            ).first()

            # The blurb is an optional field on a shared form: a rating update that omits it (e.g. a
            # numbers-only "adjust my rating") must NOT wipe an existing quick take. Track whether the
            # caller actually submitted a blurb, and preserve the stored one when they didn't.
            blurb_submitted = 'blurb' in request.data
            preserved_blurb = existing_rating.blurb if existing_rating else ''

            # The recommendation gets the SAME protection, for the same reason: "adjust my hours" must not
            # be able to destroy an answer it never mentioned. It is required, so an omission on a NEW
            # rating is still rejected -- but on an existing one the stored value stands in, which is the
            # difference between a partial update and a partial wipe.
            #
            # Injected BEFORE validation rather than restored after, because unlike the blurb this field is
            # required: leaving it absent would fail `is_valid()` and never reach a restore step.
            data = request.data
            if 'recommendation' not in data and existing_rating and existing_rating.recommendation:
                data = data.copy()
                data['recommendation'] = existing_rating.recommendation

            form = UserConceptRatingForm(data, instance=existing_rating)
            if not form.is_valid():
                return Response(
                    {'success': False, 'errors': form.errors},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            # Publishing a public quick take requires the same community-guidelines agreement every other
            # UGC surface enforces (comments gate on it via can_comment). A numbers-only rating never does.
            if blurb_submitted and form.cleaned_data.get('blurb') and not profile.guidelines_agreed:
                return Response(
                    {'success': False, 'needs_guidelines': True,
                     'error': 'Please agree to the community guidelines to post a quick take.'},
                    status=status.HTTP_403_FORBIDDEN,
                )

            rating = form.save(commit=False)
            rating.profile = profile
            rating.concept = concept
            rating.concept_trophy_group = ctg_fk
            if not blurb_submitted:
                rating.blurb = preserved_blurb   # an omitted field must not blank an existing quick take
            rating.save()

            RatingService.invalidate_cache(concept)
            RatingService.invalidate_group_cache(concept, ctg)

            updated_averages = RatingService.get_community_averages_for_group(concept, ctg)

            return Response({
                'success': True,
                'message': 'Rating updated!' if existing_rating else 'Rating submitted successfully!',
                'community_averages': updated_averages,
                'blurb': rating.blurb,   # sanitized/stored value, so the client's live card matches on reload
                'recommendation': rating.recommendation,
                # The LABEL comes from the server. Every other word the ratings JS prints has a Python twin
                # it mirrors (rating_verdict, rating_summary, rating_tone), but a choices label has no such
                # function -- mirroring it would mean hardcoding four display strings in JS that drift the
                # first time anyone rewords one here.
                # `recommendation_label`, NOT get_recommendation_display: the middle option names what was
                # rough, and `get_recommendation_display` always says "platinum". This view serves DLC
                # packs (ctg_fk is set) and 100%-no-platinum games, where the radio the hunter just
                # clicked said "trophies" -- echoing "platinum" back would contradict the form. Same
                # `is_base and concept-defines-one` rule the form's own copy was built from.
                'recommendation_label': rating.recommendation_label(has_platinum=has_plat),
            })

        except Exception as e:
            logger.exception(f"Group rating error: {e}")
            return Response(
                {'error': 'Internal error.'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class BlurbReportView(APIView):
    """Report a rating's public 'quick take' blurb for moderation (reactive: publish -> report -> staff hide)."""
    authentication_classes = [SessionAuthentication, TokenAuthentication]
    permission_classes = [IsAuthenticated]

    @method_decorator(ratelimit(key='user', rate='10/m', method='POST', block=True))
    def post(self, request, rating_id):
        """POST /api/v1/ratings/blurb/<rating_id>/report/  Body: {reason, details?}"""
        try:
            profile, err = _get_profile_or_error(request)
            if err:
                return err

            from trophies.models import UserConceptRating, BlurbReport
            from trophies.services.comment_service import CommentService

            can, reason_msg = CommentService.can_interact(profile)   # linked-profile gate (shared with comments)
            if not can:
                return Response({'error': reason_msg}, status=status.HTTP_403_FORBIDDEN)

            # Only a live (present + not-already-hidden) blurb is reportable.
            rating = (UserConceptRating.objects.filter(id=rating_id, blurb_hidden=False)
                      .exclude(blurb='').first())
            if not rating:
                return Response({'error': 'Blurb not found.'}, status=status.HTTP_404_NOT_FOUND)
            if rating.profile_id == profile.id:
                return Response({'error': "You can't report your own quick take."},
                                status=status.HTTP_400_BAD_REQUEST)

            valid_reasons = {c[0] for c in BlurbReport.REPORT_REASONS}
            reason_code = request.data.get('reason') if request.data.get('reason') in valid_reasons else 'other'
            details = (request.data.get('details') or '')[:500]

            _report, created = BlurbReport.objects.get_or_create(
                rating=rating, reporter=profile,
                defaults={'reason': reason_code, 'details': details},
            )
            msg = 'Thanks -- our team will take a look.' if created else "You've already reported this."
            return Response({'success': True, 'message': msg})

        except Exception as e:
            logger.exception(f"Blurb report error: {e}")
            return Response({'error': 'Internal error.'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class WizardQueueView(APIView):
    """Queue of ratable games waiting to be rated for the Rate My Games wizard.

    Ratings-only (the review half was removed when reviews were archived).
    """
    authentication_classes = [SessionAuthentication, TokenAuthentication]
    permission_classes = [IsAuthenticated]

    def get(self, request):
        """
        GET /api/v1/ratings/wizard/queue/?queue_type=base|dlc&limit=20&offset=0

        queue_type=base: base game concepts missing a rating.
        queue_type=dlc: DLC groups (missing a rating) grouped by parent concept.
        """
        try:
            profile, err = _get_profile_or_error(request)
            if err:
                return err

            from trophies.models import EarnedTrophy, UserConceptRating
            from trophies.services.review_hub_service import ReviewHubService

            queue_type = request.query_params.get('queue_type', 'base')
            if queue_type not in ('base', 'dlc'):
                queue_type = 'base'
            limit = min(safe_int(request.query_params.get('limit', 20), 20), 50)
            offset = max(safe_int(request.query_params.get('offset', 0), 0), 0)
            include_shovelware = safe_bool(request.query_params.get('include_shovelware'))

            # All ratable concept IDs (platinumed + 100% non-plat). Shovelware
            # is excluded unless the user opts in via the wizard toggle.
            ratable_concept_ids = ReviewHubService.get_ratable_concept_ids(
                profile, include_shovelware=include_shovelware,
            )

            if not ratable_concept_ids:
                if queue_type == 'dlc':
                    return Response({'groups': [], 'total_items': 0, 'has_more': False})
                return Response({'queue': [], 'count': 0, 'has_more': False})

            if queue_type == 'dlc':
                return self._get_dlc_queue(
                    profile, ratable_concept_ids, limit, offset,
                    include_shovelware=include_shovelware,
                )

            # ── Base game queue ──────────────────────────────────────── #
            # "Rated" means COMPLETE -- a row that carries a recommendation -- not merely a row that
            # exists. Every rating written before the recommendation shipped therefore comes back through
            # here exactly once, which is the backfill: it collects the recommendation and hands the
            # hunter one opportunity to add a quick take, without the take ever gating anything.
            # The definition lives in ReviewHubService so the queue, the meter and the header cannot
            # drift apart (they were nine separate copies of it).
            #
            # SCALAR ids only. A hunter's whole rating set can run to thousands of rows, and this
            # runs again on every scroll page -- materializing ORM instances (7 scalars, a 140-char
            # blurb and two datetimes each) to answer two membership tests is the CLAUDE.md whale
            # anti-pattern. The full rows are fetched below for the <=50 concepts on THIS page, which
            # are the only ones whose prefill is ever read.
            base_scope = dict(
                profile=profile,
                concept_id__in=ratable_concept_ids,
                concept_trophy_group__isnull=True,
            )
            any_rated_ids = set(
                UserConceptRating.objects.filter(**base_scope)
                .values_list('concept_id', flat=True)
            )
            rated_concept_ids = set(
                ReviewHubService.complete_ratings(**base_scope)
                .values_list('concept_id', flat=True)
            )

            wanted_ids = [cid for cid in ratable_concept_ids if cid not in rated_concept_ids]

            # Fetch instances (not .values()) so cover_url's IGDB-first chain
            # works for anchored concepts (empty concept_icon_url).
            concepts = list(
                Concept.objects.filter(id__in=wanted_ids)
                .select_related('igdb_match')
                .defer('igdb_match__raw_response')
                .order_by(Lower('unified_title'))
            )
            # NEVER-RATED FIRST, then the recommendation backlog. A hunter with three new games and three
            # hundred old ratings must not have the new ones buried behind the backlog -- and the backlog
            # items are the fast ones (everything is prefilled, one tap), so they lose nothing by
            # following. Stable within each half: the title ordering above is preserved.
            concepts.sort(key=lambda c: c.id in any_rated_ids)

            total_count = len(concepts)
            paginated = concepts[offset:offset + limit]
            has_more = (offset + limit) < total_count

            paginated_ids = [c.id for c in paginated]

            # The prefill rows, bounded to this page -- see the note on `any_rated_ids` above. Only a
            # re-served rating has one, so this is empty for a hunter with no backlog.
            existing = {
                r.concept_id: r
                for r in UserConceptRating.objects.filter(
                    profile=profile,
                    concept_id__in=[cid for cid in paginated_ids if cid in any_rated_ids],
                    concept_trophy_group__isnull=True,
                )
            }

            # Pre-fetch user's gameplay stats for these concepts
            from trophies.models import ProfileGame
            game_stats = {}
            for row in ProfileGame.objects.filter(
                profile=profile,
                game__concept_id__in=paginated_ids,
            ).values('game__concept_id').annotate(
                max_progress=Max('progress'),
                total_earned=Sum('earned_trophies_count'),
                total_unearned=Sum('unearned_trophies_count'),
                total_play=Sum('play_duration'),
            ):
                cid = row['game__concept_id']
                hours = None
                if row['total_play']:
                    hours = int(row['total_play'].total_seconds()) // 3600
                earned = row['total_earned'] or 0
                unearned = row['total_unearned'] or 0
                game_stats[cid] = {
                    'progress': row['max_progress'] or 0,
                    'earned_trophies': earned,
                    'total_trophies': earned + unearned,
                    'play_hours': hours,
                }

            plat_dates = {}
            for et in EarnedTrophy.objects.filter(
                profile=profile,
                earned=True,
                trophy__trophy_type='platinum',
                trophy__game__concept_id__in=paginated_ids,
            ).values('trophy__game__concept_id', 'earned_date_time'):
                cid = et['trophy__game__concept_id']
                dt = et['earned_date_time']
                if dt and (cid not in plat_dates or dt > plat_dates[cid]):
                    plat_dates[cid] = dt

            from trophies.models import Trophy, Game
            concepts_with_plat = set(
                Trophy.objects.filter(
                    game__concept_id__in=paginated_ids,
                    trophy_type='platinum',
                ).values_list('game__concept_id', flat=True).distinct()
            )

            # Concepts that have at least one non-shovelware game. When the
            # user opted into shovelware, anything NOT in this set only shows
            # because of that opt-in, so we badge it. (Skip the query when the
            # opt-in is off: nothing in the queue can be shovelware then.)
            shovelware_concept_ids = set()
            if include_shovelware:
                clean_concept_ids = set(
                    Game.objects.filter(concept_id__in=paginated_ids)
                    .exclude(shovelware_status__in=['auto_flagged', 'manually_flagged'])
                    .values_list('concept_id', flat=True).distinct()
                )
                shovelware_concept_ids = set(paginated_ids) - clean_concept_ids

            queue = []
            for c in paginated:
                cid = c.id
                has_plat = cid in concepts_with_plat
                item = {
                    'concept_id': cid,
                    'unified_title': c.unified_title,
                    # The COVER is the wizard header's only artwork -- it carried the concept's landscape
                    # image too, as a wash behind the text, and that was dropped for reading as noise
                    # behind the question being asked. Nothing consumed the field once the wash went.
                    'concept_icon_url': c.cover_url or '',
                    'slug': c.slug,
                    'has_rating': cid in existing,
                    'trophy_group_id': 'default',
                    'trophy_group_name': 'Base Game',
                    'hours_label': 'Hours to Platinum' if has_plat else 'Hours to Complete',
                    # The recommendation's middle option names what was rough, so it follows the same
                    # has-platinum fact the hours label does.
                    **UserConceptRating.recommendation_copy(has_plat),
                    'is_shovelware': cid in shovelware_concept_ids,
                }
                # A re-served rating MUST arrive with its own scores. The form's defaults are 5/5/5/3.0,
                # so a card that loads blank and is submitted for its recommendation silently overwrites a
                # considered 8/9/2/4.5 with mush -- and the hunter has no way to notice. This is why the
                # queue sends the row rather than just a flag, and why the prefill branch that was deleted
                # when the queue served only fresh games has to come back with it.
                prior = existing.get(cid)
                if prior:
                    item['existing'] = prior.as_prefill()
                    item['existing_blurb'] = prior.blurb
                    item['rated_at'] = prior.updated_at.isoformat()
                if cid in game_stats:
                    item['stats'] = game_stats[cid]
                if cid in plat_dates:
                    item['platinum_date'] = plat_dates[cid].isoformat()
                queue.append(item)

            return Response({
                'queue': queue,
                'count': total_count,
                'has_more': has_more,
                'next_offset': offset + limit,
                # The wizard's progress meter measures the hunter's LIBRARY, not this queue. The queue is
                # unrated-only, so a meter denominated in it shrinks by one every time you rate something
                # and can never fill. `ratable_total` is every game they could rate and `rated_total` is
                # how many they already have -- both already computed above, so this costs nothing.
                'ratable_total': len(ratable_concept_ids),
                'rated_total': len(rated_concept_ids),
            })

        except Exception as e:
            logger.exception(f"Wizard queue error: {e}")
            return Response(
                {'error': 'Failed to load game queue.'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    def _get_dlc_queue(self, profile, ratable_concept_ids, limit, offset,
                       include_shovelware=False):
        """Build the DLC rating queue grouped by parent concept (ratings-only)."""
        from collections import OrderedDict
        from django.db.models import Count
        from trophies.models import Trophy, EarnedTrophy, UserConceptRating, Game
        from trophies.services.review_hub_service import ReviewHubService

        # `raw_response` is the ~30 KB IGDB API blob and nothing here reads it. Without the defer it rides
        # along on every row of a list() that spans EVERY ratable concept's DLC groups before any
        # pagination -- for a hunter with a four-figure completed library that is tens of MB of JSON
        # loaded to answer a 20-item page. (The base-game path above already defers it.)
        all_dlc_groups = list(
            ConceptTrophyGroup.objects.filter(
                concept_id__in=ratable_concept_ids,
            ).exclude(
                trophy_group_id='default',
            ).select_related('concept', 'concept__igdb_match')
            .defer('concept__igdb_match__raw_response')
            .order_by(Lower('concept__unified_title'), 'sort_order')
        )

        if not all_dlc_groups:
            return Response({'groups': [], 'total_items': 0, 'has_more': False})

        dlc_concept_ids = {g.concept_id for g in all_dlc_groups}
        dlc_group_ids = {g.trophy_group_id for g in all_dlc_groups}

        totals = {}
        for row in Trophy.objects.filter(
            game__concept_id__in=dlc_concept_ids,
            trophy_group_id__in=dlc_group_ids,
        ).values('game_id', 'game__concept_id', 'trophy_group_id').annotate(
            total=Count('id'),
        ):
            totals[(row['game_id'], row['trophy_group_id'])] = (row['total'], row['game__concept_id'])

        earned = {}
        if totals:
            for row in EarnedTrophy.objects.filter(
                profile=profile,
                trophy__game_id__in={k[0] for k in totals},
                trophy__trophy_group_id__in=dlc_group_ids,
                earned=True,
            ).values('trophy__game_id', 'trophy__trophy_group_id').annotate(
                cnt=Count('id'),
            ):
                earned[(row['trophy__game_id'], row['trophy__trophy_group_id'])] = row['cnt']

        completed_pairs = set()
        for (game_id, group_id), (total, concept_id) in totals.items():
            if total > 0 and earned.get((game_id, group_id), 0) >= total:
                completed_pairs.add((concept_id, group_id))

        dlc_groups = [
            g for g in all_dlc_groups
            if (g.concept_id, g.trophy_group_id) in completed_pairs
        ]

        if not dlc_groups:
            return Response({'groups': [], 'total_items': 0, 'has_more': False})

        dlc_ctg_ids = [g.id for g in dlc_groups]
        # Same rule as the base queue: a row only counts as rated once it carries a recommendation, so
        # pre-recommendation DLC ratings come back through here once, prefilled.
        # Scalar ids only, for the same whale reason as the base queue: a completed-DLC library runs to
        # thousands of groups and this rebuilds on every scroll page. The rows themselves are fetched
        # after the slice, for the packs actually on this page.
        dlc_any_rated = set(
            UserConceptRating.objects.filter(
                profile=profile, concept_trophy_group_id__in=dlc_ctg_ids,
            ).values_list('concept_trophy_group_id', flat=True)
        )
        dlc_rated = set(
            ReviewHubService.complete_ratings(
                profile, concept_trophy_group_id__in=dlc_ctg_ids,
            ).values_list('concept_trophy_group_id', flat=True)
        )

        # Never-rated packs lead, the recommendation backlog follows -- see the base queue for why.
        dlc_groups.sort(key=lambda g: g.id in dlc_any_rated)

        # Concepts that surface only because of the shovelware opt-in (no
        # non-shovelware game). Skip the query when the opt-in is off.
        shovelware_concept_ids = set()
        if include_shovelware:
            clean_concept_ids = set(
                Game.objects.filter(concept_id__in=dlc_concept_ids)
                .exclude(shovelware_status__in=['auto_flagged', 'manually_flagged'])
                .values_list('concept_id', flat=True).distinct()
            )
            shovelware_concept_ids = set(dlc_concept_ids) - clean_concept_ids

        groups_dict = OrderedDict()
        total_items = 0

        for g in dlc_groups:
            has_rating = g.id in dlc_rated
            if has_rating:
                continue  # ratings-only wizard: skip already-rated DLC

            cid = g.concept_id
            if cid not in groups_dict:
                groups_dict[cid] = {
                    'concept_id': cid,
                    'unified_title': g.concept.unified_title,
                    'concept_icon_url': g.concept.cover_url or '',
                    'slug': g.concept.slug,
                    'is_shovelware': cid in shovelware_concept_ids,
                    'items': [],
                    # Parallel to `items`: the CTG primary key behind each one, so the prefill can be
                    # attached AFTER the slice. It never leaves the server -- the item itself carries only
                    # the PSN group id the client sends back -- and it is stripped when the page's groups
                    # are rebuilt below.
                    'ctg_pks': [],
                }

            item = {
                'trophy_group_id': g.trophy_group_id,
                'trophy_group_name': g.display_name,
                'has_rating': g.id in dlc_any_rated,
                'is_dlc': True,
                'hours_label': 'Hours to Complete',
                # A DLC pack never ends in a platinum, so this half of the queue is unconditional.
                **UserConceptRating.recommendation_copy(has_platinum=False),
            }
            groups_dict[cid]['items'].append(item)
            groups_dict[cid]['ctg_pks'].append(g.id)
            total_items += 1

        # Flattened BY CONCEPT, not in `dlc_groups` order. A concept's packs have to stay adjacent, or a
        # game with two completed packs gets one on page 1 and the other twenty items later -- which is
        # what iterating the sorted group list produced, because that list is partitioned never-rated
        # first and a concept can have one pack in each half.
        flat_items = [
            (grp, item, ctg_pk)
            for grp in groups_dict.values()
            for item, ctg_pk in zip(grp['items'], grp['ctg_pks'])
        ]

        page_items = flat_items[offset:offset + limit]
        has_more = (offset + limit) < len(flat_items)

        # Prefill for THIS page only. Same hazard as the base queue: without these the form loads at
        # 5/5/5/3.0 and submitting for the recommendation overwrites a real rating.
        page_prior_ids = [ctg_pk for _, _, ctg_pk in page_items if ctg_pk in dlc_any_rated]
        if page_prior_ids:
            for prior in UserConceptRating.objects.filter(
                profile=profile, concept_trophy_group_id__in=page_prior_ids,
            ):
                for _, item, ctg_pk in page_items:
                    if ctg_pk == prior.concept_trophy_group_id:
                        item['existing'] = prior.as_prefill()
                        item['existing_blurb'] = prior.blurb
                        item['rated_at'] = prior.updated_at.isoformat()
                        break

        page_groups_dict = OrderedDict()
        for grp, item, _ in page_items:
            cid = grp['concept_id']
            if cid not in page_groups_dict:
                page_groups_dict[cid] = {
                    k: v for k, v in grp.items() if k not in ('items', 'ctg_pks')
                }
                page_groups_dict[cid]['items'] = []
            page_groups_dict[cid]['items'].append(item)

        return Response({
            'groups': list(page_groups_dict.values()),
            'total_items': total_items,
            'has_more': has_more,
            'next_offset': offset + limit,
            # Same as the base queue: the meter is denominated in every ratable DLC group, not in the
            # unrated remainder. `dlc_groups` is already the completed (= ratable) set.
            'ratable_total': len(dlc_groups),
            'rated_total': sum(1 for g in dlc_groups if g.id in dlc_rated),
        })


class TrophyListView(APIView):
    """Condensed trophy list for a concept trophy group.

    Used by the Rate My Games wizard's reference panel. Moved here from
    the review views when reviews were archived — the rating wizard is now
    its only consumer.
    """
    authentication_classes = [SessionAuthentication, TokenAuthentication]
    permission_classes = []

    def get(self, request, concept_id, group_id):
        """
        GET /api/v1/ratings/<concept_id>/group/<group_id>/trophies/

        Returns a deduplicated trophy list for the specified group with
        the authenticated user's earned status (if logged in).
        """
        concept, ctg, err = _get_concept_and_group(concept_id, group_id)
        if err:
            return err

        from trophies.models import Trophy, EarnedTrophy

        trophies_qs = Trophy.objects.filter(
            game__concept=concept,
            trophy_group_id=group_id,
        ).order_by('trophy_id').values(
            'trophy_id', 'trophy_type', 'trophy_name',
            'trophy_detail', 'trophy_icon_url',
        )

        # Deduplicate by trophy_id (same trophy across multi-region stacks)
        seen = set()
        trophies = []
        for t in trophies_qs:
            if t['trophy_id'] not in seen:
                seen.add(t['trophy_id'])
                trophies.append(t)

        # Earned status for authenticated user
        earned_set = set()
        if request.user.is_authenticated:
            profile = getattr(request.user, 'profile', None)
            if profile:
                earned_set = set(
                    EarnedTrophy.objects.filter(
                        profile=profile,
                        earned=True,
                        trophy__game__concept=concept,
                        trophy__trophy_group_id=group_id,
                    ).values_list('trophy__trophy_id', flat=True).distinct()
                )

        result = []
        for t in trophies:
            result.append({
                'trophy_id': t['trophy_id'],
                'trophy_type': t['trophy_type'],
                'trophy_name': t['trophy_name'],
                'trophy_detail': t['trophy_detail'],
                'trophy_icon_url': t['trophy_icon_url'] or '',
                'earned': t['trophy_id'] in earned_set,
            })

        return Response({
            'trophies': result,
            'count': len(result),
            'group_name': ctg.display_name,
        })
