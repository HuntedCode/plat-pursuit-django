"""`announce_contracts`: the Discord post when a publishing wave lands.

The question this lane opened was "how does this work with games that need admin review?" The
answer is structural rather than a special case: a staged or review-queued candidate is
`is_live=False`, so it has no `went_live_at`, so it can never reach the announcer. Publishing is
the only act that makes a contract announceable.
"""
import io

import pytest
from django.conf import settings
from django.core.management import call_command
from django.core.management.base import CommandError
from django.utils import timezone

from core.management.commands.announce_contracts import MAX_WAVE
from core.services import contract_announcer
from core.services.contract_announcer import DISCORD_DESCRIPTION_LIMIT, MAX_JOB_LINES
from tests.factories import ProfileFactory
from trophies.models import Contract, Job

pytestmark = pytest.mark.django_db


@pytest.fixture
def posted(monkeypatch):
    """Capture the webhook POST instead of making one. Returns the list of (url, payload)."""
    calls = []

    class _Resp:
        status_code = 204
        text = ''

    def _fake_post(url, json=None, **kw):
        calls.append((url, json))
        return _Resp()

    monkeypatch.setattr('trophies.discord_utils.discord_notifications.requests.post', _fake_post)
    return calls


def _contract(name, *, live=True, jobs=None, days_ago=0, announced=False):
    c = Contract.objects.create(name=name, slug=name.lower().replace(' ', '-'),
                                igdb_id=abs(hash(name)) % 9_000_000 + 1_000_000, is_live=live)
    c.jobs.set(jobs or list(Job.objects.exclude(is_fallback=True)[:1]))
    stamp = timezone.now() - timezone.timedelta(days=days_ago) if live else None
    Contract.objects.filter(pk=c.pk).update(
        went_live_at=stamp, announced_at=timezone.now() if announced else None)
    c.refresh_from_db()
    return c


def _attach_game(contract, *, played_count=0, cover=False):
    """Give `contract` a real member game: an ANCHORED concept whose TRUSTED IGDB match carries the
    contract's igdb_id. That is the whole membership rule -- there is no join table, so a game becomes
    a member purely by matching the raw id (see contracts_service._member_gate).
    """
    from django.utils import timezone as _tz

    from tests.factories import ConceptFactory, GameFactory, IGDBMatchFactory

    concept = ConceptFactory(anchor_migration_completed_at=_tz.now())
    IGDBMatchFactory(concept=concept, igdb_id=contract.igdb_id, status='accepted',
                     **({'igdb_cover_image_id': 'co1abc'} if cover else {}))
    return GameFactory(concept=concept, played_count=played_count)


def _post_text(payload):
    """Every embed's title + description, joined. The post became a lead embed plus one per
    discipline, so "is this in the post" is no longer "is this in embeds[0]"."""
    nl = chr(10)
    parts = [e.get("title", "") + nl + e.get("description", "") for e in payload["embeds"]]
    return nl.join(parts)


def _lead(payload):
    """The lead embed: the count, the headliners and the board link."""
    return payload['embeds'][0]


def _blocks(payload):
    """The per-discipline embeds, in the order they were built."""
    return payload['embeds'][1:]


def _run(**kw):
    out = io.StringIO()
    call_command('announce_contracts', stdout=out, **kw)
    return out.getvalue()


# ── what is announceable ─────────────────────────────────────────────────────────────────────────

def test_a_staged_contract_is_never_announced(posted):
    """THE admin-review question. A candidate awaiting review is is_live=False, so it has no
    went_live_at and cannot reach the announcer -- no special case needed."""
    _contract('Needs Review', live=False)

    out = _run()

    assert posted == [], 'a staged contract was announced'
    assert 'No new contracts' in out


def test_the_launch_set_is_not_announced(posted):
    """The ~1,000 badge-derived contracts carry went_live_at = NULL by decision. The first run
    after the cutover must say nothing rather than dumping the catalogue into the channel."""
    c = _contract('Launch Era')
    Contract.objects.filter(pk=c.pk).update(went_live_at=None)

    _run()

    assert posted == []


