"""`/jobs/` (the public catalogue) + `/jobs/<slug>/` (what a job is, its contracts, its board).

Jobs get a PUBLIC catalogue in the Browse hub, not under Leaderboards: a catalogue of jobs is a browse
surface. Its relationship to Career's Dossier is the split this codebase already settled for Collection vs
Browse Badges -- "SCOPE, not pagination". Career shows YOUR standing across the 24 jobs; this shows what
the jobs are.

The load-bearing constraint is that it works SIGNED OUT. A job page an anonymous visitor cannot read
defeats the discovery it exists for, and the board being identical for everyone is what keeps it
cacheable.
"""
import pathlib

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from trophies.models import Contract, EarnedContract, Job, ProfileJobXP
from trophies.util_modules.constants import CONTRACT_XP_TOTAL
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


def _contract_game(contract, title='Some Game'):
    """Give a Contract a real member GAME, so the card can link to it.

    Membership is DERIVED, not a join: a contract's members are the anchored, trusted-matched concepts
    whose `IGDBMatch.igdb_id` equals the contract's own (`contracts_service._member_at_igdb`). So the
    concept needs `anchor_migration_completed_at` set AND a trusted match on the contract's id -- a plain
    GameFactory is not a member of anything.
    """
    from django.utils import timezone
    from tests.factories import ConceptFactory, GameFactory, IGDBMatchFactory

    concept = ConceptFactory(unified_title=title, anchor_migration_completed_at=timezone.now())
    IGDBMatchFactory(concept=concept, igdb_id=contract.igdb_id)   # factory default status is trusted
    return GameFactory(concept=concept)


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

def _component_css():
    """Every component stylesheet concatenated.

    These assertions are about RULES, not about which file holds them. Three of them broke when the
    `.rp-*` job variants moved from `jobs.css` to `elements.css` (beside the rules they override, to kill
    an import-order dependency) -- a correct refactor failing tests that had encoded a filename.
    """
    root = pathlib.Path(__file__).resolve().parents[2] / 'static' / 'css' / 'components'
    return '\n'.join(f.read_text(encoding='utf-8') for f in sorted(root.glob('*.css')))


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


def test_the_card_shows_the_contract_XP_that_feeds_the_job(client):
    """Every Contract pays the same global T split EVENLY across the jobs it names, so a job's supply is
    the sum of its share of each live contract.

    The whole fixture is one assertion about that split: `a` takes a solo contract (6000), half of a
    two-job one (3000) and a third of a three-job one (2000). A draft contract pays nothing because it is
    not offered, and an override replaces T for its own contract only.
    """
    _solo()
    a, b, c = _job('a', 'A'), _job('b', 'B'), _job('c', 'C')
    _contract('Solo', [a])
    _contract('Shared', [a, b])
    _contract('Triple', [a, b, c])
    _contract('Draft', [c], live=False)
    rich = _contract('Rich', [b])
    Contract.objects.filter(pk=rich.pk).update(xp_total_override=9000)

    supply = {j.slug: j.xp_supply for j in client.get(reverse('jobs_browse')).context['jobs']}
    assert supply == {'a': 11000, 'b': 14000, 'c': 2000}


def test_the_supply_is_not_the_contract_count_in_disguise(client):
    """The reason both figures are on the card. They can point OPPOSITE ways, and this is the shape that
    does it: `many` is fed by three contracts it shares six ways, `few` by two it has to itself. More
    contracts, less to gain.

    If this ever fails by the two agreeing, the second figure has stopped earning its place -- which is a
    product question, not a bug.
    """
    _solo()
    many = _job('many', 'Many')
    few = _job('few', 'Few')
    filler = [_job(f'f{i}', f'F{i}') for i in range(5)]
    for i in range(3):
        _contract(f'Split {i}', [many] + filler)      # six ways -> 1000 each
    for i in range(2):
        _contract(f'Whole {i}', [few])                # 6000 each

    jobs = {j.slug: j for j in client.get(reverse('jobs_browse')).context['jobs']}
    assert jobs['many'].contract_count == 3 and jobs['few'].contract_count == 2
    assert jobs['many'].xp_supply == 3000 and jobs['few'].xp_supply == 12000, (
        'the split is not being applied -- every contract is paying its full total to every job'
    )


def test_a_multi_job_contract_does_not_pay_each_job_in_full(client):
    """THE SILENT FAILURE, pinned on its own because it produces plausible numbers.

    Dropping the `/ F('nj')` -- the "simplification" of summing each contract's total instead of this
    job's share of it -- stays valid SQL and returns a whole, believable figure for every job, uniformly
    too high. Verified by mutation: remove the division and this test plus its sibling above go red, and
    nothing else on the page changes.

    (The OTHER way to get this wrong fails loudly, which is worth knowing so nobody "hardens" against it:
    computing the job count as `Count('jobs')` on the queryset already filtered by `jobs=OuterRef('pk')`
    makes Django raise `FieldError: Cannot compute Sum('share'): 'share' is an aggregate`. That is why the
    count is its own correlated subquery -- not to avoid a wrong number, but because there is no other way
    to express it.)
    """
    _solo()
    a, b = _job('a', 'A'), _job('b', 'B')
    _contract('Shared', [a, b])

    supply = {j.slug: j.xp_supply for j in client.get(reverse('jobs_browse')).context['jobs']}
    assert supply == {'a': CONTRACT_XP_TOTAL // 2, 'b': CONTRACT_XP_TOTAL // 2}, (
        f'a two-job contract paid {supply} -- the job count is being read off the filtered join'
    )


