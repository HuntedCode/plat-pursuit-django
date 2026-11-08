"""Platinum Discord pings: ONE consolidated embed per sync (trophies/discord_utils/discord_notifications.py).

Hunters who clear a stack of short games in one session used to ping the channel once per platinum, during
the per-game `sync_trophies` jobs. Now each qualifying platinum is held in `pending_discord_plats:{id}` and
`sync_complete` sends them together.
"""
import ast
import pathlib
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.utils import timezone

from tests.factories import (
    ConceptFactory, EarnedTrophyFactory, GameFactory, IGDBMatchFactory, ProfileFactory, TrophyFactory,
)
from trophies.discord_utils import discord_notifications as dn
from trophies.services.psn_api_service import PsnApiService

pytestmark = pytest.mark.django_db

NOW = timezone.now
DISCORD_ID = '123456789012345678'


@pytest.fixture(autouse=True)
def redis(fake_redis, monkeypatch):
    monkeypatch.setattr('trophies.util_modules.cache.redis_client', fake_redis)
    return fake_redis


@pytest.fixture
def send():
    with patch('trophies.discord_utils.discord_notifications.queue_webhook_send') as mock_send:
        yield mock_send


def _hunter(**kw):
    return ProfileFactory(discord_id=DISCORD_ID, **kw)


def _plat(profile, when=None, game=None, name=None, **trophy_kw):
    trophy = TrophyFactory(game=game or GameFactory(), trophy_type='platinum', **trophy_kw,
                           **({'trophy_name': name} if name else {}))
    return EarnedTrophyFactory(profile=profile, trophy=trophy, earned_date_time=when or NOW() - timedelta(hours=1))


def _queued(redis, profile):
    return {int(i) for i in redis.smembers(f'pending_discord_plats:{profile.id}')}


def _sync_earn(profile, when=None):
    """A platinum arriving through the real sync write path."""
    trophy = TrophyFactory(game=GameFactory(), trophy_type='platinum')
    data = SimpleNamespace(earned=True, trophy_hidden=False, progress=None, progress_rate=None,
                           progressed_date_time=None, earned_date_time=when or NOW() - timedelta(minutes=5))
    et, _ = PsnApiService.create_or_update_earned_trophy_from_trophy_data(profile, trophy, data)
    return et


# --- queueing (the sync write path) --------------------------------------------------------------------------

def test_the_sync_path_holds_platinums_instead_of_pinging(redis, send):
    profile = _hunter()
    a, b = _sync_earn(profile), _sync_earn(profile)

    assert send.call_count == 0, 'a platinum pinged Discord mid-sync, one message per game again'
    assert _queued(redis, profile) == {a.id, b.id}


def test_an_old_or_unlinked_platinum_is_never_queued(redis):
    linked, unlinked = _hunter(), ProfileFactory()
    _sync_earn(linked, when=NOW() - timedelta(days=3))
    _sync_earn(unlinked)

    assert _queued(redis, linked) == set(), 'a platinum older than two days was queued'
    assert _queued(redis, unlinked) == set(), 'a hunter with no Discord was queued'


def test_a_retried_batch_does_not_list_a_platinum_twice(redis, send):
    profile = _hunter()
    et = _plat(profile)
    dn.queue_platinum_announcement(profile.id, et.id)
    dn.queue_platinum_announcement(profile.id, et.id)
    assert redis.scard(f'pending_discord_plats:{profile.id}') == 1, 'the queue is no longer a deduping Set'

    dn.flush_platinum_announcements(profile)

    embed = send.call_args.args[0]['embeds'][0]
    assert embed['title'].startswith('🎉 New Platinum for'), 'one platinum read as several'


def test_the_queue_expires(redis):
    profile = _hunter()
    dn.queue_platinum_announcement(profile.id, _plat(profile).id)
    assert 0 < redis.ttl(f'pending_discord_plats:{profile.id}') <= dn._PENDING_PLATS_TTL


# --- flushing (sync_complete) --------------------------------------------------------------------------------

