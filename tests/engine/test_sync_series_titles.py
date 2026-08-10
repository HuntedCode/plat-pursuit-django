"""`sync_series_titles`: reconcile UserTitle against the badges actually held.

The gap it closes: `grant_series_title` runs ONLY on the `award` branch, and `diff` only emits `award`
when the badge is not already held -- so a badge earned before its series had a title never gets one,
and re-running `evaluate_badges` cannot fix it. That understates a title's holders, and since a title is
the UNION of its editions' earners, it made titles read rarer than the easiest edition granting them.
"""
import pytest
from django.core.management import call_command
from django.utils import timezone

from trophies.models import Title, UserGroupBadge, UserTitle
from tests.factories import BadgeSeriesFactory, GroupBadgeFactory, PlatformGroupFactory, ProfileFactory

pytestmark = pytest.mark.django_db


def _series(name, title_name):
    return BadgeSeriesFactory(name=name, title=Title.objects.create(name=title_name))


def _edition(series):
    return GroupBadgeFactory(series=series, platform_group=PlatformGroupFactory(), is_live=True)


def _hold(profile, edition, when=None):
    return UserGroupBadge.objects.create(
        profile=profile, group_badge=edition,
        earned_at=when or timezone.now(),
    )


def test_grants_the_title_to_a_hunter_who_already_held_the_badge():
    """The whole point: the badge is held, the title was never granted, and evaluate_badges can't help
    because there is nothing to award."""
    p = ProfileFactory()
    series = _series('Spider-Man', 'Web Slinger')
    _hold(p, _edition(series))

    call_command('sync_series_titles')

    ut = UserTitle.objects.get(profile=p, title=series.title)
    assert ut.source_type == 'badge_series'


def test_dry_run_writes_nothing():
    p = ProfileFactory()
    series = _series('Spider-Man', 'Web Slinger')
    _hold(p, _edition(series))

    call_command('sync_series_titles', '--dry-run')

    assert not UserTitle.objects.exists()


def test_the_title_is_the_union_of_its_editions():
    """A title is granted by ANY edition, so its holders are a union -- it can never be rarer than the
    single easiest edition. Getting this wrong is what made a title whose Ultra HD edition most of the
    community holds read as Mythic."""
    ultra_only, legacy_only, both = ProfileFactory(), ProfileFactory(), ProfileFactory()
    series = _series('Spider-Man', 'Web Slinger')
    ultra, legacy = _edition(series), _edition(series)
    _hold(ultra_only, ultra)
    _hold(legacy_only, legacy)
    _hold(both, ultra)
    _hold(both, legacy)

    call_command('sync_series_titles')

    holders = set(UserTitle.objects.filter(title=series.title).values_list('profile_id', flat=True))
    assert holders == {ultra_only.id, legacy_only.id, both.id}
    assert UserTitle.objects.filter(profile=both, title=series.title).count() == 1, 'one row per hunter'


def test_earned_at_comes_from_the_badge_not_the_backfill():
    """UserTitle.earned_at is auto_now_add, so a naive bulk_create stamps every backfilled row with the
    moment the command ran -- telling a hunter they earned a years-old title today, and ordering the
    "Yours" view (most recent first) by when the backfill happened."""
    p = ProfileFactory()
    series = _series('Spider-Man', 'Web Slinger')
    old = timezone.now() - timezone.timedelta(days=900)
    _hold(p, _edition(series), when=old)

    call_command('sync_series_titles')

    assert UserTitle.objects.get(profile=p).earned_at == old


def test_earliest_edition_wins_when_a_hunter_holds_several():
    p = ProfileFactory()
    series = _series('Spider-Man', 'Web Slinger')
    first = timezone.now() - timezone.timedelta(days=900)
    _hold(p, _edition(series), when=timezone.now())
    _hold(p, _edition(series), when=first)

    call_command('sync_series_titles')

    assert UserTitle.objects.get(profile=p).earned_at == first, 'the title dates from the first earn'


def test_two_series_sharing_a_title_reconcile_together():
    """BadgeSeries.title has no unique constraint. Reconciling per SERIES would make the second series'
    pass treat the first series' earners as orphans and undo what it just granted."""
    a_only, b_only = ProfileFactory(), ProfileFactory()
    shared = Title.objects.create(name='Shared Word')
    alpha = BadgeSeriesFactory(name='Alpha', title=shared)
    beta = BadgeSeriesFactory(name='Beta', title=shared)
    _hold(a_only, _edition(alpha))
    _hold(b_only, _edition(beta))

    call_command('sync_series_titles', '--prune')

    assert UserTitle.objects.filter(title=shared).count() == 2


def test_an_existing_grant_is_left_alone():
    p = ProfileFactory()
    series = _series('Spider-Man', 'Web Slinger')
    _hold(p, _edition(series))
    original = UserTitle.objects.create(profile=p, title=series.title, source_type='badge_series',
                                        source_id=series.id)

    call_command('sync_series_titles')

    assert UserTitle.objects.get(profile=p).id == original.id