def test_a_newly_published_contract_is_announced_and_stamped(posted):
    c = _contract('Fresh Drop')

    _run()

    assert len(posted) == 1
    c.refresh_from_db()
    assert c.announced_at is not None
    assert 'Fresh Drop' in posted[0][1]['embeds'][0]['description']


def test_a_second_run_says_nothing(posted):
    """Idempotency is what lets this sit on a schedule beside a bursty pipeline."""
    _contract('Fresh Drop')
    _run()
    posted.clear()

    out = _run()

    assert posted == [] and 'No new contracts' in out


def test_a_failed_post_leaves_the_wave_pending(monkeypatch):
    """announced_at is stamped only after a confirmed 2xx. A wave swallowed by a failed POST is
    a wave the community never hears about, with nothing in the DB to show what went missing."""
    c = _contract('Fresh Drop')

    class _Resp:
        status_code = 500
        text = 'boom'

    monkeypatch.setattr('trophies.discord_utils.discord_notifications.requests.post',
                        lambda *a, **k: _Resp())

    with pytest.raises(CommandError):
        _run()

    c.refresh_from_db()
    assert c.announced_at is None, 'a failed post consumed the wave'


# ── the payload ──────────────────────────────────────────────────────────────────────────────────

def test_nothing_new_builds_no_payload():
    """Returning None rather than an empty post: a channel that gets a '0 new contracts' message
    every day is a channel people mute."""
    assert contract_announcer.build_announcement([]) is None


def test_every_job_that_gained_work_is_named(posted):
    """Renamed from `..._grouped_by_job`, because the grouping moved: jobs are now listed inside their
    DISCIPLINE's own embed rather than in one flat description. What has to stay true is that a hunter
    following a job can see it gained work."""
    jobs = list(Job.objects.exclude(is_fallback=True)[:2])
    _contract('Gun Game', jobs=[jobs[0]])
    _contract('Other Game', jobs=[jobs[1]])

    _run()
    payload = posted[0][1]

    text = _post_text(payload)
    assert jobs[0].name in text and jobs[1].name in text
    assert '2 new contracts' in _lead(payload)['description'], 'the header carries the wave total'


def test_each_discipline_gets_its_own_tinted_embed(posted):
    """The whole reason for the shape. An embed carries one colour and cannot tint lines, so the
    discipline IS the embed -- which is what lets the colour do the labelling."""
    from core.services.contract_announcer import DISCIPLINE_COLORS

    jobs = {}
    for job in Job.objects.exclude(is_fallback=True):
        jobs.setdefault(job.discipline, job)
    picked = list(jobs.items())[:2]
    assert len(picked) == 2, 'need two disciplines to prove the split'
    for disc, job in picked:
        _contract(f'For {disc}', jobs=[job])

    _run()
    blocks = _blocks(posted[0][1])

    assert len(blocks) == 2, 'one embed per discipline that gained work'
    colours = {b['color'] for b in blocks}
    assert colours == {DISCIPLINE_COLORS[d][0] for d, _j in picked}


