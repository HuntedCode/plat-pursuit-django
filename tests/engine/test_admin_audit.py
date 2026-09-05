"""The two audit logs, and the rules they both obey.

`ModerationAction` (content decisions) and `AdminAction` (accounts, billing, system) are separate
tables on purpose: a billing entry has no `BlurbReport` to point at, and a moderation entry has
nothing to say about a Stripe subscription id.

They are NOT separate rules. An abstract model base would have enforced that by sharing the schema,
at the cost of making `ModerationAction`'s per-field `help_text` -- the best documentation in that
model -- go generic. So the contract is asserted here instead, by introspection, which is the
stronger guard of the two: it also catches a `null=True` quietly added to `reason`, which
inheritance would happily carry into both tables at once.
"""
import pytest
from django.core.serializers.json import DjangoJSONEncoder
from django.db import models
from django.db.models import Q

from core.models import AdminAction
from core.services import audit
from trophies.models import ModerationAction

#: Both logs. Parametrized rather than looped so a failure names the table.
LOGS = [ModerationAction, AdminAction]
LOG_IDS = ['ModerationAction', 'AdminAction']


def _field(model, name):
    return model._meta.get_field(name)


# ── the shared contract ──────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize('model', LOGS, ids=LOG_IDS)
def test_the_actor_survives_their_account_being_deleted(model):
    """SET_NULL, never CASCADE. Deleting a staff account must not erase what they did -- which is
    precisely the moment somebody would want it erased."""
    actor = _field(model, 'actor')

    assert actor.remote_field.on_delete is models.SET_NULL
    assert actor.null is True


@pytest.mark.parametrize('model', LOGS, ids=LOG_IDS)
def test_the_actors_name_is_frozen_beside_the_link(model):
    """The FK goes null; this is what keeps the entry readable afterwards."""
    label = _field(model, 'actor_label')

    assert isinstance(label, models.CharField)
    assert label.max_length == audit.MAX_LABEL_LENGTH


@pytest.mark.parametrize('model', LOGS, ids=LOG_IDS)
def test_a_reason_is_declared_required(model):
    """The one field that turns a list of timestamps into an audit trail.

    Note what this does NOT prove: `blank` is a forms attribute with no DDL behind it, and Postgres
    NOT NULL does not exclude the empty string. The database half is the two tests below.
    """
    reason = _field(model, 'reason')

    assert isinstance(reason, models.TextField)
    assert reason.null is False, 'a null reason answers "what happened" but never "why"'
    assert reason.blank is False


@pytest.mark.parametrize('model', LOGS, ids=LOG_IDS)
def test_the_database_itself_refuses_a_blank_reason(model, db):
    """`create(reason='')` used to succeed on both tables while the help_text called a reason
    REQUIRED -- the same shape as claiming a row lock and shipping no constraint. The DEPTH of a
    reason stays a service rule; this is the floor beneath it."""
    from django.db import IntegrityError

    with pytest.raises(IntegrityError):
        model.objects.create(action=model.ACTIONS[0][0], reason='')


@pytest.mark.parametrize('model', LOGS, ids=LOG_IDS)
def test_the_database_refuses_a_reason_of_only_whitespace(model, db):
    from django.db import IntegrityError

    with pytest.raises(IntegrityError):
        model.objects.create(action=model.ACTIONS[0][0], reason='   \n  ')


@pytest.mark.parametrize('model', LOGS, ids=LOG_IDS)
def test_when_it_happened_is_stamped_not_supplied(model):
    created = _field(model, 'created_at')

    assert created.auto_now_add is True, 'a caller-supplied timestamp is a forgeable one'


@pytest.mark.parametrize('model', LOGS, ids=LOG_IDS)
@pytest.mark.parametrize('name', ['changed', 'evidence'])
def test_the_diff_and_the_evidence_encode_dates_and_decimals(model, name):
    """DjangoJSONEncoder on both. A datetime or Decimal reaching a plain JSONField raises mid-write,
    which would take down the change the entry was recording along with the entry."""
    field = _field(model, name)

    assert isinstance(field, models.JSONField)
    assert field.encoder is DjangoJSONEncoder


