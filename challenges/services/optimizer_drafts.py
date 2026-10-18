"""The hunter's latest Challenge Optimizer plan, autosaved so "Open my plan" can bring it back (owner, 2026-10-16).

See `OptimizerDraft` for what is kept and why. Three jobs here: SAVE what a built plan was made from, FORGET the
games an Apply put on the board, and say what the page should REOPEN.

TWEAKS ARE KEPT IN SLUGS, the plan door's own wire shape: `{pins: [slug], square_pins: [{type, key, contract,
lock?}],
excluded: [slug], penalized: [slug], left_open: [{type, key}]}`. The page posts them back unchanged and the door re-resolves them, so a
contract that went away since is simply dropped there, as it would be from any request.
"""
from django.db import transaction

from challenges.models import OptimizerDraft
from challenges.services import allowance
from trophies.models import Contract

TWEAK_LISTS = ('pins', 'excluded', 'penalized')
SQUARE_PIN_FIELDS = ('type', 'key', 'contract')
SQUARE_FIELDS = ('type', 'key')


def save(profile, options, tweaks):
    """Keep the plan just built: `options` (its answers) and `tweaks` (the door's cleaned slug shape)."""
    OptimizerDraft.objects.update_or_create(
        profile=profile, defaults={'answers': allowance.answers_of(options), 'tweaks': tweaks})


def forget_applied(profile, applied):
    """What an Apply placed leaves the draft: its games' must-haves, and the square pins on the squares it wrote
    (they are on the board now, and carrying them would re-plan them as unplaced). `applied`: (type, key, slug) per
    written square. Only THOSE squares: the same game locked as a pick elsewhere keeps its lock. Everything else
    ("not this one", rerolls, the answers) stays."""
    applied = list(applied)
    if not applied:
        return
    slugs = {slug for _, _, slug in applied}
    squares = {(t, k) for t, k, _ in applied}
    # LOCKED: a plan autosaving from another tab between the read and the write would otherwise get this draft's
    # old tweaks written over its own.
    with transaction.atomic():
        draft = OptimizerDraft.objects.select_for_update().filter(profile=profile).first()
        if draft is None:
            return
        tweaks = _clean(draft.tweaks)
        kept = {**tweaks,
                'pins': [s for s in tweaks['pins'] if s not in slugs],
                'square_pins': [e for e in tweaks['square_pins'] if (e['type'], e['key']) not in squares]}
        if kept != tweaks:
            draft.tweaks = kept
            draft.save(update_fields=['tweaks', 'updated_at'])


def to_reopen(profile, spent_answers=None, active_types=()):
    """What "Open my plan" reopens, or None: `{answers, tweaks, updated_at}`. `tweaks` and `updated_at` are None
    when only the spent answers are known (no draft of that plan): the page then keeps whatever must-haves the
    hunter has typed rather than wiping them. Must-haves come back as `[{slug, name}]`, for their chips.

    A FREE HUNTER WHOSE WEEK IS IN USE reopens THAT plan (`spent_answers`): the draft only lends it its tweaks
    (and its weight and `picks`) when it is the same plan, because any other answers are locked until next week."""
    draft = OptimizerDraft.objects.filter(profile=profile).first()
    if spent_answers is not None:
        if draft is not None and allowance.same_plan(draft.answers, spent_answers, active_types):
            return _reopen(draft.answers, draft.tweaks, draft.updated_at)
        return {'answers': dict(spent_answers), 'tweaks': None, 'updated_at': None}
    if draft is None:
        return None
    return _reopen(draft.answers, draft.tweaks, draft.updated_at)


def _reopen(answers, tweaks, updated_at):
    tweaks = _clean(tweaks)
    names = dict(Contract.objects.filter(slug__in=tweaks['pins']).values_list('slug', 'name')) if tweaks['pins'] else {}
    return {
        'answers': dict(answers or {}),
        'tweaks': {**tweaks, 'pins': [{'slug': s, 'name': names[s]} for s in tweaks['pins'] if s in names]},
        'updated_at': updated_at,
    }


def _clean(raw):
    """The stored tweaks in their full shape, whatever an older or damaged row holds: a list of strings per list
    field and well-formed square pins, never a 500 on the page that reads them."""
    raw = raw if isinstance(raw, dict) else {}
    out = {f: [s for s in _list(raw.get(f)) if isinstance(s, str)] for f in TWEAK_LISTS}
    out['square_pins'] = [{**{k: e[k] for k in SQUARE_PIN_FIELDS}, **({'lock': True} if e.get('lock') is True else {})}
                          for e in _list(raw.get('square_pins'))
                          if isinstance(e, dict) and all(isinstance(e.get(k), str) for k in SQUARE_PIN_FIELDS)]
    out['left_open'] = [{k: e[k] for k in SQUARE_FIELDS} for e in _list(raw.get('left_open'))
                        if isinstance(e, dict) and all(isinstance(e.get(k), str) for k in SQUARE_FIELDS)]
    return out


def _list(value):
    return value if isinstance(value, list) else []


def door_status(profile, user):
    """Where this hunter stands, for the My Challenges door (owner, 2026-10-17): when their plan was last saved,
    whether they are on the weekly allowance, and when its next free plan comes. Two single-row reads. Dates are
    `allowance.when_context`s, for `partials/_when.html`."""
    saved_at = OptimizerDraft.objects.filter(profile=profile).values_list('updated_at', flat=True).first()
    state = allowance.state_for(profile)
    return {
        'saved': allowance.when_context(saved_at, user) if saved_at else None,
        'free': not state.unlimited,
        'next': allowance.when_context(state.next_free_at, user) if state.in_use else None,
    }