def test_the_disciplines_appear_in_canonical_order_every_time(posted):
    """A recurring post whose sections reshuffle by size teaches nobody where to look. Canonical
    order means Combat is always where Combat was."""
    from core.services.contract_announcer import DISCIPLINE_ORDER

    by_disc = {}
    for job in Job.objects.exclude(is_fallback=True):
        by_disc.setdefault(job.discipline, job)
    # Give the LAST canonical discipline the biggest group, so size-ordering would put it first.
    order = [d for d in DISCIPLINE_ORDER if d in by_disc]
    assert len(order) >= 2
    first, last = order[0], order[-1]
    # The LAST canonical discipline must be BIGGER by the measure a size-sort would use -- which is
    # the number of JOBS in the block, not the number of contracts. The first version of this fixture
    # gave each discipline one job and differing contract counts, so both blocks were size 1, the sort
    # tied, stable ordering preserved canonical order, and the test passed against a size-ordered
    # build. Caught by mutation.
    last_jobs = list(Job.objects.filter(discipline=last).exclude(is_fallback=True)[:3])
    assert len(last_jobs) >= 2, 'need several jobs in one discipline to outweigh the other'
    _contract('One For First', jobs=[by_disc[first]])
    for i, job in enumerate(last_jobs):
        _contract(f'Many For Last {i}', jobs=[job])

    _run()
    titles = [b['title'] for b in _blocks(posted[0][1])]

    labels = dict(Job.DISCIPLINES)
    assert titles.index(labels[first]) < titles.index(labels[last]), (
        'blocks are ordered by size, so the post reshuffles between waves'
    )


def test_a_multi_job_contract_is_counted_under_each_of_its_jobs(posted):
    """That IS the fact being reported -- one game levelling several jobs. Picking a "primary" job
    would invent a hierarchy the model does not have.

    It is COUNTED under each now rather than NAMED under each: the post lists titles only in the
    headline section and counts them per job below. The consequence is that the per-job counts sum to
    more than the wave total, which is why the header carries the authoritative number.
    """
    jobs = list(Job.objects.exclude(is_fallback=True)[:2])
    _contract('Does Both', jobs=jobs)

    _run()
    payload = posted[0][1]

    for job in jobs:
        assert job.name in _post_text(payload), f'{job.name} was not told it gained work'
    lead = _lead(payload)['description']
    assert '1 new contract' in lead and '1 new contracts' not in lead
    assert 'Does Both' in lead, 'the only contract in the wave should be a headliner'


def test_the_header_total_is_the_wave_not_the_sum_of_the_blocks(posted):
    """One contract feeding two jobs in two disciplines produces two blocks each saying "1 new
    contract" -- summing to two, for a wave of one. The header is the number that is true."""
    by_disc = {}
    for job in Job.objects.exclude(is_fallback=True):
        by_disc.setdefault(job.discipline, job)
    two = list(by_disc.values())[:2]
    assert len(two) == 2
    _contract('Spans Two Disciplines', jobs=two)

    _run()
    payload = posted[0][1]

    assert '1 new contract' in _lead(payload)['description']
    assert len(_blocks(payload)) == 2, 'the contract should appear under both disciplines'


def test_a_large_wave_summarises_rather_than_listing_everything(posted):
    """A wall of titles is unreadable well before Discord's cap."""
    job = Job.objects.exclude(is_fallback=True).first()
    for i in range(20):
        _contract('Title Number %02d' % i, jobs=[job])

    _run(force=True)

    lead = _lead(posted[0][1])['description']
    # Three headliners are named; the other seventeen are counted. The old assertion looked for
    # "and 14 more", which came from a per-job title cap that no longer exists.
    assert 'and 17 more' in lead, 'a 20-contract wave should name a few and count the rest'


#: A real PlayStation title, not 'Title Number 07'. The size test used to use 15-char names, which
#: could not approach the 4096 cap from any direction -- its assertion could not fail.
_LONG_TITLE = "Marvel's Spider-Man: Miles Morales Ultimate Edition Remastered"


def _long_wave(count):
    jobs = list(Job.objects.exclude(is_fallback=True)[:MAX_JOB_LINES + 2])
    for i in range(count):
        picked = jobs[i % len(jobs):][:3] or jobs[:3]
        _contract('%s %02d' % (_LONG_TITLE, i), jobs=picked)


