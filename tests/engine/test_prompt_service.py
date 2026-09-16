"""The only thing that writes a prompt.

P0 put the shape of the data in the database and left four rules it could not hold. This file is where
those rules become code, so it leads with them rather than with the CRUD:

* **A poll has exactly one bucket, forever.** `unique(response, bucket) WHERE single_slot` allows one
  placement PER BUCKET, so "a hunter votes once" is a statement about bucket count. A second bucket
  buys a second vote with every flag set correctly and nothing in Postgres would notice.
* **Sub-resources resolve WITHIN their prompt.** Nothing ties a placement's three foreign keys to one
  prompt, so a bucket id from somebody else's prompt satisfies every constraint.
* **`shape` is immutable**, which the service holds by having no argument for it.
* **An answered prompt can be closed but not deleted or hidden**, because a response cannot be read
  without the structure it was placed into.

The rest is the house service contract: refuse before starting, lock before the value you protect,
counts move with the rows they count, positions stay dense.
"""
import inspect

import pytest

from prompts.models import (FREE_MAX_PROMPTS, MAX_BUCKETS_PER_PROMPT, MAX_GAMES_PER_PROMPT,
                            MIN_GAMES_TO_PUBLISH, SHAPE_GRID, SHAPE_POLL, SHAPE_TIER, SHAPES,
                            Prompt, PromptBucket, PromptGame, PromptPlacement, PromptResponse)
from prompts.services import prompt_service as svc
from tests.factories import ConceptFactory, ProfileFactory
from users.models import UserRestriction

pytestmark = pytest.mark.django_db


def _hunter(psn='hunter', premium=False):
    profile = ProfileFactory(is_linked=True, psn_username=psn)
    if premium:
        profile.user_is_premium = True
        profile.save(update_fields=['user_is_premium'])
    return profile


def _restrict(profile):
    UserRestriction.objects.create(user=profile.user, profile=profile, scope='all_ugc',
                                   reason='testing', created_by_label='Admin')


def _publishable(prompt, owner):
    """Give it whatever its shape needs to clear the publish floor."""
    if not prompt.buckets.exists():
        svc.create_bucket(prompt, owner, label='Best of them')
    need = MIN_GAMES_TO_PUBLISH[prompt.shape] - prompt.games.count()
    for _ in range(max(need, 0)):
        svc.add_concept(prompt, owner, ConceptFactory())
    return prompt


def _prompt(owner, shape=SHAPE_TIER, **kwargs):
    """Create one, and PUBLISH IT PROPERLY if the caller wants it public.

    `create_prompt` has no `is_public` argument any more: every shape has a floor to clear before it
    can go up, and a brand-new prompt clears none of them, so the parameter could only ever have meant
    "refuse this create". Publishing is the second act, here as in the product.
    """
    public = kwargs.pop('is_public', False)
    prompt = svc.create_prompt(owner, shape=shape, title=kwargs.pop('title', 'Rank them'), **kwargs)
    if public:
        _publishable(prompt, owner)
        svc.update_prompt(prompt, owner, is_public=True)
        prompt.refresh_from_db()
    return prompt


def _answer(prompt, hunter, game=None, bucket=None):
    """One response from somebody, placing one game if the prompt has any."""
    response = PromptResponse.objects.create(prompt=prompt, profile=hunter)
    if game is not None and bucket is not None:
        PromptPlacement.objects.create(response=response, prompt_game=game, bucket=bucket,
                                       single_slot=prompt.is_single_slot,
                                       no_duplicates=prompt.forbids_duplicates)
        response.placement_count = 1
        response.save(update_fields=['placement_count'])
    return response


# ── the rules the database could not hold ────────────────────────────────────────────────────────


def test_a_poll_is_born_with_exactly_one_bucket_and_can_never_get_another():
    """THE ONE-VOTE GUARANTEE, which is a statement about bucket count and not about flags.

    `unique(response, bucket) WHERE single_slot` permits one placement per bucket. Give a poll a
    second bucket and every hunter can place a second game with `single_slot=True` on both rows,
    colliding with nothing. The database cannot refuse it -- the count lives on the parent -- so this
    service is the only thing standing there."""
    owner = _hunter()
    poll = _prompt(owner, SHAPE_POLL, title='Best Souls game?')

    assert poll.buckets.count() == 1
    assert MAX_BUCKETS_PER_PROMPT[SHAPE_POLL] == 1

    with pytest.raises(svc.PromptError) as caught:
        svc.create_bucket(poll, owner, label='Second box')
    assert poll.buckets.count() == 1
    # THE MESSAGE, not merely the refusal. Two things refuse this -- the explicit poll branch and the
    # cap of 1 -- so "it raised" cannot tell which one ran, and either could be deleted with this test
    # still green. Naming the poll pins the branch written to say something true; the cap behind it
    # stays as the belt to that braces.
    assert 'poll' in str(caught.value).lower()