def test_a_job_with_no_contracts_reports_zero_rather_than_nothing(client):
    """`Coalesce` on the subquery. A correlated aggregate over no rows is NULL, which renders as the empty
    string in the template -- so the tile would read "contracts" and a bare "XP" with no figure at all."""
    _solo()
    _job('lonely', 'Lonely')
    job = client.get(reverse('jobs_browse')).context['jobs'][0]
    assert job.xp_supply == 0
    assert '0</b> XP' in client.get(reverse('jobs_browse')).content.decode()


def test_the_second_figure_costs_no_extra_query(client):
    """It is a correlated subquery on the SAME queryset, not a second pass. Asserted against the count with
    the annotation removed being identical -- a per-card evaluation would scale with the catalogue, which
    the sibling test above already guards, but this pins that the wall is still ONE read."""
    _solo()
    jobs = [_job(f'j{i}', f'Job {i}') for i in range(4)]
    for j in jobs:
        _contract(f'C {j.slug}', [j])

    with CaptureQueriesContext(connection) as ctx:
        resp = client.get(reverse('jobs_browse'))
    assert resp.status_code == 200
    reads = [q for q in ctx.captured_queries if 'trophies_job' in q['sql'] and 'SELECT' in q['sql']]
    assert len(reads) == 1, f'the wall costs {len(reads)} reads of the job table, not one'


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

    # Scoped to the WALL. `'zzz' in body` passed whether or not the empty state existed -- the search
    # input echoes the query back into its `value`, so the needle was always present.
    searched = client.get(reverse('jobs_browse'), {'q': 'zzz'}, HTTP_HX_REQUEST='true').content.decode()
    assert 'Nothing here is called' in searched, 'the empty state does not say a search emptied it'
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


def test_the_tally_ticks_from_the_previous_value_on_every_filter(client):
    """Site-wide behaviour, and this page shipped without it: the count animated on first paint and then
    snapped silently on every filter, which reads as broken rather than as restraint.

    Pinned as MARKUP because the behaviour is in a script this suite cannot execute. `from` is the part
    worth guarding -- without it a filter trimming 25 to 24 sweeps up from zero instead of ticking one
    step, which is the same animation the page already plays on arrival and says nothing about the change.
    """
    _solo()
    _job('a', 'Ranger')
    body = client.get(reverse('jobs_browse')).content.decode()

    script = body[body.index('data-jobs-count') :]
    assert 'function syncTally' in script
    assert '{ from: countLast }' in script, 'the tally sweeps from zero rather than ticking from the old value'
    # ...and it is called on the swap, not only at boot.
    after = script[script.index("addEventListener('htmx:afterSwap'"):]
    assert 'syncTally()' in after[:after.index('});')], 'the tally is never updated after a filter'


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
    # `Ready to Claim` -- the CASING is the assertion. This read `'Ready to claim'`, a string that
    # appears nowhere in the codebase, so the negative was vacuously true on every input: drop the
    # `{% if user.is_authenticated %}` guard and the badge renders for anonymous readers with the test
    # still green. Same trap as `'contract'` against `Contracts`, one letter over.
    assert 'Ready to Claim' not in contracts, 'viewer state leaked into the anonymous view'
    assert 'rp-accept' not in contracts, 'a claim control rendered for an anonymous viewer'


def test_a_linked_viewer_sees_their_own_state_on_each_contract(client):
    """The card carries the viewer's status badge, from the shared Career card since 2026-08.

    The state has to be a REACHED one. This asserted merely that an `EarnedContract` existed and the
    placeholder it was written against printed "In progress" for any row that had one -- which was wrong
    about the product: an accepted contract with no progress on it is Not Started, and the shared card
    says so. So the fixture now reaches the 100% tier, and the assertion is the badge that state produces.
    """
    from django.utils import timezone

    job = _job('archivist', 'Archivist')
    contract = _contract('Mine', [job])
    profile = ProfileFactory(is_linked=True)
    EarnedContract.objects.create(profile=profile, contract=contract, full_reached_at=timezone.now())
    client.force_login(profile.user)

    body = client.get(reverse('job_detail', args=['archivist']), {'tab': 'contracts'}).content.decode()
    assert 'Ready to Claim' in body, 'the card is not showing the viewer their own state'


def test_the_contract_card_shows_only_THIS_jobs_tiles_not_the_whole_roster(client):
    """The one block that differs between this page's card and Career's.

    Career renders a 5x5 map of all 25 jobs, lit and dim, because a reader browsing every contract needs
    to know what each one touches. On a page that IS a job, twenty-four of those cells answer a question
    already answered and the twenty-fifth is the only one that can be acted on -- so the map collapses to
    this contract's own jobs, named, with the current one marked.
    """
    _solo()
    a = _job('a', 'Archivist')
    b = _job('b', 'Blacksmith')
    _job('c', 'Cartographer')          # a third job, in the roster but NOT on this contract
    _contract('Shared', [a, b])

    body = client.get(reverse('job_detail', args=['a']), {'tab': 'contracts'}).content.decode()

    assert 'rp-jobtiles' in body and 'rp-jobgrid' not in body, 'the 25-cell map rendered on job detail'
    assert 'Blacksmith' in body, "the contract's other job is not shown"
    assert 'Cartographer' not in body, 'a job this contract does not touch is on the card'
    # ...and the job you are standing on is the marked one.
    marked = body[body.index('rp-jobtiles'):]
    marked = marked[:marked.index('</div>')]
    assert 'is-this' in marked


