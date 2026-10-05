"""`/community/challenges/<id>/day/<month>/<day>/` -- one Calendar square, opened.

A FRAGMENT ENDPOINT, following `JobContractsResultsView`: it answers with the rendered partial and the
board injects it. So these tests read markup rather than JSON, and the gating assertions are about
STATUS CODES, which is where this endpoint's risk actually lives -- it is the first per-user-data read
this app serves to an anonymous caller.
"""
import datetime as dt

import pytest
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from challenges.models import CHALLENGE_TYPE_AZ, CHALLENGE_TYPE_CALENDAR
from challenges.services import calendar_fill
from tests.factories import ConceptFactory, GameFactory, ProfileFactory, UserFactory
from django.utils import timezone

from trophies.models import EarnedTrophy, ProfileGame, Trophy

pytestmark = pytest.mark.django_db

_SEQ = {'n': 0}


def _hunter(tz='UTC'):
    user = UserFactory(user_timezone=tz)
    return ProfileFactory(user=user, is_linked=True, user_is_premium=True)


def _run(profile=None, challenge_type=CHALLENGE_TYPE_CALENDAR):
    """A real run, created through the service with the beta door lifted -- the same shape
    `test_calendar_surfaces._run` uses, and for the same reason: these tests want the rows the real
    creation path makes."""
    from challenges.services import challenge_service as svc

    original = svc.TYPES_NOT_YET_CREATABLE
    svc.TYPES_NOT_YET_CREATABLE = frozenset()
    try:
        return svc.start(profile or _hunter(), challenge_type)
    finally:
        svc.TYPES_NOT_YET_CREATABLE = original


def _platted(profile, when, *, shovelware=False, name=None):
    _SEQ['n'] += 1
    game = GameFactory(
        concept=ConceptFactory(anchor_migration_completed_at=timezone.now()),
        title_name=name or ('Game %d' % _SEQ['n']),
        shovelware_status='auto_flagged' if shovelware else 'clean',
    )
    trophy = Trophy.objects.create(
        game=game, trophy_type='platinum', trophy_id=_SEQ['n'],
        trophy_name='Platinum %d' % _SEQ['n'],
    )
    ProfileGame.objects.create(profile=profile, game=game, has_plat=True, progress=100)
    EarnedTrophy.objects.create(profile=profile, trophy=trophy, earned=True, earned_date_time=when)
    return game


def _utc(y, m, d, hour=12):
    return dt.datetime(y, m, d, hour, 0, tzinfo=dt.timezone.utc)


def _url(run, month, day):
    return reverse('challenge_calendar_day', args=[run.id, month, day])


def _fill(run):
    calendar_fill.apply_to_run(run)
    return run


# ── who may open a square ────────────────────────────────────────────────────────────────────────

def test_an_anonymous_reader_may_open_a_square_on_a_visible_run():
    """PUBLIC, which is the owner's call (2026-10-04): the board is public "if the challenge is not
    hidden", so a square is too. This is the first per-user-data read this app serves with no login in
    front of it, which is why the limiter below exists."""
    profile = _hunter()
    _platted(profile, _utc(2019, 3, 3))
    run = _fill(_run(profile))

    resp = Client().get(_url(run, 3, 3))

    assert resp.status_code == 200
    assert 'pp-cday' in resp.content.decode()


def test_a_hidden_run_answers_nobody_but_its_owner():
    """ONE DEFINITION OF VISIBLE. `Challenge.objects.readable_by` is what the detail page delegates to,
    and its docstring spells out the cost of a second spelling: visibility gains a condition, the
    anonymous path inherits it and the signed-in path does not, and "a run that should have gone dark
    would then be served to every signed-in visitor and nobody else, which is the hardest kind of leak
    to notice." So this endpoint must ask the same queryset rather than filtering `is_deleted` itself.
    """
    owner = _hunter()
    _platted(owner, _utc(2019, 5, 5))
    run = _fill(_run(owner))
    # HIDDEN THROUGH THE SERVICE, not by writing the flag: `challenge_deleted_at_matches_flag` requires
    # the stamp to travel with it, so a hand-written `is_deleted` raises. Going through `hide` also means
    # this test exercises the state a hunter can actually reach.
    from challenges.services import challenge_service as svc
    svc.hide(run, owner)
    run.refresh_from_db()
    assert run.is_deleted

    assert Client().get(_url(run, 5, 5)).status_code == 404, 'a hidden run answered an anonymous reader'

    stranger = Client()
    stranger.force_login(_hunter().user)
    assert stranger.get(_url(run, 5, 5)).status_code == 404, (
        'a hidden run answered a signed-in stranger -- the leak shape `readable_by` warns about')

    mine = Client()
    mine.force_login(owner.user)
    assert mine.get(_url(run, 5, 5)).status_code == 200, 'the owner must still be able to open it'


