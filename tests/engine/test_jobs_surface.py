"""`/jobs/` (the public catalogue) + `/jobs/<slug>/` (what a job is, its contracts, its board).

Jobs get a PUBLIC catalogue in the Browse hub, not under Leaderboards: a catalogue of jobs is a browse
surface. Its relationship to Career's Dossier is the split this codebase already settled for Collection vs
Browse Badges -- "SCOPE, not pagination". Career shows YOUR standing across the 24 jobs; this shows what
the jobs are.

The load-bearing constraint is that it works SIGNED OUT. A job page an anonymous visitor cannot read
defeats the discovery it exists for, and the board being identical for everyone is what keeps it
cacheable.
"""
import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from trophies.models import Contract, EarnedContract, Job, ProfileJobXP
from tests.factories import ProfileFactory

pytestmark = pytest.mark.django_db


def _job(slug, name, discipline='combat', **kw):
    return Job.objects.create(slug=slug, name=name, discipline=discipline, **kw)


def _xp(job, name, xp, level=1):
    """Give a hunter XP in a job. Counts are read LIVE off `ProfileJobXP` -- the `Job.entrants` denorm
    went with the Job Boards directory, which was the only thing that needed a count it could gate and
    sort on before pagination."""
    p = ProfileFactory(display_psn_username=name)
    ProfileJobXP.objects.create(profile=p, job=job, total_xp=xp, level=level, is_linked=True)
    return p


_IGDB = [9000]


def _contract(name, jobs, *, live=True):
    _IGDB[0] += 1
    c = Contract.objects.create(
        name=name, slug=name.lower().replace(' ', '-'), igdb_id=_IGDB[0], is_live=live)
    c.jobs.set(jobs)
    return c


# ------------------------------------------------------------------ the catalogue ------------------------

def test_the_jobs_catalogue_is_public(client):
    """An anonymous visitor is exactly who this page is for -- it is the readable surface of a system they
    have not signed up for yet."""
    _job('archivist', 'Archivist')
    resp = client.get(reverse('jobs_browse'))
    assert resp.status_code == 200 and 'Archivist' in resp.content.decode()


def test_the_catalogue_counts_are_grouped_not_per_card(client):
    """The contracts figure is an ANNOTATION on the queryset, so the page costs the same number of queries
    whatever the catalogue holds. A count per card is the shape that looks fine at 25 jobs and is wrong on
    principle -- and it became load-bearing rather than merely tidy when `contracts` became a SORT, which
    has to happen in the database.

    (It was two grouped reads and two figures until 2026-08; the hunter count went, and the contracts
    count moved from a second grouped query into the main one, so this page now runs FEWER queries than it
    did before it gained a sort.)
    """
    jobs = [_job(f'j{i}', f'Job {i}') for i in range(3)]
    for j in jobs:
        _xp(j, f'H{j.slug}', 100)
        _contract(f'C {j.slug}', [j])
    with CaptureQueriesContext(connection) as small:
        client.get(reverse('jobs_browse'))

    more = [_job(f'k{i}', f'Job K{i}') for i in range(12)]
    for j in more:
        _xp(j, f'H{j.slug}', 100)
        _contract(f'C {j.slug}', [j])
    with CaptureQueriesContext(connection) as large:
        client.get(reverse('jobs_browse'))

    assert len(large.captured_queries) == len(small.captured_queries), (
        f'{len(small.captured_queries)} queries for 3 jobs but {len(large.captured_queries)} for 15'
    )


# ------------------------------------------------------------------ ordering ----------------------------

def _solo():
    """Empty the catalogue so a test can assert on EXACT contents or order.

    `0247_seed_jobs` is a data migration, so the 25 real jobs exist in the test database before any
    factory runs. Tests that only check a property (is it public, does the query count hold) are happy
    alongside them; anything asserting a full ordering has to start from nothing or it is asserting
    against the seed.
    """
    Job.objects.all().delete()


def _seeded_catalogue():
    """One job per discipline, created in an order that matches NEITHER answer.

    Built deliberately out of sequence so the assertions cannot pass by accident: alphabetical by name,
    alphabetical by discipline slug and canonical radar order are three different results here.
    """
    return [
        _job('mind-j', 'Zeta', discipline='mind', display_order=0),
        _job('finesse-j', 'Alpha', discipline='finesse', display_order=0),
        _job('combat-j', 'Mu', discipline='combat', display_order=0),
        _job('heart-j', 'Beta', discipline='heart', display_order=0),
        _job('exploration-j', 'Nu', discipline='exploration', display_order=0),
    ]


