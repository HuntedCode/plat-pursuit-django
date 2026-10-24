"""The Challenge Optimizer's doors: owner-only JSON that never redirects, metered builds, and an Apply that
goes through `assign` line by line and never overwrites a square that changed since the plan was drawn."""
import json

import pytest
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from challenges.models import CHALLENGE_TYPE_AZ, CHALLENGE_TYPE_JOBS
from challenges.optimizer_views import BUILD_RATE
from challenges.services import challenge_service as svc
from challenges.services import planner
from tests.factories import ConceptFactory, GameFactory, IGDBMatchFactory, ProfileFactory, UserFactory
from trophies.models import Contract, EarnedContract, Job

pytestmark = pytest.mark.django_db

AZ, JOBS = CHALLENGE_TYPE_AZ, CHALLENGE_TYPE_JOBS
_SEQ = {'n': 0}


def _hunter(client, *, linked=True):
    user = UserFactory()
    profile = ProfileFactory(user=user, user_is_premium=True, is_linked=linked)
    client.force_login(user)
    return profile


def _contract(name, *, jobs=(), platforms=('PS5',)):
    _SEQ['n'] += 1
    contract = Contract.objects.create(name=name, slug='pv-%d' % _SEQ['n'], is_live=True, igdb_id=840_000 + _SEQ['n'])
    if jobs:
        contract.jobs.set(jobs)
    concept = ConceptFactory(anchor_migration_completed_at=timezone.now())
    IGDBMatchFactory(concept=concept, igdb_id=contract.igdb_id, status='accepted')
    GameFactory(concept=concept, title_platform=list(platforms))
    return contract


def _post(client, name, payload):
    return client.post(reverse(name), data=json.dumps(payload), content_type='application/json')


def _plan(client, **payload):
    payload.setdefault('types', [AZ, JOBS])
    payload.setdefault('badge_started', False)
    return _post(client, 'challenge_optimizer_plan', payload)


def _body(resp):
    return json.loads(resp.content)


# --- gates ------------------------------------------------------------------------------------------------

@pytest.mark.parametrize('name', ['challenge_optimizer_plan', 'challenge_optimizer_alternatives',
                                  'challenge_optimizer_apply'])
def test_no_post_door_redirects_an_anonymous_caller(client, name):
    resp = _post(client, name, {})
    assert resp.status_code in (401, 403)
    assert resp['Content-Type'].startswith('application/json')


def test_the_search_door_does_not_redirect_an_anonymous_caller(client):
    resp = client.get(reverse('challenge_optimizer_search'), {'q': 'blood'})
    assert resp.status_code in (401, 403)
    assert resp['Content-Type'].startswith('application/json')


def test_an_unlinked_account_is_refused_in_json(client):
    _hunter(client, linked=False)
    resp = _plan(client)
    assert resp.status_code == 403
    assert 'error' in _body(resp)


def test_the_page_redirects_an_anonymous_visitor_to_log_in(client):
    resp = client.get(reverse('challenge_optimizer'))
    assert resp.status_code == 302


def test_the_page_seeds_runs_platforms_and_series(client):
    profile = _hunter(client)
    profile.user.browse_defaults = {'games': {'platform': ['PS4']}}
    profile.user.save(update_fields=['browse_defaults'])
    svc.start(profile, AZ)

    resp = client.get(reverse('challenge_optimizer'))

    assert resp.status_code == 200
    runs = {r['type']: r for r in resp.context['runs']}
    assert runs[AZ]['run'] is not None and runs[AZ]['open'] == 26 and runs[AZ]['picked'] == 0
    assert runs[JOBS]['run'] is None and runs[JOBS]['picked'] == 0
    checked = {p['code'] for p in resp.context['platforms'] if p['checked']}
    assert checked == {'PS4'}


def test_the_page_offers_to_start_a_missing_run_in_place(client):
    profile = _hunter(client)
    svc.start(profile, AZ)

    html = client.get(reverse('challenge_optimizer')).content.decode()

    assert reverse('challenge_start', args=[JOBS]) in html
    assert 'name="return_to" value="optimizer"' in html
    assert reverse('challenge_start', args=[AZ]) not in html   # A-Z is already running: a checkbox, not a Start


def test_a_hunter_with_no_runs_gets_the_wizard_not_a_dead_end(client):
    _hunter(client)
    resp = client.get(reverse('challenge_optimizer'))
    assert resp.status_code == 200
    html = resp.content.decode()
    assert 'data-cplan-wizard' in html and 'Nothing to plan yet' not in html
    assert reverse('challenge_start', args=[AZ]) in html and reverse('challenge_start', args=[JOBS]) in html


def test_a_hidden_run_is_offered_back_as_resume_with_its_progress(client):
    profile = _hunter(client)
    run = svc.start(profile, AZ)
    svc.hide(run, profile)

    html = client.get(reverse('challenge_optimizer')).content.decode()

    assert 'Resume A-Z' in html
    assert 'bd-chip--ghost shrink-0">Hidden' in html   # My Challenges' own Hidden chip, via the shared summary


def _free_hunter(client):
    user = UserFactory()
    ProfileFactory(user=user, user_is_premium=False, is_linked=True)
    client.force_login(user)


@override_settings(CHALLENGES_BETA_MEMBERS_ONLY=True)
def test_the_beta_gate_still_holds_on_the_optimizers_start(client):
    _free_hunter(client)
    html = client.get(reverse('challenge_optimizer')).content.decode()
    assert 'Starting one is in beta for members first.' in html
    assert 'Challenges are in beta for members first.' in html   # the hint does not invite a Start that is absent
    assert reverse('challenge_start', args=[AZ]) not in html


@override_settings(CHALLENGES_BETA_MEMBERS_ONLY=True)
def test_a_refused_start_lands_back_on_the_optimizer_with_its_reason(client):
    _free_hunter(client)
    resp = client.post(reverse('challenge_start', args=[AZ]), {'return_to': 'optimizer'}, follow=True)
    assert resp.redirect_chain[-1][0] == reverse('challenge_optimizer')
    assert any('beta' in str(m).lower() for m in resp.context['messages'])


def test_a_start_from_the_optimizer_says_it_is_ready_to_plan(client):
    _hunter(client)
    resp = client.post(reverse('challenge_start', args=[AZ]), {'return_to': 'optimizer'}, follow=True)
    assert any('ready to plan' in str(m) for m in resp.context['messages'])


@pytest.mark.parametrize('return_to, lands_on', [
    ('optimizer', 'challenge_optimizer'),
    ('https://evil.example/', 'my_challenges'),   # a name from the allowlist, never a URL
    ('', 'my_challenges'),
])
def test_start_returns_to_a_named_page_only(client, return_to, lands_on):
    _hunter(client)
    resp = client.post(reverse('challenge_start', args=[AZ]), {'return_to': return_to})
    assert resp.status_code == 302
    assert resp['Location'] == reverse(lands_on)


def _start_json(client, challenge_type):
    return client.post(reverse('challenge_optimizer_start', args=[challenge_type]),
                       data='{}', content_type='application/json')


def test_starting_in_place_returns_the_card_a_reload_would_draw(client):
    profile = _hunter(client)

    body = _body(_start_json(client, JOBS))

    assert body['outcome'] == 'created' and body['open'] == svc.active_run(profile, JOBS).total_slots
    assert 'ready to plan' in body['message']
    assert 'data-cplan-type' in body['html'] and f'value="{JOBS}"' in body['html']
    page = client.get(reverse('challenge_optimizer')).content.decode()
    assert 'Include Job Coverage' in page and 'Include Job Coverage' in body['html']


