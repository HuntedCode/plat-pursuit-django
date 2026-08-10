"""Data layer for plat cards -- the share card a hunter gets for finishing a game.

**What earns a card:** the game's DEFAULT trophy group at 100%. Not "has a platinum" -- that was the
old rule, and it silently excluded every 100%-with-no-platinum game, a whole class of real achievement.
The default-group rule is a strict superset (a platinum lives in the default group, so plat implies the
group is done), which is why ONE query yields both card variants:

    platinum -- the game defines a platinum, so completing the group means they popped it
    full     -- the game defines no platinum; the group is simply finished

**Why ProfileTrophyGroup:** it already stores exactly this, per (profile, trophy group), refreshed on
every sync by `PSNApiService.update_profilegame_stats` and seeded by `backfill_profile_trophy_groups`.
`progress` is floored, so it reads 100 iff every trophy in the group is earned. The new badge system
uses the same read for its BASE bar (`badge_orchestrator.evaluate_with_catalog`). So eligibility here is
a read, never a live aggregate over EarnedTrophy -- which is what keeps it whale-safe.

**The staleness guard** mirrors the badge engine's invariant: whole-game 100% implies the base list is
done, so a PTG row that hasn't caught up must not hide a completion the hunter can plainly see. See
`eligible_completions`.

Cards are keyed on the game's default `TrophyGroup`, not on the ProfileTrophyGroup row: TrophyGroup ids
are stable, the PTG row is a denorm that a backfill may legitimately delete and rewrite, and keying on
the group means the "is this yours" question is answered by the eligibility predicate rather than by
which row happened to exist when the link was made.
"""
import logging

from django.db.models import F, IntegerField, Q, Value
from django.db.models.functions import Cast, Coalesce

from trophies.models import (
    BadgeSeries, EarnedTrophy, ProfileGame, ProfileTrophyGroup, SeriesBadgeStanding, Stage,
    TrophyGroup, UserConceptRating, UserTitle,
)
from trophies.constants import badge_attribution_rank
from trophies.templatetags.job_icons import _ICONS as _JOB_ICONS

logger = logging.getLogger(__name__)

PLATINUM = 'platinum'
FULL = 'full'

#: The two card names, as the user-facing product calls them. Not on the card itself (the card says
#: "PLATINUM" / "100% COMPLETE"); this is for surfaces that talk ABOUT cards -- the browse page filter,
#: the download filename.
VARIANT_LABELS = {
    PLATINUM: 'Platinum Card',
    FULL: '100% Card',
}

#: The card names ONE badge -- the spine band draws `badge_lines.0` and nothing else. Kept as a
#: constant (rather than hardcoding a [0]) because which badge leads is a real decision, made by the
#: sort in `_badge_lines`; the cap is just how many survive it.
BADGE_LINE_CAP = 1

#: How many art grounds a card offers. Each is another image to cache and another swatch in the
#: picker, and past a handful the choice stops being a choice.
ART_OPTION_CAP = 4

#: Raw Lucide path geometry per icon name, borrowed from the site's job-icon library so the card and
#: the site draw the same glyphs from one source. (The tag itself is unusable here -- see the note in
#: _contract_line.)
JOB_ICON_PATHS = _JOB_ICONS

#: Medallion backing metals, per `.pp-med[data-tier]` in components/badge-medallion.css -- the
#: source-of-truth hexes the whole badge system tints from (badge-detail.css says as much). Ported here
#: because the card renders with no stylesheet. (`glow` is the lighter edge the site uses for rims and
#: hover states; nothing on the card needs it yet, but it's kept so the map stays a faithful copy.)
#:
#: NOT the same thing as the trophy-tier dots in TIER_DISPLAY, which share the names bronze/silver/
#: gold/platinum but describe TROPHIES, not a badge's backing metal.
MEDALLION_COLOURS = {
    'bronze':   {'c': '#cf9160', 'glow': '#e6ad74'},
    'silver':   {'c': '#b9c6d6', 'glow': '#dae4f0'},
    'gold':     {'c': '#e7c25c', 'glow': '#f4d778'},
    'platinum': {'c': '#8fd2ea', 'glow': '#c4edfb'},
}

