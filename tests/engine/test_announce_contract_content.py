"""What the announcement actually SAYS: the cover art, the colours, and the per-job counts.

Split from `test_announce_contracts.py`, which covers announceability, idempotency and the command
surface. These three areas were the audit's findings, and they share a shape: each is the visible
substance of the redesign, and each was mutation-dead.

  - `cover_url_for` could be replaced with `return ''` and every test stayed green, because the only
    art assertion used a contract with no member game at all -- comparing '' to '' whatever the
    function did. Nothing read `embeds[0]['image']`.
  - The colour test compared a SET of colours to a SET of colours, which cannot express the pairing
    that is the entire claim ("the colour IS the label"). Reversing the mapping passed.
  - Every "N new contracts" assertion was against the LEAD embed, so the per-job counts -- the whole
    content of every discipline block -- were asserted nowhere.
"""
import math

import pytest
from django.utils import timezone

from core.services import contract_announcer
from trophies.models import Contract, Job

pytestmark = pytest.mark.django_db


def _contract(name, *, jobs=None, days_ago=0):
    c = Contract.objects.create(name=name, slug=name.lower().replace(' ', '-'),
                                igdb_id=abs(hash(name)) % 9_000_000 + 1_000_000, is_live=True)
    c.jobs.set(jobs or list(Job.objects.exclude(is_fallback=True)[:1]))
    Contract.objects.filter(pk=c.pk).update(
        went_live_at=timezone.now() - timezone.timedelta(days=days_ago))
    c.refresh_from_db()
    return c


def _attach_game(contract, *, played_count=0, cover=False, status='accepted'):
    """A real member game: an ANCHORED concept whose TRUSTED IGDB match carries the contract's
    igdb_id. That is the whole membership rule -- there is no join table."""
    from tests.factories import ConceptFactory, GameFactory, IGDBMatchFactory

    concept = ConceptFactory(anchor_migration_completed_at=timezone.now())
    IGDBMatchFactory(concept=concept, igdb_id=contract.igdb_id, status=status,
                     **({'igdb_cover_image_id': 'co1abc'} if cover else {}))
    return GameFactory(concept=concept, played_count=played_count)


# ── cover art ────────────────────────────────────────────────────────────────────────────────────

def test_the_lead_image_comes_from_a_headliner_that_HAS_art():
    """The rule this feature rests on: the image comes from the first headliner WITH art, not simply
    the first headliner. Both are still named -- art is not a condition of being one."""
    top = _contract('Most Played No Art')
    second = _contract('Less Played Has Art')
    _attach_game(top, played_count=900)
    _attach_game(second, played_count=5, cover=True)

    lead = contract_announcer.build_announcement([top, second])['embeds'][0]

    assert 'Most Played No Art' in lead['description']
    assert 'Less Played Has Art' in lead['description']
    assert 'image' in lead, 'a headliner had art and the embed carries none'
    assert lead['image']['url'].startswith('http'), (
        'Discord fetches this itself and cannot resolve a relative path'
    )


def test_no_art_anywhere_means_no_image_rather_than_a_broken_one():
    """An `image` whose url is empty renders as a broken embed, which is worse than no image."""
    c = _contract('Nothing To Show')
    _attach_game(c, played_count=10)

    lead = contract_announcer.build_announcement([c])['embeds'][0]

    assert 'image' not in lead


def test_the_cover_comes_from_a_MEMBER_game_only():
    """Membership is the anchored + TRUSTED-match rule, not "any game at this igdb id". Dropping the
    gate would post art from a match a curator has explicitly rejected."""
    from tests.factories import ConceptFactory, GameFactory, IGDBMatchFactory

    c = _contract('Guarded')
    # Art reachable WITHOUT the IGDB branch, so removing the gate genuinely changes the answer. A
    # rejected match alone proves nothing: `display_image_url` already skips an untrusted IGDB cover,
    # so the fallback chain returned '' either way and the assertion held against a gateless build.
    concept = ConceptFactory(anchor_migration_completed_at=timezone.now())
    IGDBMatchFactory(concept=concept, igdb_id=c.igdb_id, status='rejected')
    GameFactory(concept=concept, played_count=999,
                title_icon_url='https://cdn.example/not-a-member.png')

    assert contract_announcer.cover_url_for(c) == '', 'art from a non-member game was used'


def test_the_cover_follows_the_most_played_member():
    """Same ordering as the ranking itself, so the art always belongs to the game that earned the
    slot rather than to whichever member happened to be first."""
    c = _contract('Two Members')
    _attach_game(c, played_count=5, cover=True)
    big = _attach_game(c, played_count=900, cover=True)

    url = contract_announcer.cover_url_for(c)

    assert url, 'no cover was produced at all'
    assert url == (big.display_image_url or ''), 'the art came from the less-played member'


# ── the colours ──────────────────────────────────────────────────────────────────────────────────