def test_resuming_in_place_brings_back_the_hidden_run(client):
    profile = _hunter(client)
    run = svc.start(profile, AZ)
    svc.hide(run, profile)

    body = _body(_start_json(client, AZ))

    assert body['outcome'] == 'resumed'
    assert svc.active_run(profile, AZ).pk == run.pk
    # The RUNNING card comes back, not the Resume card it replaces.
    assert 'Include A-Z' in body['html'] and 'Resume A-Z' not in body['html']


def test_a_stale_tabs_second_start_swaps_in_the_running_card_quietly(client):
    _hunter(client)
    _start_json(client, AZ)

    body = _body(_start_json(client, AZ))

    assert body['outcome'] == 'already_active' and body['message'] == ''
    assert 'Include A-Z' in body['html']


def test_my_challenges_cards_keep_their_h2_through_the_shared_partial(client):
    _hunter(client)
    html = client.get(reverse('my_challenges')).content.decode()
    assert '<h2 class="text-base md:text-lg font-bold leading-tight min-w-0">A-Z</h2>' in html


def test_apply_returns_step_ones_cards_re_rendered(client):
    profile = _hunter(client)
    contract = _contract('Astro Bot')
    svc.start(profile, AZ)

    body = _body(_apply(client, {'type': AZ, 'key': 'A', 'contract': contract.slug}))

    assert set(body['cards']) == {AZ}
    assert '25 open squares' in body['cards'][AZ] and '1 planned' in body['cards'][AZ]


@override_settings(CHALLENGES_BETA_MEMBERS_ONLY=True)
def test_the_in_place_door_holds_the_beta_gate_in_json(client):
    _free_hunter(client)
    resp = _start_json(client, AZ)
    assert resp.status_code == 400 and 'beta' in _body(resp)['error'].lower()


def test_the_in_place_door_refuses_the_calendar_and_strangers(client):
    _hunter(client)
    assert _start_json(client, 'calendar').status_code == 404
    client.logout()
    resp = _start_json(client, AZ)
    assert resp.status_code in (401, 403) and resp['Content-Type'].startswith('application/json')


def test_step_one_cards_carry_my_challenges_details(client):
    profile = _hunter(client)
    svc.start(profile, AZ)
    html = client.get(reverse('challenge_optimizer')).content.decode()
    # The shared summary partial: the type tile, the state chip and the Horizon, as My Challenges draws them.
    assert 'pp-ctype' in html and 'In progress' in html and 'pp-horizon' in html
    assert 'Include A-Z' in html


def test_my_challenges_offers_the_optimizer_only_with_a_run_to_plan(client):
    profile = _hunter(client)
    assert client.get(reverse('my_challenges')).context['can_plan'] is False
    svc.start(profile, JOBS)
    assert client.get(reverse('my_challenges')).context['can_plan'] is True


def test_the_run_page_links_the_optimizer_for_its_owner_only(client):
    profile = _hunter(client)
    run = svc.start(profile, AZ)
    optimizer_url = reverse('challenge_optimizer')
    assert optimizer_url in client.get(reverse('challenge_detail', args=[run.pk])).content.decode()
    client.logout()
    assert optimizer_url not in client.get(reverse('challenge_detail', args=[run.pk])).content.decode()


# --- plan -------------------------------------------------------------------------------------------------

def test_a_plan_places_a_double_in_both_runs_and_names_its_squares(client):
    profile = _hunter(client)
    job = Job.objects.order_by('slug').first()
    double = _contract('Bloodborne', jobs=[job])
    svc.start(profile, AZ)
    svc.start(profile, JOBS)

    body = _body(_plan(client))

    first = body['lines'][0]
    assert first['slug'] == double.slug
    assert [(s['type'], s['key']) for s in first['squares']] == [(AZ, 'B'), (JOBS, job.slug)]
    assert first['squares'][1]['label'] == job.name
    assert body['built'] is True


def test_a_second_plan_reads_the_cached_pool(client):
    profile = _hunter(client)
    _contract('Astro Bot')
    svc.start(profile, AZ)

    assert _body(_plan(client, types=[AZ]))['built'] is True
    assert _body(_plan(client, types=[AZ]))['built'] is False


def test_planning_with_no_active_run_is_refused(client):
    _hunter(client)
    resp = _plan(client)
    assert resp.status_code == 400


def test_a_body_that_is_not_json_is_a_400_not_a_500(client):
    _hunter(client)
    resp = client.post(reverse('challenge_optimizer_plan'), data='not json', content_type='application/json')
    assert resp.status_code == 400


def test_an_unknown_source_is_refused(client):
    profile = _hunter(client)
    svc.start(profile, AZ)
    assert _plan(client, source='everything').status_code == 400


def test_pins_travel_as_slugs_and_unknown_slugs_are_ignored(client):
    profile = _hunter(client)
    off = _contract('Astro Bot', platforms=('PS4',))
    svc.start(profile, AZ)

    body = _body(_plan(client, types=[AZ], platforms=['PS5'], tweaks={'pins': [off.slug, 'no-such-game']}))

    [line] = body['lines']
    assert line['slug'] == off.slug
    assert line['pinned'] is True and line['outside_filters'] is True


def test_builds_are_metered_but_cached_re_solves_are_not(client):
    profile = _hunter(client)
    _contract('Astro Bot')
    svc.start(profile, AZ)
    limit = int(BUILD_RATE.split('/')[0])

    # Each distinct platform set is a fresh pool, i.e. a build.
    platform_sets = [['PS5'], ['PS4'], ['PS3'], ['PSVITA'], ['PSVR'], ['PSVR2'], ['PS4', 'PS5']]
    statuses = [_plan(client, types=[AZ], platforms=p).status_code for p in platform_sets[:limit + 1]]
    assert statuses[:limit] == [200] * limit
    assert statuses[limit] == 429

    # The first pool is still cached, so planning against it costs no build.
    assert _plan(client, types=[AZ], platforms=platform_sets[0]).status_code == 200


# --- alternatives and search ------------------------------------------------------------------------------

def test_alternatives_offer_doubles_first(client):
    profile = _hunter(client)
    job = Job.objects.order_by('slug').first()
    single = _contract('Apex')
    double = _contract('Astro Bot', jobs=[job])
    svc.start(profile, AZ)
    svc.start(profile, JOBS)
    _plan(client)   # builds the pool

    body = _body(_post(client, 'challenge_optimizer_alternatives',
                       {'types': [AZ, JOBS], 'badge_started': False, 'type': AZ, 'key': 'A'}))

    assert [r['slug'] for r in body['rows']] == [double.slug, single.slug]
    assert [s['key'] for s in body['rows'][0]['also']] == [job.slug]


def test_alternatives_flag_games_that_also_fit_the_lines_other_square(client):
    profile = _hunter(client)
    job = Job.objects.order_by('slug').first()
    single = _contract('Apex')
    double = _contract('Astro Bot', jobs=[job])
    svc.start(profile, AZ)
    svc.start(profile, JOBS)
    _plan(client)

    ask = {'types': [AZ, JOBS], 'badge_started': False, 'type': AZ, 'key': 'A',
           'partners': [{'type': JOBS, 'key': job.slug}]}
    body = _body(_post(client, 'challenge_optimizer_alternatives', ask))

    # The default tab is "covers both" when it has games, and the counts say what each tab holds.
    assert body['group'] == 'both' and body['counts'] == {'both': 1, 'rest': 1, 'partner': 0}
    assert [r['slug'] for r in body['rows']] == [double.slug]
    assert body['rows'][0]['also'] == []   # the partner is the line's own square, never "also"
    rest = _body(_post(client, 'challenge_optimizer_alternatives', {**ask, 'group': 'rest'}))
    assert rest['group'] == 'rest' and [r['slug'] for r in rest['rows']] == [single.slug]