def test_a_run_of_another_type_has_no_squares_to_open():
    """THE TYPE IS PART OF THE GATE, not an assumption. An A-Z run has `ChallengeSlot` rows and no
    `CalendarDay` rows, so without the `challenge_type` filter this would fall through to the square
    lookup and 404 anyway -- by accident, and only while that stays true. Asked explicitly."""
    run = _run(challenge_type=CHALLENGE_TYPE_AZ)
    assert Client().get(_url(run, 3, 3)).status_code == 404


@pytest.mark.parametrize('month,day', [(2, 29), (4, 31), (13, 1), (6, 0)])
def test_a_key_that_is_not_a_square_is_not_found(month, day):
    """THE RUN'S OWN ROWS ARE THE RANGE CHECK. They are generated from `CALENDAR_MONTH_DAYS`, so asking
    the run whether a square exists is the same question as validating the key and cannot drift from it.
    29 February is the interesting one: it is a real date and deliberately NOT a square -- it folds onto
    the 28th -- so a reader who guesses the URL gets a 404 rather than a second view of the 28th."""
    run = _fill(_run())
    assert Client().get(_url(run, month, day)).status_code == 404


# ── what a square says ───────────────────────────────────────────────────────────────────────────

def test_the_square_lists_its_platinums_newest_first_with_the_year_leading():
    """THE YEAR IS THE ORGANISING ELEMENT (owner: "mainly by year"), because a square has no year of its
    own -- "3 March" means every 3 March in the hunter's history."""
    profile = _hunter()
    _platted(profile, _utc(2017, 7, 7), name='Older Game')
    _platted(profile, _utc(2022, 7, 7), name='Newer Game')
    run = _fill(_run(profile))

    body = Client().get(_url(run, 7, 7)).content.decode()

    assert 'Older Game' in body and 'Newer Game' in body
    assert body.index('Newer Game') < body.index('Older Game'), 'newest first'
    assert '2022' in body and '2017' in body


def test_the_squares_tally_counts_the_rows_it_rendered():
    """ON A FRESH FILL THE TWO AGREE, which is what this asks: the header's figure and the list under it
    are the same number, so the fragment reads as one object.

    THE FIRST VERSION ASSERTED THE OPPOSITE REASONING -- "the fragment prints the same column rather
    than counting the list it just rendered" -- and the column is the wrong source. See the test below.
    """
    profile = _hunter()
    for year in (2016, 2019, 2023):
        _platted(profile, _utc(year, 8, 8))
    run = _fill(_run(profile))

    body = Client().get(_url(run, 8, 8)).content.decode()
    square = run.calendar_days.get(month=8, day=8)

    assert square.plat_count == 3, 'the stored column agrees on a fresh fill'
    assert 'pp-tally">3</span>' in body
    assert body.count('pp-cday__row') == 3, 'and the list under it is the same length'


