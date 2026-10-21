"""The contracts board pass (fix plan branch 6: DL-05, DL-07, DL-08, FV-14).

Pins what a claim says it pays (`claim_xp`, and the words around it), the modal reading the card's own dict so
the two cannot disagree, the modal's claim (in place on Career, a link everywhere else), the phone row's markup
and stylesheet, and the cover fan's drawn marks. Design record: docs/design/reviews/contracts-board.md.
"""
import itertools
import re
from pathlib import Path

import pytest
from django.utils import timezone

from trophies.models import Contract, EarnedContract, Job
from trophies.services.career_service import claim_board_url
from trophies.services.contracts_service import build_contract_modal, contracts_page
from trophies.util_modules.constants import CONTRACT_PLATINUM_FRAC, CONTRACT_XP_TOTAL
from tests.career_sources import career_script
from tests.factories import ConceptFactory, GameFactory, IGDBMatchFactory, ProfileFactory

pytestmark = pytest.mark.django_db

ROOT = Path(__file__).resolve().parents[2]
_igdb_seq = itertools.count(91001)
PLAT_XP = round(CONTRACT_XP_TOTAL * CONTRACT_PLATINUM_FRAC)
BONUS_XP = CONTRACT_XP_TOTAL - PLAT_XP


def _contract(slug, jobs=('gunslinger', 'mage')):
    igdb_id = next(_igdb_seq)
    c = Contract.objects.create(name=slug, slug=slug, is_live=True, igdb_id=igdb_id)
    c.jobs.set(Job.objects.filter(slug__in=jobs))
    concept = ConceptFactory(anchor_migration_completed_at=timezone.now())
    IGDBMatchFactory(concept=concept, igdb_id=igdb_id)
    GameFactory(concept=concept)   # PS5 by default, so it is on the current-gen board
    return c


def _row(profile, contract, *, plat=None, full=None, has_platinum=True):
    """The hunter's claim row. `plat` / `full`: None (not reached), 'ready' (reached) or 'banked'."""
    now = timezone.now()
    fields = {'has_platinum': has_platinum}
    if plat:
        fields['platinum_reached_at'] = now
        if plat == 'banked':
            fields['platinum_accepted_at'] = now
    if full:
        fields['full_reached_at'] = now
        if full == 'banked':
            fields['full_accepted_at'] = now
    return EarnedContract.objects.create(profile=profile, contract=contract, **fields)


def _linked_client(client):
    profile = ProfileFactory(is_linked=True)
    client.force_login(profile.user)
    return profile


# ---- What a claim pays (DL-08) ----

@pytest.mark.parametrize('plat, full, has_platinum, expected', [
    ('ready', None, True, PLAT_XP),                  # platinum earned, 100% not yet
    ('ready', 'ready', True, CONTRACT_XP_TOTAL),     # both at once
    ('banked', 'ready', True, BONUS_XP),             # platinum banked earlier, 100% now
    (None, 'ready', False, CONTRACT_XP_TOTAL),       # no platinum tier: 100% pays it all
    ('banked', None, True, 0),                       # nothing ready
])
def test_claim_xp_is_what_one_press_banks(plat, full, has_platinum, expected):
    profile = ProfileFactory()
    c = _contract('pays-%s-%s-%s' % (plat, full, has_platinum))
    _row(profile, c, plat=plat, full=full, has_platinum=has_platinum)

    assert build_contract_modal(profile, c.slug)['claim_xp'] == expected


@pytest.mark.parametrize('plat, full', [('ready', None), ('banked', 'ready'), ('ready', 'ready')])
def test_the_label_is_what_accepting_actually_banks_even_on_an_odd_override(plat, full):
    """6,015 x 0.70 = 4,210.5: the accept path rounds half UP (Decimal), and a float `round()` rounded it to
    even, so the card promised 4,210 and the claim banked 4,211 (and 1,805 vs 1,804 for the 100% tier).
    Held against the real accept, not a constant."""
    from trophies.services.contract_service import accept_contract

    profile = ProfileFactory()
    c = _contract('odd-%s-%s' % (plat, full))
    c.xp_total_override = 6015
    c.save(update_fields=['xp_total_override'])
    _row(profile, c, plat=plat, full=full)

    promised = build_contract_modal(profile, c.slug)['claim_xp']
    assert promised == accept_contract(profile, c) and promised > 0


