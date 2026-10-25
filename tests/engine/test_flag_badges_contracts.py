"""A contract wears the same flag badges wherever it shows (owner, 2026-10-24): the challenge picker's "can this still
be finished?" rule (`challenges/services/contract_flags.py`), on the Career board and a job's contracts, the contract
modal, the new-contracts modal and a challenge's squares."""
import pytest
from django.db import connection
from django.template.loader import render_to_string
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from challenges.models import CHALLENGE_TYPE_AZ
from challenges.services import challenge_service as svc
from challenges.services import eligibility, slot_render
from challenges.services.contract_flags import contract_flag_keys
from tests.engine.test_challenge_picker_filters import _contract, _hunter
from tests.engine.test_jobs_surface import _job, _solo
from tests.factories import GameFactory
from trophies.services import contracts_service

pytestmark = pytest.mark.django_db


def _online(name):
    """A live contract whose only game has online trophies and is delisted."""
    contract = _contract(name)
    contract.concept.games.update(has_online_trophies=True, is_delisted=True)
    return contract


def test_a_shared_membership_read_answers_exactly_as_a_fresh_one():
    """The board passes the membership it read for its covers; the answer must not depend on who read it."""
    flagged, clean = _online('Bravo Shared'), _contract('Bravo Clean')
    membership = eligibility.contract_membership([flagged, clean])

    assert contract_flag_keys([flagged, clean]) == contract_flag_keys([flagged, clean], membership)
    assert contract_flag_keys([flagged, clean])[flagged.id] == ['delisted', 'online']
    with CaptureQueriesContext(connection) as ctx:
        contract_flag_keys([flagged, clean], membership)
    assert len(ctx.captured_queries) == 1, 'with membership in hand, only the games are read'


def test_the_board_and_a_jobs_contracts_wear_the_badges(client):
    _solo()
    job = _job('archivist', 'Archivist')
    flagged = _online('Bravo Board')
    flagged.jobs.set([job])

    page = contracts_service.contracts_page(None, jobs=['archivist'])
    card = next(c for c in page['contracts'] if c['slug'] == flagged.slug)
    assert card['flag_keys'] == ['delisted', 'online']

    body = client.get(reverse('job_contracts', args=['archivist'])).content.decode()
    assert '<use href="#pp-gflag-online"/>' in body and '<use href="#pp-gflag-delisted"/>' in body
    assert 'pp-gflags pp-gflags--strip pp-gflags--sm' in body, 'along the banner foot on a tile'


def test_the_contract_modal_shows_each_games_own_flags():
    """The modal is a grid of the contract's games, each one list, so each wears its own; the contract-level
    badges stay on the board's tile."""
    _solo()
    job = _job('archivist', 'Archivist')
    contract = _online('Bravo Modal')
    contract.jobs.set([job])

    modal = contracts_service.build_contract_modal(None, contract.slug)

    assert 'flag_keys' not in modal, 'nothing in the modal draws the contract-level keys'
    html = render_to_string('trophies/partials/contracts/_contract_game.html',
                            {'game': modal['games'][0]['game'], 'pgame': None, 'fam': '', 'profile': None})
    assert 'pp-gflags' in html, "a member game shows its own list's flags"


def test_the_new_contracts_modal_badges_its_rows():
    contract = _online('Bravo New')
    rows = [contract]
    contract.flag_keys = contract_flag_keys(rows)[contract.pk]
    html = render_to_string('trophies/partials/career/_new_contracts.html',
                            {'nc': {'heroes': rows, 'rows': rows, 'disciplines': [], 'total': 1, 'extra': 0}})
    assert html.count('<use href="#pp-gflag-online"/>') == 2, 'the hero cover and the list row'


def test_a_square_wears_its_games_flags_until_it_is_finished():
    """By the picker's rule, under the key chip, and said in the square's one sentence. A finished square shows
    none: its trophies are already earned."""
    profile = _hunter()
    run = svc.start(profile, CHALLENGE_TYPE_AZ)
    flagged = _online('Bravo Square')
    svc.assign(run, profile, 'B', flagged)
    slot = run.slots.get(key='B')

    card = next(c for c in slot_render.slot_cards(run) if c['key'] == 'B')
    assert card['flag_keys'] == ['delisted', 'online']
    html = render_to_string('challenges/partials/_square_body.html', {'card': card})
    assert '<span aria-hidden="true"><span class="pp-gflags pp-gflags--strip pp-gflags--sm">' in html
    assert 'Bravo Square, in progress, delisted, online trophies' in html

    type(slot).objects.filter(pk=slot.pk).update(is_completed=True, completed_at=timezone.now(), completed_via='live')
    done = next(c for c in slot_render.slot_cards(run) if c['key'] == 'B')
    assert done['flag_keys'] == []
    assert slot_render.card_for(run.slots.get(key='B'))['flag_keys'] == []


def test_a_finished_contract_wears_no_badges_on_the_board_as_a_finished_square_does():
    """Ready to Claim or Claimed on the board, Banked in History: its trophies are earned, so "can it still be
    finished?" is moot.
    The flags are read only for the open ones, and a page with none open reads nothing."""
    claimed, banked, ready, open_ = (_online('Bravo Claimed'), _online('Bravo Banked'), _online('Bravo Ready'),
                                     _online('Bravo Open'))
    cards = [{'status': 'accepted'}, {'status': 'available', 'is_history': True}, {'status': 'claimable'},
             {'status': 'pursuing'}]

    contracts_service._attach_flags(cards, [claimed, banked, ready, open_])

    assert [c['flag_keys'] for c in cards] == [[], [], [], ['delisted', 'online']]
    with CaptureQueriesContext(connection) as ctx:
        contracts_service._attach_flags([{'status': 'accepted'}], [claimed])
    assert len(ctx.captured_queries) == 0