def test_the_partner_tab_and_the_badge_filter_reach_the_sheet(client):
    profile = _hunter(client)
    job = Job.objects.order_by('slug').first()
    job_only = _contract('7 Days', jobs=[job])   # a digit takes no letter
    svc.start(profile, AZ)
    svc.start(profile, JOBS)
    _plan(client)
    ask = {'types': [AZ, JOBS], 'badge_started': False, 'type': AZ, 'key': 'A',
           'partners': [{'type': JOBS, 'key': job.slug}]}

    body = _body(_post(client, 'challenge_optimizer_alternatives', ask))
    assert body['group'] == 'partner' and [r['slug'] for r in body['rows']] == [job_only.slug]
    # No badge counts here, so the filter leaves nothing; a value other than true is ignored.
    assert _body(_post(client, 'challenge_optimizer_alternatives', {**ask, 'badge_only': True}))['counts'] == {
        'both': 0, 'rest': 0, 'partner': 0}
    assert _body(_post(client, 'challenge_optimizer_alternatives', {**ask, 'badge_only': 'yes'}))['counts'] == {
        'both': 0, 'rest': 0, 'partner': 1}


def test_fallback_moves_off_an_asked_for_tab_that_is_empty(client):
    profile = _hunter(client)
    job = Job.objects.order_by('slug').first()
    job_only = _contract('7 Days', jobs=[job])
    svc.start(profile, AZ)
    svc.start(profile, JOBS)
    _plan(client)
    ask = {'types': [AZ, JOBS], 'badge_started': False, 'type': AZ, 'key': 'A',
           'partners': [{'type': JOBS, 'key': job.slug}], 'group': 'both'}
    assert _body(_post(client, 'challenge_optimizer_alternatives', ask))['group'] == 'both'   # asked: kept
    moved = _body(_post(client, 'challenge_optimizer_alternatives', {**ask, 'fallback': True}))
    assert moved['group'] == 'partner' and [r['slug'] for r in moved['rows']] == [job_only.slug]
    assert _body(_post(client, 'challenge_optimizer_alternatives', {**ask, 'fallback': 'yes'}))['group'] == 'both'


def test_a_game_replacing_a_game_in_each_challenge_is_one_swap_card(client):
    # Apex holds A and 7 Days holds the job, both unfinished; Astro Bot fits both, so it would replace each. The
    # planner suggests per square (two swaps); the plan JSON merges them into one card naming both games.
    profile = _hunter(client)
    job = Job.objects.order_by('slug').first()
    apex = _contract('Apex')
    days = _contract('7 Days', jobs=[job])
    astro = _contract('Astro Bot', jobs=[job])
    az_run = svc.start(profile, AZ)
    jobs_run = svc.start(profile, JOBS)
    svc.assign(az_run, profile, 'A', apex)
    svc.assign(jobs_run, profile, job.slug, days)

    [swap] = _body(_plan(client))['swaps']

    assert swap['suggested']['slug'] == astro.slug
    assert [(r['square']['key'], r['current']['slug']) for r in swap['replaces']] == [('A', apex.slug),
                                                                                     (job.slug, days.slug)]
    assert swap['also'] == []   # a square it would REPLACE is never "also covers"


def test_a_swap_card_never_claims_a_square_the_game_already_holds(client):
    # Astro Bot already sits unfinished in the job; the swap solve keeps it there and suggests it for A too. It
    # covers nothing new in the job, so the card must not say "Also covers" it.
    profile = _hunter(client)
    job = Job.objects.order_by('slug').first()
    apex = _contract('Apex')
    astro = _contract('Astro Bot', jobs=[job])
    az_run = svc.start(profile, AZ)
    jobs_run = svc.start(profile, JOBS)
    svc.assign(az_run, profile, 'A', apex)
    svc.assign(jobs_run, profile, job.slug, astro)

    [swap] = _body(_plan(client))['swaps']

    assert [r['current']['slug'] for r in swap['replaces']] == [apex.slug]
    assert swap['also'] == []


# --- leave open and the Job view (owner, 2026-10-17) --------------------------------------------------------

def test_a_square_left_open_comes_back_as_left_open_not_planned(client):
    profile = _hunter(client)
    _contract('Apex')
    svc.start(profile, AZ)
    body = _body(_plan(client, types=[AZ], tweaks={'left_open': [{'type': AZ, 'key': 'A'}]}))
    assert [s['key'] for s in body['left_open']] == ['A']
    assert all(sq['key'] != 'A' for line in body['lines'] for sq in line['squares'])
    assert all(s['key'] != 'A' for s in body['empty'])


def test_the_saved_plan_keeps_only_squares_still_open(client):
    from challenges.models import OptimizerDraft
    profile = _hunter(client)
    run = svc.start(profile, AZ)
    svc.assign(run, profile, 'B', _contract('Bastion'))   # filled by hand since it was left open
    _plan(client, types=[AZ], tweaks={'left_open': [{'type': AZ, 'key': 'A'}, {'type': AZ, 'key': 'B'}]})
    assert OptimizerDraft.objects.get(profile=profile).tweaks['left_open'] == [{'type': AZ, 'key': 'A'}]


@pytest.mark.parametrize('left_open', ['A', {'type': AZ}, 5])
def test_a_crafted_left_open_list_is_a_400_not_a_500(client, left_open):
    profile = _hunter(client)
    svc.start(profile, AZ)
    assert _plan(client, types=[AZ], tweaks={'left_open': left_open}).status_code == 400


def test_junk_left_open_entries_are_dropped(client):
    profile = _hunter(client)
    svc.start(profile, AZ)
    resp = _plan(client, types=[AZ], tweaks={'left_open': [None, 'A', {'type': AZ}, {'type': AZ, 'key': ['A']}]})
    assert resp.status_code == 200 and _body(resp)['left_open'] == []


def test_the_job_groups_follow_the_run_pages_discipline_order(client):
    profile = _hunter(client)
    svc.start(profile, AZ)
    run = svc.start(profile, JOBS)
    body = _body(_plan(client))

    from challenges.services.slot_render import slot_groups
    expected = [(g['slug'], [c['key'] for c in g['cards']]) for g in slot_groups(run)]
    assert [(g['slug'], g['keys']) for g in body['job_groups']] == expected
    assert all(g['label'] for g in body['job_groups'])


def test_an_a_z_only_plan_has_no_job_groups(client):
    profile = _hunter(client)
    svc.start(profile, AZ)
    assert _body(_plan(client, types=[AZ]))['job_groups'] == []


def test_discipline_sequence_puts_the_radar_five_first_then_others_then_the_unknown():
    from challenges.services.slot_render import discipline_sequence
    assert discipline_sequence(['', 'mind', 'zz', 'combat', 'mind', 'aa']) == ['combat', 'mind', 'zz', 'aa', '']


def _covering_setup(client):
    """Both runs, and a game that fits A and the first job: a valid partner would flag it `both`."""
    profile = _hunter(client)
    job = Job.objects.order_by('slug').first()
    double = _contract('Astro Bot', jobs=[job])
    svc.start(profile, AZ)
    svc.start(profile, JOBS)
    _plan(client)
    return job, double


def _alternatives(client, **extra):
    return _post(client, 'challenge_optimizer_alternatives',
                 {'types': [AZ, JOBS], 'badge_started': False, 'type': AZ, 'key': 'A', **extra})


@pytest.mark.parametrize('partners', ['jobs', [['jobs', 'x']], [{'type': ['jobs'], 'key': 'x'}], [None], {'a': 1},
                                      [{'type': JOBS, 'key': ['x']}]])
