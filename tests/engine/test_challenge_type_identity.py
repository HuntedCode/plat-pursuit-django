"""A challenge TYPE reads as itself on every surface: one glyph, one unit, its own pitch and reward line.

WHY THIS FILE EXISTS. Each surface used to say a type its own way, and the third type exposed every place
that assumed there were two: My Challenges' Calendar card printed Job Coverage's description (the template
had two branches for three types), its reward line was missing (its titles are a ladder, not a title per
completion), the hub's type filter gave the Calendar the "All" grid icon, and the Hall of Fame plaque counted
a Calendar run in "squares". One icon partial, one unit map and one rung reader now answer for all of them,
and these pins hold each surface to them.
"""
import pytest
from django.template.loader import render_to_string
from django.test import Client
from django.urls import reverse

from challenges.models import (CALENDAR_DAY_MARKERS, CHALLENGE_TYPE_AZ, CHALLENGE_TYPE_CALENDAR,
                               CHALLENGE_TYPE_JOBS, CHALLENGE_TYPE_SHORT_LABELS, CHALLENGE_TYPE_UNITS,
                               CalendarDay)
from challenges.services import calendar_fill
from challenges.services import challenge_service as svc
from challenges.services import rewards
from tests.factories import ProfileFactory, UserFactory

pytestmark = pytest.mark.django_db

#: A path fragment unique to each type's Lucide glyph, and to the generic challenge flag.
GLYPH = {
    CHALLENGE_TYPE_AZ: 'm3 15 4-8 4 8',            # case-sensitive (Aa)
    CHALLENGE_TYPE_JOBS: 'M16 20V4a2 2 0 0 0-2-2h-4',  # briefcase
    CHALLENGE_TYPE_CALENDAR: 'M3 10h18',            # calendar-days
}
FLAG = 'M4 15s1-1 4-1 5 2 8 2 4-1 4-1V3'


def _hunter(client=None):
    user = UserFactory()
    profile = ProfileFactory(user=user, user_is_premium=True)
    if client is not None:
        client.force_login(user)
    return profile


def _calendar_run(profile, months=()):
    """A Calendar run with every day of `months` filled shovelware-free, recounted (which grants rungs)."""
    run = svc.start(profile, CHALLENGE_TYPE_CALENDAR)
    ids = [d.pk for d in run.calendar_days.all() if d.month in months]
    CalendarDay.objects.filter(pk__in=ids).update(in_all=True, in_clean=True, plat_count=1)
    calendar_fill._recount_calendar(run)
    run.refresh_from_db()
    return run


def _icon(kind):
    return render_to_string('partials/icons/challenge_type.html', {'type': kind})


# ── the shared pieces ────────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize('kind', [CHALLENGE_TYPE_AZ, CHALLENGE_TYPE_JOBS, CHALLENGE_TYPE_CALENDAR])
def test_each_type_has_its_own_glyph(kind):
    svg = _icon(kind)
    assert GLYPH[kind] in svg
    assert all(GLYPH[other] not in svg for other in GLYPH if other != kind), 'two types share a glyph'
    assert FLAG not in svg


def test_an_unknown_type_falls_back_to_the_flag_rather_than_nothing():
    """A fourth type is never iconless before it gets its own glyph."""
    svg = _icon('mystery')
    assert FLAG in svg and not any(path in svg for path in GLYPH.values())


def test_every_type_counts_in_its_own_unit():
    assert CHALLENGE_TYPE_UNITS == {CHALLENGE_TYPE_AZ: 'letters', CHALLENGE_TYPE_JOBS: 'jobs',
                                    CHALLENGE_TYPE_CALENDAR: 'days'}
    profile = _hunter()
    assert svc.start(profile, CHALLENGE_TYPE_AZ).unit == 'letters'
    assert svc.start(profile, CHALLENGE_TYPE_CALENDAR).unit == 'days'


def test_a_type_missing_from_the_unit_map_says_squares():
    run = svc.start(_hunter(), CHALLENGE_TYPE_AZ)
    run.challenge_type = 'mystery'
    assert run.unit == 'squares'