def test_realistic_titles_cannot_overrun_discords_cap(posted):
    """The failure this guards is not cosmetic. Over 4096 Discord answers 400, the command raises,
    the wave is never stamped, and the IDENTICAL wave fails identically every night until someone
    runs --limit by hand. Fail-closed retry is what makes a transient error safe and a
    deterministic one permanent, so the deterministic one must not be reachable."""
    _long_wave(MAX_WAVE)

    _run(force=True)

    desc = posted[0][1]['embeds'][0]['description']
    assert len(desc) <= DISCORD_DESCRIPTION_LIMIT, f'{len(desc)} chars would be a 400 from Discord'
    assert 'See them on your board' in desc, 'the CTA must survive the trim'


def test_a_trimmed_wave_still_reads_as_a_wave(posted):
    """Trimming must degrade the post, not gut it -- the headline count and the link are what make
    it worth posting at all, and the jobs that did not fit are counted rather than dropped."""
    _long_wave(MAX_WAVE)

    _run(force=True)

    payload = posted[0][1]
    # The count and the link live in the LEAD; the "and N more jobs" tail lives inside whichever
    # discipline block ran out of room. Two embeds, two assertions.
    assert '%d new contracts' % MAX_WAVE in _lead(payload)['description']
    assert 'See them on your board' in _lead(payload)['description']
    # NOT an "and N more jobs" assertion any more. That tail came from a cap applied across the whole
    # post; per discipline it is unreachable, because the catalogue has exactly five jobs in each
    # (migration 0247). What trimming still means here is that a 40-contract wave degrades to a
    # readable post: three named headliners, the rest counted, and every block legal.
    assert 'and 37 more' in _lead(payload)['description'], 'the unnamed contracts were dropped'
    for embed in payload['embeds']:
        assert len(embed.get('description', '')) <= DISCORD_DESCRIPTION_LIMIT


def test_a_curator_typed_link_does_not_become_a_live_link_in_the_channel(posted):
    """Contract names are curator-authored free text going into a markdown description."""
    _contract('Free Robux](http://evil.test)')

    _run()

    desc = posted[0][1]['embeds'][0]['description']
    assert '](http://evil.test)' not in desc
    assert 'Free Robux' in desc, 'escaping must not eat the name'


def test_markdown_in_a_real_title_is_shown_not_interpreted(posted):
    """A game legitimately titled with asterisks or underscores should read as its own title."""
    _contract('Sam & Max: *Beyond* Time_and_Space')

    _run()

    desc = posted[0][1]['embeds'][0]['description']
    assert r'\*Beyond\*' in desc
    assert r'Time\_and\_Space' in desc


def test_the_link_lands_on_the_board_with_latest_applied(posted):
    """The post's job is to get a reader to the contracts. Dropping them on an unfiltered board
    makes them hunt for what the post just told them about."""
    _contract('Fresh Drop')

    _run()

    assert 'new=1' in posted[0][1]['embeds'][0]['description']


# ── the wave-size guard ──────────────────────────────────────────────────────────────────────────

def test_an_oversized_wave_is_refused(posted):
    """The cutover case: the seed creates ~1,000 contracts live at once, each stamped by save().
    Refusing is the only way this command can protest before the wall has been posted."""
    job = Job.objects.exclude(is_fallback=True).first()
    for i in range(MAX_WAVE + 1):
        _contract('Bulk %03d' % i, jobs=[job])

    with pytest.raises(CommandError, match='safety limit'):
        _run()

    assert posted == []
    assert not Contract.objects.filter(announced_at__isnull=False).exists()


def test_a_dry_run_can_inspect_an_oversized_wave(posted):
    """Inspecting the wave is how an operator decides between --baseline and --force, so the guard
    that refuses to POST one must not also refuse to SHOW it. A dry run posts nothing."""
    job = Job.objects.exclude(is_fallback=True).first()
    for i in range(MAX_WAVE + 3):
        _contract('Bulk %03d' % i, jobs=[job])

    out = _run(dry_run=True)

    assert posted == []
    assert 'DRY RUN' in out and str(MAX_WAVE + 3) in out
    assert not Contract.objects.filter(announced_at__isnull=False).exists()