def _slugs(resp):
    """Job slugs in RENDERED order, read off the tiles' hrefs -- the order a reader actually sees, rather
    than the queryset's, which a template could reorder without anyone noticing."""
    import re
    return re.findall(r'/jobs/([a-z0-9-]+)/"', resp.content.decode())


def test_the_default_order_is_the_CANONICAL_discipline_sequence(client):
    """Career's arrangement, which is NOT what the column gives.

    `discipline` holds slugs, so ordering on it sorts alphabetically -- combat, exploration, finesse,
    heart, mind -- while the canonical radar order is combat, exploration, MIND, HEART, FINESSE. The two
    agree for the first two disciplines and then diverge, which is precisely why this needs pinning: a
    regression to `order_by('discipline')` looks right until you reach the third row.
    """
    _solo()
    _seeded_catalogue()
    assert _slugs(client.get(reverse('jobs_browse'))) == [
        'combat-j', 'exploration-j', 'mind-j', 'heart-j', 'finesse-j']


def test_display_order_breaks_ties_WITHIN_a_discipline_only(client):
    """`display_order` is 0-4 within a discipline, not global, so it can only ever be the second key.
    Sorting on it first would interleave all five disciplines (every slot 0, then every slot 1)."""
    _solo()
    _job('c1', 'Second', discipline='combat', display_order=1)
    _job('c0', 'First', discipline='combat', display_order=0)
    _job('m0', 'Also First', discipline='mind', display_order=0)

    assert _slugs(client.get(reverse('jobs_browse'))) == ['c0', 'c1', 'm0']


def test_sorting_by_contracts_counts_only_LIVE_ones(client):
    """The figure on the tile and the key it sorts by are the same annotation, so they cannot disagree --
    which is the whole reason the count moved into the queryset. A draft contract is not something a
    reader can act on, so it must not inflate the number that says a job is worth pursuing."""
    _solo()
    a, b, c = _job('a', 'A'), _job('b', 'B'), _job('c', 'C')
    _contract('One', [b])
    _contract('Two', [b])
    _contract('Draft', [c], live=False)
    _contract('Live', [a])

    resp = client.get(reverse('jobs_browse'), {'sort': 'contracts'})
    assert _slugs(resp)[:2] == ['b', 'a'], 'not ordered by live contract count'
    assert _slugs(resp)[2] == 'c'
    counts = {j.slug: j.contract_count for j in resp.context['jobs']}
    assert counts == {'b': 2, 'a': 1, 'c': 0}, 'a draft contract was counted'


def test_a_contract_feeding_several_jobs_is_not_counted_twice(client):
    """`distinct=True` on the annotation. A Contract's XP splits across every job it names, so the join
    multiplies without it -- and the failure is invisible on single-job contracts, which is most of them."""
    _solo()
    a, b = _job('a', 'A'), _job('b', 'B')
    _contract('Shared', [a, b])
    _contract('Also Shared', [a, b])

    counts = {j.slug: j.contract_count for j in client.get(reverse('jobs_browse')).context['jobs']}
    assert counts == {'a': 2, 'b': 2}


def test_sorting_alphabetically_ignores_discipline(client):
    _solo()
    _seeded_catalogue()
    assert _slugs(client.get(reverse('jobs_browse'), {'sort': 'alpha'})) == [
        'finesse-j', 'heart-j', 'combat-j', 'exploration-j', 'mind-j']


def test_an_unknown_sort_falls_back_to_the_default(client):
    """A junk `?sort=` is a URL somebody typed or an old bookmark, not an error worth a 500 -- and falling
    back SILENTLY is safe only because the select renders the effective sort, so the control never shows
    an ordering the wall does not have."""
    _solo()
    _seeded_catalogue()
    resp = client.get(reverse('jobs_browse'), {'sort': 'nonsense'})
    assert resp.context['sort'] == 'discipline'
    assert _slugs(resp)[0] == 'combat-j'


# ------------------------------------------------------------------ filtering ---------------------------

def test_the_discipline_chip_filters_the_wall(client):
    _solo()
    _job('c', 'Combatant', discipline='combat')
    _job('m', 'Thinker', discipline='mind')

    resp = client.get(reverse('jobs_browse'), {'discipline': 'mind'})
    assert _slugs(resp) == ['m']
    assert resp.context['selected_discipline'] == 'mind'