#: The five discipline colours (--disc-* in components/elements.css) as hex, because the card is
#: rendered in a document with no stylesheet and no custom properties. Keep in sync with that file.
DISCIPLINE_COLOURS = {
    'combat': '#fc5855',
    'exploration': '#59d38c',
    'mind': '#9c93ff',
    'heart': '#ff68a0',
    'finesse': '#fcb442',
}


# ── Eligibility ───────────────────────────────────────────────────────────────────────────────────

def eligible_completions(profile):
    """Every completion this profile can make a card from, as a real queryset (so the browse page can
    filter, sort and paginate it in the DB).

    The base read is the profile's own default-group standings -- an index seek on the
    (profile, trophy_group) unique index, bounded by games played, never by trophies earned.

    The OR arm is the staleness guard. `ProfileGame.progress` is the whole-game percentage, so 100 there
    means every trophy including DLC, which necessarily includes the base list. If a PTG row lags behind
    a sync, the hunter would otherwise look at a 100% game on their profile and find no card for it. The
    subquery is over the same profile's ProfileGames, so it stays bounded too.

    This only rescues a STALE row, not an absent one -- the base read is ProfileTrophyGroup, so a
    missing row has nothing for the `Q()` to match. The badge engine handles both
    (`base_map.get(g.id, (0, None))`), and its comment names "a missing/stale" row explicitly. A
    completion with no PTG row at all therefore cannot produce a card, which is the one gap left here;
    `backfill_profile_trophy_groups` is the repair, and the browse page and the endpoints share this
    predicate so at least they agree with each other about what exists.
    """
    completed_games = ProfileGame.objects.filter(profile=profile, progress=100).values('game_id')
    return (
        ProfileTrophyGroup.objects
        .filter(profile=profile, trophy_group__trophy_group_id='default')
        .filter(Q(progress=100) | Q(trophy_group__game_id__in=completed_games))
        # How many platinums the GAME defines -- the variant discriminator, annotated once here so a
        # browse row knows which card it is without a per-row Python call, and so `variant_filter` has
        # something to filter on rather than re-deriving it.
        .annotate(plat_defined=Coalesce(
            Cast(F('trophy_group__game__defined_trophies__platinum'), IntegerField()), Value(0),
        ))
        .select_related(
            'trophy_group',
            'trophy_group__game',
            'trophy_group__game__concept',
            'trophy_group__game__concept__igdb_match',
        )
        # raw_response is the ~30 KB IGDB blob no card or row ever reads; joining it in was the
        # trigger for the May 2026 web-server OOM.
        .defer('trophy_group__game__concept__igdb_match__raw_response')
    )


def resolve_variant(game):
    """`platinum` when the game defines a platinum, else `full`.

    Reads the GAME's defined_trophies rather than the group's: the platinum always lives in the default
    group so the two agree, but the game-level field is the one reliably populated across the catalogue
    (it is what the profile game filters key on too).
    """
    defined = game.defined_trophies or {}
    try:
        return PLATINUM if int(defined.get('platinum') or 0) > 0 else FULL
    except (TypeError, ValueError):
        return FULL


def variant_filter(qs, variant):
    """Narrow an `eligible_completions` queryset to one variant, in the DB.

    Filters the `plat_defined` annotation from `eligible_completions` -- a CAST, not a raw JSON
    transform. `exclude(defined_trophies__platinum__gt=0)` does NOT match rows where the key is absent
    (the `->` yields SQL NULL and `NOT NULL` is NULL), so a game with `defined_trophies = {}` fell out
    of BOTH arms while `resolve_variant` (pure Python) called it FULL: invisible to the counts, but
    still rendering, with an ordinal from a ladder it wasn't in. Casting also fixes jsonb's
    string-vs-number ordering, where a stored "1" sorts below any number and reads as "no platinum".
    Every other consumer in the codebase casts (browse_helpers, game_views, admin).
    """
    if variant == PLATINUM:
        return qs.filter(plat_defined__gt=0)
    if variant == FULL:
        return qs.filter(plat_defined=0)
    return qs