def test_baseline_stamps_without_posting(posted):
    """The cutover step. It must work on a wave too big to POST, which is exactly the case it
    exists for -- so it cannot sit behind the size check."""
    job = Job.objects.exclude(is_fallback=True).first()
    for i in range(MAX_WAVE + 5):
        _contract('Bulk %03d' % i, jobs=[job])

    out = _run(baseline=True)

    assert posted == [], 'baseline must not post'
    assert 'Baselined' in out
    assert not contract_announcer.pending_contracts().exists()


def test_limit_trickles_the_oldest_first(posted):
    job = Job.objects.exclude(is_fallback=True).first()
    _contract('Published First', jobs=[job], days_ago=5)
    _contract('Published Second', jobs=[job], days_ago=1)

    _run(limit=1)

    desc = posted[0][1]['embeds'][0]['description']
    assert 'Published First' in desc and 'Published Second' not in desc
    assert contract_announcer.pending_contracts().count() == 1, 'the rest stay pending'


def test_a_wave_goes_to_the_contracts_channel_when_one_is_configured(posted, settings):
    """A wave is a different KIND of post to everything else on the platinum channel -- a catalogue
    notice rather than somebody's achievement -- and it lands daily, so it gets its own room."""
    settings.DISCORD_CONTRACTS_WEBHOOK_URL = 'https://example.test/contracts'
    settings.DISCORD_PLATINUM_WEBHOOK_URL = 'https://example.test/platinum'
    _contract('Announce Me')

    _run()

    assert [url for url, _ in posted] == ['https://example.test/contracts']


def test_an_unset_contracts_channel_falls_back_rather_than_failing(posted, settings):
    """This runs from a daily cron. A deploy that has not configured the new channel yet should keep
    announcing, not start erroring every morning at 06:00."""
    settings.DISCORD_CONTRACTS_WEBHOOK_URL = None
    settings.DISCORD_PLATINUM_WEBHOOK_URL = 'https://example.test/platinum'
    _contract('Announce Me')

    out = _run()

    assert [url for url, _ in posted] == ['https://example.test/platinum']
    # AND IT SAYS SO. "we made a contracts channel" and "the posts still go to the platinum channel"
    # look identical from here, so a silent fallback is the wrong kind of safe.
    assert 'DISCORD_CONTRACTS_WEBHOOK_URL is unset' in out


def test_the_channel_it_used_is_named_on_success(posted, settings):
    """The operator's only confirmation that the new env var took effect."""
    settings.DISCORD_CONTRACTS_WEBHOOK_URL = 'https://example.test/contracts'
    _contract('Announce Me')

    out = _run()

    assert 'the contracts channel' in out


def test_no_webhook_at_all_is_refused_before_posting(posted, settings):
    """`requests.post(None, ...)` raises a MissingSchema that post_webhook_sync redacts to "URL
    redacted" -- a true statement about a url that does not exist, and useless to debug from. It also
    must not stamp: a wave recorded as announced but posted nowhere is gone for good."""
    from django.core.management.base import CommandError

    settings.DISCORD_CONTRACTS_WEBHOOK_URL = None
    settings.DISCORD_PLATINUM_WEBHOOK_URL = None
    _contract('Announce Me')

    with pytest.raises(CommandError) as err:
        _run()

    assert 'DISCORD_CONTRACTS_WEBHOOK_URL' in str(err.value)
    assert not posted
    assert Contract.objects.filter(announced_at__isnull=False).count() == 0


def test_the_setting_actually_reads_the_environment(settings):
    """The `settings` fixture overrides the module value, so every test above passes even if
    settings.py never read the env var at all -- and a setting that is not wired is exactly the
    failure mode that left DISCORD_INVITE_URL dead in production. Pinned at the source."""
    from pathlib import Path

    from django.conf import settings as real

    src = (Path(real.BASE_DIR) / 'plat_pursuit' / 'settings.py').read_text(encoding='utf-8')
    assert "DISCORD_CONTRACTS_WEBHOOK_URL = os.getenv('DISCORD_CONTRACTS_WEBHOOK_URL')" in src