def test_an_unknown_discipline_is_ignored_rather_than_emptying_the_wall(client):
    """The filter comes off the querystring, so it can hold anything. Applying an unrecognised value
    verbatim would return nothing and read as "there are no jobs" -- a plausible, wrong answer."""
    _solo()
    _job('c', 'Combatant', discipline='combat')
    assert _slugs(client.get(reverse('jobs_browse'), {'discipline': 'wizardry'})) == ['c']


def test_search_matches_name_and_description(client):
    """Mid-word, deliberately: the site-wide PREFIX rule exists to keep search off a scan of a large
    table, and this table is 25 curated rows."""
    _solo()
    _job('a', 'Cartographer', description='You map the world.')
    _job('b', 'Slayer', description='You fight everything.')

    assert _slugs(client.get(reverse('jobs_browse'), {'q': 'ograph'})) == ['a']
    assert _slugs(client.get(reverse('jobs_browse'), {'q': 'fight'})) == ['b']


def test_search_and_discipline_compose(client):
    """Two filters that AND. Each dropping the other on change was a real bug on the boards' filter form;
    the shared browse form sends the whole form, which is what stops it here."""
    _solo()
    _job('a', 'Ranger', discipline='combat')
    _job('b', 'Ranger Two', discipline='mind')

    resp = client.get(reverse('jobs_browse'), {'q': 'Ranger', 'discipline': 'mind'})
    assert _slugs(resp) == ['b']


def test_the_empty_state_says_WHICH_filter_emptied_it(client):
    """"No jobs match" over an empty wall is true and useless. The reader needs to know which control to
    undo, and the two cases read differently."""
    _solo()
    _job('a', 'Ranger', discipline='combat')

    searched = client.get(reverse('jobs_browse'), {'q': 'zzz'}).content.decode()
    assert 'zzz' in searched, 'the empty state does not name the search that emptied it'

    filtered = client.get(reverse('jobs_browse'), {'discipline': 'mind'}).content.decode()
    assert 'No jobs in this discipline yet' in filtered


# ------------------------------------------------------------------ the HTMX swap -----------------------

def test_an_htmx_request_returns_the_WALL_ALONE(client):
    """The swap target is `#browse-results`, so a response carrying the page chrome would nest a second
    toolbar inside the results on every filter -- and keep nesting."""
    _job('a', 'Ranger')

    full = client.get(reverse('jobs_browse')).content.decode()
    partial = client.get(reverse('jobs_browse'), HTTP_HX_REQUEST='true').content.decode()

    assert 'jobs-wall' in partial and 'Ranger' in partial
    assert 'data-browse-form' not in partial, 'the partial carries the toolbar'
    assert 'id="browse-results"' not in partial, 'the partial re-renders its own swap container'
    assert '<h1' not in partial
    # ...and the full page has all of it, so the two are not both stripped.
    assert 'data-browse-form' in full and 'id="browse-results"' in full


def test_the_page_wires_the_shared_browse_controller(client):
    """`/jobs/` was the last browse page still doing a full-page `form.submit()` on a debounce, which lost
    the reader's scroll position and the focus of the field they were typing in. Pinned by MARKUP because
    the behaviour lives in a shared script this test cannot execute."""
    _job('a', 'Ranger')
    body = client.get(reverse('jobs_browse')).content.decode()

    assert 'browse-filters.js' in body
    for hook in ('data-browse-form', 'data-live-search', 'hx-target="#browse-results"'):
        assert hook in body, f'missing {hook}'
    # The search affordances the wrapper advertised for months with nothing wired to them.
    assert 'data-search-clear' in body and 'data-search-wrap' in body


def test_the_catalogue_does_not_show_a_hunter_count(client):
    """Dropped 2026-08 with the batch counter behind it. How many people have touched a job says more
    about which games happen to be popular than about the job, and it was the one figure on the tile a
    reader could do nothing with. Pinned so it does not drift back in as "context"."""
    _solo()
    job = _job('a', 'Ranger')
    _xp(job, 'SomeHunter', 500)

    # The WALL alone, not the page: `base.html`'s own meta description says "trophy hunters", so a
    # whole-page substring check fails on correct code -- the exact false positive this file's sibling
    # tests keep tripping over.
    wall = client.get(reverse('jobs_browse'), HTTP_HX_REQUEST='true').content.decode()
    assert 'hunter' not in wall.lower(), 'the catalogue is advertising a hunter count again'


# ------------------------------------------------------------------ job detail ---------------------------

