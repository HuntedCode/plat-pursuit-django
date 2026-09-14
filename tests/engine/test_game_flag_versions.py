"""Reporting one issue against several versions of the same game.

The Game page is concept-level: it wraps every trophy list that resolves to one IGDB id, and a data
problem ("delisted", "trophies unobtainable") is usually true of more than one of them. Before this,
its Report button posted a single game id -- the HOST list, elected by platform priority -- so a
reader on the PS5 list filed against whatever version won that election, silently.

The fan-out is server-side because it has to be: the endpoint is rate-limited to 5/min per user, so
a client loop over a six-list concept would 429 part way through with some rows already filed and
nothing in the UI able to say which.

The security line is `version_peer_qs`: a submitted game id is only accepted if it is a version of
the anchor. Without that the endpoint takes arbitrary ids, and 5 requests/min x MAX_BULK_VERSIONS is
a mass-flagging tool pointed anywhere in the catalog.
"""
import re

import pytest

from tests.factories import ConceptFactory, GameFactory, ProfileFactory, UserFactory
from trophies.models import GameFlag, IGDBMatch
from trophies.services.game_flag_service import GameFlagService
from users.services import restriction_service

pytestmark = pytest.mark.django_db


def _hunter(psn=None):
    """A linked reporter. The username defaults to ProfileFactory's uniqueness sequence rather than a
    pinned literal -- several tests below need TWO reporters, and a pinned one IntegrityErrors."""
    kwargs = {'is_linked': True, 'guidelines_agreed': True}
    if psn is not None:
        kwargs['psn_username'] = psn
    return ProfileFactory(**kwargs)


def _match(concept, igdb_id):
    return IGDBMatch.objects.create(concept=concept, igdb_id=igdb_id, status='accepted')


def _game(igdb_id=None, **over):
    concept = ConceptFactory()
    if igdb_id is not None:
        _match(concept, igdb_id)
    over.setdefault('title_platform', ['PS4'])
    return GameFactory(concept=concept, **over)


def _versions(igdb_id=777, platforms=('PS5', 'PS4', 'PS3')):
    """N trophy lists that all resolve to one IGDB id, via SEPARATE concepts -- the split-concept
    case, which is the one a naive same-concept membership check would get wrong."""
    games = []
    for plat in platforms:
        concept = ConceptFactory()
        _match(concept, igdb_id)
        games.append(GameFactory(concept=concept, title_platform=[plat]))
    return games


def _post(client, anchor, game_ids, flag_type='delisted', details=''):
    return client.post(
        f'/api/v1/games/{anchor.pk}/flag/versions/',
        {'game_ids': game_ids, 'flag_type': flag_type, 'details': details},
        content_type='application/json',
    )


# ── the fan-out ──────────────────────────────────────────────────────────────────────────────────

def test_one_request_files_one_flag_per_selected_version(client):
    hunter = _hunter()
    ps5, ps4, ps3 = _versions()
    client.force_login(hunter.user)

    resp = _post(client, ps5, [ps5.pk, ps4.pk], details='Store page is gone')

    assert resp.status_code == 200
    assert resp.json()['created'] == 2
    assert set(GameFlag.objects.values_list('game_id', flat=True)) == {ps5.pk, ps4.pk}
    assert ps3.pk not in set(GameFlag.objects.values_list('game_id', flat=True))


def test_every_row_carries_the_same_type_and_details(client):
    """One issue reported against several versions -- not several different reports. A moderator
    reading the queue has to see the same claim on each row."""
    hunter = _hunter()
    ps5, ps4, _ps3 = _versions()
    client.force_login(hunter.user)

    _post(client, ps5, [ps5.pk, ps4.pk], flag_type='unobtainable', details='Servers closed')

    flags = GameFlag.objects.all()
    assert {f.flag_type for f in flags} == {'unobtainable'}
    assert {f.details for f in flags} == {'Servers closed'}
    assert {f.reporter_id for f in flags} == {hunter.pk}
    assert {f.status for f in flags} == {'pending'}