def test_career_still_renders_the_full_job_map(client):
    """The variant is opt-in: no `job`, no change. Career passes none, so its card must be untouched --
    this is the assertion that makes 'a variant, not a second template' safe to have done."""
    from trophies.services import contracts_service

    import pathlib
    root = pathlib.Path(__file__).resolve().parents[2]
    src = (root / 'templates' / 'trophies' / 'partials' / 'contracts' / '_contract_card.html').read_text(encoding='utf-8')
    assert '{% if job %}' in src and 'rp-jobgrid' in src, 'the map branch is gone'
    # The roster loop must not use `job` as its variable: it would shadow the page-scope one, and the two
    # mean opposite things (one job vs all 25).
    # Bounded by the LOOP, not by the next `{% endif %}` -- the first one is inline on the cell's own
    # class attribute, so slicing there ends the window before the loop body it is checking. Third time
    # today an index-to-the-next-delimiter slice has cut short of its subject.
    grid = src[src.index('rp-jobgrid'):]
    grid = grid[:grid.index('{% endfor %}{% endfor %}')]
    assert '{% for rjob in d.jobs %}' in grid, 'the map loop shadows the page-scope `job` again'
    # ...and the loop BODY consistently uses the renamed variable, which is what the rename was for: a
    # half-rename leaves the branch reading the page-scope `job` for some of its values.
    assert 'rjob.slug in p.element_slugs' in grid and '{{ rjob.name }}' in grid


def test_the_contracts_tab_shows_legacy_and_already_banked_contracts(client):
    """Two `contracts_page` defaults that are right for Career and wrong here, both silent.

    `platforms` defaults to current-gen, because Career is a board of what you could go and play. `scope`
    defaults to 'board', which hides fully-banked contracts, because Career is a to-do list. This page is
    a CATALOGUE of what feeds a job: a PS3 contract still feeds it, and so does one you finished last
    year. Either default left in place would quietly shorten the list under a header that counts the
    full set.
    """
    from django.utils import timezone

    _solo()
    job = _job('archivist', 'Archivist')
    legacy = _contract('Legacy Only', [job])
    banked = _contract('Long Done', [job])
    profile = ProfileFactory(is_linked=True)
    EarnedContract.objects.create(profile=profile, contract=banked,
                                  full_reached_at=timezone.now(), full_accepted_at=timezone.now())
    client.force_login(profile.user)

    body = client.get(reverse('job_detail', args=['archivist']), {'tab': 'contracts'}).content.decode()
    assert 'Long Done' in body, 'a fully-banked contract is missing -- scope is still Career\'s default'
    assert 'Legacy Only' in body, 'a legacy-platform contract is missing -- platforms is still the default'


def test_the_contracts_tab_pages_from_its_own_public_endpoint(client):
    """Infinite scroll needs a cards-only page N, and it must be PUBLIC: this is the same catalogue an
    anonymous visitor already sees on the page, so gating the second screenful would stop the tab halfway
    down for exactly the readers it exists to persuade. (Career's equivalent 404s the unlinked, because
    that whole surface is personal.)"""
    _solo()
    job = _job('archivist', 'Archivist')
    _contract('Only One', [job])

    resp = client.get(reverse('job_contracts', args=['archivist']))
    body = resp.content.decode()

    assert resp.status_code == 200, 'the results endpoint is gated'
    assert 'Only One' in body and 'rp-row' in body
    assert '<h1' not in body and 'jobd-switch' not in body, 'the endpoint returned the whole page'
    assert resp['X-Has-Next'] == '0'


def test_the_scroller_pages_by_the_same_number_the_endpoint_does(client):
    """`InfiniteScroller` derives its next page number from how many cards are already in the grid, so a
    literal in the template that drifted from `CONTRACTS_PER_PAGE` would silently skip or repeat a whole
    page. Passed through the context for exactly that reason."""
    from trophies.services import contracts_service

    _solo()
    job = _job('archivist', 'Archivist')
    _contract('One', [job])

    body = client.get(reverse('job_detail', args=['archivist'])).content.decode()
    assert f'paginateBy: {contracts_service.CONTRACTS_PER_PAGE},' in body
    # ...and it fetches the RESULTS endpoint, not the page's own path (the scroller's default).
    assert reverse('job_contracts', args=['archivist']) in body
    assert 'url: grid.dataset.resultsUrl' in body


def test_the_cover_skeleton_is_settled_on_every_page_that_renders_the_card(client):
    """REGRESSION, reported from the browser as "strange flashing" when switching tabs.

    `.rp-tile__art::before` shimmers on an INFINITE CSS animation and the only thing that stops it is
    `.is-loaded`, which JS adds when the image arrives. Job detail adopted the contract card without that
    call, so every cover pulsed forever -- and because `display: none` restarts a CSS animation, hiding
    and re-showing the tab re-synced all of them into one page-wide flash.

    The behaviour now travels with the markup (`PlatPursuit.progressiveArt`, shared with Career), which is
    the actual lesson: a skeleton whose stop condition lives in JS is not a CSS-only component, and
    reusing the template without the call looks fine in a screenshot and wrong in motion.
    """
    _solo()
    job = _job('archivist', 'Archivist')
    _contract('One', [job])

    body = client.get(reverse('job_detail', args=['archivist'])).content.decode()
    assert 'progressiveArt(grid)' in body, 'the first page of cards never settles its cover skeleton'
    # ...and appended pages too, or the flashing comes back the moment you scroll.
    assert 'onAppend' in body and 'progressiveArt(n)' in body, (
        'scroll-appended cards shimmer forever -- the skeleton is only settled for page 1'
    )