def test_a_stale_stored_count_cannot_reach_the_fragment():
    """THE MODAL MUST NOT CONTRADICT ITSELF. `plat_count` is written by `apply_to_run`, and
    `runs_due_for_sweep` only makes a run due when `total_plats` moves -- so a hunter who changes their
    own timezone (which re-keys every day while moving no counter) or a game that gets reclassified
    leaves the column stale with nothing to correct it until their next platinum. For a dormant hunter,
    indefinitely.

    PRINTING THAT COLUMN ABOVE A LIVE LIST imports the staleness into the one place that has the truth,
    and the contradiction lands inside a single response: "4 platinums" over three rows. The header
    counts what it rendered instead.

    THE BOARD CAN STILL DISAGREE WITH THE MODAL -- that gap is real and documented on
    `runs_due_for_sweep`. What this pins is that the modal agrees with itself.
    """
    profile = _hunter()
    _platted(profile, _utc(2019, 1, 20))
    run = _fill(_run(profile))

    # The shape a missed sweep leaves behind, written directly because no reachable action produces it
    # on demand.
    run.calendar_days.filter(month=1, day=20).update(plat_count=9)

    body = Client().get(_url(run, 1, 20)).content.decode()

    assert 'pp-tally">1</span>' in body, 'the header must count the rows it rendered'
    assert 'pp-tally">9</span>' not in body, 'the stale stored count leaked into the fragment'
    assert body.count('pp-cday__row') == 1


def test_a_shovelware_platinum_is_listed_and_marked():
    """THE ONLY EXPLANATION AN OPEN SQUARE HAS. A day in `all` and not `clean` draws nothing, and this
    row is why -- a real platinum on a flagged game. Omitting it would leave the one square that has an
    answer opening onto the same emptiness as a square with none."""
    profile = _hunter()
    _platted(profile, _utc(2018, 9, 9), shovelware=True, name='Flagged Game')
    run = _fill(_run(profile))

    body = Client().get(_url(run, 9, 9)).content.decode()

    assert 'Flagged Game' in body
    assert 'shovelware' in body, 'the row must say why it does not count'
    assert 'pp-cday__row--out' in body


def test_a_square_with_nothing_on_it_says_so_rather_than_rendering_an_empty_list():
    """REACHABLE EVEN THOUGH THE BOARD ONLY MAKES `in_all` SQUARES CLICKABLE: a game can be reclassified
    between the fill and the click, and fills are monotone so the square keeps drawing."""
    run = _fill(_run())
    body = Client().get(_url(run, 10, 10)).content.decode()

    assert 'No platinums on this date.' in body
    assert 'pp-cday__list' not in body


def test_the_square_that_holds_the_leap_day_shows_both_of_its_days():
    """THE FOLD REACHES THE FRAGMENT. 29 February fills the 28 February square and `plat_count` SUMS the
    two, so the list has to as well or the tally in the header contradicts the rows underneath it --
    inside one response, which is the most visible version of that failure."""
    profile = _hunter()
    _platted(profile, _utc(2015, 2, 28), name='Feb The Twentyeighth')
    _platted(profile, _utc(2016, 2, 29), name='Leap Day Game')
    run = _fill(_run(profile))

    body = Client().get(_url(run, 2, 28)).content.decode()

    assert 'Leap Day Game' in body and 'Feb The Twentyeighth' in body
    assert 'pp-tally">2</span>' in body


def test_the_dates_are_the_owners_not_the_readers():
    """THE TRAP A PUBLIC PAGE SETS. Middleware activates the VIEWER's timezone, so a square resolved from
    the request would list a Tokyo reader a different set than a London one -- for somebody else's
    calendar. The square was keyed in the owner's zone, so the list must be too."""
    owner = _hunter('Asia/Tokyo')
    _platted(owner, _utc(2021, 3, 2, hour=23), name='Late Night Plat')
    run = _fill(_run(owner))

    # A reader in London, where that instant is still 2 March.
    reader = Client()
    reader.force_login(UserFactory(user_timezone='Europe/London'))

    assert 'Late Night Plat' in reader.get(_url(run, 3, 3)).content.decode(), (
        "the owner's own square came back empty to a reader in another timezone")
    assert 'No platinums on this date.' in reader.get(_url(run, 3, 2)).content.decode()


# ── cost ─────────────────────────────────────────────────────────────────────────────────────────

