"""The landing pass (fix plan branch 7: FV-05, FV-06). Design record: docs/design/reviews/landing-pass.md.

FV-05: the hero's one first step is "Look up", and a found profile nobody has an account on offers "That's you?
Sign up free to claim it.", carrying the name through sign-up and email confirmation to Link PSN, filled in.
FV-06: the Profile Card and the share images, 0.28 scale on a phone, keep a caption and open a real-size viewer.
"""
import re
from pathlib import Path
from urllib.parse import quote

import pytest
from django.core import mail
from django.core.cache import cache
from django.urls import reverse

from tests.factories import ProfileFactory, UserFactory

pytestmark = pytest.mark.django_db

ROOT = Path(__file__).resolve().parents[2]
CF = {'HTTP_CF_RAY': '8f0000000000abcd-LHR'}
SEARCH_URL = reverse('search_sync_profile')
STATUS_URL = reverse('add_sync_status')


@pytest.fixture(autouse=True)
def _clean_cache():
    cache.clear()
    yield
    cache.clear()


def _landing(client):
    return client.get('/', **CF).content.decode()


def _strip_js_comments(src):
    return re.sub(r'//[^\n]*', '', re.sub(r'/\*.*?\*/', '', src, flags=re.S))


def _landing_js():
    return _strip_js_comments((ROOT / 'static/js/landing.js').read_text(encoding='utf-8'))


# ---- FV-05: one first step ----

def test_the_search_says_look_up(client):
    body = _landing(client)
    button = re.search(r'<button type="submit" class="pp-cta land-search__go">([^<]*)</button>', body)
    assert button and button.group(1) == 'Look up'
    assert 'Start tracking' not in body


def test_the_claim_line_ships_hidden_with_the_urls_it_needs(client):
    body = _landing(client)
    claim = re.search(r'<p class="land-search__claim" data-land-claim hidden>(.*?)</p>', body, re.S)
    assert claim, 'the claim line is missing or not hidden until a profile is found'
    assert "That's you?" in claim.group(1) and 'Sign up free to claim it</a>' in claim.group(1)
    assert 'data-url-signup="%s"' % reverse('account_signup') in body
    assert 'data-url-link="%s"' % reverse('link_psn') in body


@pytest.mark.parametrize('owned, claimable', [(False, True), (True, False)])
def test_a_tracked_name_says_whether_it_can_be_claimed(client, owned, claimable):
    """The anonymous hero's commonest case (a name we already hold) and the status poll both carry it, so
    nobody is invited to claim a hunter who already has an account."""
    ProfileFactory(psn_username='claimme', account_id='acc-claimme',
                   **({} if owned else {'user': None, 'is_linked': False}))

    search = client.post(SEARCH_URL, {'psn_username': 'claimme'})
    assert search.status_code == 403 and search.json()['reason'] == 'sign_in'
    assert search.json()['claimable'] is claimable

    status = client.get(STATUS_URL, {'psn_username': 'claimme'})
    assert status.json()['claimable'] is claimable


def test_a_hunter_still_in_the_sync_queue_is_claimable(client):
    """An empty `account_id` means "still queued", never "PSN does not know this name": a failed first sync
    DELETES its row (`Profile.set_sync_status('error')`). A brief `account_id` gate hid the claim from a real
    hunter who searched twice while the queue was busy, and the `sign_in` path never polls again to recover."""
    ProfileFactory(psn_username='queued', user=None, is_linked=False, account_id='', sync_status='syncing')

    assert client.post(SEARCH_URL, {'psn_username': 'queued'}).json()['claimable'] is True
    assert client.get(STATUS_URL, {'psn_username': 'queued'}).json()['claimable'] is True