def test_the_modal_and_the_board_card_agree():
    """The modal is built from the card's own dict, so status and what a claim pays cannot drift apart."""
    profile = ProfileFactory()
    c = _contract('agree')
    _row(profile, c, plat='ready')
    card = next(p for p in contracts_page(profile)['contracts'] if p['slug'] == 'agree')
    modal = build_contract_modal(profile, 'agree')

    for key in ('status', 'claim_xp', 'plat_xp', 'bonus_xp', 'plat_banked', 'full_banked', 'xp_each'):
        assert modal[key] == card[key], key
    assert modal['status'] == 'claimable'


def test_the_anonymous_modal_has_nothing_to_claim():
    c = _contract('anon-pass')
    modal = build_contract_modal(None, c.slug)
    assert modal['status'] == 'available' and modal['claim_xp'] == 0
    assert modal['games'][0]['profile_game'] is None


def test_a_jobless_or_unknown_contract_has_no_modal():
    c = _contract('jobless', jobs=())
    assert build_contract_modal(ProfileFactory(), c.slug) is None
    assert build_contract_modal(ProfileFactory(), 'no-such-contract') is None


# ---- The card (DL-05, DL-08) ----

def _board_card(client, slug):
    html = client.get('/career/?view=contracts').content.decode()
    start = html.index('data-slug="%s"' % slug)
    start = html.rindex('<div class="rp-row ', 0, start)
    depth = 0
    for tag in re.finditer(r'<(/?)div\b', html[start:]):   # the card ends where its own <div> closes
        depth += -1 if tag.group(1) else 1
        if depth == 0:
            return html[start:start + tag.end()]
    raise AssertionError('unbalanced card markup')


def test_the_claim_button_names_the_xp_not_the_tier(client):
    profile = _linked_client(client)
    c = _contract('label-plat')
    _row(profile, c, plat='ready')

    card = _board_card(client, 'label-plat')
    button = re.search(r'<button[^>]*class="pp-cta pp-cta--xs rp-accept"[^>]*>(.*?)</button>', card, re.S)
    assert button, 'the claim button is missing or not the house button'
    assert button.group(1).strip() == 'Claim {:,} XP'.format(PLAT_XP)
    assert 'Claim plat' not in card and 'Claim both' not in card
    assert '</b> at platinum</span>' in card, 'the tier bar still says "plat"'


def test_the_card_uses_house_primitives(client):
    profile = _linked_client(client)
    _row(profile, _contract('house'), plat='ready')
    card = _board_card(client, 'house')

    classes = ' '.join(re.findall(r'class="([^"]*)"', card)).split()
    retired = [c for c in classes if c in ('btn', 'badge') or c.startswith(('btn-', 'badge-'))]
    assert not retired, retired
    assert 'bd-chip bd-chip--primary rp-badge--solid rp-tile__badge' in card


def test_the_platform_strip_shows_only_the_contracts_platforms(client):
    _linked_client(client)
    _contract('plats')
    card = _board_card(client, 'plats')
    strip = card.split('class="rp-plats"', 1)[1].split('</div>', 1)[0]
    assert strip.count('rp-platcell') == 1 and 'rp-platcell is-lit">PS5<' in strip


def test_the_phone_row_has_its_cover_and_names_the_contracts_own_jobs(client):
    _linked_client(client)
    _contract('row', jobs=('gunslinger', 'mage'))
    card = _board_card(client, 'row')

    assert 'class="rp-row__thumb"' in card and '<img src=' in card.split('rp-row__thumb', 1)[1].split('</span>', 1)[0]
    meta = card.split('class="rp-meta"', 1)[1].split('</div>', 1)[0]
    assert meta.count('class="rp-meta__job') == 2, 'the row should show this contract\'s own jobs, not the 25'
    assert meta.count('<span class="sr-only">') == 2, 'the row\'s jobs are not named for screen readers'
    assert 'rp-meta__plat">PS5<' in meta