def get_completion(profile, trophy_group_id):
    """The profile's standing on `trophy_group_id`, or None if they haven't earned a card for it.

    This is the ownership check every card endpoint runs: it answers "is this completion yours" with
    the same predicate that built the list, so a deep link can never show a card the browse page won't.
    """
    return eligible_completions(profile).filter(trophy_group_id=trophy_group_id).first()


def completion_for_earned_trophy(profile, earned_trophy_id):
    """Resolve a platinum EarnedTrophy id to its completion.

    Kept because platinum-earned notifications already in the wild deep-link by EarnedTrophy id, and
    because the pre-2026-08 share endpoints were keyed that way for external/mobile consumers.
    """
    game_id = (
        EarnedTrophy.objects
        .filter(id=earned_trophy_id, profile=profile, earned=True, trophy__trophy_type='platinum')
        .values_list('trophy__game_id', flat=True)
        .first()
    )
    if not game_id:
        return None
    group_id = (
        TrophyGroup.objects
        .filter(game_id=game_id, trophy_group_id='default')
        .values_list('id', flat=True)
        .first()
    )
    return get_completion(profile, group_id) if group_id else None


# ── Ordinals ──────────────────────────────────────────────────────────────────────────────────────

def _platinum_ordinal(profile, earned_trophy):
    """"Platinum #N" at the time of earning.

    Deliberately unchanged from the original implementation: the ordinal is a coupled pair with the
    listing sort (`earned_date_time DESC NULLS LAST, -id`), and the two silently desync if either side
    is rewritten. NULL-date platinums (PSN occasionally returns no timestamp) sort to the END of the
    timeline and therefore take the LOWEST ordinals.
    """
    plats = EarnedTrophy.objects.filter(profile=profile, earned=True, trophy__trophy_type='platinum')
    earned_date = earned_trophy.earned_date_time
    if not earned_date:
        return plats.filter(earned_date_time__isnull=True, id__lte=earned_trophy.id).count()
    return plats.filter(
        Q(earned_date_time__isnull=True)
        | Q(earned_date_time__lt=earned_date)
        | Q(earned_date_time=earned_date, id__lte=earned_trophy.id)
    ).count()


def _full_ordinal(profile, standing, *, self_in_ladder=True):
    """"100% #N" -- the full-completion variant counts its OWN ladder.

    A single shared ladder across both variants would have renumbered every platinum card already
    shared, so the two counts stay independent. Ties break by trophy_group_id for a total order, the
    same reason the leaderboard indexes carry a unique final key.
    """
    completed_at = standing.last_trophy_at
    qs = variant_filter(eligible_completions(profile), FULL)
    if not completed_at:
        count = qs.filter(last_trophy_at__isnull=True, trophy_group_id__lte=standing.trophy_group_id).count()
    else:
        count = qs.filter(
            Q(last_trophy_at__isnull=True)
            | Q(last_trophy_at__lt=completed_at)
            | Q(last_trophy_at=completed_at, trophy_group_id__lte=standing.trophy_group_id)
        ).count()
    # A game that DEFINES a platinum but has no earned row is rendered as a full card even though it
    # isn't in the FULL ladder, so it doesn't count itself. Without the +1 it reads one low, and reads
    # 0 -- which the template drops entirely -- for a hunter with no other full completions.
    return count if self_in_ladder else count + 1


# ── Badge series + title ──────────────────────────────────────────────────────────────────────────