def test_job_detail_is_public_and_both_tabs_render_signed_out(client):
    """The anon/authed split is PER TAB, not per page. Ranks is identical for everyone; Contracts shows
    the games and what they pay without a viewer's state."""
    job = _job('archivist', 'Archivist')
    _xp(job, 'TopHunter', 900, level=9)
    _contract('Some Contract', [job])

    ranks = client.get(reverse('job_ranks_panel', args=['archivist'])).content.decode()
    assert 'TopHunter' in ranks, 'the board is not visible signed out'

    contracts = client.get(reverse('job_detail', args=['archivist']), {'tab': 'contracts'}).content.decode()
    assert 'Some Contract' in contracts
    assert 'Ready to claim' not in contracts, 'viewer state leaked into the anonymous view'


def test_a_linked_viewer_sees_their_own_state_on_each_contract(client):
    job = _job('archivist', 'Archivist')
    contract = _contract('Mine', [job])
    profile = ProfileFactory(is_linked=True)
    EarnedContract.objects.create(profile=profile, contract=contract)
    client.force_login(profile.user)

    body = client.get(reverse('job_detail', args=['archivist']), {'tab': 'contracts'}).content.decode()
    assert 'In progress' in body or 'Ready to claim' in body or 'Banked' in body


def test_the_contract_state_lookup_is_batched_for_the_page(client):
    """The trap this page's own comment names: a lookup per row looks fine at 24 contracts and is not at
    200, and paging bounds the rows rendered but never the queries per row."""
    job = _job('archivist', 'Archivist')
    profile = ProfileFactory(is_linked=True)
    client.force_login(profile.user)

    for i in range(2):
        EarnedContract.objects.create(profile=profile, contract=_contract(f'Few {i}', [job]))
    with CaptureQueriesContext(connection) as small:
        client.get(reverse('job_detail', args=['archivist']), {'tab': 'contracts'})

    for i in range(12):
        EarnedContract.objects.create(profile=profile, contract=_contract(f'Many {i}', [job]))
    with CaptureQueriesContext(connection) as large:
        client.get(reverse('job_detail', args=['archivist']), {'tab': 'contracts'})

    assert len(large.captured_queries) == len(small.captured_queries), (
        f'{len(small.captured_queries)} queries for 2 contracts but {len(large.captured_queries)} for 14'
    )


def test_the_job_board_ranks_by_xp(client):
    job = _job('archivist', 'Archivist')
    _xp(job, 'Lower', 100)
    _xp(job, 'Higher', 900)
    body = client.get(reverse('job_ranks_panel', args=['archivist'])).content.decode()
    assert body.index('Higher') < body.index('Lower')


def test_a_job_with_no_xp_says_so_rather_than_rendering_an_empty_wall(client):
    _job('quiet', 'Quiet Job')
    body = client.get(reverse('job_ranks_panel', args=['quiet'])).content.decode()
    assert 'Nobody has banked XP here yet' in body


def test_the_page_never_builds_the_board(client):
    """The tabs switch IN PLACE now, so both panels are on the page -- which would normally mean paying
    for both. It does not, because only the CHEAP one is server-rendered: the board is fetched from
    `job_ranks_panel` when its tab is opened.

    That preserves a saving the `?tab=` version got for free from a full page reload, and it is the same
    trade game detail makes with its leaderboard panel.
    """
    job = _job('archivist', 'Archivist')
    _xp(job, 'Someone', 500)
    for i in range(3):
        _contract(f'Con {i}', [job])

    with CaptureQueriesContext(connection) as page:
        client.get(reverse('job_detail', args=['archivist']))
    sql = ' '.join(q['sql'] for q in page.captured_queries)

    # The header tally and the viewer's rank ARE on the page -- they sit above the switcher and show on
    # both tabs -- so ProfileJobXP is expected. What must NOT be there is the board's own row read.
    from trophies.views.career_views import JobRanksPanelView
    # Derived from PAGE_SIZE rather than hardcoded, or a literal would go vacuously true (never matching,
    # always passing) the day the window size changed. There is no `+ 1` any more: the look-ahead row
    # existed to answer "is there a next page", and a virtualized board asks a COUNT instead.
    board_read = f'LIMIT {JobRanksPanelView.PAGE_SIZE}'

    assert 'trophies_profilejobxp' in sql, 'the header hunter tally is missing'
    assert board_read not in sql, 'the page built the board rows despite shipping an empty Ranks panel'

    # And the panel, asked for directly, does build them.
    with CaptureQueriesContext(connection) as panel:
        client.get(reverse('job_ranks_panel', args=['archivist']))
    assert board_read in ' '.join(q['sql'] for q in panel.captured_queries)