def test_career_and_job_detail_settle_the_skeleton_the_SAME_way():
    """Two pages render this card and the stop condition is one line of CSS away from being wrong on
    either. Career's copy was extracted rather than duplicated, so a fix to one is a fix to both."""
    import pathlib as _p
    root = _p.Path(__file__).resolve().parents[2]
    utils = (root / 'static' / 'js' / 'utils.js').read_text(encoding='utf-8')
    career = (root / 'templates' / 'trophies' / 'career.html').read_text(encoding='utf-8')

    assert 'function progressiveArt(scope)' in utils
    assert 'window.PlatPursuit.progressiveArt = progressiveArt;' in utils
    assert 'PP.progressiveArt(scope)' in career, 'Career kept its own copy'
    # The hand-rolled body must be gone from the page, not merely unused beside the shared one.
    assert "art.dataset.plInit = '1'" not in career


def test_the_tab_strip_switches_in_place_instead_of_navigating(client):
    """REGRESSION, reported as "strange flashing, almost like there are multiple animations playing per
    swap" -- which is exactly what it was.

    This page's tabs are `<a href="?tab=...">` ON PURPOSE: the server honours the param, so the strip
    works with JS off. But `wireTablist` bound click without preventing the default, so a tab click
    switched the panel, ignited the chip, slid the view in, AND THEN followed the href -- a full page load
    that replayed every entrance animation on top of the transition that had just run.

    Both halves are asserted, because either alone is wrong: anchors WITHOUT the prevention is the bug,
    and buttons (which is how badge and game detail dodge it) would silently drop the no-JS fallback.
    """
    _solo()
    job = _job('archivist', 'Archivist')
    _contract('One', [job])

    body = client.get(reverse('job_detail', args=['archivist'])).content.decode()
    strip = body[body.index('id="jobd-switch"'):]
    strip = strip[:strip.index('</div>')]
    assert strip.count('<a ') == 2, 'the tabs stopped being links, so the no-JS fallback is gone'
    assert '?tab=contracts' in strip and '?tab=ranks' in strip

    utils = (pathlib.Path(__file__).resolve().parents[2] / 'static' / 'js' / 'utils.js').read_text(encoding='utf-8')
    click = utils[utils.index("tab.addEventListener('click'"):]
    click = click[:click.index('select(tab);')]
    assert 'e.preventDefault()' in click, 'a link tab still navigates after switching in place'
    # Modifier and middle clicks must survive: those mean "open this elsewhere", and these hrefs are real
    # URLs that land on the right tab.
    assert 'e.metaKey' in click and 'e.ctrlKey' in click and 'e.button === 0' in click


def test_the_card_links_to_the_contracts_game(client):
    """The tier-2 control was a dead button here: its opener is page-local to Career, and its
    `contract_modal` URL is login + linked gated, so even wired it would have 404'd the anonymous visitors
    this page exists for.

    It deep-linked to `/career/?contract=<slug>` for one afternoon. That was worse -- a filter the board
    controller did not know about, so the first chip click repopulated the board while the URL still
    claimed the filter, the facets counted a population the board was not showing, and `?scope=history`
    badged untouched contracts as "Banked".

    The GAME is exact, public, and already carries the shared contract modal, so it shows MORE of the
    contract than a filtered board would have.
    """
    _solo()
    job = _job('archivist', 'Archivist')
    contract = _contract('Some Contract', [job])
    game = _contract_game(contract)

    body = client.get(reverse('job_detail', args=['archivist'])).content.decode()
    assert reverse('game_detail', args=[game.np_communication_id]) in body
    assert 'view the game' in body
    # The dead affordance is gone, and so is the parameter that replaced it.
    assert 'data-modal-url' not in body
    assert 'contract=some-contract' not in body


def test_the_game_link_is_offered_to_anon_too(client):
    """Unlike the Career deep link it replaced. Game detail is public, and its contract modal has a public
    preview variant -- so the reader this page exists to persuade gets the same door, not a hidden one."""
    _solo()
    job = _job('archivist', 'Archivist')
    contract = _contract('Some Contract', [job])
    _contract_game(contract)

    body = client.get(reverse('job_detail', args=['archivist'])).content.decode()
    assert 'view the game' in body
    # Anonymous viewers get the PREVIEW endpoint, linked ones the real modal.
    assert reverse('contract_modal_preview', args=['some-contract']) in body
    assert reverse('contract_modal', args=['some-contract']) not in body


def test_the_board_has_no_contract_filter(client):
    """It was added and removed the same afternoon. Pinned so it does not come back without the controller
    integration it needs: URL seeding, param rebuilding, a clearable token, facet counting, and
    empty-state attribution all have to learn a filter, or it becomes a state the reader cannot escape."""
    from trophies.services import contracts_service
    import inspect

    sig = inspect.signature(contracts_service.contracts_page)
    assert 'contract' not in sig.parameters, 'the exact-contract filter is back'
    src = inspect.getsource(contracts_service._filter_contracts)
    assert 'qs.filter(slug=' not in src