# ── the Calendar's next rung ─────────────────────────────────────────────────────────────────────────

def test_a_hunter_with_nothing_is_playing_for_the_first_rung():
    assert rewards.next_calendar_rung(_hunter()) == (50, 'Calendar Marker')


def test_the_next_rung_skips_what_the_run_has_passed():
    """62 days in: the 50 rung is behind the run (and granted), so the next is Keeper at 100."""
    profile = _hunter()
    _calendar_run(profile, months=[1, 3])
    assert rewards.next_calendar_rung(profile, done=62) == (100, 'Calendar Keeper')


def test_a_rung_the_run_has_passed_is_not_next_even_when_it_was_never_granted():
    """PASSED AND HELD ARE DIFFERENT QUESTIONS. A grant can fail -- `_ensure_title` declines a name another
    system owns and logs the collision -- so a hunter can be at 62 days without holding Calendar Marker. The
    line must not tell them they are playing for a rung already behind them."""
    assert rewards.next_calendar_rung(_hunter(), done=62) == (100, 'Calendar Keeper')


def test_the_next_rung_skips_titles_the_hunter_already_holds():
    """THE TITLES BELONG TO THE HUNTER, NOT THE RUN. A hunter who reached 120 days on an earlier run holds
    Marker and Keeper; a fresh count of 30 would grant neither again, so the line must not promise them."""
    profile = _hunter()
    _calendar_run(profile, months=[1, 2, 3, 4])       # 120 days: Marker and Keeper granted
    assert rewards.next_calendar_rung(profile, done=30) == (200, 'Calendar Chronicler')


def test_a_hunter_holding_the_whole_ladder_has_no_next_rung():
    profile = _hunter()
    _calendar_run(profile, months=range(1, 13))        # 365 days: every rung granted
    assert rewards.next_calendar_rung(profile, done=0) is None
    assert CALENDAR_DAY_MARKERS[-1] == 365


def test_the_next_rung_is_one_query():
    from django.db import connection
    from django.test.utils import CaptureQueriesContext
    profile = _hunter()
    with CaptureQueriesContext(connection) as queries:
        rewards.next_calendar_rung(profile, done=10)
    assert len(queries) == 1


# ── My Challenges ────────────────────────────────────────────────────────────────────────────────────

def _my_challenges(client):
    resp = client.get(reverse('my_challenges'))
    assert resp.status_code == 200
    return resp, resp.content.decode()


def _card_html(body, label):
    """One type card's markup, from its own `<section>` (so the icon tile, which sits before the heading, is
    inside the slice) to the next card or the end of the page."""
    heading = body.index('>%s</h2>' % label)
    start = body.rindex('<section class="card', 0, heading)
    end = body.find('<section class="card', heading)
    return body[start:end if end != -1 else len(body)]


def test_every_type_card_wears_its_glyph_and_its_own_pitch():
    """The Calendar printed Job Coverage's description when the template had two branches for three types.
    Each card now carries its own pitch, and none carries another's."""
    client = Client()
    _hunter(client)
    _, body = _my_challenges(client)
    az = _card_html(body, 'A-Z')
    jobs = _card_html(body, 'Job Coverage')
    calendar = _card_html(body, 'Plat Calendar')
    assert 'One game for every letter, A to Z.' in az
    assert 'One game for every job, Freelancer included.' in jobs
    assert 'A shovelware-free platinum on every day of the year.' in calendar
    assert 'every job' not in calendar, 'the Calendar card printed Job Coverage copy again'
    # EACH CARD ITS OWN GLYPH, checked per card: counting tiles would pass with three identical glyphs.
    for card, kind in ((az, CHALLENGE_TYPE_AZ), (jobs, CHALLENGE_TYPE_JOBS), (calendar, CHALLENGE_TYPE_CALENDAR)):
        assert card.count('class="pp-ctype') == 1
        assert GLYPH[kind] in card, kind
        assert all(GLYPH[other] not in card for other in GLYPH if other != kind), kind