def test_a_single_version_still_works(client):
    """The selector's floor. Picking one version must not need a different code path than picking
    three, or the common case is the untested one."""
    hunter = _hunter()
    ps5, _ps4, _ps3 = _versions()
    client.force_login(hunter.user)

    resp = _post(client, ps5, [ps5.pk])

    assert resp.json()['created'] == 1
    assert GameFlag.objects.count() == 1


def test_unmatched_concepts_fall_back_to_their_own_concept(client):
    """A concept with no IGDB match still has versions -- its own concept's lists. The fallback is
    what keeps a PSN-only title from being alone by accident."""
    hunter = _hunter()
    concept = ConceptFactory()
    a = GameFactory(concept=concept, title_platform=['PS4'])
    b = GameFactory(concept=concept, title_platform=['PS5'])
    client.force_login(hunter.user)

    resp = _post(client, a, [a.pk, b.pk])

    assert resp.json()['created'] == 2


# ── the membership boundary ──────────────────────────────────────────────────────────────────────

def test_a_game_id_outside_the_anchors_versions_is_refused(client):
    """THE security test. An id the anchor's own page could never have offered is dropped, not
    filed -- the client's list is exactly what an attacker controls."""
    hunter = _hunter()
    ps5, ps4, _ps3 = _versions(igdb_id=777)
    unrelated = _game(igdb_id=999)
    client.force_login(hunter.user)

    resp = _post(client, ps5, [ps5.pk, unrelated.pk])

    assert resp.status_code == 200
    assert GameFlag.objects.filter(game=unrelated).count() == 0, 'filed against an unrelated game'
    assert set(GameFlag.objects.values_list('game_id', flat=True)) == {ps5.pk}
    assert ps4.pk not in set(GameFlag.objects.values_list('game_id', flat=True))


def test_a_payload_of_only_foreign_ids_is_rejected_outright(client):
    hunter = _hunter()
    ps5, _ps4, _ps3 = _versions(igdb_id=777)
    unrelated = _game(igdb_id=999)
    client.force_login(hunter.user)

    resp = _post(client, ps5, [unrelated.pk])

    assert resp.status_code == 400
    assert GameFlag.objects.count() == 0


def test_more_versions_than_the_cap_is_refused_without_filing_any(client):
    """The per-request cap is what stops 5 requests/min from meaning unlimited rows. It must refuse
    the whole payload, not truncate it -- a silently truncated report is worse than a rejected one.

    The versions must be REAL PEERS. An earlier version of this test sent `range(1, CAP + 2)` --
    arbitrary integers that are not versions of the anchor -- so the 400 came from the membership
    check and deleting the cap entirely still passed. It was also non-deterministic: on a fresh
    database those low pks could BE real rows and the request would take a third path.
    """
    over_cap = GameFlagService.MAX_BULK_VERSIONS + 1
    hunter = _hunter()
    peers = _versions(platforms=tuple(f'PLAT{i}' for i in range(over_cap)))
    client.force_login(hunter.user)

    resp = _post(client, peers[0], [g.pk for g in peers])

    assert resp.status_code == 400
    assert str(GameFlagService.MAX_BULK_VERSIONS) in resp.json()['error'], (
        'the refusal came from somewhere other than the cap')
    assert GameFlag.objects.count() == 0


def test_the_cap_counts_the_submitted_set_before_the_peer_filter(client):
    """Two different guards: the view caps what was SUBMITTED (before the DB is touched), the service
    caps what SURVIVED the peer filter. A payload of 26 junk ids must be refused by the first one
    rather than quietly shrinking to nothing and reporting a different problem."""
    hunter = _hunter()
    ps5, _ps4, _ps3 = _versions()
    client.force_login(hunter.user)

    junk = list(range(9_000_000, 9_000_000 + GameFlagService.MAX_BULK_VERSIONS + 1))
    resp = _post(client, ps5, junk)

    assert resp.status_code == 400
    assert str(GameFlagService.MAX_BULK_VERSIONS) in resp.json()['error']
    assert GameFlag.objects.count() == 0


def test_an_empty_selection_is_refused(client):
    hunter = _hunter()
    ps5, _ps4, _ps3 = _versions()
    client.force_login(hunter.user)

    resp = _post(client, ps5, [])

    assert resp.status_code == 400
    assert GameFlag.objects.count() == 0