def test_the_test_webhook_still_wins_over_the_contracts_channel(posted, settings):
    """--test-webhook means "not the live channel", and gaining a second live channel must not turn
    it into "not the OLD live channel"."""
    settings.DISCORD_CONTRACTS_WEBHOOK_URL = 'https://example.test/contracts'
    settings.DISCORD_TEST_WEBHOOK_URL = 'https://example.test/preview'
    _contract('Announce Me')

    _run(test_webhook=True)

    assert [url for url, _ in posted] == ['https://example.test/preview']


def test_the_test_webhook_does_not_consume_the_wave(posted, settings):
    """A preview that stamped announced_at would mean the community never heard about the wave."""
    settings.DISCORD_TEST_WEBHOOK_URL = 'https://example.test/hook'
    c = _contract('Fresh Drop')

    _run(test_webhook=True)

    assert posted[0][0] == 'https://example.test/hook'
    c.refresh_from_db()
    assert c.announced_at is None


def test_dry_run_posts_nothing_and_stamps_nothing(posted):
    c = _contract('Fresh Drop')

    out = _run(dry_run=True)

    assert posted == []
    c.refresh_from_db()
    assert c.announced_at is None
    assert 'Fresh Drop' in out


def test_republishing_an_announced_contract_does_not_re_announce(posted):
    """went_live_at is stamped once and never reset, and announced_at rides beside it. A contract
    pulled back for a fix and re-published is not news."""
    c = _contract('Bounced')
    _run()
    posted.clear()

    c.refresh_from_db()   # the announcer stamped via .update(); a stale instance would write None back
    c.is_live = False
    c.save()
    c.is_live = True
    c.save()

    _run()
    assert posted == []


def test_the_lifecycle_stamps_are_not_typeable_in_the_admin():
    """Both columns mean "set once, never reset", and the change form is `fields = '__all__'`. Left
    writable, a curator opening the form to fix a typo posts back whatever the page rendered with --
    clearing the stamp and re-announcing a contract the community already heard about."""
    from django.contrib.admin.sites import AdminSite

    from trophies.admin import ContractAdmin

    readonly = ContractAdmin(Contract, AdminSite()).readonly_fields

    assert 'went_live_at' in readonly and 'announced_at' in readonly


def test_the_announcement_link_actually_opens_the_contracts_tab(client, posted):
    """The post's CTA is its whole point, and a wrong param does not look broken: Career renders
    the contracts panel correctly filtered but leaves it `hidden`, so the reader lands on Jobs.
    Walked end to end -- the URL is taken OUT of the payload and fetched -- because asserting
    'new=1' in the string is what let `?tab=` (job detail's param) ship."""
    import re
    from urllib.parse import urlparse

    profile = ProfileFactory(is_linked=True)
    client.force_login(profile.user)
    _contract('Fresh Drop')
    _run()

    href = re.search(r'\]\((https?://[^)]+)\)',
                     posted[0][1]['embeds'][0]['description']).group(1)
    parsed = urlparse(href)

    resp = client.get(parsed.path + '?' + parsed.query)

    assert resp.status_code == 200
    assert resp.context['active_view'] == 'contracts', (
        f"{href} does not open the Contracts tab")
    assert resp.context['new_window_days']


def test_the_deep_link_scrolls_the_board_into_view(client):
    """`new` is the one filter that arrives from an EXTERNAL link, so the reader has no idea the
    board is filtered unless the page takes them to it."""
    from core.services.contract_announcer import BOARD_URL

    profile = ProfileFactory(is_linked=True)
    client.force_login(profile.user)

    src = client.get(BOARD_URL).content.decode()

    guard = src.split('function scrollToFilteredBoard', 1)[1].split('return;', 2)[1]
    assert "qp.has('new')" in guard, 'a Latest deep link lands above the board it filtered'