def test_crafted_partners_are_dropped_not_a_500(client, partners):
    # The game below WOULD cover both with a valid partner, so finding it only in "rest" proves the crafted one
    # was dropped.
    _covering_setup(client)
    resp = _alternatives(client, partners=partners)
    assert resp.status_code == 200
    assert _body(resp)['counts'] == {'both': 0, 'rest': 1, 'partner': 0}


def test_every_alternative_is_reachable_a_page_at_a_time(client, monkeypatch):
    # Owner, 2026-10-17: no cap. A tab pages through ALL of its games; `more` and `offset` say where the next
    # page starts, counted on the page taken.
    from challenges import optimizer_views
    monkeypatch.setattr(optimizer_views, 'ALTERNATIVES_PAGE', 2)
    profile = _hunter(client)
    games = [_contract(f'Apex {i}') for i in range(5)]
    svc.start(profile, AZ)
    _plan(client, types=[AZ])
    ask = {'types': [AZ], 'badge_started': False, 'type': AZ, 'key': 'A'}

    seen, offset, more, pages = [], 0, True, 0
    while more:
        body = _body(_post(client, 'challenge_optimizer_alternatives', {**ask, 'offset': offset}))
        assert body['group'] == 'rest' and body['counts'] == {'both': 0, 'rest': 5, 'partner': 0}
        assert len(body['rows']) <= 2   # one page, never the whole tab
        seen += [r['slug'] for r in body['rows']]
        offset, more, pages = body['offset'] + body['showing'], body['more'], pages + 1
    assert pages == 3 and sorted(seen) == sorted(g.slug for g in games)
    assert all(r['platforms'] for r in body['rows'])   # the sheet's cards carry platforms


def test_other_options_never_spends_the_solve_bucket(client, monkeypatch):
    # Every page and tab of the sheet spends its own bucket: more Other options calls than the solve budget allows
    # (40 a minute) still leave the next re-solve answered.
    from challenges import optimizer_views
    monkeypatch.setattr(optimizer_views, 'SHEET_RATE', '100/m')
    profile = _hunter(client)
    _contract('Apex')
    svc.start(profile, AZ)
    ask = {'types': [AZ], 'badge_started': False, 'type': AZ, 'key': 'A'}
    for i in range(41):
        assert _post(client, 'challenge_optimizer_alternatives', {**ask, 'offset': i % 2}).status_code == 200
    assert _plan(client, types=[AZ]).status_code == 200


def test_other_options_has_its_own_bucket_for_every_page_and_tab(client, monkeypatch):
    from challenges import optimizer_views
    monkeypatch.setattr(optimizer_views, 'SHEET_RATE', '2/m')
    profile = _hunter(client)
    svc.start(profile, AZ)
    ask = {'types': [AZ], 'badge_started': False, 'type': AZ, 'key': 'A'}
    assert _post(client, 'challenge_optimizer_alternatives', ask).status_code == 200
    assert _post(client, 'challenge_optimizer_alternatives', {**ask, 'offset': 24}).status_code == 200
    resp = _post(client, 'challenge_optimizer_alternatives', {**ask, 'group': 'rest'})
    assert resp.status_code == 429 and _body(resp)['error']


@pytest.mark.parametrize('extra', [{'group': 'nope'}, {'group': ['both']}, {'offset': -3}, {'offset': '2'},
                                   {'offset': True}, {'offset': 1.5}])
def test_a_crafted_tab_or_offset_falls_back_not_a_500(client, extra):
    _covering_setup(client)
    resp = _alternatives(client, **extra)
    # No partner here, so the fallback tab is "rest", from the start.
    assert resp.status_code == 200 and _body(resp)['offset'] == 0 and _body(resp)['group'] == 'rest'


def test_a_square_left_open_is_never_also_covered_in_the_sheet(client):
    job, double = _covering_setup(client)
    body = _body(_alternatives(client, tweaks={'left_open': [{'type': JOBS, 'key': job.slug}]}))
    assert [(r['slug'], r['also']) for r in body['rows']] == [(double.slug, [])]
    assert [s['key'] for s in _body(_alternatives(client))['rows'][0]['also']] == [job.slug]


def test_an_unobtainable_must_have_outside_the_platforms_is_still_called_outside_your_filters(client):
    profile = _hunter(client)
    gone = _contract('Apex', platforms=('PS4',))
    from trophies.models import Game
    Game.objects.filter(concept__igdb_match__igdb_id=gone.igdb_id).update(is_obtainable=False)
    svc.start(profile, AZ)
    body = _body(_plan(client, types=[AZ], platforms=['PS5'], tweaks={'pins': [gone.slug]}))
    [line] = body['lines']
    assert line['slug'] == gone.slug and line['outside_filters'] is True


def test_an_unobtainable_must_have_is_not_called_outside_your_filters(client):
    profile = _hunter(client)
    gone = _contract('Apex')
    from trophies.models import Game
    Game.objects.filter(concept__igdb_match__igdb_id=gone.igdb_id).update(is_obtainable=False)
    svc.start(profile, AZ)
    body = _body(_plan(client, types=[AZ], tweaks={'pins': [gone.slug]}))
    [line] = body['lines']
    assert line['slug'] == gone.slug and line['outside_filters'] is False
    assert line['availability'] == ['unobtainable']


def test_the_lines_own_game_is_left_out_of_the_sheet(client):
    job, double = _covering_setup(client)
    partners = [{'type': JOBS, 'key': job.slug}]
    assert [r['slug'] for r in _body(_alternatives(client, partners=partners))['rows']] == [double.slug]
    body = _body(_alternatives(client, partners=partners, current=double.slug))
    assert body['rows'] == []


@pytest.mark.parametrize('current', [['x'], 42, ''])
def test_a_crafted_current_is_ignored_not_a_500(client, current):
    _covering_setup(client)
    resp = _alternatives(client, current=current)
    assert resp.status_code == 200 and len(_body(resp)['rows']) == 1


def test_the_search_merges_one_game_across_both_runs(client):
    profile = _hunter(client)
    job = Job.objects.order_by('slug').first()
    contract = _contract('Bloodborne', jobs=[job])
    svc.start(profile, AZ)
    svc.start(profile, JOBS)

    body = _body(client.get(reverse('challenge_optimizer_search'), {'q': 'blood'}))

    [row] = body['rows']
    assert row['slug'] == contract.slug
    assert [f['key'] for f in row['fits'][AZ]] == ['B']
    assert [f['key'] for f in row['fits'][JOBS]] == [job.slug]


# --- apply ------------------------------------------------------------------------------------------------

def _apply(client, *lines):
    return _post(client, 'challenge_optimizer_apply', {'lines': list(lines)})


def test_apply_fills_open_squares_through_assign(client):
    profile = _hunter(client)
    job = Job.objects.order_by('slug').first()
    double = _contract('Bloodborne', jobs=[job])
    az_run, jobs_run = svc.start(profile, AZ), svc.start(profile, JOBS)

    body = _body(_apply(client, {'type': AZ, 'key': 'B', 'contract': double.slug},
                        {'type': JOBS, 'key': job.slug, 'contract': double.slug}))

    assert body['applied'] == 2
    assert az_run.slots.get(key='B').contract_id == double.id
    assert jobs_run.slots.get(key=job.slug).contract_id == double.id
    assert body['runs'][AZ]['filled_count'] == 1


def test_apply_never_overwrites_a_square_filled_since_the_plan(client):
    profile = _hunter(client)
    planned, placed = _contract('Bloodborne'), _contract('Brothers')
    run = svc.start(profile, AZ)
    svc.assign(run, profile, 'B', placed)   # another tab got there first

    body = _body(_apply(client, {'type': AZ, 'key': 'B', 'contract': planned.slug}))

    assert body['results'][0]['status'] == 'skipped'
    assert run.slots.get(key='B').contract_id == placed.id