def test_a_sync_of_several_platinums_is_one_ping_rarest_first(send):
    profile = _hunter()
    common = _plat(profile, name='Participation', trophy_earn_rate=62.5, trophy_icon_url='https://x/common.png')
    rare = _plat(profile, name='Legend', trophy_earn_rate=0.8, trophy_icon_url='https://x/rare.png',
                 when=NOW() - timedelta(hours=5))
    for et in (common, rare):
        dn.queue_platinum_announcement(profile.id, et.id)

    dn.flush_platinum_announcements(profile)

    assert send.call_count == 1
    embed = send.call_args.args[0]['embeds'][0]
    assert embed['title'] == f'🎉 2 New Platinums for {profile.display_psn_username}!'
    assert embed['description'].index('Legend') < embed['description'].index('Participation'), 'not rarest first'
    assert embed['thumbnail'] == {'url': 'https://x/rare.png'}, 'the rarest platinum should headline'


def test_an_unreported_rate_sorts_after_every_known_one(send):
    """0.0 is the model default for "PSN has not reported a rate yet", not a real 0%."""
    profile = _hunter()
    unknown = _plat(profile, name='Day One', trophy_earn_rate=0.0)
    known = _plat(profile, name='Measured', trophy_earn_rate=90.0)
    for et in (unknown, known):
        dn.queue_platinum_announcement(profile.id, et.id)

    dn.flush_platinum_announcements(profile)

    description = send.call_args.args[0]['embeds'][0]['description']
    assert description.index('Measured') < description.index('Day One')


def test_a_flush_clears_the_queue_so_the_next_sync_is_silent(redis, send):
    profile = _hunter()
    dn.queue_platinum_announcement(profile.id, _plat(profile).id)

    dn.flush_platinum_announcements(profile)
    dn.flush_platinum_announcements(profile)

    assert send.call_count == 1, 'the same platinums were announced by two syncs'
    assert _queued(redis, profile) == set()


def test_a_single_platinum_keeps_the_message_hunters_know(send):
    profile = _hunter()
    et = _plat(profile, name='Spotless', trophy_earn_rate=4.2)
    dn.queue_platinum_announcement(profile.id, et.id)

    dn.flush_platinum_announcements(profile)

    embed = send.call_args.args[0]['embeds'][0]
    assert embed['title'] == f'🎉 New Platinum for {profile.display_psn_username}!'
    assert '*Spotless*' in embed['description'] and '4.2% (PSN)' in embed['description']
    assert embed['footer']['text'] == f"Powered by Plat Pursuit | Earned: {et.earned_date_time:%Y-%m-%d}"


def test_shovelware_is_announced_but_sinks_into_the_tail(send):
    """One ping per sync means shovelware can no longer flood the channel, so it is no longer dropped.
    It runs common, so rarest-first ordering pushes it into "…and N more"."""
    profile = _hunter()
    for i in range(dn._MAX_PLATINUM_LINES):
        dn.queue_platinum_announcement(profile.id, _plat(profile, name=f'Real {i}', trophy_earn_rate=5.0 + i).id)
    shovel = _plat(profile, game=GameFactory(shovelware_status='auto_flagged'), name='Five Minute Plat',
                   trophy_earn_rate=88.0)
    dn.queue_platinum_announcement(profile.id, shovel.id)

    dn.flush_platinum_announcements(profile)

    embed = send.call_args.args[0]['embeds'][0]
    assert embed['title'].startswith(f'🎉 {dn._MAX_PLATINUM_LINES + 1} '), 'the shovelware platinum was dropped'
    assert 'Five Minute Plat' not in embed['description']
    assert embed['description'].endswith('…and 1 more')


def test_a_sync_of_only_shovelware_is_still_announced(send):
    profile = _hunter()
    dn.queue_platinum_announcement(
        profile.id, _plat(profile, game=GameFactory(shovelware_status='manually_flagged'), name='Quick One').id)

    dn.flush_platinum_announcements(profile)

    assert 'Quick One' in send.call_args.args[0]['embeds'][0]['description']