def test_junk_game_ids_are_refused_not_coerced(client):
    hunter = _hunter()
    ps5, _ps4, _ps3 = _versions()
    client.force_login(hunter.user)

    assert _post(client, ps5, ['not-a-number']).status_code == 400
    assert client.post(f'/api/v1/games/{ps5.pk}/flag/versions/',
                       {'game_ids': 'all', 'flag_type': 'delisted'},
                       content_type='application/json').status_code == 400
    assert GameFlag.objects.count() == 0


def test_an_invalid_flag_type_files_nothing(client):
    hunter = _hunter()
    ps5, ps4, _ps3 = _versions()
    client.force_login(hunter.user)

    resp = _post(client, ps5, [ps5.pk, ps4.pk], flag_type='make_it_free')

    assert resp.status_code == 400
    assert GameFlag.objects.count() == 0


# ── duplicates, and telling the truth about them ─────────────────────────────────────────────────

def test_re_reporting_does_not_double_file(client):
    hunter = _hunter()
    ps5, ps4, _ps3 = _versions()
    client.force_login(hunter.user)

    _post(client, ps5, [ps5.pk])
    resp = _post(client, ps5, [ps5.pk, ps4.pk])

    assert GameFlag.objects.count() == 2, 'the second submission re-filed an existing pending flag'
    body = resp.json()
    assert body['created'] == 1
    assert body['duplicates'] == 1


def test_an_all_duplicate_submission_says_so_instead_of_claiming_success(client):
    """The message this screen could most easily get wrong: a flat 'Reported 2 versions' for a
    submission that filed nothing."""
    hunter = _hunter()
    ps5, ps4, _ps3 = _versions()
    client.force_login(hunter.user)

    _post(client, ps5, [ps5.pk, ps4.pk])
    resp = _post(client, ps5, [ps5.pk, ps4.pk])

    body = resp.json()
    assert body['created'] == 0
    assert body['duplicates'] == 2
    msg = body['message'].lower()
    assert 'already reported' in msg
    # Anchored on the OPENING claim, not on a substring: "you had already reported 2 versions"
    # contains "reported 2 versions" too, so a contains-check here passes for both the true message
    # and the false one it exists to rule out.
    assert not msg.startswith('reported '), f'the all-duplicate case claimed new reports: {msg}'


def test_a_dismissed_flag_does_not_block_a_fresh_report(client):
    """Dedup is on PENDING rows only. A dismissed flag is a closed decision, and a later re-report
    is new information, not a duplicate."""
    hunter = _hunter()
    ps5, _ps4, _ps3 = _versions()
    client.force_login(hunter.user)
    _post(client, ps5, [ps5.pk])
    GameFlag.objects.update(status='dismissed')

    resp = _post(client, ps5, [ps5.pk])

    assert resp.json()['created'] == 1
    assert GameFlag.objects.filter(status='pending').count() == 1


# ── the same gates the single-game endpoint has ──────────────────────────────────────────────────

def test_anonymous_cannot_file(client):
    ps5, ps4, _ps3 = _versions()

    resp = _post(client, ps5, [ps5.pk, ps4.pk])

    assert resp.status_code in (401, 403)
    assert GameFlag.objects.count() == 0


def test_an_unlinked_profile_cannot_file(client):
    """Same gate as the single endpoint -- `CommentService.can_interact`, not an inline is_linked
    copy, so a restriction reaches both endpoints or neither."""
    user = UserFactory()
    ProfileFactory(user=user, is_linked=False, guidelines_agreed=True)
    ps5, ps4, _ps3 = _versions()
    client.force_login(user)

    resp = _post(client, ps5, [ps5.pk, ps4.pk])

    assert resp.status_code == 403
    assert GameFlag.objects.count() == 0


def test_a_reporting_restriction_blocks_the_whole_batch(client):
    """Atomic: the restriction is enforced inside `submit_flag`, the single writer, so a batch that
    hits it must roll back rather than leave the rows filed before the refusal."""
    hunter = _hunter()
    ps5, ps4, ps3 = _versions()
    admin = UserFactory()
    admin.role = 'admin'
    admin.save()
    restriction_service.apply_restriction(hunter.user, 'reports', admin, 'spam, third time')
    client.force_login(hunter.user)

    resp = _post(client, ps5, [ps5.pk, ps4.pk, ps3.pk])

    assert resp.status_code == 403
    assert GameFlag.objects.count() == 0, 'a restricted hunter filed part of a batch'