def test_the_header_figures_actually_count_up(client):
    """Both tallies carried `data-countup` and nothing on this page ever called `countUp` -- the attribute
    was inert on the hunter tally this replaced, and the rebuild doubled it before anyone noticed. A
    `data-*` hook with no caller reads exactly like a working one in the markup, which is why it survives;
    the same bug shipped on the catalogue page and was caught the same way."""
    _solo()
    job = _job('archivist', 'Archivist')
    _contract('One', [job])

    body = client.get(reverse('job_detail', args=['archivist'])).content.decode()
    assert body.count('data-countup') >= 3
    assert 'PlatPursuit.countUp' in body, 'the header figures are decorative -- nothing counts them up'
    # Scoped to the header CARD, which is what `.pp-head-cascade` marks. The selector named
    # `.jobd__tallies` until the stats row replaced it -- and a dead SELECTOR fails exactly as silently as
    # the dead `data-countup` attributes this test was written for.
    assert ".pp-head-cascade [data-countup]" in body


def test_the_header_uses_the_shared_page_header_shape(client):
    """`.pp-bhead` / `__id` / `__tally`, the structure every rebuilt browse and detail header uses. The
    discipline-tinted icon chip stays bespoke because nothing shared carries one."""
    _solo()
    _job('archivist', 'Archivist')

    body = client.get(reverse('job_detail', args=['archivist'])).content.decode()
    # From the head CONTAINER, not the <h1>: the icon chip precedes the title, so slicing at the heading
    # cuts the very element this asserts on.
    head = body[body.index('pp-bhead jobd__head'):body.index('jobd-switch')]
    assert 'pp-bhead__id' in head and 'jobd__ic' in head
    assert 'pp-head-cascade' in body, 'the header lost its shared entrance cascade'


def test_hovering_a_job_tile_can_light_its_arc_in_the_ring(client):
    """The ring is N equal arcs and which one is WHICH is otherwise guesswork. `_ring_segments` tags each
    arc with its job slug expressly so the two can be tied together; this asserts both ends of that tie
    are actually rendered, and that the shared helper is wired to them.

    Career runs the same helper over its 25-cell map -- the item selector is the only difference, which is
    why the behaviour is shared rather than copied.
    """
    _solo()
    a = _job('a', 'Archivist')
    b = _job('b', 'Blacksmith')
    _contract('Shared', [a, b])

    body = client.get(reverse('job_detail', args=['a']), {'tab': 'contracts'}).content.decode()

    # BOTH ENDS have to carry the slug or the lookup matches nothing and the hover silently does
    # nothing -- which is why each is asserted inside its own element, not as a substring of the page.
    tiles = body[body.index('rp-jobtiles'):]
    tiles = tiles[:tiles.index('</div>')]
    assert 'data-slug="a"' in tiles and 'data-slug="b"' in tiles, 'the tiles carry no slug to match on'

    ring = body[body.index('class="rp-ring"'):]
    ring = ring[:ring.index('</svg>')]
    assert 'data-slug="a"' in ring and 'data-slug="b"' in ring, 'the arcs carry no slug to match on'

    assert "ringHoverLink(grid, { itemSelector: '.rp-jobtile' })" in body


def test_the_ring_caption_names_the_job_without_a_preposition(client):
    """"+1,000" over "Archivist" -- the figure and what it feeds. The "to" read as a sentence fragment in
    a two-line ring centre where there is no room for one."""
    _solo()
    job = _job('archivist', 'Archivist')
    _contract('One', [job])

    body = client.get(reverse('job_detail', args=['archivist']), {'tab': 'contracts'}).content.decode()
    cap = body[body.index('rp-ring__xp-cap'):]
    cap = cap[:cap.index('</span>')]
    assert 'Archivist' in cap and '>to ' not in cap


def test_the_hover_link_is_shared_with_career_not_copied():
    """Third extraction this week (after the shuffle and the cover skeleton), and the same reasoning: two
    pages doing one thing drift, and the drift is invisible because each looks right on its own page."""
    root = pathlib.Path(__file__).resolve().parents[2]
    utils = (root / 'static' / 'js' / 'utils.js').read_text(encoding='utf-8')
    career = (root / 'templates' / 'trophies' / 'career.html').read_text(encoding='utf-8')

    assert 'function ringHoverLink(scope, opts)' in utils
    assert 'window.PlatPursuit.ringHoverLink = ringHoverLink;' in utils
    assert 'PP.ringHoverLink(list)' in career, 'Career kept its own copy'
    assert "querySelector('.rp-jobcell.is-lit[data-slug=" not in career
    # The lookup must be scoped to the hovered CARD: unscoped, hovering one job on a wall of 24 contracts
    # lights that slug on every card that happens to level it.
    fn = utils[utils.index('function ringHoverLink(scope, opts)'):]
    fn = fn[:fn.index('function progressiveArt')] if 'function progressiveArt' in fn else fn[:3000]
    assert 'card.querySelector' in fn and 'closest(cardSel)' in fn