def test_a_signed_in_hunter_with_no_XP_is_TOLD_they_are_not_ranked(client):
    """`{% if my_rank %}` alone made the standing block simply absent for an unranked hunter, which reads
    as a missing feature rather than as the answer to the question they opened the tab with."""
    job = _job('archivist', 'Archivist')
    _xp(job, 'Someone', 500)
    viewer = ProfileFactory(display_psn_username='Newcomer')
    client.force_login(viewer.user)

    body = client.get(reverse('job_detail', args=['archivist']), {'tab': 'ranks'}).content.decode()
    assert 'Your standing' in body and 'Not ranked yet' in body

    # ...and a hunter who IS ranked gets the number, not the placeholder.
    ProfileJobXP.objects.create(profile=viewer, job=job, total_xp=900, level=2, is_linked=True)
    body = client.get(reverse('job_detail', args=['archivist']), {'tab': 'ranks'}).content.decode()
    assert '#1' in body and 'Not ranked yet' not in body


def test_a_signed_OUT_visitor_gets_no_standing_block_at_all(client):
    """The distinction the flag exists for: nothing to say, rather than "not ranked"."""
    job = _job('archivist', 'Archivist')
    _xp(job, 'Someone', 500)
    body = client.get(reverse('job_detail', args=['archivist']), {'tab': 'ranks'}).content.decode()
    assert 'Your standing' not in body and 'Not ranked yet' not in body


def test_the_board_serves_windows_past_the_first(client):
    """A job board is the only per-entity board with no fuller surface to hand off to, so a hunter ranked
    #60 has to be reachable from this panel or not at all. It used to be a prev/next pager, which reached
    them in three clicks; it is now a virtualized wall, which reaches them by scrolling or by the rank
    box, and the panel serves each window as bare rows.
    """
    job = _job('archivist', 'Archivist')
    for i in range(60):
        _xp(job, f'Hunter{i:02d}', 1000 - i)      # descending, so rank order == creation order

    panel = client.get(reverse('job_ranks_panel', args=['archivist']))
    assert len(panel.context['rows']) == 50, 'the first window is not a full page'
    assert panel.context['rows'][0]['rank'] == 1
    assert panel.context['total'] == 60, 'the spacer would be sized to the window, not the board'

    # The second window: ROWS ONLY, no chrome -- the virtualizer splices these into its own spacer, so a
    # wrapper here would be parsed and discarded.
    window = client.get(reverse('job_ranks_panel', args=['archivist']), {'range': 51, 'count': 50})
    body = window.content.decode()
    assert body.count('<li class="lb-row') == 10
    assert '<ol' not in body and 'lb-jumpbar' not in body, 'the window carried chrome'
    # `page()` numbers by SLOT, so a window starting at 51 must continue the count rather than restart.
    assert 'Hunter50' in body and 'Hunter00' not in body
    assert len(window.context['entries']) == 10
    assert window.context['entries'][0]['rank'] == 51, 'the second window restarted the ranks at #1'


def test_a_junk_range_falls_back_to_the_first_window(client):
    job = _job('archivist', 'Archivist')
    _xp(job, 'Someone', 500)
    for raw in ('0', '-3', 'abc', ''):
        resp = client.get(reverse('job_ranks_panel', args=['archivist']), {'range': raw})
        assert resp.status_code == 200, f'range={raw!r} was not handled'
        assert resp.context['entries'][0]['rank'] == 1, f'range={raw!r} did not fall back'


def test_the_standing_chip_is_on_BOTH_tabs(client):
    """It lives in the page header, ABOVE the tab switcher, so it is chrome rather than tab content.

    Computing it inside the Ranks branch made it vanish from `?tab=contracts` -- which is the default, and
    therefore where a signed-in hunter lands. The chip only appeared once you clicked through to Ranks,
    the one tab that already shows you the board.
    """
    job = _job('archivist', 'Archivist')
    viewer = ProfileFactory(display_psn_username='Ranked')
    ProfileJobXP.objects.create(profile=viewer, job=job, total_xp=900, level=2, is_linked=True)
    client.force_login(viewer.user)

    for tab in ('contracts', 'ranks'):
        body = client.get(reverse('job_detail', args=['archivist']), {'tab': tab}).content.decode()
        assert 'Your standing' in body, f'the standing chip is missing on ?tab={tab}'
        assert '#1' in body, f'the rank is missing on ?tab={tab}'