# ── the shared same-work rule ────────────────────────────────────────────────────────────────────

def test_version_peer_qs_includes_the_anchor_itself():
    """`_build_other_versions` excludes the anchor for its 'other versions' card; the flag endpoint
    must NOT, or the version a reader is looking at is the one id it refuses."""
    from trophies.services.game_grouping_service import version_peer_qs

    ps5, ps4, ps3 = _versions()

    peers = set(version_peer_qs(ps5).values_list('pk', flat=True))

    assert peers == {ps5.pk, ps4.pk, ps3.pk}


def test_version_peer_qs_is_the_rule_build_other_versions_uses():
    """One definition, two readers. If these drift, the selector offers versions the About tab's
    card does not list -- or worse, the endpoint accepts ids no page ever showed."""
    from trophies.views.game_views import GameDetailView
    from trophies.services.game_grouping_service import version_peer_qs

    ps5, ps4, ps3 = _versions()

    card = {g.pk for g in GameDetailView()._build_other_versions(ps5)}
    endpoint = set(version_peer_qs(ps5).values_list('pk', flat=True))

    assert endpoint - {ps5.pk} == card
    assert card == {ps4.pk, ps3.pk}


def test_a_conceptless_game_is_its_own_only_version():
    from trophies.services.game_grouping_service import version_peer_qs

    orphan = GameFactory(concept=None, title_platform=['PS4'])

    assert set(version_peer_qs(orphan).values_list('pk', flat=True)) == {orphan.pk}


# ── what the two pages render ────────────────────────────────────────────────────────────────────

def _page_versions(igdb_id, platforms=('PS5', 'PS4')):
    """Versions with trophies, so both pages render their grids rather than the syncing state."""
    from tests.factories import TrophyFactory

    games = _versions(igdb_id=igdb_id, platforms=platforms)
    for i, g in enumerate(games):
        TrophyFactory(game=g, trophy_id=i + 1)
    return games


def test_the_game_page_offers_a_report_button_to_a_linked_viewer(client):
    """It had none before this: the modal was reachable only from the About tab's empty-state CTA,
    so on a game WITH about data there was no way to report at all."""
    hunter = _hunter()
    _page_versions(920)
    client.force_login(hunter.user)

    content = client.get('/games/920/').content.decode()

    # Scoped to the button's own element. Bare `'Report an issue' in content` also matches the
    # modal's <h3> on the same page, so it passed with the button's visible label deleted.
    assert re.search(r'<button[^>]*gp-report[^>]*>.*?Report an issue.*?</button>', content, re.S), (
        'the report button rendered without its visible label')


def test_the_report_button_is_not_offered_to_anonymous_visitors(client):
    """The modal itself is gated on a linked viewer, so an ungated button opens nothing.

    The positive assertions are load-bearing: with only the two `not in` checks this passed while
    the page 404'd for every visitor, which is not what it claims to prove.
    """
    _page_versions(921)

    resp = client.get('/games/921/')
    content = resp.content.decode()

    assert resp.status_code == 200
    assert 'gp-lswitch' in content, 'the page did not actually render'
    assert 'gp-report' not in content
    assert 'game-flag-modal' not in content


def test_the_game_pages_modal_lists_every_version_as_a_checkbox(client):
    hunter = _hunter()
    ps5, ps4 = _page_versions(922)
    client.force_login(hunter.user)

    content = client.get('/games/922/').content.decode()

    assert 'gd-report__versions' in content
    assert f'name="game_ids" value="{ps5.pk}"' in content
    assert f'name="game_ids" value="{ps4.pk}"' in content
    assert 'data-flag-bulk' in content, 'the form must mark itself as the bulk shape for the JS'
    # Every hook game-flag.js binds. There is no JS test harness in this repo, so the SSR contract is
    # the only thing standing between a renamed hook and a silently dead control -- and a hook
    # rendered with no caller is a bug this codebase has shipped repeatedly.
    for hook in ('data-flag-all', 'data-flag-none', 'data-flag-vcount', 'data-flag-submit'):
        assert hook in content, f'{hook} is bound by game-flag.js but no longer rendered'