def test_limit_zero_is_refused_rather_than_announcing_everything(posted):
    """`--limit 0` fell through a falsy check and posted the ENTIRE wave: the natural "do nothing"
    value doing the most destructive thing available."""
    for i in range(3):
        _contract('Wave %d' % i)

    with pytest.raises(CommandError, match='must be 1 or more'):
        _run(limit=0)

    assert posted == []
    assert contract_announcer.pending_contracts().count() == 3


def test_a_negative_limit_is_a_clean_refusal_not_a_traceback(posted):
    """It reached Django's slice and raised a raw ValueError, alone among this command's inputs."""
    _contract('Wave')

    with pytest.raises(CommandError, match='must be 1 or more'):
        _run(limit=-1)

    assert posted == []


def test_the_test_webhook_can_preview_an_oversized_wave(posted, settings):
    """Gating a read-only preview behind --force -- the flag that otherwise means "post this for
    real to the live channel" -- trains exactly the wrong reflex."""
    settings.DISCORD_TEST_WEBHOOK_URL = 'https://example.test/hook'
    _long_wave(MAX_WAVE + 3)

    _run(test_webhook=True)

    assert posted and posted[0][0] == 'https://example.test/hook'
    assert not Contract.objects.filter(announced_at__isnull=False).exists()


def test_baseline_does_not_materialise_the_wave_it_is_cleaning_up(posted):
    """--baseline is what you reach for AFTER a bulk accident, which is the worst moment to pull
    every pending row plus its prefetched jobs into memory. It needs no objects at all."""
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    _long_wave(MAX_WAVE + 5)

    with CaptureQueriesContext(connection) as ctx:
        _run(baseline=True)

    assert posted == []
    assert not contract_announcer.pending_contracts().exists()
    assert len(ctx.captured_queries) <= 3, (
        f"{len(ctx.captured_queries)} queries -- baseline should be an id read plus one UPDATE, "
        "not a prefetched materialisation")


def test_a_transport_failure_does_not_print_the_webhook_secret(monkeypatch):
    """requests embeds the full URL it was calling in connection/timeout errors ("Max retries
    exceeded with url: /api/webhooks/<id>/<token>"), so interpolating the exception verbatim put
    the webhook SECRET on stdout and into Render's job log on every transport failure."""
    import requests as _requests

    _contract('Fresh Drop')
    secret = 'https://discord.com/api/webhooks/123456789/SUPERSECRETTOKENVALUE'
    monkeypatch.setattr(settings, 'DISCORD_PLATINUM_WEBHOOK_URL', secret)

    def _boom(*a, **k):
        raise _requests.ConnectionError(f"Max retries exceeded with url: {secret}")

    monkeypatch.setattr('trophies.discord_utils.discord_notifications.requests.post', _boom)

    with pytest.raises(CommandError) as exc:
        _run()

    assert 'SUPERSECRETTOKENVALUE' not in str(exc.value)
    assert 'ConnectionError' in str(exc.value), 'the operator still needs to know what failed'


def test_an_aged_out_wave_links_to_the_unfiltered_board(posted):
    """`announced_at` and the Latest window answer different questions and share no floor. A
    webhook misconfigured for a fortnight, or a backlog trickled out with --limit, produces a
    legitimate post about contracts that have already aged out -- and filtering the board to Latest
    would land the reader on an EMPTY board, the one place the post promised its contents."""
    from trophies.util_modules.constants import NEW_CONTRACT_WINDOW_DAYS

    _contract('Long Delayed', days_ago=NEW_CONTRACT_WINDOW_DAYS + 4)

    _run()

    desc = posted[0][1]['embeds'][0]['description']
    assert 'new=1' not in desc, 'the link filters to a window this contract has already left'
    assert 'view=contracts' in desc, 'it should still land on the board'