def test_the_claim_link_carries_the_name_to_link_psn():
    js = _landing_js()
    offer = js.split('function offerClaim(name, claimable) {', 1)[1].split('\n        }\n', 1)[0]
    assert "form.dataset.urlLink + '?psn=' + encodeURIComponent(name)" in offer
    assert "form.dataset.urlSignup + '?next=' + encodeURIComponent(next)" in offer
    assert 'claim.hidden = !claimable' in offer, 'a claimed profile would still be offered'
    # Shown from both "found" paths, and cleared on every new search, a rejected name included.
    assert js.count('offerClaim(') == 5
    submit = js.split("form.addEventListener('submit'", 1)[1].split('fetch(form.action', 1)[0]
    rejected, accepted = submit.split('if (!PSN_RE.test(name)) {', 1)[1].split('return;', 1)
    assert 'offerClaim(name, false)' in rejected and 'visit.hidden = true' in rejected, \
        'a rejected name leaves the last result\'s link and claim under its error'
    assert 'offerClaim(name, false)' in accepted, 'the last search\'s claim survives a new one'


def _link_page(client, psn):
    client.force_login(UserFactory())
    return client.get(reverse('link_psn'), {'psn': psn}).content.decode()


def test_link_psn_fills_in_the_name_it_was_handed(client):
    page = _link_page(client, 'SomeHunter')
    field = re.search(r'<input[^>]*id="id_psn_username"[^>]*>', page).group(0)
    assert 'value="SomeHunter"' in field   # as typed; the POST lowercases it, as it does a typed name


@pytest.mark.parametrize('junk', ['x', 'not a name!', 'a' * 30, '"><script>'])
def test_link_psn_drops_a_name_the_field_would_refuse(client, junk):
    page = _link_page(client, junk)
    field = re.search(r'<input[^>]*id="id_psn_username"[^>]*>', page).group(0)
    assert 'value=""' in field, field
    assert 'pp-field__error' not in page, 'an error the hunter did not cause'


def test_the_name_survives_sign_up_and_email_confirmation(client, settings):
    """The whole claim, end to end on allauth's own machinery: sign up with `next`, confirm the email in the
    same browser, and land on Link PSN with the name in the URL."""
    settings.EMAIL_BACKEND = 'django.core.mail.backends.locmem.EmailBackend'
    target = reverse('link_psn') + '?psn=claimer'
    signup = reverse('account_signup') + '?next=' + quote(target, safe='')

    page = client.get(signup).content.decode()
    assert 'name="next" value="%s"' % target.replace('&', '&amp;') in page, 'sign-up dropped `next`'
    client.post(signup, {'email': 'claimer@example.com', 'password1': 'a-long-test-passphrase-52',
                         'next': target})

    link = re.search(r'(/accounts/confirm-email/[^/\s]+/)', mail.outbox[-1].body).group(1)
    resp = client.get(link)
    assert resp.status_code == 302 and resp['Location'] == target


def _sign_up_claiming(client, settings, name):
    settings.EMAIL_BACKEND = 'django.core.mail.backends.locmem.EmailBackend'
    target = reverse('link_psn') + '?psn=' + name
    client.post(reverse('account_signup') + '?next=' + quote(target, safe=''),
                {'email': '%s@example.com' % name, 'password1': 'a-long-test-passphrase-52', 'next': target})
    from users.models import CustomUser
    return CustomUser.objects.get(email='%s@example.com' % name)


def _prefilled(page):
    return re.search(r'<input[^>]*id="id_psn_username"[^>]*>', page).group(0)


def test_the_name_outlives_allauths_fifteen_minute_pending_login(client, settings):
    """allauth drops its pending login (and the `next` on it) after ACCOUNT_LOGIN_TIMEOUT, so an email confirmed
    later lands on sign-in and then the no-PSN welcome, with no `next`. The session's own record still fills Link
    PSN in. Simulated as that late path: no `?psn=`, a fresh sign-in in the same browser."""
    user = _sign_up_claiming(client, settings, 'latecomer')
    client.force_login(user)

    page = client.get(reverse('link_psn')).content.decode()
    assert 'value="latecomer"' in _prefilled(page)