def test_career_opens_the_modal_that_can_claim(client):
    _linked_client(client)
    _contract('opener')
    card = _board_card(client, 'opener')
    assert 'data-modal-url="/career/contracts/opener/modal/?claim=1"' in card


# ---- The modal (DL-07) ----

def test_careers_modal_claims_in_place(client):
    profile = _linked_client(client)
    _row(profile, _contract('modal-here'), plat='ready')
    html = client.get('/career/contracts/modal-here/modal/?claim=1').content.decode()

    foot = html.split('class="rpm__foot"', 1)[1]
    assert re.search(r'<button type="button" class="pp-cta rpm__claim" data-claim-slug="modal-here">'
                     r'Claim {:,} XP</button>'.format(PLAT_XP), foot)


def test_every_other_host_links_to_careers_ready_board(client):
    profile = _linked_client(client)
    _row(profile, _contract('modal-there'), plat='ready')
    html = client.get('/career/contracts/modal-there/modal/').content.decode()

    assert 'data-claim-slug' not in html, 'a page with no claim controller got a button that does nothing'
    # No sticky footer off Career: game and badge detail pin their own "Browse all Contracts" to that edge.
    assert 'rpm__foot' not in html, 'a second sticky footer covers the host shell\'s own'
    rewards = html.split('class="rpm__tiers"', 1)[1].split('class="rpm__section"', 1)[0]
    link = re.search(r'<a class="pp-cta pp-cta--sm rpm__claim-link" href="([^"]+)">Claim in Career</a>', rewards)
    assert link and link.group(1) == claim_board_url().replace('&', '&amp;')


def test_nothing_ready_means_no_claim_footer(client):
    profile = _linked_client(client)
    _row(profile, _contract('modal-none'), plat='banked')
    html = client.get('/career/contracts/modal-none/modal/?claim=1').content.decode()
    assert 'rpm__foot' not in html


def _tier_lines(html):
    block = html.split('class="rpm__tiers"', 1)[1].split('</ul>', 1)[0]
    return [re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', ' ', li)).strip()
            for li in (part.split('>', 1)[1] for part in block.split('<li')[1:])]


def test_the_rewards_spell_out_each_tier_and_its_state(client):
    profile = _linked_client(client)
    _row(profile, _contract('tiers-a'), plat='banked', full='ready')
    _row(profile, _contract('tiers-b'), plat=None)

    a = _tier_lines(client.get('/career/contracts/tiers-a/modal/').content.decode())
    assert a == ['Earn the platinum +{:,} XP Banked'.format(PLAT_XP),
                 'Then reach 100% +{:,} XP Ready to claim'.format(BONUS_XP)]
    b = _tier_lines(client.get('/career/contracts/tiers-b/modal/').content.decode())
    assert b[1].endswith('After the platinum'), b


def test_a_contract_without_a_platinum_shows_one_reward(client):
    profile = _linked_client(client)
    _row(profile, _contract('tiers-noplat'), full='ready', has_platinum=False)
    lines = _tier_lines(client.get('/career/contracts/tiers-noplat/modal/').content.decode())
    assert lines == ['Reach 100% +{:,} XP Ready to claim'.format(CONTRACT_XP_TOTAL)]


def test_the_preview_pitches_the_split_without_progress(client):
    _contract('tiers-preview')
    html = client.get('/career/contracts/tiers-preview/preview/').content.decode()
    lines = _tier_lines(html)
    assert lines == ['Reach 100% +{:,} XP'.format(CONTRACT_XP_TOTAL)]   # its game defines no platinum
    assert 'rpm__tier-state' not in html and 'rpm__foot' not in html


# ---- career.js: the modal's claim is handed to the board's claim path (no JS runner: source pins) ----

def _strip_comments(src):
    return re.sub(r'//[^\n]*', '', re.sub(r'/\*.*?\*/', '', src, flags=re.S))