def test_a_swap_replaces_only_the_game_it_was_offered_against(client):
    profile = _hunter(client)
    old, new = _contract('Bloodborne'), _contract('Brothers')
    run = svc.start(profile, AZ)
    svc.assign(run, profile, 'B', old)

    body = _body(_apply(client, {'type': AZ, 'key': 'B', 'contract': new.slug, 'replacing': old.slug}))

    assert body['results'][0]['status'] == 'applied'
    assert run.slots.get(key='B').contract_id == new.id


def test_apply_never_places_a_game_that_would_lock_the_square(client):
    profile = _hunter(client)
    finished = _contract('Bloodborne')
    EarnedContract.objects.create(profile=profile, contract=finished, has_platinum=True,
                                  platinum_reached_at=timezone.now())
    # B's pool is empty, so the hatch is open and only the lock confirmation stands in the way.
    run = svc.start(profile, AZ)

    body = _body(_apply(client, {'type': AZ, 'key': 'B', 'contract': finished.slug}))

    assert body['results'][0]['status'] == 'skipped'
    slot = run.slots.get(key='B')
    assert not slot.is_filled and not slot.is_completed


def test_one_refused_line_does_not_stop_the_others(client):
    profile = _hunter(client)
    good = _contract('Astro Bot')
    svc.start(profile, AZ)

    body = _body(_apply(client, {'type': AZ, 'key': 'B', 'contract': good.slug},   # wrong letter
                        {'type': AZ, 'key': 'A', 'contract': good.slug}))

    assert [r['status'] for r in body['results']] == ['skipped', 'applied']


def test_an_accepted_swap_carries_the_squares_snapshot_and_applies(client):
    # Regression: an accepted swap's line sent `replacing: ''`, so Apply skipped it as "changed" every time.
    # Renaming the occupant's slug after placing it also proves the line carries the SLOT's snapshot, which is
    # what Apply compares, rather than the contract's live slug.
    profile = _hunter(client)
    old, new = _contract('Bloodborne'), _contract('Brothers')
    run = svc.start(profile, AZ)
    svc.assign(run, profile, 'B', old)
    snapshot = run.slots.get(key='B').contract_slug
    Contract.objects.filter(pk=old.pk).update(slug='bloodborne-renamed')

    body = _body(_plan(client, types=[AZ], tweaks={'square_pins': [{'type': AZ, 'key': 'B', 'contract': new.slug}]}))
    [line] = [l for l in body['lines'] if l['slug'] == new.slug]
    assert line['replacing'] == {AZ: snapshot}

    applied = _body(_apply(client, {'type': AZ, 'key': 'B', 'contract': new.slug, 'replacing': line['replacing'][AZ]}))
    assert applied['results'][0]['status'] == 'applied'
    assert run.slots.get(key='B').contract_id == new.id


@pytest.mark.parametrize('payload', [
    {'tweaks': {'square_pins': {'a': 1}}},
    {'tweaks': {'square_pins': 7}},
    {'tweaks': 'nope'},
])
def test_crafted_tweaks_are_a_400_not_a_500(client, payload):
    profile = _hunter(client)
    svc.start(profile, AZ)
    assert _plan(client, types=[AZ], **payload).status_code == 400


@pytest.mark.parametrize('square', [{'type': ['az'], 'key': 'A'}, {'type': AZ, 'key': ['A']}, {'type': AZ}])
def test_a_crafted_alternatives_square_is_refused_not_a_500(client, square):
    profile = _hunter(client)
    svc.start(profile, AZ)
    resp = _post(client, 'challenge_optimizer_alternatives', {'types': [AZ], 'badge_started': False, **square})
    assert resp.status_code == 400


def test_the_search_says_so_when_there_is_no_run(client):
    _hunter(client)
    body = _body(client.get(reverse('challenge_optimizer_search'), {'q': 'bloodborne'}))
    assert body['no_runs'] is True and body['rows'] == []


def test_planning_without_a_run_spends_no_build(client, monkeypatch):
    _hunter(client)

    def no_build(*args, **kwargs):
        raise AssertionError('built a pool for a hunter with nothing to plan')
    monkeypatch.setattr(planner, 'build_pool', no_build)

    assert _plan(client, types=[AZ]).status_code == 400


@pytest.mark.parametrize('lines', [
    [],
    'nope',
    [{'type': AZ, 'key': 'A'}],
    [{'type': 'calendar', 'key': 'A', 'contract': 'x'}],
    [{'type': AZ, 'key': 'A', 'contract': 'x'}, {'type': AZ, 'key': 'A', 'contract': 'y'}],
])
def test_malformed_apply_bodies_are_refused_whole(client, lines):
    profile = _hunter(client)
    svc.start(profile, AZ)
    resp = _post(client, 'challenge_optimizer_apply', {'lines': lines})
    assert resp.status_code == 400


# --- step 4: badges ---------------------------------------------------------------------------------------

def _edition(name, *, badge_type='series', group=None, stages=1):
    from tests.factories import BadgeSeriesFactory, GroupBadgeFactory, StageFactory
    series = BadgeSeriesFactory(name=name, badge_type=badge_type)
    extra = {'platform_group': group} if group else {}
    gb = GroupBadgeFactory(series=series, required_stages=stages, **extra)
    StageFactory(series_slug=series.series_slug, stage_number=1)
    return gb


def _badges(client, **params):
    return client.get(reverse('challenge_optimizer_badges'), params)


def test_the_badge_picker_pages_medallion_cards_that_start_unpressed(client):
    _hunter(client)
    gb = _edition('Uncharted')

    body = _body(_badges(client))

    assert body['total'] == 1 and body['more'] is False
    assert f'data-cplan-badge="{gb.id}"' in body['html'] and 'aria-pressed="false"' in body['html']
    assert 'pp-med' in body['html'] and gb.platform_group.name in body['html']


def test_the_badge_picker_filters_like_the_gallery(client):
    from tests.factories import PlatformGroupFactory
    _hunter(client)
    legacy = PlatformGroupFactory(key='legacy-pk', platforms=['PS3'])
    keep = _edition('Uncharted', badge_type='franchise')
    _edition('Uncharted Legacy', badge_type='franchise', group=legacy)
    _edition('Naughty Dog', badge_type='developer')

    body = _body(_badges(client, q='uncharted', type='franchise', group=keep.platform_group.key))

    assert body['total'] == 1 and f'data-cplan-badge="{keep.id}"' in body['html']


def test_the_badge_picker_reports_progress_in_the_cards(client):
    from trophies.models import SeriesBadgeStanding
    profile = _hunter(client)
    gb = _edition('Uncharted', stages=5)
    SeriesBadgeStanding.objects.create(profile=profile, series_slug=gb.series.series_slug, progress_bp=4000,
                                       group_progress={gb.platform_group.key: [2, 5]})

    assert '2 of 5 stages' in _body(_badges(client))['html']


def test_the_badge_picker_hides_badges_the_hunter_holds(client):
    from trophies.models import UserGroupBadge
    profile = _hunter(client)
    held, untouched = _edition('Uncharted'), _edition('Bloodborne')
    UserGroupBadge.objects.create(profile=profile, group_badge=held)

    body = _body(_badges(client))

    assert body['total'] == 1 and f'data-cplan-badge="{untouched.id}"' in body['html']
    assert f'data-cplan-badge="{held.id}"' not in body['html']


def test_another_hunters_badge_still_shows(client):
    from trophies.models import UserGroupBadge
    from tests.factories import ProfileFactory
    _hunter(client)
    gb = _edition('Uncharted')
    UserGroupBadge.objects.create(profile=ProfileFactory(), group_badge=gb)
    assert _body(_badges(client))['total'] == 1


