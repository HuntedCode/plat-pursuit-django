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
def test_a_reason_is_structurally_required(model):
    """The one field that turns a list of timestamps into an audit trail. Not nullable, not blank:
    the services enforce it, and the column must not quietly allow what they refuse."""
    reason = _field(model, 'reason')

    assert isinstance(reason, models.TextField)
    assert reason.null is False, 'a null reason answers "what happened" but never "why"'
    assert reason.blank is False


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
def test_the_database_allows_only_one_reversal_per_entry(model):
    """The services take a row lock; this is what holds if anything ever writes without one."""
    unique = [c for c in model._meta.constraints if isinstance(c, models.UniqueConstraint)
              and c.fields == ('reverses',)]

    assert len(unique) == 1, f'{model.__name__} has no unique constraint on `reverses`'
    assert unique[0].condition == Q(reverses__isnull=False), (
        'without the partial condition, every non-reversal row collides on NULL')


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


def test_the_admin_logs_target_id_is_text_not_a_number():
    """A Stripe subscription id, a badge-series slug and a primary key all have to fit. An integer
    column can only describe the third, which is the smallest share of what this log records."""
    target_id = _field(AdminAction, 'target_id')

    assert isinstance(target_id, models.CharField)
    assert not isinstance(target_id, models.IntegerField)


def test_the_admin_log_uses_no_generic_foreign_key():
    """A GFK stores a content-type id and no label, so a deleted target reads as "somebody did
    something to nothing" -- the exact failure the frozen labels exist to prevent."""
    names = {f.__class__.__name__ for f in AdminAction._meta.get_fields()}

    assert 'GenericForeignKey' not in names


def test_the_admin_log_can_be_read_without_joining_anything():
    """The point of the type/id/label triple: an entry is legible on its own. If reading one needed
    a join, a purged target would take the entry's meaning with it."""
    row = AdminAction(
        actor_label='Hunted47', action='restriction_applied', reason='spam',
        subject_label='someone', target_type='user', target_id='42',
        target_label='quick takes, 7 days')

    rendered = str(row)

    assert 'Hunted47' in rendered and 'quick takes, 7 days' in rendered


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
        audit.require_reason(blank)


def test_a_real_reason_comes_back_trimmed():
    assert audit.require_reason('  spam, third time  ') == 'spam, third time'


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
