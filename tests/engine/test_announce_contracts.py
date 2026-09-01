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


def test_contracts_are_grouped_by_job(posted):
    jobs = list(Job.objects.exclude(is_fallback=True)[:2])
    _contract('Gun Game', jobs=[jobs[0]])
    _contract('Other Game', jobs=[jobs[1]])

    _run()

    desc = posted[0][1]['embeds'][0]['description']
    assert jobs[0].name in desc and jobs[1].name in desc
    assert '2 new contracts' in desc


def test_a_multi_job_contract_appears_under_each_of_its_jobs(posted):
    """That IS the fact being reported -- one game levelling several jobs. Picking a 'primary'
    job would invent a hierarchy the model does not have."""
    jobs = list(Job.objects.exclude(is_fallback=True)[:2])
    _contract('Does Both', jobs=jobs)

    _run()

    desc = posted[0][1]['embeds'][0]['description']
    assert desc.count('Does Both') == 2
    assert '1 new contract' in desc and '1 new contracts' not in desc


def test_a_large_wave_summarises_rather_than_listing_everything(posted):
    """A wall of titles is unreadable well before Discord's cap."""
    job = Job.objects.exclude(is_fallback=True).first()
    for i in range(20):
        _contract('Title Number %02d' % i, jobs=[job])

    _run(force=True)

    desc = posted[0][1]['embeds'][0]['description']
    assert 'and 14 more' in desc, 'a job with 20 titles should list a few and count the rest'


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

    desc = posted[0][1]['embeds'][0]['description']
    assert '%d new contracts' % MAX_WAVE in desc
    assert 'more job' in desc


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