def test_opening_a_square_costs_the_same_however_many_platinums_it_holds():
    """FLAT, AND MEASURED. This is a public request path, so an N+1 here is reachable by anyone. The live
    hazard is the cover chain: `display_image_url` reads the IGDB match FIRST on every render, so the
    joins have to be in the queryset rather than discovered per row."""
    one = _hunter()
    _platted(one, _utc(2019, 4, 4))
    one_run = _fill(_run(one))

    many = _hunter()
    for year in range(2004, 2020):
        _platted(many, _utc(year, 4, 4))
    many_run = _fill(_run(many))

    client = Client()
    with CaptureQueriesContext(connection) as small:
        assert client.get(_url(one_run, 4, 4)).status_code == 200
    with CaptureQueriesContext(connection) as big:
        assert client.get(_url(many_run, 4, 4)).status_code == 200

    assert len(big.captured_queries) == len(small.captured_queries), (
        'the square grows with its own contents: %d vs %d'
        % (len(big.captured_queries), len(small.captured_queries)))


def test_opening_a_square_does_not_fetch_the_igdb_blob():
    """THE `raw_response` GUARD this project requires beside every `igdb_match` join: a ~30 KB API blob
    no cover template reads, and the trigger for the May 2026 web-server OOM when concurrent renders
    piled the join payload up. A public endpoint is the worst place to drop it."""
    profile = _hunter()
    _platted(profile, _utc(2019, 6, 6))
    run = _fill(_run(profile))

    with CaptureQueriesContext(connection) as ctx:
        Client().get(_url(run, 6, 6))

    igdb = [q['sql'] for q in ctx.captured_queries if 'igdb' in q['sql'].lower()]
    assert igdb, 'the cover chain is not joined at all -- the flatness test above would be vacuous'
    assert not any('raw_response' in sql for sql in igdb)


def test_the_response_is_not_publicly_cacheable():
    """THE BODY IS THE SAME FOR EVERY READER -- it is the owner's library, not the viewer's -- so this
    would otherwise be a fine public cache entry. A HIDDEN run is what makes it viewer-dependent: it
    answers 404 to everyone but its owner, and a shared cache that learned the owner's 200 would serve
    it to the world."""
    profile = _hunter()
    _platted(profile, _utc(2019, 11, 11))
    run = _fill(_run(profile))

    resp = Client().get(_url(run, 11, 11))
    assert 'private' in resp['Cache-Control']
    assert 'public' not in resp['Cache-Control']


def test_the_square_endpoint_is_metered_by_ip_in_its_own_bucket(client):
    """BEHAVIOURAL, following `test_both_browse_pages_rate_limit_by_ip_in_their_own_bucket`, and drained
    through HEAD for the reason that test records: `django.views.View.setup` aliases `self.head =
    self.get` when a class defines no `head`, so a HEAD request runs the wrapped `get` and executes the
    whole query. Under `method='GET'` django_ratelimit does not count it, so `curl -I` in a loop would be
    unmetered against exactly the URL the limiter exists to protect.

    `key='ip'` AND NOT `key='user'`: `django_ratelimit`'s user handler is `str(r.user.pk)` and
    `AnonymousUser.pk` is `None`, so keying on the user would bucket every anonymous caller in the world
    together and one crawler would lock the endpoint for everybody.

    ITS OWN GROUP, because the default is derived from the decorated function's qualname -- and the
    refusal is 403 rather than 429, since `Ratelimited` subclasses `PermissionDenied` and no
    `RatelimitMiddleware` is installed.
    """
    from django.core.cache import cache

    from challenges.views import (CALENDAR_DAY_RATELIMIT_GROUP, CHALLENGES_BROWSE_RATELIMIT_GROUP)

    assert CALENDAR_DAY_RATELIMIT_GROUP != CHALLENGES_BROWSE_RATELIMIT_GROUP

    profile = _hunter()
    _platted(profile, _utc(2019, 12, 12))
    run = _fill(_run(profile))
    url = _url(run, 12, 12)

    cache.clear()
    try:
        for _ in range(60):
            assert client.head(url).status_code == 200

        assert client.get(url).status_code == 403, (
            'HEAD requests are not metered -- the budget was drained by 60 of them and GET still '
            "answered, which is the hole `method=('GET', 'HEAD')` closes")

        # A BUCKET OF ITS OWN: draining the square must not shut the browse page.
        assert client.get(reverse('challenges')).status_code == 200, (
            'the square endpoint is sharing the browse page bucket')
    finally:
        cache.clear()
