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


# -- laid out as a rectangle ----------------------------------------------------------------------

def _read(path):
    with open(path, encoding='utf-8') as handle:
        return handle.read()


def test_a_grid_renders_as_a_grid_and_carries_its_column_count(client):
    """Every shape used to render as a vertical list, so an author who picked "4 across, 3 down" got
    twelve stacked rows and no rectangle anywhere on the page -- the promise kept in the data and
    never drawn."""
    owner = _hunter('author')
    owner.user_is_premium = True
    owner.save(update_fields=['user_is_premium'])
    prompt = _grid(owner, columns=4, rows=3)
    client.force_login(owner.user)

    from django.urls import reverse
    body = client.get(reverse('prompt_detail', args=[prompt.pk])).content.decode()

    assert 'pp-pdet__rows--grid' in body
    assert 'is-cols-4' in body, 'the layout does not carry the author\'s column count'


def test_a_tier_list_does_not_render_as_a_grid(client):
    """The control: the grid layout is a grid-only treatment, not the new default for every shape."""
    owner = _hunter('author')
    owner.user_is_premium = True
    owner.save(update_fields=['user_is_premium'])
    tier = svc.create_prompt(owner, shape=SHAPE_TIER, title='Rank them')
    client.force_login(owner.user)

    from django.urls import reverse
    body = client.get(reverse('prompt_detail', args=[tier.pk])).content.decode()

    assert 'pp-pdet__rows--grid' not in body
    assert 'is-cols-' not in body


def test_the_column_count_is_capped_at_every_breakpoint():
    """Six across is unreadable at 375px whatever the author picked, so each breakpoint takes the
    smaller of their choice and what fits. Presentation only -- `grid_columns` never changes, so the
    chosen layout returns at full width.

    Asserted on the SOURCE rather than the build, because the build minifies the media queries into
    a form a substring check cannot read reliably.
    """
    import re

    css = _read('static/css/components/prompts.css')
    block = css[css.index('.pp-pdet__rows--grid {'):]

    # Every offered column count has a base (phone) rule, and none of them shows more than two.
    for columns in {c for c, _r in GRID_LAYOUTS}:
        rule = re.search(r'\.pp-pdet__rows--grid\.is-cols-%d \{ --pd-shown: (\d)' % columns, block)
        assert rule, f'{columns} columns has no phone rule'
        assert int(rule.group(1)) <= 2, f'{columns} columns shows {rule.group(1)} across on a phone'

    # ...and the ladder climbs back to the author's choice by the desktop breakpoint.
    for breakpoint in ('640px', '768px', '1024px'):
        assert f'min-width: {breakpoint}' in block, f'no {breakpoint} step'
    desktop = block[block.index('min-width: 1024px'):]
    assert '.is-cols-6 { --pd-shown: 6; }' in desktop, 'six across never returns at full width'


def test_a_grid_slot_has_no_colour_control(client):
    """Colour is the TIER convention -- the model says "a grid slot and a poll have no use for a tier
    colour" -- and dropping it buys back the width a cell needs at 375px."""
    owner = _hunter('author')
    owner.user_is_premium = True
    owner.save(update_fields=['user_is_premium'])
    prompt = _grid(owner, columns=2, rows=2)
    client.force_login(owner.user)

    from django.urls import reverse
    grid_body = client.get(reverse('prompt_detail', args=[prompt.pk])).content.decode()
    assert 'data-pd-row-colour-input' not in grid_body

    tier = svc.create_prompt(owner, shape=SHAPE_TIER, title='Rank them')
    tier_body = client.get(reverse('prompt_detail', args=[tier.pk])).content.decode()
    assert 'data-pd-row-colour-input' in tier_body, 'a tier list lost its palette'


def test_an_unnamed_slot_is_marked_before_publish_is_pressed(client):
    """The checklist, made visible. An author should see which slots still need a question while
    they are looking at them, not discover it from a refusal."""
    owner = _hunter('author')
    owner.user_is_premium = True
    owner.save(update_fields=['user_is_premium'])
    prompt = _grid(owner, columns=2, rows=2, named=False)
    client.force_login(owner.user)

    from django.urls import reverse
    url = reverse('prompt_detail', args=[prompt.pk])
    assert client.get(url).content.decode().count('is-unnamed') == 4

    for n, bucket in enumerate(prompt.buckets.order_by('position')):
        svc.update_bucket(bucket, owner, label=f'Question {n + 1}')
    assert 'is-unnamed' not in client.get(url).content.decode()


# -- the slide-out ---------------------------------------------------------------------------------

