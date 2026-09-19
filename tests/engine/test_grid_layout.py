"""A grid is a RECTANGLE of labelled slots.

Owner's call, 2026-09-19. Three things had to be true together and each is load-bearing:

1. The author picks a SIZE, not a slot count, because only dimensions can promise a rectangle. Seven
   slots at three across renders 3 + 3 + 1, and a count like 12 has four factorisations the service
   would have to choose between on the author's behalf.
2. Creating N slots means creating N LABELS -- `promptbucket_label_not_blank` forbids a blank one --
   so they arrive as `Slot 1..N`. The comment on `DEFAULT_BUCKETS` was right that this invites
   somebody to ship "Slot 1"; publishing is refused while any survive.
3. A grid therefore has no per-slot add or delete. Those are precisely the two controls that make a
   rectangle ragged, so resizing is the only thing that moves the count.
"""
import pytest

from prompts.models import (GRID_LAYOUTS, GRID_SLOT_PLACEHOLDER, MAX_BUCKETS_PER_PROMPT,
                            MAX_GRID_COLUMNS, SHAPE_GRID, SHAPE_POLL, SHAPE_TIER, PromptPlacement,
                            grid_layout_choices, is_placeholder_label)
from prompts.services import prompt_service as svc
from prompts.services import response_service as rsvc
from tests.factories import ConceptFactory, ProfileFactory

pytestmark = pytest.mark.django_db


def _hunter(psn='hunter'):
    return ProfileFactory(is_linked=True, psn_username=psn)


def _grid(owner, *, columns=3, rows=3, named=True):
    prompt = svc.create_prompt(owner, shape=SHAPE_GRID, title='Pick one each',
                               grid_columns=columns, grid_rows=rows)
    if named:
        for n, bucket in enumerate(prompt.buckets.order_by('position')):
            svc.update_bucket(bucket, owner, label=f'Question {n + 1}')
    return prompt


# ── the offered sizes ────────────────────────────────────────────────────────────────────────────

def test_every_offered_size_fits_inside_the_caps():
    """The picker cannot offer a rectangle the service would refuse, or the author meets a refusal
    for choosing something the page showed them."""
    for columns, rows in GRID_LAYOUTS:
        assert 1 <= columns <= MAX_GRID_COLUMNS, (columns, rows)
        assert columns * rows <= MAX_BUCKETS_PER_PROMPT[SHAPE_GRID], (columns, rows)


def test_the_picker_labels_lead_with_the_total():
    """"Twelve slots" is the decision an author is making; the dimensions are how it is laid out."""
    labels = {slots: label for _c, _r, slots, label in grid_layout_choices()}
    assert labels[12].startswith('12 slots')
    assert '4 across, 3 down' in labels[12]


# ── creation ─────────────────────────────────────────────────────────────────────────────────────

def test_creating_a_grid_seeds_exactly_the_rectangle():
    owner = _hunter()
    prompt = _grid(owner, columns=4, rows=3, named=False)

    assert prompt.buckets.count() == 12
    assert prompt.grid_columns == 4
    # Dense, in order, so the template lays out 4 across without a gap.
    assert list(prompt.buckets.order_by('position').values_list('position', flat=True)) == list(range(12))
    assert [b.label for b in prompt.buckets.order_by('position')][:3] == ['Slot 1', 'Slot 2', 'Slot 3']


def test_a_size_that_is_not_an_offered_rectangle_is_refused():
    """AGAINST THE LIST, not against the bounds. `columns <= 6 and columns * rows <= 36` would also
    admit 6x1 and 2x18 -- shapes the picker never shows and nothing is designed to render, which a
    hand-posted request could still reach."""
    owner = _hunter()

    for columns, rows in ((6, 1), (2, 18), (7, 2), (3, 0), (0, 3)):
        with pytest.raises(svc.PromptError, match='not a grid size'):
            svc.create_prompt(owner, shape=SHAPE_GRID, title='Nope',
                              grid_columns=columns, grid_rows=rows)


def test_junk_dimensions_are_refused_rather_than_crashing():
    owner = _hunter()
    for columns, rows in (('', '3'), ('abc', '3'), (None, 3)):
        with pytest.raises(svc.PromptError, match='not a grid size'):
            svc.create_prompt(owner, shape=SHAPE_GRID, title='Nope',
                              grid_columns=columns, grid_rows=rows)


def test_the_other_shapes_ignore_the_rectangle_entirely():
    """A tier list gets its five seeded rows and a poll its one, whatever the grid fields say."""
    owner = _hunter()
    tier = svc.create_prompt(owner, shape=SHAPE_TIER, title='Rank them', grid_columns=6, grid_rows=6)
    poll = svc.create_prompt(owner, shape=SHAPE_POLL, title='Which?', grid_columns=6, grid_rows=6)

    assert tier.buckets.count() == 5
    assert poll.buckets.count() == 1