def test_the_modal_hands_its_claim_over_once_closed():
    js = _strip_comments(career_script())
    handler = js.split(".rpm__claim[data-claim-slug]", 1)[1].split('closeEl();', 1)[0]
    assert 'closedFn = function' in handler and "'pp:contract-claim'" in handler
    hide = js.split('function hideNow()', 1)[1].split('\n    }\n', 1)[0]
    assert 'closedFn' in hide, 'nothing runs the hand-off when the dialog closes'
    listener = js.split("addEventListener('pp:contract-claim'", 1)[1].split('});', 1)[0]
    assert 'accept({ slug: slug }' in listener
    # The modal is already gone, so a claim dropped while the last one refreshes must say so.
    busy = listener.split('if (claiming)', 1)
    assert len(busy) == 2 and 'Toast.show' in busy[1].split('return;', 1)[0], 'a modal claim can vanish silently'
    assert listener.index('if (claiming)') < listener.index('accept(')


def test_a_second_close_cannot_undo_the_ceremonys_recede():
    """Escape mid-shrink after pressing the modal's Claim ran hideNow twice: the second, after the hand-off,
    stripped `pp-receded` from under the ceremony. closeEl ignores a close while one is running."""
    js = _strip_comments(career_script())
    close = js.split('function closeEl() {', 1)[1]
    assert close.lstrip().startswith('if (closing) return;'), 'closeEl no longer guards a second close'
    assert 'closing = true;' in close.split('pp-receded', 1)[0]
    hide = js.split('function hideNow()', 1)[1].split('\n    }\n', 1)[0]
    assert hide.index('closing = false;') < hide.index('closedFn'), 'the flag must clear before the hand-off runs'


# ---- Stylesheets ----

def _css(rel):
    return re.sub(r'/\*.*?\*/', '', (ROOT / rel).read_text(encoding='utf-8'), flags=re.S)


def _at_block(css, head):
    """The body of one at-rule, by brace counting (a nested rule's `}` does not end it)."""
    i = css.index(head) + len(head)
    i = css.index('{', i) + 1
    depth, j = 1, i
    while depth:
        depth += {'{': 1, '}': -1}.get(css[j], 0)
        j += 1
    return css[i:j - 1]


def test_the_phone_row_reflows_the_tile_below_640px():
    phone = _at_block(_css('static/css/components/elements.css'), '@media (max-width: 639.98px)')
    hidden = re.search(r'([^{}]*)\{\s*display:\s*none;\s*\}', phone).group(1)
    # Scoped under `.rp-row`: job detail's `.rp-jobtiles { display: grid }` comes LATER in the file, and an
    # unscoped hide lost to it on source order (the job tiles showed in every job-detail row).
    # `.rp-plats` especially: Career's TOOLBAR uses it for the platform filter, which an unscoped hide takes off phones.
    for sel in ('.rp-tile__art', '.rp-plats', '.rp-jobgrid', '.rp-jobtiles', '.rp-ring-wrap', '.rp-tiers', '.rp-contribs'):
        assert '.rp-row ' + sel in hidden, '%s is not hidden (with .rp-row specificity) in the phone row' % sel
    assert re.search(r'\.rp-row\s*\{[^}]*display:\s*grid', phone)
    assert re.search(r'\.rp-tier2-btn\s*\{[^}]*position:\s*absolute;\s*inset:\s*0', phone), 'the row no longer opens the modal'
    assert re.search(r'\.rp-status\s*\{[^}]*z-index:\s*3', phone), 'the action is under the row\'s opener'


def test_the_cover_fan_draws_a_trophy_and_a_title_bar():
    css = _css('static/css/components/series-list.css')
    mark = re.search(r'\.pp-forge__cover::after\s*\{([^}]*)\}', css)
    assert mark and 'mask-image: url("data:image/svg+xml' in mark.group(1), 'the trophy mark is gone or not drawn in CSS'
    bar = re.search(r'\.pp-forge__cover::before\s*\{([^}]*)\}', css)
    assert bar and 'linear-gradient' in bar.group(1), 'the title bar is gone'
    assert re.search(r'\.pp-forge__cover:nth-child\(2\)::after\s*\{[^}]*--color-trophy-platinum', css)