def _badge_lines(profile, concept, game):
    """The live badge series this game belongs to, and the title each one grants.

    Reads the NEW grouping-badge system (BadgeSeries / SeriesBadgeStanding / UserTitle
    `source_type='badge_series'`) -- the same read-models Collection and the Titles page use. The card
    used to read the legacy Badge/UserBadgeProgress tier-1 rows, which retire at the badge cutover.

    A game can sit in several badges at once (its Collection, its Franchise, its studio, its own
    series). The card shows ONE, so ordering matters: the series' ATTRIBUTION decides first, via the
    site-wide `badge_attribution_rank` (collection -> franchise -> developer -> fallback), so the badge
    a game leads with here is the badge it leads with on a browse card. Within one rank, a title the
    hunter actually HOLDS wins, then whichever series they are furthest along in.

    (Consequence worth knowing: holding a title on an unattributed series does NOT promote it over an
    unheld collection badge. The attribution is the stronger signal of what a game IS, and the card is
    showing the game.)
    """
    if not concept:
        return []

    live_slugs = BadgeSeries.objects.filter(group_badges__is_live=True).values_list('series_slug', flat=True)
    slugs = set(
        Stage.objects
        .filter(concepts=concept, series_slug__in=live_slugs)
        .exclude(series_slug__isnull=True).exclude(series_slug='')
        # .order_by() strips Stage.Meta.ordering, which would otherwise ride the SELECT and defeat
        # .distinct() (a concept in two same-series stages -> duplicate rows).
        .values_list('series_slug', flat=True).order_by().distinct()
    )
    if not slugs:
        return []

    series_list = list(BadgeSeries.objects.filter(series_slug__in=slugs).select_related('title'))
    standings = {
        s.series_slug: s
        for s in SeriesBadgeStanding.objects.filter(profile=profile, series_slug__in=slugs)
    }
    title_ids = [s.title_id for s in series_list if s.title_id]
    held_titles = set(
        UserTitle.objects
        .filter(profile=profile, title_id__in=title_ids, source_type='badge_series')
        .values_list('title_id', flat=True)
    ) if title_ids else set()

    lines = []
    for series in series_list:
        standing = standings.get(series.series_slug)
        progress_bp = standing.progress_bp if standing else 0
        lines.append({
            'badge_type': series.badge_type,
            'attribution_rank': badge_attribution_rank(
                series.collection_id, series.franchise_id, series.developer_id,
            ),
            'series_name': series.display_series or series.name,
            'series_slug': series.series_slug,
            'title': series.title.name if series.title_id else '',
            'title_held': series.title_id in held_titles,
            'progress_pct': round(progress_bp / 100),
            'stages_cleared': standing.stages_cleared if standing else 0,
            'stages_total': standing.stages_total if standing else 0,
        })

    lines.sort(key=lambda l: (
        l['attribution_rank'],
        not l['title_held'],
        -l['progress_pct'],
        l['series_name'].lower(),
    ))
    lines = lines[:BADGE_LINE_CAP]
    if lines:
        # Only the LEAD line gets art. Each medallion is two more images to cache and base64 into the
        # render, and only one of them is big enough on the card to be worth the payload.
        _attach_medallion(lines[0], concept, game)
    return lines