def test_progress_counts_in_each_types_unit():
    client = Client()
    profile = _hunter(client)
    svc.start(profile, CHALLENGE_TYPE_AZ)
    _calendar_run(profile, months=[3])
    _, body = _my_challenges(client)
    assert '0 of 26 letters done' in body
    assert '31 of 365 days filled' in body
    assert 'squares done' not in body


def test_the_calendar_card_names_its_next_rung():
    """Before this the Calendar card had no reward line at all."""
    client = Client()
    profile = _hunter(client)
    _calendar_run(profile, months=[1, 3])              # 62 days, Marker held
    _, body = _my_challenges(client)
    calendar = _card_html(body, 'Plat Calendar')
    assert 'Calendar Keeper' in calendar and 'at 100 days' in calendar


def test_a_calendar_card_with_the_whole_ladder_held_shows_no_reward_line():
    client = Client()
    profile = _hunter(client)
    _calendar_run(profile, months=range(1, 13))
    _, body = _my_challenges(client)
    calendar = _card_html(body, 'Plat Calendar')
    assert 'pp-cwr' not in calendar, 'the card promises a title the hunter already holds'


def test_three_cards_three_across_and_the_third_spans_at_md():
    """THE THIRD CARD SPANS THE TWO-COLUMN ROW AT `md:`, so no type sits alone in half a row."""
    client = Client()
    _hunter(client)
    _, body = _my_challenges(client)
    assert 'grid-cols-1 md:grid-cols-2 lg:grid-cols-3' in body
    assert body.count('md:col-span-2 lg:col-span-1') == 1


def test_the_subtitle_no_longer_names_two_types():
    client = Client()
    _hunter(client)
    _, body = _my_challenges(client)
    assert 'twenty-five jobs' not in body
    assert 'One run of each at a time, start to finish.' in body


def test_a_finished_row_wears_its_types_glyph():
    client = Client()
    profile = _hunter(client)
    _calendar_run(profile, months=range(1, 13))        # a finished Calendar run
    _, body = _my_challenges(client)
    finished = body[body.index('<ul class="mt-2 divide-y'):]
    assert GLYPH[CHALLENGE_TYPE_CALENDAR] in finished.split('</li>')[0]


# ── the run page, the hub and the Hall of Fame ───────────────────────────────────────────────────────

def test_the_run_page_header_wears_the_types_glyph_not_the_flag():
    client = Client()
    run = svc.start(_hunter(client), CHALLENGE_TYPE_JOBS)
    body = client.get(reverse('challenge_detail', args=[run.id])).content.decode()
    # SCOPED TO THE HEADER CARD, not "everything before </h1>": that slice includes the site nav, where a
    # briefcase icon would mask this regression.
    h1 = body.index('</h1>')
    header = body[body.rindex('<section class="card', 0, h1):h1]
    assert GLYPH[CHALLENGE_TYPE_JOBS] in header
    assert FLAG not in header, 'the run header still wears the generic flag'


def test_a_hub_card_wears_its_glyph_and_counts_in_its_unit():
    run = _calendar_run(_hunter(), months=[3])
    body = Client().get(reverse('challenges')).content.decode()
    card = body[body.index('href="%s"' % reverse('challenge_detail', args=[run.pk])):]
    card = card[:card.index('</a>')]
    assert GLYPH[CHALLENGE_TYPE_CALENDAR] in card
    assert '>days</span>' in card and '>squares</span>' not in card


def test_the_hub_filter_gives_each_type_its_own_glyph_and_keeps_the_grid_for_all():
    """The filter's `else` branch drew the four-square "All" grid for the Calendar too."""
    body = Client().get(reverse('challenges')).content.decode()
    switch = body[body.index('aria-label="Challenge type"'):]
    switch = switch[:switch.index('</div>')]
    chips = switch.split('<label class="pp-switch__chip">')[1:]
    assert len(chips) == 4
    for chip, kind in zip(chips, (CHALLENGE_TYPE_AZ, CHALLENGE_TYPE_JOBS, CHALLENGE_TYPE_CALENDAR)):
        assert GLYPH[kind] in chip, kind
    assert 'value=""' in chips[3] and 'x="14" y="14"' in chips[3], 'All lost its grid'