# ── the placeholder is a checklist, not a default ───────────────────────────────────────────────

def test_a_grid_cannot_be_published_while_a_slot_is_still_unnamed():
    """A grid's slots ARE its questions, so one still reading "Slot 4" is asking nothing."""
    owner = _hunter()
    prompt = _grid(owner, columns=2, rows=2, named=False)

    with pytest.raises(svc.PromptError, match='Name every slot') as caught:
        svc.update_prompt(prompt, owner, is_public=True)
    # It names them, so the author knows where to look.
    assert 'Slot 1' in str(caught.value)

    for n, bucket in enumerate(prompt.buckets.order_by('position')):
        svc.update_bucket(bucket, owner, label=f'Question {n + 1}')
    svc.update_prompt(prompt, owner, is_public=True)
    prompt.refresh_from_db()
    assert prompt.is_public is True


def test_one_unnamed_slot_out_of_many_still_blocks_it():
    """The floor is every slot, not most of them."""
    owner = _hunter()
    prompt = _grid(owner, columns=3, rows=3)
    last = prompt.buckets.order_by('position').last()
    svc.update_bucket(last, owner, label=GRID_SLOT_PLACEHOLDER.format(n=9))

    with pytest.raises(svc.PromptError, match='Name every slot'):
        svc.update_prompt(prompt, owner, is_public=True)


def test_the_publish_hint_names_the_unnamed_slots_before_they_are_pressed():
    """Same function the write uses, so the hint beside the button cannot word it differently."""
    owner = _hunter()
    prompt = _grid(owner, columns=2, rows=2, named=False)

    blocker = svc.publish_blocker(prompt)
    assert blocker and 'Name every slot' in blocker
    with pytest.raises(svc.PromptError) as caught:
        svc.update_prompt(prompt, owner, is_public=True)
    assert str(caught.value) == blocker


def test_only_the_generated_names_count_as_placeholders():
    """A hunter who genuinely calls a slot "Slot machines" is not held back by it."""
    assert is_placeholder_label('Slot 1') is True
    assert is_placeholder_label('Slot 36') is True
    assert is_placeholder_label('Slot 37') is False        # past the cap: nothing generated it
    assert is_placeholder_label('Slot machines') is False
    assert is_placeholder_label('Best combat') is False


def test_the_placeholder_rule_does_not_touch_the_other_shapes():
    """A tier list may legitimately keep a row called whatever it likes."""
    owner = _hunter()
    tier = svc.create_prompt(owner, shape=SHAPE_TIER, title='Rank them')
    first = tier.buckets.order_by('position').first()
    svc.update_bucket(first, owner, label='Slot 1')
    for _ in range(5):
        svc.add_concept(tier, owner, ConceptFactory())

    svc.update_prompt(tier, owner, is_public=True)
    tier.refresh_from_db()
    assert tier.is_public is True


# ── resizing ─────────────────────────────────────────────────────────────────────────────────────

def test_growing_keeps_the_named_slots_and_appends_the_rest():
    owner = _hunter()
    prompt = _grid(owner, columns=2, rows=2)

    svc.resize_grid(prompt, owner, columns=3, rows=3)

    prompt.refresh_from_db()
    assert prompt.buckets.count() == 9
    assert prompt.grid_columns == 3
    labels = [b.label for b in prompt.buckets.order_by('position')]
    assert labels[:4] == ['Question 1', 'Question 2', 'Question 3', 'Question 4']
    assert labels[4] == 'Slot 5', 'a new slot should arrive named for renaming'
    assert list(prompt.buckets.order_by('position').values_list('position', flat=True)) == list(range(9))


def test_shrinking_takes_slots_off_the_end():
    """So the ones an author already named are the ones that survive."""
    owner = _hunter()
    prompt = _grid(owner, columns=3, rows=3)

    svc.resize_grid(prompt, owner, columns=2, rows=2)

    prompt.refresh_from_db()
    assert prompt.buckets.count() == 4
    assert [b.label for b in prompt.buckets.order_by('position')] == [
        'Question 1', 'Question 2', 'Question 3', 'Question 4']


def test_shrinking_repairs_the_placement_count_of_every_answer_it_touches():
    """The slots it removes cascade their placements away. `placement_count` is denormalised, so a
    count left behind would show an answer as fuller than it is -- and the completeness the page
    renders is computed from it."""
    owner = _hunter()
    prompt = _grid(owner, columns=3, rows=3)
    slots = list(prompt.buckets.order_by('position'))

    # The author's own answer, which is the only one a draft can carry.
    for slot in slots[:6]:
        rsvc.place(prompt, owner, concept_pk=ConceptFactory().pk, bucket_id=slot.pk)
    assert rsvc.response_for(prompt, owner).placement_count == 6

    svc.resize_grid(prompt, owner, columns=2, rows=2)

    response = rsvc.response_for(prompt, owner)
    assert response.placements.count() == 4
    assert response.placement_count == 4, 'the denormalised count outlived the placements'
    assert PromptPlacement.objects.filter(response=response).count() == 4