def test_an_unearned_or_foreign_id_is_not_announced(send):
    """A rolled-back batch can leave a queued id whose earn never committed."""
    profile = _hunter()
    rolled_back = _plat(profile)
    rolled_back.earned = False
    rolled_back.save()
    someone_elses = _plat(ProfileFactory(discord_id='999'))
    for et in (rolled_back, someone_elses):
        dn.queue_platinum_announcement(profile.id, et.id)

    dn.flush_platinum_announcements(profile)

    assert send.call_count == 0


def test_a_huge_sync_still_fits_one_embed(send):
    """Discord 400s a description over 4096 characters, and the worker drops any non-429 whole."""
    profile = _hunter()
    for i in range(25):
        et = _plat(profile, game=GameFactory(title_name=f'An Extremely Long Shovelware Adjacent Title {i} ' * 2),
                   name=f'A Trophy Name That Goes On And On For A While {i}')
        dn.queue_platinum_announcement(profile.id, et.id)

    dn.flush_platinum_announcements(profile)

    assert send.call_count == 1
    embed = send.call_args.args[0]['embeds'][0]
    assert len(embed['description']) <= 4096
    assert '…and 15 more' in embed['description']
    assert embed['title'].startswith('🎉 25 '), 'the title should report the real total'


def test_worst_case_names_still_fit_one_embed(send):
    """Both names are max_length=255 and escaping doubles a markdown-heavy one, so ten lines alone can pass
    4096. The builder must stop at the budget, not trust the line cap."""
    profile = _hunter()
    for i in range(10):
        et = _plat(profile, game=GameFactory(title_name=f'{i}' + '*' * 254), name=f'{i}' + '_' * 254)
        dn.queue_platinum_announcement(profile.id, et.id)

    dn.flush_platinum_announcements(profile)

    description = send.call_args.args[0]['embeds'][0]['description']
    assert dn._utf16_len(description) <= 4096, f'description is {dn._utf16_len(description)} units -- Discord will 400 this'
    assert 'more' in description.splitlines()[-1], 'the platinums that did not fit are not counted'


def test_a_leftover_from_a_dead_sync_is_not_announced_days_later(send):
    """A sync that raised before its flush leaves the Set behind, and a later platinum keeps renewing its
    TTL. The window is re-checked at flush so the old ids cannot ping as news."""
    profile = _hunter()
    stale = _plat(profile, when=NOW() - timedelta(days=3), name='Old News')
    fresh = _plat(profile, name='Hot Off The Press')
    for et in (stale, fresh):
        dn.queue_platinum_announcement(profile.id, et.id)

    dn.flush_platinum_announcements(profile)

    embed = send.call_args.args[0]['embeds'][0]
    assert 'Old News' not in embed['description'] and 'Hot Off The Press' in embed['description']


def test_a_platinum_with_no_icon_omits_the_thumbnail(send):
    """`{'url': None}` can 400, and one embed now carries the whole batch."""
    profile = _hunter()
    dn.queue_platinum_announcement(profile.id, _plat(profile, trophy_icon_url=None).id)

    dn.flush_platinum_announcements(profile)

    assert 'thumbnail' not in send.call_args.args[0]['embeds'][0]


def test_the_headliner_game_art_is_the_large_image(send):
    """The rarest platinum's game supplies the embed's large image."""
    profile = _hunter()
    rare_game = GameFactory(concept=ConceptFactory(bg_url='https://x/bloodborne-bg.jpg'), title_image='https://x/cover.png')
    common_game = GameFactory(concept=ConceptFactory(bg_url='https://x/shovel-bg.jpg'))
    for et in (_plat(profile, game=common_game, trophy_earn_rate=90.0),
               _plat(profile, game=rare_game, trophy_earn_rate=2.0)):
        dn.queue_platinum_announcement(profile.id, et.id)

    dn.flush_platinum_announcements(profile)

    assert send.call_args.args[0]['embeds'][0]['image'] == {'url': 'https://x/bloodborne-bg.jpg'}