def test_an_unverified_account_is_not_promised_a_board_it_cannot_enter(client):
    """Every board population is gated on `is_linked` (`badge_leaderboards._linked`), so telling an
    unlinked account "Not ranked yet" offers a board that will never have them on it. Game detail already
    resolved its viewer this way; the other two panels said "signed in" and meant it."""
    _job('archivist', 'Archivist')
    unlinked = ProfileFactory(is_linked=False, display_psn_username='Unverified')
    client.force_login(unlinked.user)

    body = client.get(reverse('job_detail', args=['archivist']), {'tab': 'ranks'}).content.decode()
    assert 'Your standing' not in body and 'Not ranked yet' not in body


def test_a_crafted_window_cannot_ask_for_the_whole_board(client):
    """`range` is an OFFSET straight into the board and `count` a LIMIT, and this is a PUBLIC fragment.
    Both are clamped by the shared parser, so this asserts the panel goes THROUGH it.

    Built on a board LARGER than `MAX_COUNT` and a `range` INSIDE it, so the clamps are observed rather
    than assumed -- the first version asked past the end of a 3-row board, where an empty response is
    what you get with or without a parser.
    """
    from trophies.views.board_helpers import MAX_COUNT, MAX_START

    job = _job('archivist', 'Archivist')
    for i in range(MAX_COUNT + 20):
        _xp(job, f'Hunter{i:03d}', 10000 - i)

    greedy = client.get(reverse('job_ranks_panel', args=['archivist']), {'range': 1, 'count': 10 ** 6})
    assert greedy.status_code == 200
    assert greedy.content.decode().count('<li class="lb-row') == MAX_COUNT, (
        'the count ceiling was not applied -- a crafted URL hydrated more than 200 profiles in one read'
    )

    far = client.get(reverse('job_ranks_panel', args=['archivist']), {'range': 10 ** 12})
    assert far.status_code == 200
    assert far.context['entries'] == []
    assert MAX_START < 10 ** 12, 'the start clamp no longer bounds what this asks for'


def test_the_seam_between_windows_neither_repeats_nor_skips(client):
    """The off-by-one that REPLACED the old off-by-one. The pager had a look-ahead row and two tests
    around exact multiples of the page size; those went with it, and nothing then covered the boundary
    the windows have: a board of exactly PAGE_SIZE, and the first row past the end.
    """
    from trophies.views.career_views import JobRanksPanelView
    n = JobRanksPanelView.PAGE_SIZE

    job = _job('archivist', 'Archivist')
    for i in range(n):                                    # EXACTLY one window
        _xp(job, f'Hunter{i:03d}', 10000 - i)

    panel = client.get(reverse('job_ranks_panel', args=['archivist']))
    assert len(panel.context['rows']) == n
    assert panel.context['total'] == n

    # One past the end: empty, not a wrapped or repeated row.
    past = client.get(reverse('job_ranks_panel', args=['archivist']), {'range': n + 1})
    assert past.context['entries'] == [], 'the window past the last row was not empty'

    # ...and one MORE entrant makes that same range the real 51st row, so the boundary is exercised in
    # both directions rather than only where it happens to be empty.
    _xp(job, 'Hunter999', 1)
    now = client.get(reverse('job_ranks_panel', args=['archivist']), {'range': n + 1})
    assert len(now.context['entries']) == 1
    assert now.context['entries'][0]['rank'] == n + 1, 'the seam repeated or skipped a rank'


def test_the_tabs_are_a_real_tablist_over_real_panels(client):
    """Converted from `?tab=` links to in-place switching, so all three detail pages (game, badge, job)
    use one mechanism. The ARIA follows the behaviour rather than the other way round: it was a `nav` of
    links precisely BECAUSE nothing switched in place, and now something does."""
    _job('archivist', 'Archivist')

    body = client.get(reverse('job_detail', args=['archivist'])).content.decode()

    assert 'id="jobd-switch"' in body and 'role="tablist"' in body
    assert 'id="jobd-view-contracts"' in body and 'id="jobd-view-ranks"' in body
    assert 'data-view="ranks"' in body and 'data-ranks-src' in body
    # Contracts open, Ranks empty-and-hidden until asked for.
    assert '<li class="lb-row' not in body, 'the board was server-rendered into the page'
    # Scoped to the switcher: `aria-current="page"` is legitimately on the breadcrumb and the hub rail.
    strip = body[body.index('id="jobd-switch"'):body.index('</div>', body.index('id="jobd-switch"'))]
    assert 'aria-current' not in strip, 'the old link semantics survived the conversion'
    # They ARE still links -- deliberately, as the no-JS fallback (see the test below). What changed is
    # that they carry tab semantics and are intercepted, rather than being a nav of plain page loads.
    assert 'role="tab"' in strip and 'aria-selected' in strip


