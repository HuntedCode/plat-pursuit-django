"""The announcement's SIZE discipline, tested where it can actually be reached.

Every size assertion in `test_announce_contracts.py` is structurally incapable of failing, and that is
not a slip anyone can fix there: `Contract.name` is `max_length=255` and the lead names at most
`MAX_HIGHLIGHTS` = 3 of them, so the biggest lead any real wave can produce is roughly 1,700
characters against a 4,096 cap. A discipline block is capped at five job lines (the catalogue has
exactly five jobs per discipline), so the whole post tops out near 4,900 against a 6,000 total. The
integration fixtures cannot approach either limit, which means `_capped`, `_embed_len` and the
block-dropping `break` were all reachable only by reading them.

That matters more here than it usually would. Discord answers 400 to an oversized payload,
`announce_contracts` treats a non-2xx as fatal and does NOT stamp `announced_at`, so a deterministic
overrun is not a bad post -- it is a wave that fails identically every night until a human intervenes.
The fail-closed retry that makes a transient error safe is exactly what makes a deterministic one
permanent.

So these are unit tests against the size machinery directly, with inputs chosen to cross the limits.
"""
import pytest

from core.services import contract_announcer as A


# ── _embed_len: what Discord actually counts ─────────────────────────────────────────────────────

def test_embed_len_counts_every_field_discord_counts():
    """Title + description + footer + author + each field's name and value.

    The embeds built today carry no author and no fields, so those terms are always zero -- they are
    counted anyway because a function that is correct only for today's shapes is a trap. Adding one
    `fields` entry to a discipline block is the obvious next iteration, and an under-counting
    `_embed_len` would silently reopen the 6000 breach at exactly that moment.
    """
    embed = {
        'title': 'x' * 10,
        'description': 'y' * 100,
        'footer': {'text': 'z' * 5},
        'author': {'name': 'a' * 7},
        'fields': [{'name': 'n' * 3, 'value': 'v' * 4}, {'name': 'm' * 2, 'value': 'w' * 6}],
    }
    assert A._embed_len(embed) == 10 + 100 + 5 + 7 + (3 + 4) + (2 + 6)


def test_embed_len_ignores_what_discord_does_not_count():
    """`image.url` and `color` are not charged against the total. Counting them would make the budget
    needlessly tight for no protection."""
    plain = {'title': 'T', 'description': 'D'}
    decorated = dict(plain, color=0x123456, image={'url': 'https://cdn.example/' + 'q' * 400})
    assert A._embed_len(decorated) == A._embed_len(plain)


def test_embed_len_survives_a_bare_embed():
    assert A._embed_len({}) == 0


# ── _capped: the per-description limit ───────────────────────────────────────────────────────────

def test_a_description_over_the_cap_is_replaced_by_its_fallback():
    """The lead's fallback keeps the header and the link -- the two things that make the post worth
    sending at all -- rather than truncating mid-sentence."""
    fallback = 'the short version'
    out = A._capped('x' * (A.DISCORD_DESCRIPTION_LIMIT + 1), fallback=fallback)
    assert out == fallback


def test_a_description_over_the_cap_with_no_fallback_is_truncated_legally():
    """Blocks have no sensible short form, so they truncate. The result must still be legal: the limit
    is inclusive, so exactly the limit is fine and one over is not."""
    out = A._capped('x' * (A.DISCORD_DESCRIPTION_LIMIT + 500))
    assert len(out) <= A.DISCORD_DESCRIPTION_LIMIT
    assert out.endswith('…'), 'truncation should be visible, not silent'


def test_a_description_at_exactly_the_cap_is_left_alone():
    """Off-by-one in the safe direction is a post that loses content for no reason."""
    exact = 'x' * A.DISCORD_DESCRIPTION_LIMIT
    assert A._capped(exact) == exact


def test_a_normal_description_is_untouched():
    assert A._capped('hello') == 'hello'


# ── the message total ────────────────────────────────────────────────────────────────────────────