def test_the_last_row_cannot_be_deleted():
    """A prompt with no buckets cannot be answered at all, and on a poll the single bucket IS the
    question -- deleting it would take every vote with it by cascade and leave the poll standing."""
    owner = _hunter()
    poll = _prompt(owner, SHAPE_POLL, title='Which?')

    with pytest.raises(svc.PromptError):
        svc.delete_bucket(poll.buckets.get(), owner)

    tier = _prompt(owner, SHAPE_TIER, title='Rank')
    for bucket in list(tier.buckets.all())[1:]:
        svc.delete_bucket(bucket, owner)
    assert tier.buckets.count() == 1
    with pytest.raises(svc.PromptError):
        svc.delete_bucket(tier.buckets.get(), owner)


def test_a_sub_resource_from_another_prompt_does_not_resolve():
    """The cross-prompt gap the schema leaves open, closed where the model docstring says it is.

    A bucket from prompt B, used on prompt A, satisfies every constraint on the placement table and
    produces a row invisible to the editor but still counted. The lookups take the prompt and mean
    it."""
    owner = _hunter()
    mine = _prompt(owner, title='Mine')
    theirs = _prompt(_hunter('other'), title='Theirs')

    their_bucket = theirs.buckets.first()
    assert svc.get_bucket(mine, their_bucket.pk) is None
    assert svc.get_bucket(theirs, their_bucket.pk) == their_bucket

    their_game = svc.add_concept(theirs, theirs.owner, ConceptFactory())
    assert svc.get_game(mine, their_game.pk) is None

    # ...and the write path refuses it too, not merely the lookup.
    #
    # `remove_concept` is the one that proves the SCOPING: it takes the prompt and the game
    # separately, so a mismatched pair reaches `_lock_game`'s `prompt=locked_prompt` filter. That is
    # the shape that can be paired wrongly, and this is the assertion that holds it.
    with pytest.raises(svc.PromptError):
        svc.remove_concept(mine, owner, their_game)

    # `delete_bucket` is listed here only to say what it does NOT prove: it derives the prompt from
    # the bucket, so ownership refuses first and the cross-prompt filter is never reached. Covered as
    # ownership by `test_only_the_owner_writes`; kept here so nobody re-adds it as scoping coverage.
    with pytest.raises(svc.PromptError):
        svc.delete_bucket(their_bucket, owner)


def test_the_service_offers_no_way_to_change_a_shape():
    """Immutability held by ABSENCE, which is the only way it can be held: a tier list turned into a
    grid would leave buckets holding seven games under a one-game promise, and `single_slot` -- written
    at insert, on rows belonging to other people -- would lie everywhere at once.

    Asserted against the signature rather than by calling it, because "it raises" would be a weaker
    claim: an argument that exists can be made to work by somebody who does not read the comment."""
    assert 'shape' not in inspect.signature(svc.update_prompt).parameters


# ── an answered prompt ────────────────────────────────────────────────────────────────────────────


def test_an_answered_prompt_can_be_closed_but_not_deleted_or_hidden():
    """A response is an arrangement of THIS prompt's games into THIS prompt's buckets. Remove either
    and it is not a thing that can be rendered, so deleting an answered prompt does not remove one
    hunter's content -- it removes everybody's."""
    owner = _hunter()
    prompt = _prompt(owner, is_public=True)
    game = svc.add_concept(prompt, owner, ConceptFactory())
    _answer(prompt, _hunter('responder'), game, prompt.buckets.first())

    with pytest.raises(svc.PromptError):
        svc.delete_prompt(prompt, owner)
    with pytest.raises(svc.PromptError):
        svc.update_prompt(prompt, owner, is_public=False)

    prompt.refresh_from_db()
    assert prompt.is_deleted is False
    assert prompt.is_public is True, 'the refusal let the unpublish through anyway'

    # The exit that does exist, and it hides nothing.
    svc.set_closed(prompt, owner, closed=True)
    prompt.refresh_from_db()
    assert prompt.is_closed is True
    assert prompt.is_public is True
    assert PromptResponse.objects.filter(prompt=prompt).count() == 1