def test_the_tile_grid_never_leaves_a_ragged_last_row(client):
    """The column count follows the JOB count, and the property is what is asserted -- not the number.

    A fixed 3 columns shipped first and was wrong for half the range: 4 jobs rendered as "3 and a lone
    tile", 2 as a pair with a hole beside it. Only 3 and 6 were ever clean. A test that pinned
    `repeat(3, 1fr)` would have passed on exactly that layout, which is why this one derives the expected
    columns from the rule and checks the remainder instead.

    5 is the documented exception: no rectangle fits it at a sensible width, so it takes 3 + 2.
    """
    css = _component_css()

    def columns_for(n):
        """Read the effective column count out of the CSS the way the browser resolves it: the per-count
        override if one exists, else the base rule."""
        override = f'.rp-jobtiles[data-n="{n}"]'
        block = None
        if override in css:
            block = css[css.index(override):]
            block = block[:block.index('}')]
        else:
            block = css[css.index('.rp-jobtiles {'):]
            block = block[:block.index('}')]
        if 'repeat(' in block:
            return int(block[block.index('repeat(') + 7:].split(',')[0])
        return 1

    ragged = {n: columns_for(n) for n in (1, 2, 3, 4, 6) if n % columns_for(n)}
    assert not ragged, f'these job counts leave a lone tile on the last row: {ragged}'
    assert columns_for(5) == 3, 'five jobs should take 3 + 2; five across puts each tile under 30px'


def test_the_tile_grid_covers_every_job_count_a_contract_can_have(client):
    """1 to 6, and that range is not arbitrary: `CONTRACT_XP_TOTAL` is 6,000 precisely so the even split
    lands on clean integers across it. The layout rule and the economy constant are two facts in different
    files that have to agree.

    This used to assert ONLY the arithmetic -- it read no CSS and rendered no page, so setting the grid to
    seven columns left it green. It renders a real six-job contract now and counts the tiles, so the
    constant and the layout are checked against each other rather than the constant against itself.
    """
    from trophies.util_modules.constants import CONTRACT_XP_TOTAL

    for n in range(1, 7):
        assert CONTRACT_XP_TOTAL % n == 0, f'{CONTRACT_XP_TOTAL} does not split evenly across {n} jobs'
    assert CONTRACT_XP_TOTAL % 7 != 0, (
        'seven jobs now splits evenly, so the 1-6 range the tile grid is built around has moved'
    )

    _solo()
    jobs = [_job(f'j{i}', f'Job {i}') for i in range(6)]
    _contract('Six Ways', jobs)
    body = client.get(reverse('job_detail', args=['j0']), {'tab': 'contracts'}).content.decode()

    tiles = body[body.index('rp-jobtiles'):]
    tiles = tiles[:tiles.index('</div>')]
    # Counted by `data-slug`, one per tile. `class="rp-jobtile` is a PREFIX of both `rp-jobtiles` (the
    # container) and `rp-jobtile__name` (one per tile), so counting it returns 13 for six jobs.
    assert tiles.count('data-slug="') == 6, 'a six-job contract did not render six tiles'
    assert 'data-n="6"' in body, 'the grid was not told how many jobs to lay out'


def test_the_tile_grid_declares_its_job_count(client):
    """The CSS keys off `data-n`; without it every contract falls back to the 3-column base rule and the
    adaptation silently does nothing."""
    _solo()
    a, b = _job('a', 'A'), _job('b', 'B')
    _contract('Two Jobs', [a, b])

    body = client.get(reverse('job_detail', args=['a']), {'tab': 'contracts'}).content.decode()
    assert 'class="rp-jobtiles" data-n="2"' in body


def test_a_job_tile_reserves_room_for_a_two_line_name(client):
    """At tile width "Cartographer" wraps to two lines and "Mage" does not, so without a reserved height
    the two rows of a six-job contract sit at different heights and the grid reads as broken. Names WRAP
    rather than truncate -- the icon is already the abbreviation."""
    css = _component_css()
    rule = css[css.index('.rp-jobtile__name {'):]
    rule = rule[:rule.index('}')]
    assert 'min-height' in rule, 'the two rows will not line up'
    assert 'text-overflow' not in rule and 'nowrap' not in rule, 'a job name is being truncated'


def test_the_tile_grid_is_top_aligned_and_career_still_is_not(client):
    """`.rp-body` centres its columns vertically, which is right for Career's 5x5 map -- a fixed five-row
    block, always taller than the ring beside it -- and wrong for these tiles, which are one or two rows.
    Centred, a one-job contract floats its tiles into the middle of the card while a six-job one starts at
    the top, so the block appears to move around as you scan a wall.

    The SCOPING is the half worth guarding: `:has(.rp-jobtiles)` matches only the job-scoped variant, and
    an unscoped `align-items: start` would silently re-lay-out every card on the Contracts board -- a page
    this change has no business touching.
    """
    css = _component_css()
    elements = (pathlib.Path(__file__).resolve().parents[2]
                / 'static' / 'css' / 'components' / 'elements.css').read_text(encoding='utf-8')

    assert '.rp-body:has(.rp-jobtiles) { align-items: start; }' in css
    # Career's own rule is untouched: still centred, still declared where the card lives.
    base = elements[elements.index('.rp-body {'):]
    base = base[:base.index('}')]
    assert 'align-items: center' in base, "Career's card body is no longer centred"