def test_the_badge_picker_names_each_badges_type(client):
    _hunter(client)
    franchise = _edition('God of War', badge_type='franchise')
    series = _edition('God of War', badge_type='series')
    user = _edition('God of War', badge_type='user')

    html = _body(_badges(client))['html']

    for gb, label in ((franchise, 'Franchise'), (series, 'Series'), (user, 'User')):
        card = html[html.index(f'data-badge-id="{gb.id}"'):]
        card = card[:card.index('</li>')]
        assert f'{label}&nbsp;<span aria-hidden="true">&middot;</span> {gb.platform_group.name}' in card
        assert f'data-name="God of War ({label}, {gb.platform_group.name})"' in card
        assert f'aria-label="God of War, {label}, {gb.platform_group.name}"' in card


def test_saved_badge_picks_are_named_with_their_type():
    from challenges.optimizer_views import _with_pick_names
    gb = _edition('God of War', badge_type='franchise')
    answers = _with_pick_names({'badge_picks': [gb.id]})
    assert answers['badge_pick_names'] == {str(gb.id): f'God of War (Franchise, {gb.platform_group.name})'}


def test_the_badge_picker_refuses_strangers_in_json(client):
    resp = _badges(client)
    assert resp.status_code in (401, 403) and resp['Content-Type'].startswith('application/json')


def test_a_bad_page_number_is_page_one_not_a_500(client):
    _hunter(client)
    _edition('Uncharted')
    assert _body(_badges(client, page='nope'))['page'] == 1


def test_step_four_lists_badge_types_with_counts_and_started_cards(client):
    from trophies.models import SeriesBadgeStanding
    profile = _hunter(client)
    started = _edition('Uncharted', badge_type='franchise', stages=4)
    _edition('Naughty Dog', badge_type='developer')
    SeriesBadgeStanding.objects.create(profile=profile, series_slug=started.series.series_slug, progress_bp=2500,
                                       group_progress={started.platform_group.key: [1, 4]})

    resp = client.get(reverse('challenge_optimizer'))

    types = {t['key']: t['count'] for t in resp.context['badge_types']}
    assert types == {'franchise': 1, 'developer': 1} and resp.context['badges_total'] == 2
    assert resp.context['started_count'] == 1
    assert '1 of 4 stages' in resp.content.decode()


def test_badge_sources_and_weight_are_read_from_the_body_strictly():
    from challenges.optimizer_views import _OptimizerJsonView
    options = _OptimizerJsonView().options({
        'types': [AZ], 'badge_all': True, 'badge_started': 'yes', 'badge_types': ['developer', 'nonsense'],
        'badge_picks': [3, 3, 'x', True, -1, 7], 'badge_weight': 'priority'})
    assert options.badge_all is True and options.badge_started is False   # only a real JSON true counts
    assert options.badge_types == ('developer',)
    assert options.picked_badges == (3, 7)                                 # deduplicated; bool and junk dropped
    assert options.badge_weight == planner.WEIGHT_PRIORITY


def test_the_picks_choice_is_read_from_the_body(client):
    from challenges.optimizer_views import _OptimizerJsonView
    assert _OptimizerJsonView().options({'types': [AZ]}).picks == planner.PICKS_KEEP
    assert _OptimizerJsonView().options({'types': [AZ], 'picks': 'replan'}).replan_picks
    profile = _hunter(client)
    svc.start(profile, AZ)
    assert _plan(client, types=[AZ], picks='everything').status_code == 400


def test_step_one_counts_the_squares_already_picked(client):
    profile = _hunter(client)
    run = svc.start(profile, AZ)
    svc.assign(run, profile, 'B', _contract('Bastion'))
    resp = client.get(reverse('challenge_optimizer'))
    runs = {r['type']: r for r in resp.context['runs']}
    assert runs[AZ]['open'] == 25 and runs[AZ]['picked'] == 1
    html = resp.content.decode()
    picked = html[html.index('data-cplan-picked-for="az"'):]
    picked = picked[:picked.index('1 picked</span>')]
    assert 'data-picked="1" >' in picked   # no `hidden` on the count


def test_a_pick_that_stays_is_a_line_with_nothing_to_apply(client):
    profile = _hunter(client)
    run = svc.start(profile, AZ)
    current = _contract('Bastion')
    svc.assign(run, profile, 'B', current)
    body = _body(_plan(client, types=[AZ], picks='replan'))
    [line] = [line for line in body['lines'] if line['slug'] == current.slug]
    assert line['squares'][0]['key'] == 'B' and line['squares'][0]['stays'] is True
    assert line['replacing'] == {} and line['replaces'] == [] and line['outside_filters'] is False


def test_a_changed_pick_names_the_game_it_replaces_by_its_live_slug(client):
    profile = _hunter(client)
    run = svc.start(profile, AZ)
    current = _contract('Bastion')
    svc.assign(run, profile, 'B', current)
    other = _contract('Bloodborne')
    body = _body(_plan(client, types=[AZ], picks='replan', tweaks={'pins': [other.slug]}))
    [line] = [line for line in body['lines'] if line['slug'] == other.slug]
    assert line['squares'][0]['stays'] is False
    assert line['replaces'] == [{**line['replaces'][0], 'name': 'Bastion', 'slug': current.slug}]
    assert line['replacing'] == {AZ: current.slug}


def test_a_staying_pick_off_your_platforms_is_never_called_outside_your_filters(client):
    profile = _hunter(client)
    run = svc.start(profile, AZ)
    off = _contract('Bastion', platforms=('PS4',))
    svc.assign(run, profile, 'B', off)
    body = _body(_plan(client, types=[AZ], platforms=['PS5'], picks='replan'))
    [line] = [line for line in body['lines'] if line['slug'] == off.slug]
    assert line['squares'][0]['stays'] is True and line['outside_filters'] is False


def test_a_changed_pick_carries_its_live_slug_while_apply_compares_the_snapshot(client):
    profile = _hunter(client)
    run = svc.start(profile, AZ)
    current = _contract('Bastion')
    svc.assign(run, profile, 'B', current)
    snapshot = current.slug
    Contract.objects.filter(pk=current.pk).update(slug='bastion-renamed')
    other = _contract('Bloodborne')
    body = _body(_plan(client, types=[AZ], picks='replan', tweaks={'pins': [other.slug]}))
    [line] = [line for line in body['lines'] if line['slug'] == other.slug]
    assert line['replaces'][0]['slug'] == 'bastion-renamed' and line['replacing'] == {AZ: snapshot}


def test_a_lock_round_trips_and_is_saved_as_one(client):
    from challenges.models import OptimizerDraft
    profile = _hunter(client)
    run = svc.start(profile, AZ)
    current = _contract('Bastion')
    svc.assign(run, profile, 'B', current)
    entry = {'type': AZ, 'key': 'B', 'contract': current.slug, 'lock': True}
    _plan(client, types=[AZ], picks='replan', tweaks={'square_pins': [entry, {**entry, 'key': 'C', 'lock': 'yes'}]})
    saved = OptimizerDraft.objects.get(profile=profile).tweaks['square_pins']
    assert saved == [entry, {'type': AZ, 'key': 'C', 'contract': current.slug}]   # only a real true is a lock


def test_a_stale_lock_is_not_saved_with_the_plan(client):
    from challenges.models import OptimizerDraft
    profile = _hunter(client)
    run = svc.start(profile, AZ)
    locked, mine = _contract('Bastion'), _contract('Bloodborne')
    svc.assign(run, profile, 'B', mine)   # the hunter changed B by hand since locking Bastion there
    stale = {'type': AZ, 'key': 'B', 'contract': locked.slug, 'lock': True}
    holds = {'type': AZ, 'key': 'C', 'contract': mine.slug}   # an unmarked pin is kept as sent
    _plan(client, types=[AZ], picks='replan', tweaks={'square_pins': [stale, holds]})
    assert OptimizerDraft.objects.get(profile=profile).tweaks['square_pins'] == [holds]