def test_answering_your_own_question_does_not_lock_you_out_of_deleting_it():
    """Making a tier list and filling it in yourself is a normal thing to do, and it must not be what
    strands the prompt. The rule is about OTHER people's work."""
    owner = _hunter()
    prompt = _prompt(owner, is_public=True)
    game = svc.add_concept(prompt, owner, ConceptFactory())
    _answer(prompt, owner, game, prompt.buckets.first())

    svc.delete_prompt(prompt, owner)
    prompt.refresh_from_db()
    assert prompt.is_deleted is True


def test_a_rename_and_an_unpublish_in_one_call_change_neither():
    """The refusal is checked before anything is written, so a POST carrying both does not land the
    half that is allowed. Otherwise the author sees an error and a renamed prompt."""
    owner = _hunter()
    prompt = _prompt(owner, is_public=True, title='Original')
    game = svc.add_concept(prompt, owner, ConceptFactory())
    _answer(prompt, _hunter('responder'), game, prompt.buckets.first())

    with pytest.raises(svc.PromptError):
        svc.update_prompt(prompt, owner, title='Renamed', is_public=False)

    prompt.refresh_from_db()
    assert prompt.title == 'Original'
    assert prompt.is_public is True


# ── what each shape starts with ───────────────────────────────────────────────────────────────────


def test_each_shape_starts_with_the_rows_it_needs():
    """A tier list arrives usable because the genre has one universal convention and a tier list with
    no rows is a form, not a tier list. A grid gets nothing on purpose -- its slots ARE the question,
    so a placeholder would either be wrong or would ship as "Slot 1"."""
    owner = _hunter()

    tier = _prompt(owner, SHAPE_TIER)
    assert [b.label for b in tier.buckets.all()] == ['S', 'A', 'B', 'C', 'D']
    assert [b.position for b in tier.buckets.all()] == [0, 1, 2, 3, 4]
    assert all(b.colour for b in tier.buckets.all()), 'the tier palette is part of the convention'

    assert _prompt(owner, SHAPE_GRID, title='Grid').buckets.count() == 0
    assert _prompt(owner, SHAPE_POLL, title='Poll').buckets.count() == 1


# ── gates ─────────────────────────────────────────────────────────────────────────────────────────


def test_a_restricted_hunter_cannot_write_but_can_still_withdraw():
    """The line every UGC surface on this site draws: a restriction stops NEW WORDS and the act of
    making already-written ones visible. Taking your own content down is the opposite of the thing
    being restricted, so it stays open -- gating the whole surface would trap a restricted hunter's
    prompt in public with deletion as the moderator's only lever."""
    owner = _hunter()
    prompt = _prompt(owner, is_public=True)
    bucket = prompt.buckets.first()
    _restrict(owner)

    with pytest.raises(svc.PromptError):
        svc.create_prompt(owner, shape=SHAPE_TIER, title='Another')
    with pytest.raises(svc.PromptError):
        svc.update_prompt(prompt, owner, title='New name')
    with pytest.raises(svc.PromptError):
        svc.add_concept(prompt, owner, ConceptFactory())
    with pytest.raises(svc.PromptError):
        svc.create_bucket(prompt, owner, label='New row')
    with pytest.raises(svc.PromptError):
        svc.update_bucket(bucket, owner, label='Renamed')

    # ...and the acts that remove or hide their own content still work.
    svc.update_bucket(bucket, owner, colour='purple')
    svc.reorder_buckets(prompt, owner, list(prompt.buckets.values_list('pk', flat=True))[::-1])
    svc.set_closed(prompt, owner, closed=True)
    svc.update_prompt(prompt, owner, is_public=False)
    prompt.refresh_from_db()
    assert prompt.is_public is False

    svc.delete_prompt(prompt, owner)
    prompt.refresh_from_db()
    assert prompt.is_deleted is True


def test_an_unlinked_hunter_cannot_author():
    stranger = ProfileFactory(is_linked=False, psn_username='unlinked')
    with pytest.raises(svc.PromptError):
        svc.create_prompt(stranger, shape=SHAPE_TIER, title='Nope')