def test_the_hall_of_fame_never_counts_a_calendar_run_in_squares():
    """It leads with its PLATINUMS now ("365/365" is true of every finished Calendar run), and the type line
    wears the glyph."""
    run = _calendar_run(_hunter(), months=range(1, 13))
    assert run.is_complete
    body = Client().get(reverse('challenges_hall_of_fame')).content.decode()
    assert '>squares</span>' not in body
    assert '>platinums</span>' in body
    record = body[body.index('class="pp-chero__type"'):]
    record = record[:record.index('</span>')]
    assert GLYPH[CHALLENGE_TYPE_CALENDAR] in record, 'the plaque names the type without its glyph'


# ── short labels where the page already says "Challenges" ────────────────────────────────────────────

def test_every_type_has_a_short_label():
    """The views and the model both read `short_label_for`, which falls back to the full label -- but a type
    should not ship relying on that, so the map covers every type."""
    from challenges.models import CHALLENGE_TYPES, short_label_for
    assert set(CHALLENGE_TYPE_SHORT_LABELS) == CHALLENGE_TYPES
    assert short_label_for('mystery') == 'mystery'


def test_the_short_labels():
    """Where the page already says "Challenges", the type's own "Challenge" repeated it, and "Job Coverage
    Challenge" was the one label long enough to wrap (owner, 2026-10-08)."""
    assert CHALLENGE_TYPE_SHORT_LABELS == {CHALLENGE_TYPE_AZ: 'A-Z', CHALLENGE_TYPE_JOBS: 'Job Coverage',
                                          CHALLENGE_TYPE_CALENDAR: 'Plat Calendar'}
    run = svc.start(_hunter(), CHALLENGE_TYPE_JOBS)
    assert run.short_label == 'Job Coverage'
    run.challenge_type = 'mystery'
    assert run.short_label == 'mystery', 'a type missing from the map falls back to its display name'


def test_my_challenges_cards_use_the_short_labels():
    client = Client()
    _hunter(client)
    _, body = _my_challenges(client)
    for label in ('A-Z', 'Job Coverage', 'Plat Calendar'):
        assert '>%s</h2>' % label in body, label
    assert '>A-Z Challenge</h2>' not in body and '>Job Coverage Challenge</h2>' not in body


def test_the_hub_filter_and_cards_use_the_short_labels():
    run = svc.start(_hunter(), CHALLENGE_TYPE_JOBS)
    body = Client().get(reverse('challenges')).content.decode()
    switch = body[body.index('aria-label="Challenge type"'):]
    switch = switch[:switch.index('</div>')]
    assert 'Job Coverage Challenge' not in switch and 'Job Coverage' in switch
    card = body[body.index('href="%s"' % reverse('challenge_detail', args=[run.pk])):]
    chip = card[card.index('pp-crun__type'):]
    assert chip[:chip.index('</span>')].endswith('Job Coverage'), 'the hub chip lost its short label'


def test_the_full_name_stays_where_a_type_stands_alone():
    """The run's name, its page title and the share card keep the full label: there is no "Challenges"
    heading around them to carry the word."""
    from challenges.services import share_card
    from django.template.loader import render_to_string
    client = Client()
    run = svc.start(_hunter(client), CHALLENGE_TYPE_JOBS)
    assert run.name == 'Job Coverage Challenge'
    body = client.get(reverse('challenge_detail', args=[run.id])).content.decode()
    assert '>Job Coverage Challenge</h1>' in body
    html = render_to_string(share_card.CARD_TEMPLATE, share_card.build_card_context(run))
    assert 'Job Coverage Challenge' in html


# ── the spanning card's tablet layout ──────────────────────────────────────────────────────────────────