def test_each_block_carries_ITS_OWN_discipline_colour():
    """Set equality lost the pairing, which is the entire claim. Reversing the colour-to-block
    mapping -- Combat shipping green, Exploration red -- produced an identical set and passed."""
    by_disc = {}
    for job in Job.objects.exclude(is_fallback=True):
        by_disc.setdefault(job.discipline, job)
    picked = list(by_disc.items())[:3]
    assert len(picked) >= 2, 'need several disciplines to prove a pairing'
    wave = [_contract('Work For ' + disc, jobs=[job]) for disc, job in picked]

    payload = contract_announcer.build_announcement(wave)
    labels = dict(Job.DISCIPLINES)
    blocks = {b['title']: b['color'] for b in payload['embeds'][1:]}

    for disc, _job in picked:
        assert blocks[labels[disc]] == contract_announcer.DISCIPLINE_COLORS[disc][0], (
            disc + ' shipped in another discipline colour'
        )


def test_the_integers_really_are_the_conversion_of_the_oklch_beside_them():
    """Its sibling compares the oklch STRING to the stylesheet -- but production reads the INTEGER,
    and nothing checked the two halves of each pair agree.

    That is the likelier mistake of the two: not "a designer retuned the site" but "somebody
    reconverted and fat-fingered a hex". The conversion is reimplemented here so the pair is verified
    rather than asserted by a comment.
    """
    def oklch_to_rgb(L, C, h_deg):
        h = math.radians(h_deg)
        a, b = C * math.cos(h), C * math.sin(h)
        l_ = L + 0.3963377774 * a + 0.2158037573 * b
        m_ = L - 0.1055613458 * a - 0.0638541728 * b
        s_ = L - 0.0894841775 * a - 1.2914855480 * b
        l, m, s = l_ ** 3, m_ ** 3, s_ ** 3
        lin = (4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s,
               -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s,
               -0.0041960863 * l - 0.7034186147 * m + 1.7076147010 * s)
        out = []
        for u in lin:
            u = max(0.0, min(1.0, u))
            u = 1.055 * (u ** (1 / 2.4)) - 0.055 if u > 0.0031308 else 12.92 * u
            out.append(round(max(0.0, min(1.0, u)) * 255))
        return out

    for disc, (value, oklch) in contract_announcer.DISCIPLINE_COLORS.items():
        nums = oklch.replace('oklch(', '').replace(')', '').split()
        r, g, b = oklch_to_rgb(float(nums[0]), float(nums[1]), float(nums[2]))
        expected = (r << 16) | (g << 8) | b
        # One unit per channel: the sRGB encode rounding is the only place two correct
        # implementations may legitimately disagree.
        for shift in (16, 8, 0):
            got_c = (value >> shift) & 0xFF
            want_c = (expected >> shift) & 0xFF
            assert abs(got_c - want_c) <= 1, (
                '%s: %#08x is not the conversion of %s (expected about %#08x)'
                % (disc, value, oklch, expected)
            )


# ── what a discipline block actually says ────────────────────────────────────────────────────────

def _block_text(payload):
    return chr(10).join(b['description'] for b in payload['embeds'][1:])


def test_a_block_states_how_many_contracts_each_job_gained():
    """The count IS the content of every block, and nothing asserted it -- every "new contract"
    assertion in the sibling file is against the LEAD. So dropping the count from the job line,
    hardcoding it to 1, or breaking the plural all shipped green, leaving the blocks a bare list of
    job names: the one thing the redesign exists to say, gone."""
    job = Job.objects.exclude(is_fallback=True).first()
    wave = [_contract('First For This Job', jobs=[job]),
            _contract('Second For This Job', jobs=[job])]

    text = _block_text(contract_announcer.build_announcement(wave))

    assert '**' + job.name + '** — 2 new contracts' in text, 'block said: ' + repr(text)


def test_a_single_contract_reads_as_singular_in_its_block():
    job = Job.objects.exclude(is_fallback=True).first()

    text = _block_text(contract_announcer.build_announcement([_contract('Only One', jobs=[job])]))

    assert '1 new contract' in text and '1 new contracts' not in text


def test_jobs_within_a_block_are_ordered_by_how_much_work_they_gained():
    """Busiest job first, then alphabetical. Unasserted before, so any reordering shipped silently."""
    jobs = list(Job.objects.exclude(is_fallback=True).filter(
        discipline=Job.objects.exclude(is_fallback=True).first().discipline)[:2])
    assert len(jobs) == 2
    quiet, busy = jobs
    _contract('Only Work For Quiet', jobs=[quiet])
    for i in range(3):
        _contract('Work %d For Busy' % i, jobs=[busy])

    text = _block_text(contract_announcer.build_announcement(list(Contract.objects.all())))

    assert text.index(busy.name) < text.index(quiet.name), 'the busier job is not listed first'


def test_an_unknown_discipline_cannot_strand_the_wave():
    """`Job.discipline` is a CharField(choices=...), and choices are NOT a database constraint -- a
    value from a data migration, a fixture or raw SQL is storable. It used to reach a hard subscript
    into DISCIPLINE_COLORS, and a KeyError there means the wave is never stamped and then fails
    identically every night until a human intervenes."""
    job = Job.objects.exclude(is_fallback=True).first()
    Job.objects.filter(pk=job.pk).update(discipline='mystery')
    c = _contract('Under A Strange Banner', jobs=[job])
    c.refresh_from_db()

    payload = contract_announcer.build_announcement([c])

    assert payload is not None, 'the wave was stranded by an unrecognised discipline'
    assert '1 new contract' in payload['embeds'][0]['description']