def test_only_the_owner_writes():
    owner, other = _hunter(), _hunter('other')
    prompt = _prompt(owner)

    for call in (
        lambda: svc.update_prompt(prompt, other, title='Mine now'),
        lambda: svc.delete_prompt(prompt, other),
        lambda: svc.set_closed(prompt, other, closed=True),
        lambda: svc.add_concept(prompt, other, ConceptFactory()),
        lambda: svc.create_bucket(prompt, other, label='Mine'),
        lambda: svc.update_bucket(prompt.buckets.first(), other, label='Mine'),
        lambda: svc.delete_bucket(prompt.buckets.first(), other),
        # These two were missing, and `reorder_buckets` is the sharper omission: bucket order IS a
        # rank on a tier list, so an unguarded one lets any signed-in hunter silently rewrite what
        # every existing answer to somebody else's prompt MEANS, without touching a placement.
        lambda: svc.reorder_buckets(prompt, other,
                                    list(prompt.buckets.values_list('pk', flat=True))),
        lambda: svc.reorder_games(prompt, other, []),
        lambda: svc.remove_concept(prompt, other,
                                   svc.add_concept(prompt, owner, ConceptFactory())),
    ):
        with pytest.raises(svc.PromptError):
            call()


# ── caps ──────────────────────────────────────────────────────────────────────────────────────────


def test_the_prompt_count_is_the_perk_and_the_sizes_are_not():
    """Members author more; the SIZE caps are flat, because abuse prevention that can be purchased is
    not abuse prevention."""
    free = _hunter('free')
    for i in range(svc.max_prompts_for(free)):
        _prompt(free, title=f'Prompt {i}')
    with pytest.raises(svc.PromptError):
        _prompt(free, title='One too many')

    member = _hunter('member', premium=True)
    assert svc.max_prompts_for(member) > svc.max_prompts_for(free)

    # ...and a deleted one makes room, because the cap counts what is visible.
    svc.delete_prompt(Prompt.objects.owned_by(free).first(), free)
    _prompt(free, title='Back under the cap')


def test_the_pool_cap_is_per_shape():
    """A 200-option poll is not a poll; a 20-game tier list is a mean ceiling. One number would be
    wrong for two of the three."""
    owner = _hunter()
    poll = _prompt(owner, SHAPE_POLL, title='Poll')
    for _ in range(MAX_GAMES_PER_PROMPT[SHAPE_POLL]):
        svc.add_concept(poll, owner, ConceptFactory())

    with pytest.raises(svc.PromptError):
        svc.add_concept(poll, owner, ConceptFactory())

    # The same count is nowhere near a tier list's ceiling.
    assert MAX_GAMES_PER_PROMPT[SHAPE_TIER] > MAX_GAMES_PER_PROMPT[SHAPE_POLL]


def test_a_game_cannot_be_added_twice():
    owner = _hunter()
    prompt = _prompt(owner)
    concept = ConceptFactory()
    svc.add_concept(prompt, owner, concept)

    with pytest.raises(svc.PromptError):
        svc.add_concept(prompt, owner, concept)


# ── the pool, and what removing from it does to other people ─────────────────────────────────────


def test_removing_a_game_closes_the_gap_and_repairs_every_count_it_moves():
    """The widest write in the feature: the row's placements cascade out of every response, including
    ones belonging to people the author has never met.

    Positions stay dense because the browse tile's cover mosaic is a bounded prefetch
    (`position__lt=4`), so a hole renders three covers on a four-game prompt. `placement_count` is
    repaired on the responses that actually lost something."""
    owner = _hunter()
    prompt = _prompt(owner)
    bucket = prompt.buckets.first()
    first, second, third = [svc.add_concept(prompt, owner, ConceptFactory()) for _ in range(3)]

    # DELIBERATELY DRIFTED, so a recompute is distinguishable from a nudge. Starting from a correct
    # 3, `update(game_count=F('game_count') - 1)` lands on the same 2 a recompute does -- and the
    # whole argument of `_recount`'s docstring is that recomputing CORRECTS existing drift.
    prompt.game_count = 99
    prompt.save(update_fields=['game_count'])

    loser = _answer(prompt, _hunter('loser'), second, bucket)
    bystander = _answer(prompt, _hunter('bystander'), third, bucket)
    # DELIBERATELY DRIFTED, because a correct count cannot detect a widened repair: recomputing a
    # response that lost nothing lands on the number it already had. 9 is wrong, and only the
    # narrowing keeps it wrong.
    bystander.placement_count = 9
    bystander.save(update_fields=['placement_count'])

    svc.remove_concept(prompt, owner, second)

    prompt.refresh_from_db()
    assert prompt.game_count == 2
    assert list(prompt.games.order_by('position').values_list('position', flat=True)) == [0, 1]

    loser.refresh_from_db()
    assert loser.placement_count == 0, 'the count still includes a placement that cascaded away'
    assert not loser.placements.exists()

    bystander.refresh_from_db()
    assert bystander.placement_count == 9, (
        'a response that lost nothing was walked -- the repair is sized to the prompt, not to the merge'
    )
    assert bystander.placements.get().prompt_game_id == third.pk