def _attach_medallion(line, concept, game):
    """Give a badge line its real medallion art + edition, in place.

    The card should show the badge OBJECT, not just its name -- it's the product's signature. The
    edition matters too ("Ultra HD" vs "Legacy HD" are different badges to a hunter), and the art is
    per-edition, so both come from the same GroupBadge.

    Picks the edition whose platform group actually covers this game, falling back to the series' first
    live edition -- a card for a PS5 completion shouldn't show the Legacy HD medallion.
    """
    from trophies.models import GroupBadge
    from trophies.services.badge_detail_service import group_medallion_layers

    editions = list(
        GroupBadge.objects
        .filter(series__series_slug=line['series_slug'], is_live=True)
        .select_related('platform_group', 'series', 'series__submitted_by')
        .order_by('id')
    )
    if not editions:
        return

    # THIS game's platforms, not every game on the concept. Unioning across the concept gave
    # {PS3, PS4, PS5} for exactly the multi-generation stacks that editions exist to distinguish, so
    # the match degenerated to "first live edition by id" and a PS5 completion could show Legacy HD.
    platforms = set(game.title_platform or [])
    edition = next(
        (e for e in editions if platforms & set(e.platform_group.platforms or [])),
        editions[0],
    )

    tier, layers, is_avatar = group_medallion_layers(edition)
    metal = MEDALLION_COLOURS.get(tier, MEDALLION_COLOURS['gold'])

    # SUBJECT ART ONLY. group_medallion_layers returns [backdrop_plate, subject]; the plate exists to
    # sit behind a circle mask, and the card doesn't mask -- it shows the badge's own silhouette. Left
    # in, the plate renders as its own shape behind a shield and reads as a stray sliver of metal.
    # An avatar subject keeps its circle (a raw square PSN photo is not a badge), so it needs no plate
    # either. `main` is never empty (art_layers falls back to a static default), so [-1:] is safe.
    art = layers[-1:]

    line.update({
        'edition': edition.platform_group.name,
        'medallion_layers': art,
        'medallion_is_avatar': is_avatar,
        # The edition ("Ultra HD", "Legacy HD") wears its own backing metal, same as everywhere else
        # the badge system names one -- so the edition label agrees with the badge's own colouring.
        'medallion_colour': metal['c'],
    })


def hunter_totals(profile):
    """(platinums, full completions) -- the hunter's standing, for the browse page's header counts.

    NOT what the card's identity line uses. The card prints "#{ordinal}" from `_platinum_ordinal`,
    which counts EarnedTrophy rows, and printing a total from a DIFFERENT population directly beneath
    it let the card read "PLATINUM #47 ... 35 platinums" whenever a platinum's ProfileTrophyGroup row
    was missing. See `platinum_count`, which counts the same rows the ordinal does.
    """
    completions = eligible_completions(profile)
    return (
        variant_filter(completions, PLATINUM).count(),
        variant_filter(completions, FULL).count(),
    )


def platinum_count(profile):
    """The hunter's platinum total, counted over the SAME rows as `_platinum_ordinal`.

    The card shows the ordinal and the total together, so they have to come from one population or a
    hunter sees a #N larger than their own total.
    """
    return EarnedTrophy.objects.filter(
        profile=profile, earned=True, trophy__trophy_type='platinum',
    ).count()


def _contract_line(profile, concept):
    """The Job Board contract this game IS, and the jobs it feeds -- or None.

    The other half of the product spine: badges are the collection, contracts are the career. A card
    that shows one and not the other tells half the story of what a completion was worth.

    Contracts are keyed on the raw IGDB id, so membership is derived from the concept's IGDBMatch
    (`contract_by_concept_map`) rather than stored.

    Deliberately NO XP figure. A contract's XP splits evenly across its jobs, so a single total on the
    card only reads correctly if every job is shown beside it -- and at up to 6 jobs, showing all the
    names AND the total doesn't fit. Naming every job is the more useful half, and dropping the number
    removes any chance of crediting the wrong jobs. (The ledger read that produced it is gone with it,
    so this is two queries cheaper per card.)
    """
    if not concept:
        return None
    from trophies.services.contract_service import contract_by_concept_map

    contract = contract_by_concept_map([concept.id]).get(concept.id)
    if not contract:
        return None

    return {
        'name': contract.name,
        # Job glyphs travel as raw Lucide PATH geometry, not via the {% job_icon %} tag: that tag sizes
        # itself with Tailwind classes (`w-5 h-5`), and the card is rendered in a document with no
        # stylesheet, so its <svg> would come out unsized. The template wraps this in an <svg> with
        # explicit width/height instead.
        'jobs': [
            {
                'name': job.name,
                'icon_paths': JOB_ICON_PATHS.get(job.icon or '', ''),
                'colour': DISCIPLINE_COLOURS.get(job.discipline, '#9da5b1'),
            }
            for job in contract.jobs.all()
        ],
    }