def test_a_legacy_row_on_a_held_badge_is_adopted():
    """The "Be the first on a title you are wearing" bug.

    UserTitle is unique on (profile, title) WITHOUT source_type, so when a series reuses a legacy Badge's
    Title, get_or_create returns the legacy row untouched and the new system's grant is never recorded.
    The hunter holds the title -- it shows, they can equip it -- but every count filtered to
    `badge_series` is blind to them. They hold the badge, so the row is ours to claim."""
    legacy_holder, clean = ProfileFactory(), ProfileFactory()
    series = _series('Spider-Man', 'Web Slinger')
    edition = _edition(series)
    _hold(legacy_holder, edition)
    _hold(clean, edition)
    UserTitle.objects.create(profile=legacy_holder, title=series.title, source_type='badge', source_id=1)

    call_command('sync_series_titles')

    assert UserTitle.objects.filter(profile=legacy_holder).count() == 1, 'adopted, not duplicated'
    countable = UserTitle.objects.filter(title=series.title, source_type='badge_series')
    assert set(countable.values_list('profile_id', flat=True)) == {legacy_holder.id, clean.id}


def test_adoption_keeps_the_original_earned_at():
    """A bookkeeping correction, not a re-grant: the date is real, and rewriting it would shuffle the
    title's position in the "Yours" ordering."""
    p = ProfileFactory()
    series = _series('Spider-Man', 'Web Slinger')
    _hold(p, _edition(series), when=timezone.now())
    UserTitle.objects.create(profile=p, title=series.title, source_type='badge', source_id=1)
    original = UserTitle.objects.get(profile=p).earned_at

    call_command('sync_series_titles')

    assert UserTitle.objects.get(profile=p).earned_at == original


def test_a_legacy_row_with_no_badge_behind_it_is_left_alone():
    """Adoption is justified by the HELD BADGE, not by the title existing. Someone whose only claim is a
    retired legacy badge has not earned it under the new system, and claiming their row would inflate the
    numerator with exactly the population the source_type filter exists to exclude."""
    p = ProfileFactory()
    series = _series('Spider-Man', 'Web Slinger')
    _edition(series)                       # they hold no badge in it
    UserTitle.objects.create(profile=p, title=series.title, source_type='badge', source_id=1)

    call_command('sync_series_titles', '--prune')

    row = UserTitle.objects.get(profile=p)
    assert row.source_type == 'badge', 'not adopted'
    assert not UserTitle.objects.filter(source_type='badge_series').exists()


def test_orphans_survive_by_default_and_go_only_with_prune():
    """A title with no badge behind it may be a re-authored series rather than a revoke, so deleting
    something a hunter can see they earned must be asked for."""
    p = ProfileFactory()
    series = _series('Spider-Man', 'Web Slinger')
    _edition(series)                       # nobody holds it
    UserTitle.objects.create(profile=p, title=series.title, source_type='badge_series', source_id=series.id)

    call_command('sync_series_titles')
    assert UserTitle.objects.filter(profile=p).exists(), 'default run must not delete'

    call_command('sync_series_titles', '--prune')
    assert not UserTitle.objects.filter(profile=p).exists()


def test_prune_does_not_touch_legacy_or_one_off_titles():
    """Only this system's own grants are its to remove -- a one-off 'milestone' award has no badge behind
    it by definition and would be orphaned by any badge-based reconcile."""
    p = ProfileFactory()
    _series('Spider-Man', 'Web Slinger')
    UserTitle.objects.create(profile=p, title=Title.objects.create(name='Case Hardened'),
                             source_type='milestone', source_id=None)

    call_command('sync_series_titles', '--prune')

    assert UserTitle.objects.filter(profile=p, source_type='milestone').exists()


def test_series_scope_still_reconciles_the_whole_title():
    """--series scopes which title to fix, not which earners count. Narrowing to one series' badges would
    make every earner of a SIBLING series granting the same title look like an orphan."""
    a_only, b_only = ProfileFactory(), ProfileFactory()
    shared = Title.objects.create(name='Shared Word')
    alpha = BadgeSeriesFactory(series_slug='alpha', name='Alpha', title=shared)
    beta = BadgeSeriesFactory(series_slug='beta', name='Beta', title=shared)
    _hold(a_only, _edition(alpha))
    _hold(b_only, _edition(beta))

    call_command('sync_series_titles', '--series', 'alpha', '--prune')

    assert UserTitle.objects.filter(title=shared).count() == 2


def test_a_series_with_no_title_is_skipped():
    p = ProfileFactory()
    titleless = BadgeSeriesFactory(name='Titleless')
    _hold(p, _edition(titleless))

    call_command('sync_series_titles')

    assert not UserTitle.objects.exists()