def test_deleting_a_row_returns_its_games_to_the_tray():
    """A placement is an association, not the content. The pool is untouched and the cards simply
    become unplaced again -- which is why `bucket` cascades where `GameListItem.section` sets null."""
    owner = _hunter()
    prompt = _prompt(owner)
    game = svc.add_concept(prompt, owner, ConceptFactory())
    doomed_row, keeper = prompt.buckets.all()[0], prompt.buckets.all()[1]
    responder = _answer(prompt, _hunter('responder'), game, doomed_row)

    svc.delete_bucket(doomed_row, owner)

    assert PromptGame.objects.filter(pk=game.pk).exists(), 'a pool game went with the row'
    responder.refresh_from_db()
    assert responder.placement_count == 0
    assert not responder.placements.exists()
    assert PromptBucket.objects.filter(prompt=prompt).exists()
    assert keeper.pk in set(prompt.buckets.values_list('pk', flat=True))
    assert list(prompt.buckets.order_by('position').values_list('position', flat=True)) == [0, 1, 2, 3]


# ── bucket ordering ───────────────────────────────────────────────────────────────────────────────


def test_reordering_refuses_a_partial_order():
    """A drag that posts a subset means the client and the server disagree about what exists, and
    applying it would renumber the rows the client remembered around the ones it forgot."""
    owner = _hunter()
    prompt = _prompt(owner)
    ids = list(prompt.buckets.values_list('pk', flat=True))

    with pytest.raises(svc.PromptError):
        svc.reorder_buckets(prompt, owner, ids[:2])
    with pytest.raises(svc.PromptError):
        svc.reorder_buckets(prompt, owner, ids + [ids[0]])
    with pytest.raises(svc.PromptError):
        svc.reorder_buckets(prompt, owner, ['not-an-id'] + ids[1:])

    assert list(prompt.buckets.order_by('position').values_list('pk', flat=True)) == ids


def test_reordering_does_not_disturb_the_answers_already_given():
    """Placements point at the bucket, not at its position. On a tier list the order IS a rank, so
    this changes what those answers MEAN -- which is the author's prerogative and the reason the
    control exists -- but no row moves."""
    owner = _hunter()
    prompt = _prompt(owner)
    game = svc.add_concept(prompt, owner, ConceptFactory())
    top = prompt.buckets.order_by('position').first()
    responder = _answer(prompt, _hunter('responder'), game, top)

    svc.reorder_buckets(prompt, owner, list(prompt.buckets.values_list('pk', flat=True))[::-1])

    placement = responder.placements.get()
    assert placement.bucket_id == top.pk
    top.refresh_from_db()
    assert top.position == 4, 'the row did not actually move'
    responder.refresh_from_db()
    assert responder.placement_count == 1


# ── text ──────────────────────────────────────────────────────────────────────────────────────────