def test_the_art_follows_the_shared_landscape_chain(send):
    """`Concept.get_landscape_url` is the site's one landscape chain (trusted IGDB screenshot first, PSN
    bg_url last). The embed must use it, not read bg_url directly, or Discord shows different art than
    every banner on the site."""
    profile = _hunter()
    concept = ConceptFactory(bg_url='https://x/psn-bg.jpg')
    IGDBMatchFactory(concept=concept, igdb_screenshot_image_ids=['shot1'])
    dn.queue_platinum_announcement(profile.id, _plat(profile, game=GameFactory(concept=concept)).id)

    dn.flush_platinum_announcements(profile)

    url = send.call_args.args[0]['embeds'][0]['image']['url']
    assert url == concept.get_landscape_url() and 'shot1' in url


def test_a_lone_platinum_gets_the_art_too(send):
    profile = _hunter()
    game = GameFactory(concept=ConceptFactory(bg_url='https://x/bg.jpg'))
    dn.queue_platinum_announcement(profile.id, _plat(profile, game=game).id)

    dn.flush_platinum_announcements(profile)

    assert send.call_args.args[0]['embeds'][0]['image'] == {'url': 'https://x/bg.jpg'}


def test_no_background_falls_back_to_the_cover(send):
    """No landscape art at all (no trusted IGDB media, no PSN bg_url): the portrait cover chain."""
    profile = _hunter()
    game = GameFactory(concept=ConceptFactory(bg_url=None), title_image='https://x/cover.png')
    dn.queue_platinum_announcement(profile.id, _plat(profile, game=game).id)

    dn.flush_platinum_announcements(profile)

    assert send.call_args.args[0]['embeds'][0]['image'] == {'url': 'https://x/cover.png'}


def test_no_usable_art_omits_the_image(send):
    profile = _hunter()
    game = GameFactory(concept=ConceptFactory(bg_url=None), title_image=None, title_icon_url='/static/x.png')
    dn.queue_platinum_announcement(profile.id, _plat(profile, game=game).id)

    dn.flush_platinum_announcements(profile)

    assert send.call_count == 1, 'missing art cost the ping'
    assert 'image' not in send.call_args.args[0]['embeds'][0], 'a relative or empty URL was sent to Discord'


def test_stacks_of_one_game_collapse_to_one_line(send):
    """Shovelware hunters pop 4+ regional/platform stacks of one game at once. Stacks share a concept."""
    profile = _hunter()
    concept = ConceptFactory()
    for rate in (91.0, 88.5, 93.2, 90.0):
        et = _plat(profile, game=GameFactory(concept=concept, title_name='Jumpy Cat'), trophy_earn_rate=rate)
        dn.queue_platinum_announcement(profile.id, et.id)
    dn.queue_platinum_announcement(profile.id, _plat(profile, name='Legend', trophy_earn_rate=3.0).id)

    dn.flush_platinum_announcements(profile)

    embed = send.call_args.args[0]['embeds'][0]
    assert embed['title'].startswith('🎉 5 '), 'the title should count platinums, not lines'
    assert embed['description'].count('Jumpy Cat') == 1, 'a stack was listed once per version'
    assert '**Jumpy Cat** · 🌟 88.5% (×4)' in embed['description'], 'the stack shows its rarest rate and count'
    assert embed['description'].index('Legend') < embed['description'].index('Jumpy Cat')


def test_games_with_no_concept_never_stack_together(send):
    """A concept-less game stands alone: two of them must not merge into one "(×2)" line."""
    profile = _hunter()
    for title in ('Orphan One', 'Orphan Two'):
        dn.queue_platinum_announcement(
            profile.id, _plat(profile, game=GameFactory(concept=None, title_name=title)).id)

    dn.flush_platinum_announcements(profile)

    description = send.call_args.args[0]['embeds'][0]['description']
    assert 'Orphan One' in description and 'Orphan Two' in description and '×' not in description