@pytest.mark.parametrize('model', LOGS, ids=LOG_IDS)
def test_an_undo_is_a_new_entry_pointing_at_the_old_one(model):
    reverses = _field(model, 'reverses')

    assert reverses.remote_field.model is model, 'reverses must be a self-FK'
    assert reverses.remote_field.on_delete is models.SET_NULL


@pytest.mark.parametrize('model', LOGS, ids=LOG_IDS)
def test_only_one_reversal_per_entry_is_DECLARED(model):
    """The declaration only. The database half is `test_only_one_reversal_can_exist_per_entry_...`
    below, now parametrized over both logs -- this one alone would pass against a model whose
    constraint had never reached a migration."""
    unique = [c for c in model._meta.constraints if isinstance(c, models.UniqueConstraint)
              and c.fields == ('reverses',)]

    assert len(unique) == 1, f'{model.__name__} has no unique constraint on `reverses`'
    assert unique[0].condition == Q(reverses__isnull=False), (
        'without the partial condition, every non-reversal row collides on NULL')


@pytest.mark.parametrize('model', LOGS, ids=LOG_IDS)
def test_a_log_is_indexed_for_the_questions_it_exists_to_answer(model):
    """"Who did this", "what happened lately", "what kind of thing was this" -- each an index.

    Deleting `adminaction_subject_idx` used to leave this whole file green, which made the claim
    that introspection beats an abstract base weaker than advertised: the contract covered what the
    two tables SHARE and skipped the thing that makes `AdminAction` worth building.
    """
    indexed = {tuple(index.fields) for index in model._meta.indexes}

    assert ('-created_at', '-id') in indexed, 'the log cannot be paged newest-first off an index'
    assert any(fields[0] == 'actor' for fields in indexed), 'cannot ask what one actor has done'
    assert any(fields[0] == 'action' for fields in indexed), 'cannot filter the log by kind'
    for fields in indexed:
        assert fields[-1] == '-id', (
            f'{fields} stops before the `-id` tie-break `ordering` relies on, so paging needs a '
            f'sort on top of the index')


def test_the_admin_log_can_answer_everything_done_to_one_person():
    """The claim the whole no-GFK argument rests on. A GenericForeignKey could not be indexed this
    way at all, so if this index goes, the reason for the design goes with it."""
    indexed = {tuple(index.fields) for index in AdminAction._meta.indexes}

    assert any(fields[0] == 'subject_user' for fields in indexed)


@pytest.mark.parametrize('model', LOGS, ids=LOG_IDS)
def test_paging_a_log_is_deterministic(model):
    """`created_at` is auto_now_add, so a bulk write produces identical timestamps. Without the id
    tie-break, two adjacent pages can show the same row or skip one."""
    assert model._meta.ordering == ['-created_at', '-id']


@pytest.mark.parametrize('model', LOGS, ids=LOG_IDS)
def test_is_reversed_is_derived_and_not_a_column(model):
    """A stored flag would be a second copy of a fact the FK already carries, and the two would
    eventually disagree about whether something was undone."""
    assert isinstance(getattr(model, 'is_reversed', None), property)
    assert 'is_reversed' not in {f.name for f in model._meta.get_fields()}


# ── what only AdminAction carries ────────────────────────────────────────────────────────────────

def test_the_admin_log_can_name_the_person_it_was_done_to():
    """`subject_user` is what makes "everything ever done to this account" one indexed query.
    `ModerationAction` gets the same pair in P2; until then this is the log that can answer it."""
    subject = _field(AdminAction, 'subject_user')

    assert subject.remote_field.on_delete is models.SET_NULL
    assert subject.null is True
    assert _field(AdminAction, 'subject_label').max_length == audit.MAX_LABEL_LENGTH