def test_the_viewed_list_is_the_one_pre_checked(client):
    """The decided default: submitting without touching the selector reports the version on screen,
    so the zero-click path matches what the old single-game button was TRYING to do."""
    hunter = _hunter()
    ps5, ps4 = _page_versions(923)
    client.force_login(hunter.user)

    content = client.get(f'/games/923/?list={ps4.np_communication_id}').content.decode()

    # Scoped to each row's own input, never a bare 'checked' search: the page has other
    # checkboxes, and the two version rows sit next to each other in the markup.
    assert _row_is_checked(content, ps4.pk), 'the version being viewed was not pre-checked'
    assert not _row_is_checked(content, ps5.pk), 'a version the reader was not viewing came pre-checked'


def _row_is_checked(content, game_pk):
    """True when THIS version's checkbox carries `checked`. Anchored on the input's own value so a
    `checked` belonging to the neighbouring row cannot satisfy it."""
    m = re.search(rf'name="game_ids" value="{game_pk}"(.*?)/>', content, re.S)
    assert m, f'no version row rendered for game {game_pk}'
    return 'checked' in m.group(1)


def test_the_trophy_list_page_keeps_the_single_list_modal(client):
    """The shared partial branches on `flag_versions`, which only the Game page passes. List detail
    IS one trophy list -- a version selector there would ask a question with one answer."""
    hunter = _hunter()
    ps5, _ps4 = _page_versions(924)
    client.force_login(hunter.user)

    resp = client.get(f'/games/{ps5.np_communication_id}/')
    content = resp.content.decode()

    assert resp.status_code == 200
    # The positive assertion anchors the three negatives: without it they all pass on any page that
    # failed to render the modal at all.
    assert 'game-flag-modal' in content, 'the report modal should still be on the list page'
    assert 'gd-report__versions' not in content
    assert 'name="game_ids"' not in content
    assert 'data-flag-bulk' not in content


def test_the_service_rolls_back_rows_it_had_already_filed(monkeypatch):
    """THE rollback test, and it has to force a MID-loop failure to be one.

    A restriction -- the only refusal reachable in the loop today -- is account-wide, so it fires on
    iteration #1 and there is never a partial batch to roll back. A test built on it passes with
    `@transaction.atomic` stripped from both methods, which is exactly what it claims to pin (proven
    by mutation: removing both decorators left the restriction version green).

    So this one lets version #1 through and refuses #2, the only shape where "0 rows" means rolled
    back rather than never written.
    """
    from trophies.services.game_flag_service import GameFlagSubmissionError

    hunter = _hunter()
    ps5, ps4, ps3 = _versions()
    real = GameFlagService.submit_flag
    calls = {'n': 0}

    def flaky(game, reporter, flag_type, details=''):
        calls['n'] += 1
        if calls['n'] == 2:
            return None, 'Your account is currently restricted from filing reports.'
        return real(game, reporter, flag_type, details)

    monkeypatch.setattr(GameFlagService, 'submit_flag', staticmethod(flaky))

    with pytest.raises(GameFlagSubmissionError):
        GameFlagService.submit_flags([ps5, ps4, ps3], hunter, 'delisted')

    assert calls['n'] == 2, 'the loop kept going after a refusal'
    assert GameFlag.objects.count() == 0, (
        'the row filed before the refusal survived -- the batch is not atomic')


def test_a_restriction_hit_inside_the_loop_files_nothing():
    """The same guarantee through its real trigger. Kept alongside the monkeypatched test above: that
    one proves atomicity, this one proves the restriction is actually consulted by the single writer.
    Neither covers both.
    """
    from trophies.services.game_flag_service import GameFlagSubmissionError

    hunter = _hunter()
    ps5, ps4, ps3 = _versions()
    admin = UserFactory()
    admin.role = 'admin'
    admin.save()
    restriction_service.apply_restriction(hunter.user, 'reports', admin, 'spam, third time')

    with pytest.raises(GameFlagSubmissionError):
        GameFlagService.submit_flags([ps5, ps4, ps3], hunter, 'delisted')

    assert GameFlag.objects.count() == 0