def _fake_wave(n_contracts, *, jobs_per_contract, name_len=250, job_name_len=50):
    """Contracts and jobs at their model maximums, without a database.

    `build_announcement` only reads `.name`, `.went_live_at` and `.jobs.all()` off a contract, so
    plain objects are enough -- and they let this reach sizes the real catalogue cannot produce.
    """
    from django.utils import timezone

    class _Job:
        def __init__(self, name, discipline):
            self.name, self.discipline = name, discipline

    class _Contract:
        def __init__(self, name, jobs):
            self.name, self._jobs, self.pk = name, jobs, id(self)
            self.went_live_at = timezone.now()

        @property
        def jobs(self):
            return type('M', (), {'all': staticmethod(lambda: self._jobs)})()

    discs = list(A.DISCIPLINE_ORDER)
    made = []
    for i in range(n_contracts):
        jobs = [
            _Job(('J%02d' % j).ljust(job_name_len, 'q'), discs[(i + j) % len(discs)])
            for j in range(jobs_per_contract)
        ]
        made.append(_Contract(('C%03d' % i).ljust(name_len, 'w'), jobs))
    return made


@pytest.mark.django_db
def test_a_pathological_wave_still_fits_discords_message_total(monkeypatch):
    """The limit that did not exist before this redesign, crossed on purpose.

    Names at their 255-char maximum, every discipline populated, far more contracts than MAX_WAVE.
    `highlights` is stubbed because it is the one part that needs a database; everything measured here
    is the assembly.
    """
    wave = _fake_wave(200, jobs_per_contract=5)
    monkeypatch.setattr(A, 'highlights', lambda cs, limit=A.MAX_HIGHLIGHTS: [(c, '') for c in cs[:3]])

    payload = A.build_announcement(wave)
    embeds = payload['embeds']

    total = sum(A._embed_len(e) for e in embeds)
    assert total <= A.DISCORD_TOTAL_LIMIT, f'{total} chars would be a 400, and a 400 strands the wave'
    for embed in embeds:
        assert len(embed.get('description', '')) <= A.DISCORD_DESCRIPTION_LIMIT


@pytest.mark.django_db
def test_the_post_never_exceeds_discords_embed_count(monkeypatch):
    """Ten per message. Six is the structural maximum today (a lead plus five disciplines), but
    nothing in the code says ten, so nothing would catch a sixth discipline plus a second lead."""
    wave = _fake_wave(60, jobs_per_contract=5)
    monkeypatch.setattr(A, 'highlights', lambda cs, limit=A.MAX_HIGHLIGHTS: [(c, '') for c in cs[:3]])

    embeds = A.build_announcement(wave)['embeds']

    assert len(embeds) <= 10
    assert len(embeds) == 1 + len(A.DISCIPLINE_ORDER), 'every discipline should be represented here'


@pytest.mark.django_db
def test_an_oversized_post_drops_whole_blocks_rather_than_overrunning(monkeypatch):
    """The degradation path, forced by shrinking the budget rather than by inventing impossible data.

    Whatever the limits are, the rule has to hold: drop blocks, never exceed. Reaching this branch
    with real data would need a catalogue far larger than the one that exists, which is precisely why
    it needs a test that does not depend on the catalogue.
    """
    wave = _fake_wave(40, jobs_per_contract=5)
    monkeypatch.setattr(A, 'highlights', lambda cs, limit=A.MAX_HIGHLIGHTS: [(c, '') for c in cs[:3]])
    monkeypatch.setattr(A, '_TOTAL_BUDGET', 1200)          # tight enough to bite

    embeds = A.build_announcement(wave)['embeds']

    assert sum(A._embed_len(e) for e in embeds) <= 1200
    assert len(embeds) >= 1, 'the lead survives: the count and the link are why the post is sent'
    assert len(embeds) < 1 + len(A.DISCIPLINE_ORDER), 'nothing was actually dropped, so this proves nothing'
    assert 'See them on your board' in embeds[0]['description']