def test_the_footer_spans_the_days_earned(send):
    profile = _hunter()
    first = _plat(profile, when=NOW() - timedelta(days=1, hours=2))
    last = _plat(profile, when=NOW() - timedelta(minutes=5))
    for et in (first, last):
        dn.queue_platinum_announcement(profile.id, et.id)

    dn.flush_platinum_announcements(profile)

    footer = send.call_args.args[0]['embeds'][0]['footer']['text']
    assert footer.endswith(f"Earned: {first.earned_date_time:%Y-%m-%d} to {last.earned_date_time:%Y-%m-%d}")


def test_more_counts_platinums_when_hidden_lines_are_stacks(send):
    """The title counts platinums, so the tail must too, or the numbers stop adding up."""
    profile = _hunter()
    for i in range(dn._MAX_PLATINUM_LINES):
        dn.queue_platinum_announcement(profile.id, _plat(profile, trophy_earn_rate=1.0 + i).id)
    concept = ConceptFactory()
    for _ in range(3):
        dn.queue_platinum_announcement(
            profile.id, _plat(profile, game=GameFactory(concept=concept), trophy_earn_rate=95.0).id)

    dn.flush_platinum_announcements(profile)

    assert send.call_args.args[0]['embeds'][0]['description'].endswith('…and 3 more')


def test_psn_text_cannot_ping_the_server_or_break_formatting(send):
    profile = _hunter()
    et = _plat(profile, game=GameFactory(title_name='Game @everyone **bold**'), name='[x](http://e.vil)')
    dn.queue_platinum_announcement(profile.id, et.id)

    dn.flush_platinum_announcements(profile)

    payload = send.call_args.args[0]
    assert payload['allowed_mentions'] == {'users': [DISCORD_ID]}
    assert '\\*\\*bold\\*\\*' in payload['embeds'][0]['description']
    assert '\\[x\\]\\(http://e.vil\\)' in payload['embeds'][0]['description']


def test_a_trailing_backslash_cannot_break_the_italics(send):
    r"""Unescaped, `Slash\` would escape the closing `*` and leave the line's formatting open."""
    profile = _hunter()
    dn.queue_platinum_announcement(profile.id, _plat(profile, name='Slash\\').id)

    dn.flush_platinum_announcements(profile)

    assert r'*Slash\\*' in send.call_args.args[0]['embeds'][0]['description']


def test_a_flush_never_raises(send):
    profile = _hunter()
    with patch('trophies.util_modules.cache.redis_client.pipeline', side_effect=RuntimeError('redis down')):
        dn.flush_platinum_announcements(profile)
        dn.queue_platinum_announcement(profile.id, 1)
    assert send.call_count == 0


# --- wiring --------------------------------------------------------------------------------------------------

def test_sync_complete_flushes_before_announcing_badges():
    """AST, not import or substring: importing token_keeper registers an atexit logger, and a substring scan
    matches a commented-out call. Platinums go first so a sync that earns both reads in order."""
    source = pathlib.Path(__file__).resolve().parents[2] / 'trophies' / 'token_keeper.py'
    target = next(n for n in ast.walk(ast.parse(source.read_text(encoding='utf-8')))
                  if isinstance(n, ast.FunctionDef) and n.name == '_job_sync_complete')
    calls = [n for n in ast.walk(target) if isinstance(n, ast.Call)]
    lines = {n.func.id: n.lineno for n in calls if isinstance(n.func, ast.Name)}
    [stats_phase] = [n.lineno for n in calls if isinstance(n.func, ast.Name) and n.func.id == '_set_phase'
                     and n.args and getattr(n.args[0], 'value', None) == 'stats_badges']

    assert 'flush_platinum_announcements' in lines, 'sync_complete no longer sends the queued platinums'
    assert stats_phase < lines['flush_platinum_announcements'], (
        'the flush moved above the completeness re-queue, so a re-queued sync would ping twice'
    )
    assert lines['flush_platinum_announcements'] < lines['evaluate_for_sync']