def test_the_grid_settings_belong_to_the_grid_button():
    """Attached to the card rather than floating at the bottom of the form, so they read as "this
    option's settings" -- and OUTSIDE the `<label>`, because a click on a control inside a label is
    routed to the label and would re-trigger the radio."""
    markup = _read('templates/prompts/browse.html')

    wrap = markup[markup.index('pp-pdlg__shape-wrap'):markup.index('</fieldset>')]
    label_close = wrap.index('</label>')
    panel = wrap.index('data-pr-create-grid')
    assert panel > label_close, 'the settings panel is inside the label'
    assert '{% if value == grid_shape %}' in wrap, 'the template hardcodes the shape name'


def test_the_settings_slide_rather_than_appearing():
    """`PP.animatePanel` rather than a hand-rolled height tween: it measures while collapsed and
    releases back to `auto` on `transitionend`, which is what stops a panel sticking at a fixed
    height the first time its contents wrap. It also drops to the end state under reduced motion."""
    js = _read('static/js/prompts-browse.js')
    sync = js[js.index('function syncShape('):js.index('Array.prototype.forEach.call(')]

    assert 'PP.animatePanel' in sync
    assert 'wanted === open' in sync, 'a re-selected shape would replay the reveal'

    css = _read('static/css/components/prompts.css')
    panel = css[css.index('.pp-pdlg__shape-extra {'):]
    # `animatePanel`'s contract: it cannot measure without the clip, and without a transition there
    # is no `transitionend` to release the height.
    assert 'overflow: hidden' in panel[:400]
    assert 'transition: height' in panel[:400]


def test_the_first_paint_does_not_play_a_reveal():
    """Motion marks a CHANGE, not a state. Reopening the dialog on the shape it closed with should
    show the settings already out."""
    js = _read('static/js/prompts-browse.js')
    assert 'syncShape(false)' in js, 'opening the dialog animates a panel that was already open'
    assert 'syncShape(true)' in js, 'changing shape does not animate'


def test_a_grid_slot_name_is_a_field_you_can_see(client):
    """It was a span that turned into an input on click, which on a grid read as plain text on a
    draggable square -- nothing said it could be typed into, and naming a 36-slot grid cost a click
    per slot before a single keystroke."""
    owner = _hunter('author')
    owner.user_is_premium = True
    owner.save(update_fields=['user_is_premium'])
    prompt = _grid(owner, columns=2, rows=2)
    client.force_login(owner.user)

    from django.urls import reverse
    body = client.get(reverse('prompt_detail', args=[prompt.pk])).content.decode()

    assert body.count('pp-pdet__row-field') == 4, 'every slot should be a visible field'
    # ...and no hidden span to click first.
    assert 'data-pd-row-label-view' not in body


def test_an_unnamed_slot_is_an_empty_field_with_its_number_behind_it(client):
    """A field holding the literal "Slot 4" invites an author to select it and type over it; an empty
    one with a grey "Slot 4" behind it is simply waiting. `data-saved` carries the empty string so a
    blurred empty field does not fill itself back in with its own placeholder."""
    owner = _hunter('author')
    owner.user_is_premium = True
    owner.save(update_fields=['user_is_premium'])
    prompt = _grid(owner, columns=2, rows=2, named=False)
    client.force_login(owner.user)

    from django.urls import reverse
    body = client.get(reverse('prompt_detail', args=[prompt.pk])).content.decode()

    assert 'placeholder="Slot 1"' in body
    assert 'value=""' in body and 'data-saved=""' in body
    assert 'value="Slot 1"' not in body, 'the placeholder text was put in the field itself'


def test_a_tier_row_keeps_the_click_to_edit_swap(client):
    """Five always-on inputs down the left of a tier list would read as a form rather than a ranking,
    and a tier label is set once and rarely touched."""
    owner = _hunter('author')
    owner.user_is_premium = True
    owner.save(update_fields=['user_is_premium'])
    tier = svc.create_prompt(owner, shape=SHAPE_TIER, title='Rank them')
    client.force_login(owner.user)

    from django.urls import reverse
    body = client.get(reverse('prompt_detail', args=[tier.pk])).content.decode()

    assert 'data-pd-row-label-view' in body, 'the tier row lost its read span'
    assert 'pp-pdet__row-field' not in body


def test_the_revert_value_lives_on_the_input_not_the_span():
    """A grid slot has no span, so the span could not go on being the source of truth for what to
    revert to on Escape or on a failed save."""
    js = _read('static/js/prompt-detail.js')

    assert 'function savedValue(input)' in js
    rename = js[js.index('function saveLabel('):js.index('capture: `blur` does not bubble')]
    assert 'label.textContent.trim()' not in rename, 'the save still reads the span'
    assert 'savedValue(input)' in rename

    keys = js[js.index("if (e.key === 'Escape')"):]
    assert 'savedValue(e.target)' in keys[:300], 'Escape still reverts from the span'