def test_only_the_spanning_card_lays_out_side_by_side():
    """`pp-ccard--wide` rides the LAST card only -- the one that spans both columns at `md:` -- and every card
    carries the two blocks it rearranges, so the layout never depends on a card's state."""
    client = Client()
    _hunter(client)
    _, body = _my_challenges(client)
    assert body.count('pp-ccard--wide') == 1
    assert 'md:col-span-2 lg:col-span-1 pp-ccard--wide' in body
    assert body.count('class="pp-ccard__main"') == 3 and body.count('class="pp-ccard__side"') == 3
    calendar = _card_html(body, 'Plat Calendar')
    main = calendar[calendar.index('class="pp-ccard__main"'):calendar.index('class="pp-ccard__side"')]
    side = calendar[calendar.index('class="pp-ccard__side"') + len('class="pp-ccard__side">'):]
    # INSIDE, not merely after: an emptied side block followed by the reward would pass an ordering check.
    assert not side.lstrip().startswith('</div>'), 'the side block is empty'
    assert 'pp-cwr' not in main and 'pt-3 mt-auto' not in main, 'the reward or actions moved into the main block'
    assert side.index('pp-cwr') < side.index('pt-3 mt-auto'), 'the reward and actions left the side block'


def test_the_side_by_side_rule_is_scoped_to_tablet_width():
    """At `lg:` the three cards share one row again, so the grid must stop there; on a phone it never starts."""
    import re
    from pathlib import Path
    css = (Path(__file__).resolve().parents[2] / 'static' / 'css' / 'components' / 'challenges.css'
           ).read_text(encoding='utf-8')
    # THE BLOCK THAT CARRIES THIS RULE, not the first with this query: the Hall of Fame's Calendar hero has
    # a tablet-only block of its own.
    blocks = re.findall(r'@media \(min-width: 768px\) and \(max-width: 1023\.98px\) \{.*?\n\}', css, re.S)
    found = [b for b in blocks if '.pp-ccard--wide > .card-body' in b]
    assert len(found) == 1, 'the tablet-only media query is gone'
    assert 'display: grid' in found[0]
    outside = css.replace(found[0], '')
    assert '.pp-ccard--wide > .card-body' not in outside, 'the side-by-side grid leaked outside tablet width'


def test_the_hall_of_fame_calendar_plaque_shows_its_busiest_day_and_years():
    """The two lines that replace "365/365 days" on a finished Calendar: the busiest day and the years the
    calendar took to fill. Rendered, not just computed -- a template that drops them passes every data test."""
    import datetime
    run = _calendar_run(_hunter(), months=range(1, 13))
    CalendarDay.objects.filter(challenge=run).update(earned_on=datetime.date(2016, 5, 5))
    CalendarDay.objects.filter(challenge=run, month=3, day=3).update(
        plat_count=7, earned_on=datetime.date(2023, 3, 3))
    body = Client().get(reverse('challenges_hall_of_fame')).content.decode()
    plaque = body[body.index('Busiest day'):]
    plaque = plaque[:plaque.index('pp-chero__record')]
    assert '7' in plaque and 'platinums on 3 Mar' in plaque
    assert 'Earned' in plaque and '2016 to 2023' in plaque


def test_the_card_wrappers_keep_the_card_bodys_gap():
    """Wrapping a card's children in `__main`/`__side` took them out of DaisyUI's `.card-body` flex gap, so every
    section lost 8px of space at every width. Each wrapper carries the 0.5rem gap itself."""
    import re
    from pathlib import Path
    css = re.sub(r'/\*.*?\*/', '', (Path(__file__).resolve().parents[2] / 'static' / 'css' / 'components'
                                    / 'challenges.css').read_text(encoding='utf-8'), flags=re.S)
    for wrapper in ('.pp-ccard__main {', '.pp-ccard__side {'):
        rule = css[css.index(wrapper):]
        rule = rule[:rule.index('}')]
        assert 'flex-direction: column' in rule and 'gap: 0.5rem' in rule, wrapper