def test_an_old_tab_ranks_bookmark_still_opens_the_board(client):
    """`?tab=` was the switching mechanism and is now only an entry point. It has to keep working: it is
    what old bookmarks carry and what an old bookmark or a shared link carries."""
    job = _job('archivist', 'Archivist')
    _xp(job, 'Someone', 500)

    body = client.get(reverse('job_detail', args=['archivist']), {'tab': 'ranks'}).content.decode()

    # Asserted on the TAB, not by scanning the panel for `hidden` -- the panel's own spinner carries
    # `aria-hidden` and a substring check would match it.
    tab = body[body.index('id="jobd-tab-ranks"'):]
    tab = tab[:tab.index('>')]
    assert 'aria-selected="true"' in tab, 'arriving on ?tab=ranks did not select the Ranks tab'

    # ...and the Contracts panel is the one closed instead.
    panel = body[body.index('id="jobd-view-contracts"'):]
    panel = panel[:panel.index('>')]
    assert 'hidden' in panel, 'both panels are open on ?tab=ranks'


def test_the_tab_script_actually_REACHES_the_browser(client):
    """The regression this file could not see.

    The switcher was written into `{% block extra_js %}`. `base.html` declares no such block, and Django
    DISCARDS a child block the parent never declared -- silently, with no error and no warning. So the
    entire script was dropped from the response: the chips had no handler, `?tab=ranks` showed a spinner
    that never resolved, and the job board was unreachable. Every test in this file stayed green, because
    every one of them asserted markup or hit the panel endpoint directly.

    Asserting a distinctive symbol from inside the block, so the block NAME being wrong fails here.
    """
    _job('archivist', 'Archivist')
    body = client.get(reverse('job_detail', args=['archivist'])).content.decode()

    assert 'jobd-switch' in body, 'the switcher markup is missing'
    assert "getElementById('jobd-switch')" in body, (
        'the tab script did not render -- check the block name against the ones base.html declares'
    )
    assert 'wireTablist' in body, 'the switcher is not wired to the shared tablist helper'


def test_the_tabs_still_work_without_javascript(client):
    """They were converted to `<button>`, which left a no-JS reader unable to reach EITHER tab -- the
    `?tab=` version had a server-side fallback for free. They are links again, intercepted by JS."""
    job = _job('archivist', 'Archivist')
    _xp(job, 'Someone', 500)

    body = client.get(reverse('job_detail', args=['archivist'])).content.decode()
    strip = body[body.index('id="jobd-switch"'):body.index('</div>', body.index('id="jobd-switch"'))]

    assert 'href="?tab=ranks"' in strip and 'href="?tab=contracts"' in strip, (
        'the tabs have no href -- without JS neither tab is reachable'
    )
    # ...and the server still honours it, which is what makes the href a real fallback.
    resp = client.get(reverse('job_detail', args=['archivist']), {'tab': 'ranks'})
    assert resp.context['active_tab'] == 'ranks'


def test_the_url_written_by_the_switcher_is_the_one_the_server_reads(client):
    """It wrote `?view=` (syncViewParam's default) while the view read `?tab=`. So clicking Ranks produced
    a URL that reloaded on Contracts, and arriving on `?tab=ranks` then clicking Contracts left `tab=ranks`
    in the URL while Contracts was on screen. One param, read and written."""
    _job('archivist', 'Archivist')
    body = client.get(reverse('job_detail', args=['archivist'])).content.decode()

    assert "param: 'tab'" in body, 'the switcher writes a param the server does not read'
    assert "default: 'contracts'" in body


def test_the_tabs_slide_like_every_other_switcher(client):
    """The house standard for a segmented switcher. Built without it, job detail's tabs swapped
    instantly while nine other surfaces glide."""
    _job('archivist', 'Archivist')
    body = client.get(reverse('job_detail', args=['archivist'])).content.decode()

    assert 'PlatPursuit.slideViewIn' in body, 'the tab swap has no directional slide'