def test_markup_is_stripped_and_the_entity_encoded_form_lands_in_the_same_place():
    """THE FIXPOINT, which is the only part of this worth a test.

    `CommentService.sanitize_text` bleaches and then `html.unescape()`s its own output, so it is not
    idempotent: one pass over `&lt;script&gt;` returns a live `<script>`, which is safe only under
    Django auto-escaping -- and a title is headed for `og:title`. Running it to a fixpoint means both
    spellings converge on the same harmless text, which is what this asserts. Checked by running the
    sanitizer rather than assumed, because the first version of this test asserted a REFUSAL and the
    refusal is not what happens."""
    owner = _hunter()

    plain = svc.create_prompt(owner, shape=SHAPE_TIER, title='<script>alert(1)</script>')
    encoded = svc.create_prompt(owner, shape=SHAPE_TIER,
                                title='&lt;script&gt;alert(1)&lt;/script&gt;')

    assert plain.title == 'alert(1)'
    assert encoded.title == 'alert(1)', 'a single pass would have stored live markup here'
    assert '<' not in encoded.title

    prompt = _prompt(owner, title='Labels too')
    assert svc.create_bucket(prompt, owner, label='<b>S</b>').label == 'S'


def test_a_character_the_cleaner_cannot_remove_is_refused_rather_than_stored():
    """The cleaner converges on `Best game < 2010` with the `<` intact, and a partly-cleaned string is
    the one thing this function must not store: the guarantee callers rely on is "no angle brackets",
    so it refuses instead of half-keeping it."""
    owner = _hunter()

    with pytest.raises(svc.PromptError):
        svc.create_prompt(owner, shape=SHAPE_TIER, title='Best game < 2010')
    with pytest.raises(svc.PromptError):
        svc.create_bucket(_prompt(owner), owner, label='> B')


def test_a_banned_word_is_refused_without_naming_itself():
    """Every hunter-written string, not just the title -- the label is the one most easily missed,
    because it feels structural, and it reaches furthest: it renders on every answer anybody gives.

    The matched word is deliberately not echoed back. Telling somebody probing the filter exactly
    which term tripped it hands them the list."""
    from django.core.cache import cache

    from trophies.models import BannedWord

    BannedWord.objects.create(word='shovelware', is_active=True)
    cache.delete('banned_words:active')
    owner = _hunter()
    # try/finally, because the ROW rolls back with the test transaction and the CACHE does not. Any
    # failure below would otherwise leave 'shovelware' banned process-wide for 300 seconds, breaking
    # every later test in this worker that writes a title -- as a cascade that looks unrelated to
    # whatever actually broke.
    try:
        with pytest.raises(svc.PromptError) as caught:
            svc.create_prompt(owner, shape=SHAPE_TIER, title='Ranking the shovelware')
        assert 'shovelware' not in str(caught.value)

        prompt = _prompt(owner, title='Clean title')
        with pytest.raises(svc.PromptError):
            svc.update_prompt(prompt, owner, description='full of shovelware')
        with pytest.raises(svc.PromptError):
            svc.create_bucket(prompt, owner, label='shovelware')
    finally:
        cache.delete('banned_words:active')


def test_a_title_is_required_and_bounded():
    owner = _hunter()
    with pytest.raises(svc.PromptError):
        svc.create_prompt(owner, shape=SHAPE_TIER, title='x' * 500)
    with pytest.raises(svc.PromptError):
        svc.create_prompt(owner, shape=SHAPE_TIER, title='   ')


def test_a_colour_has_to_be_one_of_ours():
    """`colour` is interpolated into a CSS class name and, unlike `shape`, has no CheckConstraint
    behind it -- so this is the only thing between a written value and a class attribute."""
    owner = _hunter()
    prompt = _prompt(owner)

    with pytest.raises(svc.PromptError):
        svc.create_bucket(prompt, owner, label='Custom', colour='red; --pp-x: url(evil)')
    svc.update_bucket(prompt.buckets.first(), owner, colour='purple')
    assert prompt.buckets.first().colour == 'purple'
    # Blank is a real value: a grid slot and a poll have no use for a tier colour.
    svc.update_bucket(prompt.buckets.first(), owner, colour='')


def test_an_unknown_shape_never_reaches_the_column():
    owner = _hunter()
    with pytest.raises(svc.PromptError):
        svc.create_prompt(owner, shape='bracket', title='Not yet')
    with pytest.raises(svc.PromptError):
        svc.create_prompt(owner, shape='', title='Nothing')


def test_grid_columns_are_bounded_where_the_template_divides_by_them():
    owner = _hunter()
    with pytest.raises(svc.PromptError):
        svc.create_prompt(owner, shape=SHAPE_GRID, title='Grid', grid_columns=0)
    with pytest.raises(svc.PromptError):
        svc.create_prompt(owner, shape=SHAPE_GRID, title='Grid', grid_columns='three')