def test_a_locked_pick_that_stays_is_not_tagged_your_pick(client):
    profile = _hunter(client)
    run = svc.start(profile, AZ)
    current = _contract('Bastion')
    svc.assign(run, profile, 'B', current)
    lock = {'type': AZ, 'key': 'B', 'contract': current.slug, 'lock': True}
    body = _body(_plan(client, types=[AZ], picks='replan', tweaks={'square_pins': [lock]}))
    [line] = [line for line in body['lines'] if line['slug'] == current.slug]
    assert line['squares'][0]['stays'] is True and line['pinned'] is False


def test_apply_never_rewrites_a_square_that_already_holds_the_game(client):
    profile = _hunter(client)
    run = svc.start(profile, AZ)
    current = _contract('Bastion')
    svc.assign(run, profile, 'B', current)
    body = _body(_post(client, 'challenge_optimizer_apply',
                       {'lines': [{'type': AZ, 'key': 'B', 'contract': current.slug, 'replacing': current.slug}]}))
    assert body['applied'] == 0 and body['results'][0]['reason'] == "It's already your pick there."


def test_other_options_never_offers_a_locked_pick_as_a_square_a_game_also_covers(client):
    profile = _hunter(client)
    job = Job.objects.order_by('slug').first()
    double = _contract('Astro Bot', jobs=[job])
    svc.start(profile, AZ)
    jobs_run = svc.start(profile, JOBS)
    pick = _contract('Bloodborne', jobs=[job])
    svc.assign(jobs_run, profile, job.slug, pick)
    ask = {'types': [AZ, JOBS], 'badge_started': False, 'type': AZ, 'key': 'A', 'picks': 'replan'}
    _plan(client, picks='replan')

    def also(extra):
        rows = _body(_post(client, 'challenge_optimizer_alternatives', {**ask, **extra, 'group': 'rest'}))['rows']
        return {r['slug']: [s['key'] for s in r['also']] for r in rows}[double.slug]

    assert also({}) == [job.slug]
    lock = {'type': JOBS, 'key': job.slug, 'contract': pick.slug, 'lock': True}
    assert also({'tweaks': {'square_pins': [lock]}}) == []


def test_start_fresh_is_an_answer_the_door_takes(client):
    from challenges.optimizer_views import _OptimizerJsonView
    assert _OptimizerJsonView().options({'types': [AZ], 'picks': 'fresh'}).fresh_picks


def test_an_unknown_badge_weight_is_refused(client):
    profile = _hunter(client)
    svc.start(profile, AZ)
    assert _plan(client, types=[AZ], badge_weight='maximum').status_code == 400



def test_a_page_past_the_end_has_no_more_and_unknown_types_are_dropped(client):
    _hunter(client)
    _edition('Uncharted')
    body = _body(_badges(client, page='999', type='nonsense'))
    assert body['more'] is False and body['total'] == 1   # the junk type filtered nothing out


def test_a_badge_page_costs_a_fixed_number_of_queries(client, django_assert_max_num_queries):
    _hunter(client)
    for i in range(6):
        _edition(f'Series {i}')
    _edition('Community One', badge_type='user')   # a user badge reads its submitter's avatar for art
    client.get(reverse('challenge_optimizer_badges'))   # warm the session / user lookups
    with django_assert_max_num_queries(10):
        resp = client.get(reverse('challenge_optimizer_badges'))
    assert _body(resp)['total'] == 7


def test_started_cards_name_their_type_too(client):
    from django.template.loader import render_to_string
    from trophies.models import SeriesBadgeStanding
    from challenges.optimizer_views import badge_step_context
    profile = _hunter(client)
    gb = _edition('God of War', badge_type='franchise', stages=4)
    SeriesBadgeStanding.objects.create(profile=profile, series_slug=gb.series.series_slug, progress_bp=2500,
                                       group_progress={gb.platform_group.key: [1, 4]})
    [card] = badge_step_context(profile)['started_badges']
    html = render_to_string('challenges/partials/_optimizer_badge_card.html', {'card': card, 'selectable': False})
    assert f'Franchise&nbsp;<span aria-hidden="true">&middot;</span> {gb.platform_group.name}' in html


def test_step_four_builds_only_the_cards_it_shows(client, django_assert_max_num_queries):
    from trophies.models import SeriesBadgeStanding
    profile = _hunter(client)
    for i in range(12):
        gb = _edition(f'Started {i}', stages=4)
        SeriesBadgeStanding.objects.create(profile=profile, series_slug=gb.series.series_slug, progress_bp=2500,
                                           group_progress={gb.platform_group.key: [1, 4]})
    from challenges.optimizer_views import STARTED_PREVIEW, badge_step_context
    with django_assert_max_num_queries(12):
        ctx = badge_step_context(profile)
    assert ctx['started_count'] == 12 and len(ctx['started_badges']) == STARTED_PREVIEW
    assert ctx['started_more'] == 12 - STARTED_PREVIEW


def _fill_line_slots():
    """Every whole-catalogue slot held by a build that started just now."""
    import time
    from challenges.services import build_line
    build_line.redis_client.zadd(build_line.HOLDERS_KEY, {f'busy-{i}': time.time() for i in range(build_line.SLOTS)})


def _free_line_slots():
    from challenges.services import build_line
    build_line.redis_client.delete(build_line.HOLDERS_KEY)


def _busy_catalogue(client):
    profile = _hunter(client)
    _contract('Astro Bot')
    _edition('Uncharted')   # a live edition, so "All badges" has a catalogue to build
    svc.start(profile, AZ)
    _fill_line_slots()
    return profile


def test_a_busy_catalogue_puts_the_hunter_in_line_not_out(client):
    _busy_catalogue(client)

    first = _plan(client, types=[AZ], badge_all=True)

    body = _body(first)
    assert first.status_code == 202 and body['queued'] is True and body['position'] == 1 and body['eta_seconds'] >= 1
    # The slots free up: re-asking with the ticket builds the plan.
    _free_line_slots()
    second = _plan(client, types=[AZ], badge_all=True, line_ticket=body['ticket'])
    assert second.status_code == 200 and 'lines' in _body(second)


def test_waiting_in_line_is_not_charged_against_the_build_limit(client):
    _busy_catalogue(client)
    limit = int(BUILD_RATE.split('/')[0])

    ticket = _body(_plan(client, types=[AZ], badge_all=True))['ticket']
    for _ in range(limit + 2):   # far more re-asks than the build limit allows builds
        resp = _plan(client, types=[AZ], badge_all=True, line_ticket=ticket)
        assert resp.status_code == 202
        assert _body(resp)['ticket'] == ticket   # the same place in line, not a new one each time


def test_a_crafted_ticket_does_not_skip_the_line_or_the_limit(client):
    _busy_catalogue(client)
    resp = _plan(client, types=[AZ], badge_all=True, line_ticket='not-a-real-ticket')
    assert resp.status_code == 202 and _body(resp)['ticket'] != 'not-a-real-ticket'


def test_another_hunters_ticket_does_not_waive_the_build_limit(client):
    from challenges.services import build_line
    _busy_catalogue(client)
    theirs = _body(_plan(client, types=[AZ], badge_all=True))['ticket']

    svc.start(_hunter(client), AZ)   # a different hunter, same browser
    assert not build_line.valid_ticket(theirs, client.session['_auth_user_id'])
    resp = _plan(client, types=[AZ], badge_all=True, line_ticket=theirs)
    assert resp.status_code == 202 and _body(resp)['ticket'] != theirs