# ---------------------------------------------------------------------------------------------------
# The country filter (2026-08). `ProfileJobXP` already carried the mirrored `country_code` that all six
# standing stores gained, so this board never joins Profile to slice itself.
# ---------------------------------------------------------------------------------------------------

def _in_country(job, name, xp, code, country):
    p = ProfileFactory(display_psn_username=name, country_code=code, country=country, is_linked=True)
    ProfileJobXP.objects.create(profile=p, job=job, total_xp=xp, level=2,
                                country_code=code, is_linked=True)
    return p


def test_the_job_board_can_be_sliced_by_country(client):
    job = _job('archivist', 'Archivist')
    _in_country(job, 'Brit', 900, 'GB', 'United Kingdom')
    _in_country(job, 'Yank', 800, 'US', 'United States')
    _in_country(job, 'Brit2', 700, 'GB', 'United Kingdom')

    whole = client.get(reverse('job_ranks_panel', args=['archivist']))
    assert whole.context['total'] == 3

    sliced = client.get(reverse('job_ranks_panel', args=['archivist']), {'country': 'GB'})
    body = sliced.content.decode()
    assert sliced.context['total'] == 2, 'the tally counted the whole board under a slice'
    assert 'Brit' in body and 'Yank' not in body
    # Renumbered WITHIN the slice, or the second GB hunter reads as #3 on a two-row board.
    assert [e['rank'] for e in sliced.context['rows']] == [1, 2]


def test_the_job_slice_is_carried_onto_every_later_window(client):
    """A window that drops the filter returns hunters from everywhere with ranks that keep counting up,
    so it reads as the board rather than as a bug. The badge board has this test; job detail shipped its
    filter without one."""
    job = _job('archivist', 'Archivist')
    for i in range(60):
        _in_country(job, f'GB{i:02d}', 10000 - i, 'GB', 'United Kingdom')
    for i in range(60):
        _in_country(job, f'US{i:02d}', 9000 - i, 'US', 'United States')

    panel = client.get(reverse('job_ranks_panel', args=['archivist']), {'country': 'GB'})
    assert 'data-lb-params="country=GB"' in panel.content.decode()

    window = client.get(reverse('job_ranks_panel', args=['archivist']),
                        {'range': 51, 'country': 'GB'})
    body = window.content.decode()
    assert body.count('<li class="lb-row') == 10
    assert 'GB50' in body and 'US' not in body, 'the second window ignored the filter'


def test_the_job_picker_offers_only_countries_on_THIS_board(client):
    job = _job('archivist', 'Archivist')
    _in_country(job, 'Brit', 900, 'GB', 'United Kingdom')
    other = _job('curator', 'Curator')
    _in_country(other, 'Aussie', 900, 'AU', 'Australia')

    codes = {c['code'] for c in
             client.get(reverse('job_ranks_panel', args=['archivist'])).context['countries']}
    assert codes == {'GB'}, f'the picker offered countries with nobody on this board: {codes}'


def test_an_unknown_job_country_falls_back_to_the_whole_board(client):
    job = _job('archivist', 'Archivist')
    _in_country(job, 'Brit', 900, 'GB', 'United Kingdom')

    for raw in ('ZZ', 'not-a-code', ''):
        resp = client.get(reverse('job_ranks_panel', args=['archivist']), {'country': raw})
        assert resp.status_code == 200, f'country={raw!r} was not handled'
        assert resp.context['total'] == 1, f'country={raw!r} emptied the board'
        assert resp.context['selected_country'] == '', f'country={raw!r} was accepted'


def test_the_job_rank_is_read_under_the_SAME_slice_as_the_rows(client):
    """The viewer's rank and the row numbering are produced by different code, and the highlight lands on
    whichever row matches. Applying a filter to one and not the other puts it on a stranger."""
    job = _job('archivist', 'Archivist')
    _in_country(job, 'Yank', 5000, 'US', 'United States')       # ahead overall, off the GB board
    me = _in_country(job, 'Brit', 900, 'GB', 'United Kingdom')
    client.force_login(me.user)

    whole = client.get(reverse('job_ranks_panel', args=['archivist']))
    assert whole.context['my_rank'] == 2

    sliced = client.get(reverse('job_ranks_panel', args=['archivist']), {'country': 'GB'})
    assert sliced.context['my_rank'] == 1
    assert 'data-lb-viewer-rank="1"' in sliced.content.decode()