# ── what the audit found untested ─────────────────────────────────────────────────────────────────


def test_a_restricted_hunter_cannot_publish_a_prompt_they_wrote_earlier():
    """THE BYPASS `update_list` HAD, which this service's `or publishing` clause exists to prevent and
    which nothing was testing.

    Without that clause the route around a restriction is: write prompts privately, get restricted for
    something unrelated, then POST `is_public=true` on each. They all go public with no gate, and
    deletion is the moderator's only remaining lever."""
    owner = _hunter()
    private = _prompt(owner, is_public=False, title='Written before')
    # Publishable, so the restriction is the ONLY thing that can refuse this. Without the games it
    # would raise either way and the test would prove nothing about the gate.
    _publishable(private, owner)
    _restrict(owner)

    with pytest.raises(svc.PromptError):
        svc.update_prompt(private, owner, is_public=True)

    private.refresh_from_db()
    assert private.is_public is False


def test_a_tier_list_cannot_grow_unlimited_rows():
    """The cap behind the poll branch. The poll test deliberately asserts the poll MESSAGE so it can
    tell which refusal fired -- which leaves the cap itself untested, and a loop posting
    `create_bucket` would give one tier list thousands of rows, each rendering a header on every
    answer to that prompt."""
    owner = _hunter()
    prompt = _prompt(owner)
    cap = MAX_BUCKETS_PER_PROMPT[SHAPE_TIER]

    while prompt.buckets.count() < cap:
        svc.create_bucket(prompt, owner, label=f'Row {prompt.buckets.count()}')

    with pytest.raises(svc.PromptError):
        svc.create_bucket(prompt, owner, label='One too many')
    assert prompt.buckets.count() == cap


def test_a_tier_list_holds_far_more_games_than_a_poll():
    """The per-shape pool cap, exercised rather than asserted as a dict comparison. Hardcoding the
    poll's ceiling for every shape would cap a 200-game tier list at 20 and refuse the 21st with
    "This holds 20 games"."""
    owner = _hunter()
    tier = _prompt(owner)
    for _ in range(MAX_GAMES_PER_PROMPT[SHAPE_POLL] + 5):
        svc.add_concept(tier, owner, ConceptFactory())

    tier.refresh_from_db()
    assert tier.game_count == MAX_GAMES_PER_PROMPT[SHAPE_POLL] + 5


def test_a_member_actually_gets_the_bigger_allowance():
    """`max_prompts_for`'s member branch was compared as a constant and never exercised. Hardcoding
    the free cap would silently give members three."""
    member = _hunter('member', premium=True)
    for i in range(svc.max_prompts_for(_hunter('yardstick')) + 1):
        _prompt(member, title=f'Prompt {i}')

    assert Prompt.objects.owned_by(member).count() > FREE_MAX_PROMPTS


def test_the_seeded_rows_would_survive_the_checks_the_service_applies_to_authored_ones():
    """`create_prompt` writes `DEFAULT_BUCKETS` straight to the database without running them through
    `_check_label` or `_check_colour`, so a seed is the one label and colour on the site that nothing
    validates. Adding `('S', 'crimson')` would ship a CSS class the stylesheet does not define, with a
    green suite."""
    for shape, seeds in svc.DEFAULT_BUCKETS.items():
        assert shape in SHAPES
        for label, colour in seeds:
            assert svc._check_label(label) == label
            assert svc._check_colour(colour) == colour
        if shape == SHAPE_POLL:
            assert len(seeds) == 1, 'a poll seeded with two rows is two votes'


def test_deleting_twice_is_a_no_op():
    """A second click must not answer "that is not yours" about a prompt just removed."""
    owner = _hunter()
    prompt = _prompt(owner)
    svc.delete_prompt(prompt, owner)
    svc.delete_prompt(prompt, owner)
    prompt.refresh_from_db()
    assert prompt.is_deleted is True


# ── the pivot must come from the locked row, not the caller's copy ───────────────────────────────


