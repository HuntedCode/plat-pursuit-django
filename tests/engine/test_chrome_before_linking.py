"""What the chrome asks of an account that has not linked PSN yet, and the house notice it speaks in.

FV-11 (docs/design/reviews/first-visit.md): a brand-new account with no PSN saw the Premium button and a
glowing NEW pill on its avatar, while the only thing it can usefully do is link. The owner's call
(2026-10): both wait for a linked PSN. What's New stays reachable from the account menu either way.

FV-15 / DL-10: messages rendered in three different styles across the first-visit journey; since 2026-10
there is one renderer (partials/_messages.html, which the breadcrumb includes) and one look (.pp-notice).
"""
import pytest
from django.contrib.messages import constants
from django.contrib.messages.storage.base import Message
from django.template.loader import render_to_string
from django.urls import reverse

from tests.factories import ProfileFactory

pytestmark = pytest.mark.django_db

CF = {'HTTP_CF_RAY': '8f0000000000abcd-LHR'}


def _signed_in(client, *, linked):
    profile = ProfileFactory(is_linked=linked)
    assert not profile.user.premium_tier, 'fixture must be a non-member'
    client.force_login(profile.user)
    return client.get(reverse('about'), **CF).content.decode()


def test_an_unlinked_account_sees_no_premium_button_and_no_new_pill(client):
    body = _signed_in(client, linked=False)

    assert 'pp-navpremium' not in body, 'Premium asked for before the account has linked PSN'
    assert 'pp-av__new' not in body, 'the avatar pill glows before the account has linked PSN'
    assert 'something new to read' not in body, 'the avatar label still announces the pill it hides'
    assert reverse('whats_new') in body, "What's New must stay reachable from the menu"


def test_a_linked_non_member_still_sees_both(client):
    """The gate is LINKING, not membership or novelty: once linked, the chrome is what it always was."""
    body = _signed_in(client, linked=True)

    assert 'pp-navpremium' in body
    assert 'pp-av__new' in body


def _render(template, levels):
    msgs = [Message(level, f'msg {i}') for i, level in enumerate(levels)]
    return render_to_string(template, {'messages': msgs})


def test_messages_render_as_house_notices_in_their_tone():
    html = _render('partials/_messages.html',
                   [constants.SUCCESS, constants.ERROR, constants.WARNING, constants.INFO, constants.DEBUG])

    for tone in ('success', 'error', 'warning'):
        assert f'pp-notice pp-notice--{tone}' in html, tone
    assert html.count('pp-notice pp-notice--info') == 2, 'info and debug both read as info'
    assert html.count('role="alert"') == 1 and html.count('role="status"') == 4
    assert html.count('class="pp-notice__icon"') == 5, 'every notice carries its tone icon'
    assert 'alert-' not in html and 'class="alert' not in html, 'DaisyUI alert is back'


def test_the_breadcrumb_renders_messages_through_the_one_renderer():
    html = render_to_string('partials/breadcrumb.html', {
        'messages': [Message(constants.SUCCESS, 'Saved.')], 'breadcrumb': [{'text': 'Home', 'url': '/'}]})

    assert html.count('Saved.') == 1
    assert 'pp-notice pp-notice--success' in html
    assert 'alert-success' not in html