def test_the_name_survives_fix_it(client, settings):
    """"Wrong address? Fix it" deletes the account, clears allauth's pending login (and its `next`) and reopens
    sign-up bare. The claimed name must still be waiting after the second sign-up."""
    _sign_up_claiming(client, settings, 'fixer')
    client.post(reverse('account_verification_fix'))
    from users.models import CustomUser
    assert not CustomUser.objects.filter(email='fixer@example.com').exists(), 'Fix it did not run'
    client.post(reverse('account_signup'), {'email': 'fixed@example.com', 'password1': 'a-long-test-passphrase-52'})
    client.force_login(CustomUser.objects.get(email='fixed@example.com'))

    assert 'value="fixer"' in _prefilled(client.get(reverse('link_psn')).content.decode())


def test_a_claim_never_reaches_someone_who_signs_up_next_on_a_shared_browser(client, settings):
    """A claims "alice" on a library PC and confirms on their phone, never signing in here. B then signs up on
    that PC: B's Link PSN must not arrive with "alice" in it."""
    _sign_up_claiming(client, settings, 'alice')
    from users.models import CustomUser
    client.post(reverse('account_signup'), {'email': 'b@example.com', 'password1': 'a-long-test-passphrase-52'})
    client.force_login(CustomUser.objects.get(email='b@example.com'))

    assert 'value=""' in _prefilled(client.get(reverse('link_psn')).content.decode())


def test_a_claim_never_reaches_someone_who_only_signs_in_on_a_shared_browser(client, settings):
    """Nobody is signed in after A's claim, so a stranger's sign-in KEEPS the session data (Django only flushes
    when a different user was signed in): the claim's own owner check is the only thing that stops it."""
    _sign_up_claiming(client, settings, 'alice')
    client.force_login(UserFactory())
    assert client.session.get('pp_claimed_psn', {}).get('name') == 'alice', 'the session was wiped: proves nothing'

    assert 'value=""' in _prefilled(client.get(reverse('link_psn')).content.decode())


def test_someone_elses_fix_it_does_not_hand_them_the_claim(client, settings):
    """A claims "alice" and leaves; C signs up on the same browser, mistypes, and presses Fix it. Releasing the
    claim without checking whose it was made C's next sign-up adopt "alice" (audit-caught)."""
    _sign_up_claiming(client, settings, 'alice')
    from users.models import CustomUser
    client.post(reverse('account_signup'), {'email': 'c-typo@example.com', 'password1': 'a-long-test-passphrase-52'})
    client.post(reverse('account_verification_fix'))
    assert not CustomUser.objects.filter(email='c-typo@example.com').exists(), 'Fix it did not run'
    client.post(reverse('account_signup'), {'email': 'c@example.com', 'password1': 'a-long-test-passphrase-52'})
    client.force_login(CustomUser.objects.get(email='c@example.com'))

    assert 'value=""' in _prefilled(client.get(reverse('link_psn')).content.decode())


def test_only_a_link_psn_next_is_remembered(client, settings):
    settings.EMAIL_BACKEND = 'django.core.mail.backends.locmem.EmailBackend'
    for nxt in ('/games/?psn=sneaky', 'https://evil.example' + reverse('link_psn') + '?psn=sneaky'):
        email = 'n%d@example.com' % len(nxt)
        client.post(reverse('account_signup') + '?next=' + quote(nxt, safe=''),
                    {'email': email, 'password1': 'a-long-test-passphrase-52', 'next': nxt})
        from users.models import CustomUser
        assert CustomUser.objects.filter(email=email).exists(), 'the sign-up failed, so this tested nothing'
        assert 'pp_claimed_psn' not in client.session, nxt


def test_step_one_forgets_the_claimed_name(client, settings):
    """A prefill and nothing more: once step 1 is submitted, whatever name they chose, it is gone. (An invalid
    name here, so the test never reaches the PSN lookup.)"""
    user = _sign_up_claiming(client, settings, 'oneshot')
    client.force_login(user)
    assert client.session.get('pp_claimed_psn', {}).get('name') == 'oneshot'

    client.post(reverse('link_psn'), {'action': 'submit_username', 'psn_username': 'x'})
    assert 'pp_claimed_psn' not in client.session