def test_a_stale_instance_cannot_hide_a_prompt_that_has_been_answered():
    """RULE 2, WHICH THIS FILE STATES AND DID NOT HOLD.

    The unpublish refusal is gated on the prompt already being public, and that value used to be read
    off the caller's object. Two tabs: publish in one, four hunters answer, "make private" from the
    other -- whose instance still says private. The conjunct short-circuited, the answered-check never
    ran, and the answers ended up orphaned behind a private prompt."""
    owner = _hunter()
    prompt = _prompt(owner, is_public=False)
    _publishable(prompt, owner)
    stale = Prompt.objects.get(pk=prompt.pk)          # the other tab, captured while private

    svc.update_prompt(prompt, owner, is_public=True)
    game = svc.add_concept(prompt, owner, ConceptFactory())
    _answer(prompt, _hunter('responder'), game, prompt.buckets.first())

    with pytest.raises(svc.PromptError):
        svc.update_prompt(stale, owner, is_public=False)

    prompt.refresh_from_db()
    assert prompt.is_public is True, 'an answered prompt was hidden through a stale instance'


def test_a_stale_instance_does_not_short_circuit_closing():
    """The same class, smaller blast radius: the idempotency shortcut decided from the caller's copy,
    so an instance that still said "closed" after somebody reopened it wrote nothing and handed the
    view back an object claiming a state the row did not have."""
    owner = _hunter()
    prompt = _prompt(owner)

    # The service mutates the row it LOCKED, not the caller's object -- so after this line
    # `prompt` still says open while the row says closed. That divergence is the whole test, and
    # the reopen below is where it becomes detectable: an earlier version asserted the final
    # state, which both the correct and the broken path reach.
    svc.set_closed(prompt, owner, closed=True)
    assert prompt.is_closed is False, "the caller's object is expected to be stale here"

    svc.set_closed(prompt, owner, closed=False)

    prompt.refresh_from_db()
    assert prompt.is_closed is False, (
        'the reopen was skipped: the shortcut compared against a stale flag that already said '
        'open, so a prompt its author believes is taking answers is still closed'
    )


# ── the pool can be arranged ──────────────────────────────────────────────────────────────────────


def test_the_pool_can_be_reordered_without_touching_anybodys_answer():
    """`reorder_buckets` shipped and its counterpart did not, which was not cosmetic: the browse
    tile's cover mosaic is a bounded prefetch over the first four pool rows, so those four games are
    what represents this prompt to everybody scrolling past -- and the author could not choose them.

    The only workaround was remove-then-add, which appends to the end AND cascades that game out of
    every existing answer: the most destructive write in the feature, as the remedy for wanting a
    different cover."""
    owner = _hunter()
    prompt = _prompt(owner)
    games = [svc.add_concept(prompt, owner, ConceptFactory()) for _ in range(3)]
    responder = _answer(prompt, _hunter('responder'), games[2], prompt.buckets.first())

    svc.reorder_games(prompt, owner, [games[2].pk, games[0].pk, games[1].pk])

    assert list(prompt.games.order_by('position').values_list('pk', flat=True)) == [
        games[2].pk, games[0].pk, games[1].pk]
    assert list(prompt.games.order_by('position').values_list('position', flat=True)) == [0, 1, 2]

    # Placements point at the pool ROW, not at its position, so no answer moved.
    responder.refresh_from_db()
    assert responder.placements.get().prompt_game_id == games[2].pk
    assert responder.placement_count == 1


def test_reordering_the_pool_refuses_a_partial_order():
    owner = _hunter()
    prompt = _prompt(owner)
    games = [svc.add_concept(prompt, owner, ConceptFactory()) for _ in range(3)]
    ids = [g.pk for g in games]

    for bad in (ids[:2], ids + [ids[0]], ['not-an-id'] + ids[1:]):
        with pytest.raises(svc.PromptError):
            svc.reorder_games(prompt, owner, bad)

    assert list(prompt.games.order_by('position').values_list('pk', flat=True)) == ids


def test_a_none_profile_is_refused_rather_than_crashing():
    """`_require_owner` dereferences `profile.id`, so it must not run before the None check.

    A view using this codebase's own `getattr(request.user, 'profile', None)` idiom and forgetting the
    None branch would otherwise get an AttributeError -- which sails past every `except PromptError`
    and reaches the client as a 500 HTML page where a 400 was designed."""
    owner = _hunter()
    prompt = _prompt(owner)

    for call in (
        lambda: svc.create_prompt(None, shape=SHAPE_TIER, title='Nobody'),
        lambda: svc.add_concept(prompt, None, ConceptFactory()),
        lambda: svc.create_bucket(prompt, None, label='Nobody'),
    ):
        with pytest.raises(svc.PromptError):
            call()