def test_resizing_to_the_same_size_changes_nothing():
    owner = _hunter()
    prompt = _grid(owner, columns=3, rows=3)
    before = [b.pk for b in prompt.buckets.order_by('position')]

    svc.resize_grid(prompt, owner, columns=3, rows=3)

    assert [b.pk for b in prompt.buckets.order_by('position')] == before


def test_a_published_grid_cannot_be_resized():
    """Its slots are its questions, and the freeze is what stops them changing under an answer. This
    is also what makes shrinking safe to do without a "somebody answered" guard: a draft can only
    ever hold the author's own answer."""
    owner = _hunter()
    prompt = _grid(owner, columns=2, rows=2)
    svc.update_prompt(prompt, owner, is_public=True)

    with pytest.raises(svc.PromptError):
        svc.resize_grid(prompt, owner, columns=3, rows=3)
    prompt.refresh_from_db()
    assert prompt.buckets.count() == 4


def test_only_a_grid_has_a_size():
    owner = _hunter()
    tier = svc.create_prompt(owner, shape=SHAPE_TIER, title='Rank them')

    with pytest.raises(svc.PromptError, match='Only a grid'):
        svc.resize_grid(tier, owner, columns=3, rows=3)


def test_resizing_is_refused_for_somebody_who_is_not_the_author():
    owner = _hunter()
    prompt = _grid(owner, columns=2, rows=2)

    with pytest.raises(svc.PromptError):
        svc.resize_grid(prompt, _hunter('stranger'), columns=3, rows=3)


def test_a_resize_to_a_shape_the_picker_never_offers_is_refused():
    owner = _hunter()
    prompt = _grid(owner, columns=2, rows=2)

    with pytest.raises(svc.PromptError, match='not a grid size'):
        svc.resize_grid(prompt, owner, columns=6, rows=1)


# ── the two controls a rectangle cannot have ────────────────────────────────────────────────────

def test_a_grid_offers_no_add_or_delete_for_a_single_slot(client):
    """Those are exactly what make a rectangle ragged. The page offers a size instead."""
    owner = _hunter('author')
    owner.user_is_premium = True
    owner.save(update_fields=['user_is_premium'])
    prompt = _grid(owner, columns=2, rows=2)
    client.force_login(owner.user)

    from django.urls import reverse
    body = client.get(reverse('prompt_detail', args=[prompt.pk])).content.decode()

    assert 'data-pd-row-add' not in body, 'a grid offered an add-a-slot form'
    assert 'data-pd-row-delete' not in body, 'a grid offered a delete on one slot'
    assert 'data-pd-resize' in body, 'a grid should offer its size instead'
    # ...and renaming stays, since the author has placeholders to replace.
    assert 'data-pd-row-label-input' in body


def test_a_tier_list_keeps_both(client):
    """The control, so the test above cannot pass by the buttons vanishing everywhere."""
    owner = _hunter('author')
    owner.user_is_premium = True
    owner.save(update_fields=['user_is_premium'])
    tier = svc.create_prompt(owner, shape=SHAPE_TIER, title='Rank them')
    client.force_login(owner.user)

    from django.urls import reverse
    body = client.get(reverse('prompt_detail', args=[tier.pk])).content.decode()

    assert 'data-pd-row-add' in body
    assert 'data-pd-row-delete' in body
    assert 'data-pd-resize' not in body


def test_the_modal_offers_the_grid_fields_and_the_same_sizes(client):
    owner = _hunter('member')
    owner.user_is_premium = True
    owner.save(update_fields=['user_is_premium'])
    client.force_login(owner.user)

    body = client.get('/community/grids/').content.decode()

    assert 'data-pr-create-grid' in body
    assert 'data-pr-create-duplicates' in body
    for _c, _r, _slots, label in grid_layout_choices():
        assert label in body, f'the modal does not offer {label}'


def test_creating_a_grid_over_http_lands_the_rectangle(client):
    from django.urls import reverse

    owner = _hunter('member')
    owner.user_is_premium = True
    owner.save(update_fields=['user_is_premium'])
    client.force_login(owner.user)

    resp = client.post(reverse('prompt_create'), {
        'shape': SHAPE_GRID, 'title': 'Nine questions',
        'grid_columns': '4', 'grid_rows': '3', 'allow_duplicates': 'false',
    })
    assert resp.status_code == 200

    from prompts.models import Prompt
    prompt = Prompt.objects.get(pk=resp.json()['id'])
    assert prompt.buckets.count() == 12
    assert prompt.grid_columns == 4
    assert prompt.allow_duplicates is False