# ---- FV-06: legible on a phone ----

def test_each_showcase_picture_has_a_caption_and_a_full_size_opener(client):
    body = _landing(client)
    card = body.split('class="land-card pp-arrive__it"', 1)[1].split('</section>', 1)[0]
    assert 'data-land-zoom data-land-zoom-title="The Profile Card"' in card
    # The button is UNDER the note, not over the picture (every corner of the card holds content), and its
    # visible words are in its accessible name.
    assert 'data-land-cardframe data-land-zoom-target' in card
    after_note = card.split('class="land-card__note"', 1)[1]
    assert 'View full size<span class="sr-only"> (the Profile Card)</span></button>' in after_note
    assert ': platinums, trophy record, Pursuer rank and badges on one card.' in card

    proof = body.split('class="land-proof pp-arrive__it"', 1)[1].split('</section>', 1)[0]
    assert proof.count('data-land-zoom-open hidden') == 2 and proof.count('data-land-zoom-target') == 2
    assert 'data-land-zoom-open hidden' in card, 'without JS the button would offer a press that does nothing'
    for figure in proof.split('<figure')[1:]:
        assert figure.index('data-land-zoom-open') > figure.index('</figcaption>'), 'the button is back over the picture'
    assert proof.count('<figcaption class="land-proof__cap">') == 2


def test_the_viewer_ships_with_the_page(client):
    body = _landing(client)
    dialog = body.split('<dialog class="land-zoom" data-land-zoom-dialog', 1)
    assert len(dialog) == 2, 'the viewer is missing'
    dialog = dialog[1].split('</dialog>', 1)[0]
    assert 'data-land-zoom-stage' in dialog and 'data-land-zoom-close' in dialog


def test_the_viewer_opens_at_real_size_and_gives_the_page_back():
    js = _landing_js()
    zoom = js.split('function wireZoom() {', 1)[1].split('\n    function boot()', 1)[0]
    assert 'dialog.showModal()' in zoom
    assert 'img.width = 1200' in zoom and "querySelector('[data-land-cardscaler]')" in zoom
    # The page's own pick (the WebP), not a hard-coded PNG at ten times the bytes.
    assert 'img.src = pic.currentSrc || pic.src' in zoom
    assert 'button.hidden = false' in zoom, 'the buttons ship hidden and nothing reveals them'
    assert "stage.addEventListener('click'" in zoom and 'e.target === stage' in zoom
    closing = zoom.split("dialog.addEventListener('close'", 1)[1].split('});', 1)[0]
    assert "root.classList.remove('land-zoom-open')" in closing, 'the page stays locked after closing'
    assert 'returnTo.focus(' in closing
    assert 'wireZoom();' in js.split('function boot() {', 1)[1]


def _css(rel):
    return re.sub(r'/\*.*?\*/', '', (ROOT / rel).read_text(encoding='utf-8'), flags=re.S)


def test_the_viewer_keeps_real_size_and_never_clips_its_left_edge():
    css = _css('static/css/components/landing.css')
    item = re.search(r'\.land-zoom__card,\s*\.land-zoom__img\s*\{([^}]*)\}', css).group(1)
    assert 'width: 1200px' in item and 'max-width: none' in item
    # `margin: auto` in a grid centres while it fits and starts at the edge when it overflows; centring the
    # grid's items instead clips the overflow's left side, out of reach of the scroller.
    assert 'margin: auto' in item
    stage = re.search(r'\.land-zoom__stage\s*\{([^}]*)\}', css).group(1)
    assert 'overflow: auto' in stage and 'place-items' not in stage
    assert re.search(r'html\.land-zoom-open\s*\{\s*overflow:\s*hidden', css)