def test_a_ticket_does_not_waive_the_limit_for_answers_that_never_wait(client):
    # The waiver is for a hunter re-asking IN LINE. A narrow question (no whole-catalogue build) never waits, so a
    # valid ticket sent with it must not let a script build past the limit.
    _busy_catalogue(client)
    ticket = _body(_plan(client, types=[AZ], badge_all=True))['ticket']
    limit = int(BUILD_RATE.split('/')[0])

    platform_sets = [['PS5'], ['PS4'], ['PS3'], ['PSVITA'], ['PSVR'], ['PSVR2'], ['PS4', 'PS5']]
    statuses = [_plan(client, types=[AZ], platforms=p, line_ticket=ticket).status_code
                for p in platform_sets[:limit + 1]]
    assert statuses[limit] == 429


def test_a_cached_answer_takes_its_ticket_out_of_line(client):
    from challenges.services import build_line
    _busy_catalogue(client)
    ticket = _body(_plan(client, types=[AZ], badge_all=True))['ticket']
    _free_line_slots()
    # Another tab builds the same answers (two free slots, one ticket ahead: it goes), so the plan is now cached.
    assert _plan(client, types=[AZ], badge_all=True).status_code == 200

    assert _plan(client, types=[AZ], badge_all=True, line_ticket=ticket).status_code == 200
    assert not build_line.valid_ticket(ticket, client.session['_auth_user_id'])


def test_other_options_never_joins_the_line(client):
    from challenges.services import build_line
    _busy_catalogue(client)

    resp = _post(client, 'challenge_optimizer_alternatives',
                 {'types': [AZ], 'badge_started': False, 'badge_all': True, 'type': AZ, 'key': 'A'})

    assert resp.status_code == 429 and 'error' in _body(resp)
    assert build_line.redis_client.zcard(build_line.LINE_KEY) == 0   # no ticket left for nobody to re-ask


def test_other_options_is_not_charged_for_a_build_the_line_refused(client):
    _busy_catalogue(client)
    limit = int(BUILD_RATE.split('/')[0])
    for _ in range(limit + 1):
        resp = _post(client, 'challenge_optimizer_alternatives',
                     {'types': [AZ], 'badge_started': False, 'badge_all': True, 'type': AZ, 'key': 'A'})
        assert resp.status_code == 429
    # The build allowance is untouched: the hunter's own plan still gets a place in line, not a refusal.
    assert _plan(client, types=[AZ], badge_all=True).status_code == 202


def test_a_lost_ticket_is_charged_like_a_newcomer(client, monkeypatch):
    from challenges.services import build_line
    _busy_catalogue(client)
    limit = int(BUILD_RATE.split('/')[0])
    ticket = _body(_plan(client, types=[AZ], badge_all=True))['ticket']
    _free_line_slots()
    # Use up the build allowance on other answers.
    for p in [['PS4'], ['PS3'], ['PSVITA'], ['PSVR'], ['PSVR2'], ['PS4', 'PS5']][:limit - 1]:
        assert _plan(client, types=[AZ], platforms=p).status_code == 200

    def lost(r, lease, ticket):
        r.zrem(build_line.HOLDERS_KEY, lease)
        raise build_line.TicketLost()
    monkeypatch.setattr(build_line, '_claim', lost)   # a parallel request with the same ticket won the claim
    assert _plan(client, types=[AZ], badge_all=True, line_ticket=ticket).status_code == 429


def test_a_refusal_while_waiting_takes_the_ticket_out_of_line(client, monkeypatch):
    from challenges.services import build_line
    _busy_catalogue(client)
    ticket = _body(_plan(client, types=[AZ], badge_all=True))['ticket']
    monkeypatch.setattr(planner, 'run_states', lambda profile, types: {})   # the run ended in another tab

    assert _plan(client, types=[AZ], badge_all=True, line_ticket=ticket).status_code == 400
    assert not build_line.valid_ticket(ticket, client.session['_auth_user_id'])


@override_settings(CHALLENGES_BETA_MEMBERS_ONLY=False)
def test_an_allowance_refusal_while_waiting_takes_the_ticket_out_of_line(client):
    from challenges.services import build_line
    user = UserFactory()
    profile = ProfileFactory(user=user, user_is_premium=False, is_linked=True)
    client.force_login(user)
    _contract('Astro Bot')
    _edition('Uncharted')
    svc.start(profile, AZ)
    _fill_line_slots()
    # The confirmed free plan waits in line (nothing is spent yet)...
    queued = _plan(client, types=[AZ], badge_all=True, use_free_plan=True)
    assert queued.status_code == 202
    ticket = _body(queued)['ticket']
    # ...and a re-ask that lost its confirmation is refused, which takes it out of line.
    assert _plan(client, types=[AZ], badge_all=True, line_ticket=ticket).status_code == 409
    assert not build_line.valid_ticket(ticket, user.id)


def test_still_open_names_the_squares_badge_games_only_left_open(client):
    profile = _hunter(client)
    _contract('Astro Bot')   # a game, but on no badge
    _edition('Uncharted')    # a live edition, so "All badges" is a real source
    svc.start(profile, AZ)

    on = _body(_plan(client, types=[AZ], badge_all=True, badge_weight='only'))
    off = _body(_plan(client, types=[AZ], badge_all=True, badge_weight='bonus'))
    none_selected = _body(_plan(client, types=[AZ], badge_weight='only'))

    assert [sq['key'] for sq in on['notes']['badge_blocked']] == ['A']   # Astro Bot would fill it
    assert 'Z' in [sq['key'] for sq in on['empty']]                      # no game fits Z at all: not blamed
    assert off['notes']['badge_blocked'] == []
    assert none_selected['notes']['badge_blocked'] == []                 # no badge source: Only means nothing


def test_a_must_have_that_clears_no_stage_is_tagged_outside_filters_under_only(client):
    profile = _hunter(client)
    plain = _contract('Astro Bot')
    _edition('Uncharted')
    svc.start(profile, AZ)

    body = _body(_plan(client, types=[AZ], badge_all=True, badge_weight='only', tweaks={'pins': [plain.slug]}))

    [line] = body['lines']
    assert line['slug'] == plain.slug and line['pinned'] is True and line['outside_filters'] is True


def test_each_line_carries_its_board_positions_for_the_sort_toggle(client):
    profile = _hunter(client)
    job_a, job_b = list(Job.objects.order_by('slug')[:2])
    both = _contract('Astro Bot', jobs=[job_b])
    az_only = _contract('Bugsnax')
    svc.start(profile, AZ)
    jobs_run = svc.start(profile, JOBS)
    from challenges.services import picker
    board = list(picker.square_labels(jobs_run))

    lines = {line['slug']: line for line in _body(_plan(client))['lines']}

    assert lines[both.slug]['sort'] == {AZ: 0, JOBS: board.index(job_b.slug)}   # A is the first letter
    assert lines[az_only.slug]['sort'] == {AZ: 1}                               # B; no job square


def test_the_wizards_dock_is_opaque():
    """Review CH-09 (2026-10-24): at 94% the step's cards showed through the docked Next bar, so on a short
    laptop it read as a band printed across two cards rather than a bar floating over them."""
    from pathlib import Path

    css = (Path(__file__).resolve().parents[2] / 'static' / 'css' / 'components' / 'challenge-optimizer.css').read_text(
        encoding='utf-8')
    dock = css[css.index('.pp-cplan__dock {'):]
    dock = dock[:dock.index(chr(10) + "}")]
    assert 'background: var(--pp-bg-1);' in dock
    assert 'transparent' not in dock