def _displayed_title(profile):
    """The title the hunter is currently wearing, or ''."""
    return (
        UserTitle.objects
        .filter(profile=profile, is_displayed=True)
        .values_list('title__name', flat=True)
        .first()
    ) or ''


def _user_rating(profile, concept):
    """The hunter's own base-game rating, or None. `concept_trophy_group=NULL` is the base-game
    convention shared with RatingService."""
    if not concept:
        return None
    rating = UserConceptRating.objects.filter(
        profile=profile, concept=concept, concept_trophy_group__isnull=True,
    ).first()
    if not rating:
        return None
    return {
        # The hunter's own words about the game -- 140 chars, already auto-filtered on submit,
        # reportable, and staff-soft-hideable via `blurb_hidden`, which is why it's safe to render on
        # an image that leaves the site. Optional, so the card must look right without it.
        'blurb': '' if rating.blurb_hidden else (rating.blurb or ''),
        'overall_rating': rating.overall_rating,
        # Percentage fill for the card's star row. `overall_rating` is a 0.5-5.0 FLOAT (unlike
        # difficulty/grindiness/fun, which are 1-10 ints), so half stars are real and the row is drawn
        # as a clipped overlay rather than N whole glyphs.
        'stars_pct': round((rating.overall_rating or 0) / 5 * 100, 1),
        'difficulty': rating.difficulty,
        'grindiness': rating.grindiness,
        'fun_ranking': rating.fun_ranking,
        'hours_to_platinum': rating.hours_to_platinum,
    }


# ── The card payload ──────────────────────────────────────────────────────────────────────────────

RARITY_LABELS = {0: 'Ultra Rare', 1: 'Very Rare', 2: 'Rare', 3: 'Common'}

#: Tier dot colours for the card. The site's --color-trophy-* tokens are deliberately muted metallics
#: tuned for light-ish card surfaces; on the card's near-black ground silver (#5f5f5f) sits at roughly
#: 2.4:1 and simply disappears at embed scale. These are the same hues lifted until they read.
#: Platinum is the site token unchanged -- it was already light enough.
TIER_DISPLAY = [
    ('platinum', '#67d1f8'),
    ('gold', '#e0b055'),
    ('silver', '#b9c2cc'),
    ('bronze', '#c07a4a'),
]


def _ints(counts):
    """The integer values of a defined/earned-trophies JSON blob, tolerating junk."""
    for value in (counts or {}).values():
        try:
            yield int(value or 0)
        except (TypeError, ValueError):
            yield 0


def _tier_counts(counts):
    """[(tier, count, colour)] for the tiers this game actually has, platinum first."""
    out = []
    for tier, colour in TIER_DISPLAY:
        try:
            n = int(counts.get(tier) or 0)
        except (TypeError, ValueError, AttributeError):
            n = 0
        if n:
            out.append({'tier': tier, 'count': n, 'colour': colour})
    return out