def test_the_admin_logs_target_id_fits_every_identifier_it_must_hold():
    """Text, and WIDE ENOUGH -- the assertion that was missing while the column was 64.

    A test can say "not an integer" and prove nothing about fitting; `max_length` is the only
    property of this field carrying a design decision, and it was the one property untested. At 64 a
    100-character badge-series slug could not physically be written, and since Django does not
    truncate a CharField on save, that lands as a DataError which aborts the very transaction the
    entry was auditing.
    """
    from djstripe.models import Subscription

    target_id = _field(AdminAction, 'target_id')

    assert isinstance(target_id, models.CharField)
    assert target_id.max_length >= Subscription._meta.get_field('id').max_length, (
        'a Stripe subscription id does not fit')
    assert target_id.max_length >= 255


def test_the_admin_log_uses_no_generic_foreign_key():
    """A GFK stores a content-type id and no label, so a deleted target reads as "somebody did
    something to nothing" -- the exact failure the frozen labels exist to prevent."""
    names = {f.__class__.__name__ for f in AdminAction._meta.get_fields()}

    assert 'GenericForeignKey' not in names


@pytest.mark.parametrize('model', LOGS, ids=LOG_IDS)
def test_a_log_entry_can_be_read_without_joining_anything(model):
    """An entry is legible on its own; if reading one needed a join, a purged target would take its
    meaning with it.

    Parametrized over BOTH -- which it was not at first, and the model left out was the one that
    violated it: `ModerationAction.__str__` fell through to `self.actor.email`, which both joined and
    put a private address into the repr of a row meant to be read by other people.
    """
    row = model(actor_label='Hunted47', action=model.ACTIONS[0][0], reason='spam',
                target_label='quick takes, 7 days')

    rendered = str(row)

    assert 'Hunted47' in rendered and 'quick takes, 7 days' in rendered


def test_reading_an_entry_never_reaches_for_an_email_address(db):
    """`display_name` exists so a page names people by PSN handle rather than by email address. A
    `__str__` that undoes that on the way to a log viewer or a traceback undoes it everywhere."""
    from tests.factories import ProfileFactory, UserFactory

    actor = UserFactory()
    ProfileFactory(user=actor, psn_username='hunted47', is_linked=True)

    for model in LOGS:
        row = model.objects.create(actor=actor, actor_label='', action=model.ACTIONS[0][0],
                                   reason='no label was captured', target_label='a thing')
        assert actor.email not in str(row), model.__name__ + '.__str__ leaked an email address' 


# ── the table itself, not just its declaration ───────────────────────────────────────────────────
#
# Everything above reads `_meta`, which would pass just as happily against a model with no migration
# behind it. These two touch the real table.

def test_only_one_reversal_can_exist_per_entry_even_without_the_service(db):
    """The service will take a row lock. This is the backstop for anything that ever writes without
    one -- a shell session, a management command, a future second writer."""
    from django.db import IntegrityError

    original = AdminAction.objects.create(
        actor_label='Hunted47', action='restriction_applied', reason='spam, third time')
    AdminAction.objects.create(
        actor_label='Hunted47', action='restriction_lifted', reason='appealed, upheld',
        reverses=original)

    with pytest.raises(IntegrityError):
        AdminAction.objects.create(
            actor_label='Someone Else', action='restriction_lifted', reason='racing the first',
            reverses=original)


def test_two_unreversed_entries_do_not_collide(db):
    """The constraint is PARTIAL. Without `condition=Q(reverses__isnull=False)` every ordinary entry
    would collide with every other on NULL, and the log would accept exactly one row."""
    AdminAction.objects.create(action='restriction_applied', reason='one')
    AdminAction.objects.create(action='restriction_applied', reason='two')

    assert AdminAction.objects.count() == 2