def test_the_cover_gallery_cycles_on_hover_here_too(client):
    """The card ships its extra frames in `data-gallery` and Career plays them; this page rendered the
    attribute and nothing read it, so the art sat still.

    Third time the same shape of omission has been found on this page -- the cover skeleton, the count-up
    hooks, and now this. Adopting a component's MARKUP is not adopting the component: its `data-*` payload
    is inert until something reads it, and inert looks identical to working in a screenshot.
    """
    _solo()
    job = _job('archivist', 'Archivist')
    _contract('One', [job])

    body = client.get(reverse('job_detail', args=['archivist'])).content.decode()
    assert 'coverCarousel(grid)' in body, 'the hover gallery is never started'
    # ...and for scroll-appended cards, or the art stops moving the moment you scroll.
    appended = body[body.index('onAppend'):]
    appended = appended[:appended.index('},')]
    assert 'coverCarousel(n)' in appended


def test_the_hover_gallery_is_shared_with_career_not_copied():
    """Fourth extraction this week (shuffle, cover skeleton, ring hover, this). Career's local `CBEAT` /
    `CFADE` constants travel with the behaviour now -- the fade in particular has to match the CSS
    transition on `.rp-tile__img--fx`, and two pages holding their own copy of that number is how a
    crossfade ends up flashing on one of them."""
    root = pathlib.Path(__file__).resolve().parents[2]
    utils = (root / 'static' / 'js' / 'utils.js').read_text(encoding='utf-8')
    career = (root / 'templates' / 'trophies' / 'career.html').read_text(encoding='utf-8')

    assert 'function coverCarousel(scope, opts)' in utils
    assert 'window.PlatPursuit.coverCarousel = coverCarousel;' in utils
    assert 'PP.coverCarousel(scope' in career, 'Career kept its own copy'
    assert 'var CBEAT' not in career and "art.dataset.carInit = '1'" not in career


def test_career_still_declares_the_observer_its_reveal_depends_on():
    """REGRESSION, and one I caused: an extraction spliced `career.html` between two anchors and took the
    reveal observer and its body out with the block it was aiming at. `initReveal` was left referencing an
    undeclared `revealIO`, which THREW on every `initCards()` -- and because infinite scroll, the entrance
    stagger and the `pp:contracts-shown` listener all run after that call, they silently never started.

    A ReferenceError inside an IIFE kills the rest of the IIFE and nothing else on the page notices, which
    is why it survived a green test run and a page that still looked correct. This asserts the identifiers
    the block reads are ones it also defines.
    """
    root = pathlib.Path(__file__).resolve().parents[2]
    career = (root / 'templates' / 'trophies' / 'career.html').read_text(encoding='utf-8')

    # Every identifier `initReveal` reads must be declared in the file (or be a shared helper off PP).
    body = career[career.index('function initReveal(scope)'):]
    body = body[:body.index('}')]
    for name in ('cardRevealer',):
        assert f'var {name}' in career, f'`initReveal` reads `{name}` but nothing declares it'
        assert name in body

    # And the call order that made the failure invisible: `initCards` runs before the scroller starts.
    assert career.index('initCards(list);') < career.index('observeSentinel();')


def test_the_contract_cards_stagger_in_and_reveal_on_job_detail(client):
    """The two premium entrances the card has on Career: the batch rise as the page arrives, and the
    per-card figure animation as it scrolls into view (XP counting up, tier bars filling, ring arcs
    drawing, job tiles igniting)."""
    _solo()
    job = _job('archivist', 'Archivist')
    _contract('One', [job])

    body = client.get(reverse('job_detail', args=['archivist'])).content.decode()
    assert "cardReveal({ jobSelector: '.rp-jobtile' })" in body, 'cards never reveal their figures'
    assert 'PlatPursuit.staggerCards' in body, 'the cards never stagger in'
    # Appended pages get both, or the wall goes flat the moment you scroll.
    appended = body[body.index('onAppend'):]
    appended = appended[:appended.index('},')]
    assert 'revealer.observe(nodes)' in appended and 'staggerCards(nodes)' in appended


def test_the_entrance_plays_once_not_on_every_tab_return(client):
    """Career replays its stagger each time you return to Contracts. This page has two tabs a reader flips
    between, and re-staggering 24 cards on every return is the busyness the tab-navigation fix just
    removed -- so it plays on load, or on the FIRST activation for a reader who arrived on `?tab=ranks`."""
    _solo()
    job = _job('archivist', 'Archivist')
    _contract('One', [job])

    body = client.get(reverse('job_detail', args=['archivist'])).content.decode()
    assert 'var staggered = false;' in body and 'if (staggered ' in body
    # The panel announces itself so a reader who landed on Ranks still gets the entrance once.
    assert "pp:jobd-contracts-shown" in body


def test_the_contracts_tab_does_no_work_its_own_settings_discard(client):
    """Three costs the tab was paying for output nothing consumed, all invisible in a screenshot.

    It sorts by `name`, so the relevance and strength weights -- correlated subqueries evaluated PER ROW --
    were computed and thrown away, and `discipline_levels` (a grouped aggregate) was computed to feed
    them. And it renders the card's PILL branch, so the 25-job roster the MAP branch iterates was fetched
    per page and never read.

    Asserted as a query BUDGET rather than an exact number: the point is that it does not grow and does
    not carry passengers, not that any particular count is correct forever.
    """
    _solo()
    job = _job('archivist', 'Archivist')
    for i in range(4):
        _contract(f'C{i}', [job])

    with CaptureQueriesContext(connection) as ctx:
        resp = client.get(reverse('job_contracts', args=['archivist']))
    assert resp.status_code == 200

    sql = ' '.join(q['sql'] for q in ctx.captured_queries)
    assert 'trophies_job"."display_order' not in sql or 'GROUP BY' in sql, (
        'the 25-job roster is still being fetched for a branch this page never renders'
    )
    assert len(ctx.captured_queries) <= 6, (
        f'the results endpoint runs {len(ctx.captured_queries)} queries: '
        + '; '.join(q['sql'][:80] for q in ctx.captured_queries)
    )