def get_card_data(profile, standing):
    """Everything the card template needs, for either variant.

    `standing` is a row from `eligible_completions` (so ownership is already established).
    """
    group = standing.trophy_group
    game = group.game
    concept = game.concept
    variant = resolve_variant(game)

    profile_game = ProfileGame.objects.filter(profile=profile, game=game).first()

    # The platinum variant leads on the trophy itself; the full variant has no anchor trophy, so its
    # hero is the completion.
    platinum = None
    if variant == PLATINUM:
        platinum = (
            EarnedTrophy.objects
            .filter(profile=profile, trophy__game=game, trophy__trophy_type='platinum', earned=True)
            .select_related('trophy')
            .first()
        )

    if platinum:
        ordinal = _platinum_ordinal(profile, platinum)
        completed_at = platinum.earned_date_time
    else:
        # Two different situations land here, and they number differently:
        #   - a genuine full card (the game defines no platinum) -- it IS in the FULL ladder
        #   - a game that DEFINES a platinum but has no earned row: a data anomaly, shown as a full
        #     card rather than a platinum one with an empty hero. The FULL arm excludes games defining
        #     a platinum, so this one is absent from its own ladder and can't count itself.
        downgraded = variant == PLATINUM
        variant = FULL
        ordinal = _full_ordinal(profile, standing, self_in_ladder=not downgraded)
        completed_at = standing.last_trophy_at

    earned_counts = standing.earned_trophies or {}
    group_defined = group.defined_trophies or {}
    game_defined = game.defined_trophies or {}
    total_plats = platinum_count(profile)

    # DLC. The card is scoped to the DEFAULT group, which is what makes it safe to hand a platinum to
    # someone whose DLC is still outstanding. But that scoping also made a hunter who cleared EVERYTHING
    # look identical to one who cleared only the base list, which undersells the bigger chase.
    #
    # So the whole-game numbers appear ONLY when the whole game is done. A partial figure is never shown
    # beside a PLATINUM label: "51 of 71" reads as unfinished, and nobody should be talked out of
    # sharing a platinum by their own card. `ProfileGame.progress` is the whole-game percentage, so it
    # is exactly the "everything, DLC included" test.
    group_total = sum(_ints(group_defined))
    game_total = sum(_ints(game_defined))
    has_dlc = game_total > group_total
    all_dlc_done = has_dlc and bool(profile_game and profile_game.progress == 100)

    # When everything is done, the breakdown and the total both describe the whole game -- otherwise the
    # dots would sum to the base list while the label counted every group, and contradict each other.
    # Every trophy is earned in that state, so the game's DEFINED counts are also the earned counts.
    trophy_source = game_defined if all_dlc_done else (earned_counts or group_defined)
    trophy_total = game_total if all_dlc_done else group_total

    return {
        'variant': variant,
        'ordinal': ordinal,

        'username': profile.display_psn_username or profile.psn_username,
        'user_avatar_url': profile.avatar_url or '',
        # The title they're WEARING, not one this game granted -- the card's identity strip is the
        # hunter, and the worn title is how they present themselves everywhere else on the site.
        'display_title': _displayed_title(profile),
        'total_platinums': total_plats,

        'game_name': game.title_name,
        'concept_id': concept.id if concept else None,
        'trophy_group_id': group.id,
        'game_image': game.display_image_url_large,
        # Every landscape image the concept has, not just the best one. `landscape_urls` is already
        # ordered by quality (trusted IGDB screenshots -> artworks -> PSN bg_url), so a game with
        # several gives the hunter a real choice of backdrop instead of one take-it-or-leave-it. Capped
        # because each option is another image to cache and show as a swatch.
        'art_urls': concept.landscape_urls(limit=ART_OPTION_CAP) if concept else [],
        'region': game.region,
        'is_regional': game.is_regional,

        'trophy_icon_url': platinum.trophy.trophy_icon_url or '' if platinum else '',
        'trophy_earn_rate': round(float(platinum.trophy.trophy_earn_rate or 0), 2) if platinum else None,
        'rarity_label': RARITY_LABELS.get(platinum.trophy.trophy_rarity, '') if platinum else '',

        'completed_at': completed_at,
        'play_duration_seconds': (
            profile_game.play_duration.total_seconds()
            if profile_game and profile_game.play_duration else None
        ),
        # Group-scoped counts: the card is about finishing THIS list, so a game whose DLC is still
        # outstanding must not read as partially done.
        'trophy_total': trophy_total,
        'all_dlc_done': all_dlc_done,
        # Ordered platinum -> bronze for display. Falls back to the group's DEFINED counts because
        # `earned_trophies` is a denorm that can lag, and the group is finished either way, so the two
        # agree whenever both are present.
        'tier_counts': _tier_counts(trophy_source),
        'platform_label': ' / '.join(game.title_platform or []),

        'badge_lines': _badge_lines(profile, concept, game),
        'contract': _contract_line(profile, concept),
        'user_rating': _user_rating(profile, concept),
    }