def test_an_entry_still_names_everyone_after_both_accounts_are_deleted(db):
    """The whole reason the labels are frozen rather than read through the FKs at render time."""
    from tests.factories import ProfileFactory, UserFactory

    admin, subject = UserFactory(), UserFactory()
    ProfileFactory(user=admin, psn_username='hunted47', display_psn_username='Hunted47',
                   is_linked=True)
    ProfileFactory(user=subject, psn_username='someone', display_psn_username='Someone',
                   is_linked=True)
    # Captured BEFORE the delete: Django sets `pk` to None on the instance it deletes, so reading it
    # afterwards compares against the string "None". Which is the point in miniature -- the object
    # forgets what it was, and the log does not.
    subject_pk = str(subject.pk)
    entry = AdminAction.objects.create(
        actor=admin, actor_label=audit.frozen_label(admin),
        subject_user=subject, subject_label=audit.frozen_label(subject),
        action='restriction_applied', reason='spam, third time',
        target_type='user', target_id=subject_pk, target_label='quick takes, 7 days')

    admin.delete()
    subject.delete()
    entry.refresh_from_db()

    assert entry.actor is None and entry.subject_user is None
    assert entry.actor_label == 'Hunted47'
    assert entry.subject_label == 'Someone'
    assert entry.target_id == subject_pk, 'the id outlives the row it pointed at'


# ── the shared helpers ───────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize('blank', ['', '   ', None, 'ok', ' x '])
def test_a_reason_that_says_nothing_is_refused(blank):
    with pytest.raises(audit.AuditError):
        audit.require_reason(blank, error=audit.AuditError)


def test_a_real_reason_comes_back_trimmed():
    cleaned = audit.require_reason('  spam, third time  ', error=audit.AuditError)

    assert cleaned == 'spam, third time'


def test_the_error_class_has_no_default():
    """Deliberately awkward. Nothing anywhere catches a bare `AuditError`, so a default would let the
    next service be written the obvious way -- `require_reason(reason)` -- and turn a missing reason
    into a 500 instead of a message somebody can read. Being made to name the exception is the point.
    """
    import inspect

    signature = inspect.signature(audit.require_reason)

    assert signature.parameters['error'].default is inspect.Parameter.empty


def test_a_label_refuses_the_wrong_kind_of_object(db):
    """Loud, not silent. `moderation_service` handles both `CustomUser` and `Profile`, so passing the
    wrong one is an easy mistake -- and swallowing it writes an entry with a LIVE actor and an empty
    label, which renders as "by a deleted account" for an account that exists."""
    from tests.factories import ProfileFactory

    with pytest.raises(AttributeError):
        audit.frozen_label(ProfileFactory(is_linked=True))


def test_each_service_raises_the_exception_its_callers_catch():
    """`_ActionView` catches `ModerationError`. Handing it a bare `AuditError` would turn a missing
    reason into a 500 instead of a message the moderator can read."""
    from trophies.services.moderation_service import ModerationError, _require_reason

    with pytest.raises(ModerationError):
        _require_reason('')


def test_a_frozen_label_is_the_display_name_not_the_email(db):
    from tests.factories import ProfileFactory, UserFactory

    user = UserFactory()
    ProfileFactory(user=user, psn_username='hunted47', display_psn_username='Hunted47',
                   is_linked=True)

    assert audit.frozen_label(user) == 'Hunted47'


def test_a_frozen_label_survives_there_being_no_user():
    """Called for `subject_user` on system actions, which have nobody on the other end."""
    assert audit.frozen_label(None) == ''


def test_a_frozen_label_is_clipped_to_the_column(db):
    """An email address can be 254 characters; `actor_label` is 150. Unclipped, the freeze itself
    would raise and take down the action it was recording."""
    from tests.factories import UserFactory

    user = UserFactory(email='x' * 200 + '@example.com')

    assert len(audit.frozen_label(user)) == audit.MAX_LABEL_LENGTH


def test_the_moderation_service_freezes_names_the_same_way(db):
    """One rulebook, two readers. The moderation service kept its own copy of this until the second
    log arrived, which is two places to disagree about whether an actor is a handle or an address."""
    from tests.factories import ProfileFactory, UserFactory
    from trophies.services.moderation_service import _label

    user = UserFactory()
    ProfileFactory(user=user, psn_username='hunted47', display_psn_username='Hunted47',
                   is_linked=True)

    assert _label(user) == audit.frozen_label(user) == 'Hunted47'