def test_a_null_igdb_id_never_makes_two_unrelated_games_peers():
    """`IGDBMatch.igdb_id` is nullable and NOT unique. Branch `version_peer_qs` on the match EXISTING
    rather than on its id being non-null and the filter becomes `igdb_id IS NULL`, collapsing every
    null-id concept in the catalogue into ONE version set. This function is the bulk endpoint's
    security boundary, so that is a mass-flagging vector, not a display bug.

    `test_unmatched_concepts_fall_back_to_their_own_concept` does not cover it: concepts with NO
    match row reach the same branch by a different input.
    """
    from trophies.services.game_grouping_service import version_peer_qs

    a, b = ConceptFactory(), ConceptFactory()
    IGDBMatch.objects.create(concept=a, igdb_id=None, status='accepted')
    IGDBMatch.objects.create(concept=b, igdb_id=None, status='accepted')
    game_a = GameFactory(concept=a, title_platform=['PS4'])
    game_b = GameFactory(concept=b, title_platform=['PS5'])

    peers = set(version_peer_qs(game_a).values_list('pk', flat=True))

    assert game_b.pk not in peers, 'two unrelated null-id concepts were treated as one game'
    assert peers == {game_a.pk}


# ── failure modes the first pass missed ──────────────────────────────────────────────────────────

def test_a_missing_flag_type_is_refused(client):
    hunter = _hunter()
    ps5, _ps4, _ps3 = _versions()
    client.force_login(hunter.user)

    resp = client.post(f'/api/v1/games/{ps5.pk}/flag/versions/',
                       {'game_ids': [ps5.pk]}, content_type='application/json')

    assert resp.status_code == 400
    assert GameFlag.objects.count() == 0


def test_an_unknown_anchor_game_404s(client):
    hunter = _hunter()
    _versions()
    client.force_login(hunter.user)

    resp = client.post('/api/v1/games/99999999/flag/versions/',
                       {'game_ids': [1], 'flag_type': 'delisted'},
                       content_type='application/json')

    assert resp.status_code == 404
    assert GameFlag.objects.count() == 0


def test_a_conceptless_anchor_can_only_flag_itself(client):
    """Through the POST path, not just the helper: the view select_relateds `concept__igdb_match` on
    a null concept before `version_peer_qs` ever sees it."""
    hunter = _hunter()
    orphan = GameFactory(concept=None, title_platform=['PS4'])
    other = _game(igdb_id=4321)
    client.force_login(hunter.user)

    resp = _post(client, orphan, [orphan.pk, other.pk])

    assert resp.status_code == 200
    assert set(GameFlag.objects.values_list('game_id', flat=True)) == {orphan.pk}


def test_an_approved_flag_does_not_block_a_fresh_report(client):
    """Dedup filters `status='pending'`, so approved behaves like dismissed. Deliberate (the game may
    have changed again) but previously unpinned -- only the dismissed half had a test."""
    hunter = _hunter()
    ps5, _ps4, _ps3 = _versions()
    client.force_login(hunter.user)
    _post(client, ps5, [ps5.pk])
    GameFlag.objects.update(status='approved')

    resp = _post(client, ps5, [ps5.pk])

    assert resp.json()['created'] == 1
    assert GameFlag.objects.filter(status='pending').count() == 1


def test_one_reporters_pending_flag_does_not_silence_another_reporter(client):
    """Dedup is per (game, reporter, type). Drop `reporter` from either the pre-query or
    `submit_flag`'s lookup and `test_re_reporting_does_not_double_file` still passes -- this is the
    test that notices."""
    first, second = _hunter(), _hunter()
    ps5, _ps4, _ps3 = _versions()

    client.force_login(first.user)
    _post(client, ps5, [ps5.pk])
    client.force_login(second.user)
    resp = _post(client, ps5, [ps5.pk])

    assert resp.json()['created'] == 1, "the second reporter's flag was swallowed as a duplicate"
    assert GameFlag.objects.filter(game=ps5, status='pending').count() == 2
    assert set(GameFlag.objects.values_list('reporter_id', flat=True)) == {first.pk, second.pk}