def test_a_fresh_wave_still_gets_the_filtered_link(posted):
    """The widening is a fallback, not the default -- a normal wave should land pre-filtered."""
    _contract('Fresh Drop', days_ago=1)

    _run()

    assert 'new=1' in posted[0][1]['embeds'][0]['description']


def test_a_mixed_wave_takes_the_safe_link(posted):
    """One aged-out contract is enough: the link has to show the WHOLE wave it just described."""
    from trophies.util_modules.constants import NEW_CONTRACT_WINDOW_DAYS

    _contract('Fresh Drop', days_ago=1)
    _contract('Long Delayed', days_ago=NEW_CONTRACT_WINDOW_DAYS + 4)

    _run()

    assert 'new=1' not in posted[0][1]['embeds'][0]['description']


# ── the discipline colours ───────────────────────────────────────────────────────────────────────

def test_every_discipline_the_model_has_is_coloured():
    """A job whose discipline has no colour would raise a KeyError mid-build and strand the wave --
    the failure mode this module's whole budget discipline exists to avoid."""
    from core.services.contract_announcer import DISCIPLINE_COLORS, DISCIPLINE_ORDER

    model = [d for d, _label in Job.DISCIPLINES]
    assert set(DISCIPLINE_COLORS) == set(model), 'a discipline has no colour'
    assert list(DISCIPLINE_ORDER) == model, 'the post order drifted from the model order'


def test_the_colours_still_match_the_stylesheet():
    """The oklch source is recorded beside each Discord integer precisely so this can be checked.

    `elements.css` is where the site's discipline colours are decided; Discord cannot parse oklch, so
    the integers here are a CONVERSION. Without this test a designer retunes a hue on the site and the
    channel stays the old colour forever, with nothing anywhere disagreeing.
    """
    from pathlib import Path

    from django.conf import settings as dj_settings

    from core.services.contract_announcer import DISCIPLINE_COLORS

    css = (Path(dj_settings.BASE_DIR) / 'static' / 'css' / 'components' / 'elements.css').read_text(
        encoding='utf-8')
    for disc, (_value, oklch) in DISCIPLINE_COLORS.items():
        # The declaration as the stylesheet writes it, whitespace-insensitive on the separator only.
        needle = '--disc-' + disc + ':'
        assert needle in css, f'--disc-{disc} is gone from elements.css'
        declared = css.split(needle, 1)[1].split(';', 1)[0].strip()
        assert declared == oklch, (
            f'--disc-{disc} is {declared} in elements.css but the announcer converted {oklch}. '
            f'Reconvert it, or the Discord post is a different colour from the site.'
        )


# ── highlight selection ──────────────────────────────────────────────────────────────────────────

def test_headliners_are_ranked_by_how_many_hunters_played_them():
    """The owner's rule: lead with the games the most people here have actually played.

    `Game.played_count` is denormalized and indexed, and reached through the same correlated-subquery
    shape `annotated_contracts` uses -- contract membership is derived from the raw igdb id, so there
    is no join to make.
    """
    from core.services.contract_announcer import highlights

    small = _contract('Barely Played')
    big = _contract('Everyone Played This')
    _attach_game(small, played_count=3)
    _attach_game(big, played_count=900)

    picked = [c.name for c, _art in highlights([small, big])]

    assert picked[0] == 'Everyone Played This', f'ranked by reach, got {picked}'


def test_a_contract_with_no_cover_art_is_still_a_headliner():
    """The slot being filled is a TITLE LINE; the image is one separate thing on the embed. An
    earlier cut skipped art-less contracts and produced "5 new contracts ...and 5 more" -- a post
    naming nothing at all, strictly worse than the one it replaced."""
    from core.services.contract_announcer import highlights

    c = _contract('No Art At All')

    picked = highlights([c])

    assert [name for name, _art in [(x.name, a) for x, a in picked]] == ['No Art At All']
    assert picked[0][1] == '', 'no art was available, so the url should be empty, not invented'