def test_the_relevance_weights_are_switched_off_where_they_are_not_read(client):
    """`with_ranking` was threaded through the service and then never passed -- the plumbing existed and
    the caller kept paying. Pinned at the call site, since that is the half that was missing."""
    from trophies.views.career_views import JobDetailView

    assert JobDetailView.CONTRACT_PARAMS.get('with_ranking') is False
    assert JobDetailView.CONTRACT_PARAMS['sort'] == 'name', (
        'the sort changed -- if it now reads relevance, with_ranking must come back on'
    )


def test_the_job_icon_sprite_is_on_the_page(client):
    """The pills draw their glyphs with `<use href="#jobicon-...">`, which resolves to NOTHING if the
    sprite is absent -- names render with an invisible gap where the icon belongs, on a page that
    otherwise looks fine. Career defines it once near its toolbar; this page has to define its own."""
    _solo()
    job = _job('archivist', 'Archivist', icon='book')
    _contract('One', [job])

    body = client.get(reverse('job_detail', args=['archivist'])).content.decode()
    assert 'id="jobicon-' in body, 'the icon sprite is missing, so every job pill draws a blank'


def test_the_header_carries_the_jobs_three_objective_stats(client):
    """A real header, on the shared `.scard` row every rebuilt detail page uses -- what the job IS, before
    anything about the viewer.

    PURSUERS IS BACK HERE, reversing two earlier calls in this same rebuild, so the reasoning is pinned
    with the test. It was dropped from the catalogue TILE for saying more about which games happen to be
    popular than about the job, and dropped from this header when it was one of only two figures and read
    as the headline. As one of three on the job's own page, with its board one click away, it is a fact
    about the job rather than a claim about its worth.

    All three are already computed for the page, so the row costs no queries: contracts and XP from the
    same calls the Contracts tab makes, Pursuers from the same count the Ranks tab uses.
    """
    _solo()
    job = _job('archivist', 'Archivist')
    _contract('One', [job])
    _xp(job, 'SomeHunter', 500)

    body = client.get(reverse('job_detail', args=['archivist'])).content.decode()
    head = body[body.index('pp-bhead jobd__head'):body.index('jobd-switch')]

    # One count, not a sum: `class="scard"` is a PREFIX of `class="scard" style`, so adding the two
    # double-counts the accented cell.
    assert head.count('class="scard"') == 3, 'the header is not the three-stat row'
    for label in ('Contracts', 'Total XP', 'Pursuers'):
        assert f'>{label}</div>' in head, f'the header is missing its {label} stat'
    # The XP figure is the one the catalogue tile promised, so the two pages cannot quote different totals.
    assert str(CONTRACT_XP_TOTAL) in head or f'{CONTRACT_XP_TOTAL:,}' in head


def test_the_header_tells_a_linked_viewer_what_the_job_means_to_them(client):
    """The standing block was the RANK alone, in a bare pill -- it answered "where am I" and nothing about
    the thing the page is about. Level and banked XP are what a job means to a hunter; the rank is the
    competitive footnote, which is why it is now the small figure at the end of the head rather than the
    headline."""
    from trophies.models import ProfileJobXP

    _solo()
    job = _job('archivist', 'Archivist')
    profile = ProfileFactory(is_linked=True)
    ProfileJobXP.objects.create(profile=profile, job=job, total_xp=2500, level=2, is_linked=True)
    client.force_login(profile.user)

    body = client.get(reverse('job_detail', args=['archivist'])).content.decode()
    mine = body[body.index('jobd__mine'):body.index('jobd-switch')]

    assert 'Your standing' in mine
    assert '>2</b>' in mine and 'level' in mine, 'the block does not show the viewer their level'
    assert '2,500' in mine, 'the block does not show what they have banked'
    assert 'to next level' in mine


def test_an_unranked_linked_viewer_still_gets_the_block(client):
    """`{% if my_rank %}` alone made it vanish for a hunter who has banked nothing in this job -- exactly
    the reader most in need of being told where they stand. Level 1 is the floor, not a blank."""
    _solo()
    _job('archivist', 'Archivist')
    profile = ProfileFactory(is_linked=True)
    client.force_login(profile.user)

    body = client.get(reverse('job_detail', args=['archivist'])).content.decode()
    assert 'Your standing' in body and 'Not ranked yet' in body
    mine = body[body.index('jobd__mine'):body.index('jobd-switch')]
    assert '>1</b>' in mine


def test_the_standing_block_is_absent_for_anon(client):
    """The page is public and cacheable precisely because nothing else on it reads the viewer."""
    _solo()
    _job('archivist', 'Archivist')
    assert 'Your standing' not in client.get(reverse('job_detail', args=['archivist'])).content.decode()


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
