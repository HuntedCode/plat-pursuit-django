"""`announce_contracts`: the Discord post when a publishing wave lands.

The question this lane opened was "how does this work with games that need admin review?" The
answer is structural rather than a special case: a staged or review-queued candidate is
`is_live=False`, so it has no `went_live_at`, so it can never reach the announcer. Publishing is
the only act that makes a contract announceable.
"""
import io

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.utils import timezone

from core.management.commands.announce_contracts import MAX_WAVE
from core.services import contract_announcer
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

    monkeypatch.setattr('core.management.commands.announce_contracts.requests.post', _fake_post)
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

    monkeypatch.setattr('core.management.commands.announce_contracts.requests.post',
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
    """Discord hard-caps a description at 4096 characters, and a wall of titles is unreadable
    well before that."""
    job = Job.objects.exclude(is_fallback=True).first()
    for i in range(20):
        _contract('Title Number %02d' % i, jobs=[job])

    _run(force=True)

    desc = posted[0][1]['embeds'][0]['description']
    assert len(desc) < 4096
    assert 'and 14 more' in desc, 'a job with 20 titles should list a few and count the rest'


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