def test_overlong_details_are_truncated_not_stored_whole(client):
    hunter = _hunter()
    ps5, ps4, _ps3 = _versions()
    client.force_login(hunter.user)

    _post(client, ps5, [ps5.pk, ps4.pk], details='A' * 5000)

    for flag in GameFlag.objects.all():
        assert len(flag.details) == 500


def test_non_string_details_cannot_slip_past_the_length_cap(client):
    """`(details or '')[:500]` assumed a string. Slicing a LIST is a no-op that returns the list, and
    TextField str()s it on save -- so a 20 KB array stored 20 KB per row, 25 rows at a time, past a
    cap that looked like it enforced 500. A dict was worse: slicing one raises TypeError inside the
    transaction and surfaced as a 500 on what should be a 400."""
    hunter = _hunter()
    ps5, _ps4, _ps3 = _versions()
    client.force_login(hunter.user)

    resp = client.post(f'/api/v1/games/{ps5.pk}/flag/versions/',
                       {'game_ids': [ps5.pk], 'flag_type': 'delisted',
                        'details': ['A' * 20000]},
                       content_type='application/json')

    assert resp.status_code == 200, 'a non-string details payload 500d'
    assert len(GameFlag.objects.get().details) <= 500


def test_a_dict_details_payload_does_not_500(client):
    hunter = _hunter()
    ps5, _ps4, _ps3 = _versions()
    client.force_login(hunter.user)

    resp = client.post(f'/api/v1/games/{ps5.pk}/flag/versions/',
                       {'game_ids': [ps5.pk], 'flag_type': 'delisted', 'details': {'a': 1}},
                       content_type='application/json')

    assert resp.status_code < 500
    assert len(GameFlag.objects.get().details) <= 500


def test_float_and_bool_game_ids_are_rejected_not_coerced(client):
    """`int()` would turn 3.9 into 3 and `true` into 1, filing against a game nobody named."""
    hunter = _hunter()
    ps5, _ps4, _ps3 = _versions()
    client.force_login(hunter.user)

    assert _post(client, ps5, [float(ps5.pk)]).status_code == 400
    assert _post(client, ps5, [True]).status_code == 400
    assert GameFlag.objects.count() == 0


def test_both_flag_endpoints_share_one_rate_limit_bucket(client):
    """django_ratelimit derives the bucket from the decorated function unless `group` is named, so
    these were two budgets: 5 single-game writes AND 5 bulk writes a minute, each bulk one carrying
    up to MAX_BULK_VERSIONS rows. Verified before the fix -- the single endpoint returned 200 with
    the bulk endpoint already exhausted.

    The refusal is a 403, not a 429: `Ratelimited` subclasses `PermissionDenied`, which DRF renders
    as 403 with a `detail` key. game-flag.js reads `detail` as well as `error` because of this.
    """
    from django.core.cache import cache

    cache.clear()
    hunter = _hunter()
    ps5, _ps4, _ps3 = _versions()
    client.force_login(hunter.user)

    for _ in range(5):
        assert _post(client, ps5, [ps5.pk]).status_code == 200

    spillover = client.post(f'/api/v1/games/{ps5.pk}/flag/',
                            {'flag_type': 'unobtainable'}, content_type='application/json')

    assert spillover.status_code == 403, (
        'the single-game endpoint has its own budget -- the two endpoints are not sharing a bucket')
    assert 'detail' in spillover.json(), 'the refusal shape game-flag.js parses has changed'
    cache.clear()


def test_the_page_hands_the_modal_the_servers_cap(client):
    """The JS needs the cap to stop "All" walking a reader into a 400 on a concept with more lists
    than the endpoint accepts. Rendered from the service constant, never typed into the template, so
    raising the cap cannot leave the two disagreeing."""
    hunter = _hunter()
    _page_versions(925)
    client.force_login(hunter.user)

    content = client.get('/games/925/').content.decode()

    assert f'data-flag-max="{GameFlagService.MAX_BULK_VERSIONS}"' in content
