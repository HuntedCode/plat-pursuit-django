"""Django-admin bulk actions that go through `moderation_service` and therefore need a reason.

THE PROBLEM THIS SOLVES. `GameFlagAdmin` and `BlurbReportAdmin` carry the same two decisions the Mod
Center queues do -- approve/dismiss a flag, hide/dismiss a reported take -- and used to apply them
with `queryset.update()` and a direct service call. Same writes, no audit entry, no reason, and no
status precondition, so a bulk sweep could overwrite a decision somebody had just made and leave no
record that it happened.

Routing them through `moderation_service` fixes all of that and collides with one thing: the service
requires a REASON, and a Django admin action has nowhere to type one. A hardcoded "admin bulk sweep"
would satisfy the check and produce exactly the log the check exists to prevent -- timestamps with no
why, which answers the one question an appeal actually asks.

So: Django's standard intermediate-page pattern. The action returns a confirmation page carrying the
selected rows and a required reason; that page posts back to the same action, which now has one.

WHY EACH ROW IS ITS OWN TRANSACTION. Every service call is already atomic. Wrapping the loop in a
second `atomic()` would mean one refused row -- somebody else got there first, which is a NORMAL
outcome on a queue two people work -- rolls back the decisions that succeeded. A sweep of forty
flags should not be undone by the one a colleague handled a minute ago. The page reports both counts.
"""
from django.contrib import messages
from django.template.response import TemplateResponse

from core.services.audit import MIN_REASON_LENGTH
from trophies.services.moderation_service import ModerationError

#: The hidden field that says "this POST carries a reason", distinguishing the confirmed submission
#: from the first click that opened the page.
CONFIRM_FIELD = '_reasoned_confirm'

#: How many refusals to name individually before summarising. One message per row is unbounded: a
#: sweep of a full changelist page where most were already handled renders a wall of near-identical
#: warnings and grows the session row holding them.
MAX_NAMED_REFUSALS = 10


def _selected_rows(modeladmin, request):
    """The rows to act on, resolved from the POSTED pks rather than from the passed queryset.

    THIS IS THE FIX FOR A REAL BUG, not a stylistic choice. Django hands an action
    `ChangeList.get_queryset(request)` intersected with the selected pks -- and the confirmation page
    posts back to the same URL, query string included, so the CHANGELIST FILTER IS RE-APPLIED on the
    second submit. Both of these admins ship a status filter and are meant to be worked at
    `?status=pending`.

    So a row a colleague decided between the page rendering and the admin pressing submit no longer
    matched the filter, was dropped before this function ever saw it, never reached the service, and
    never appeared in the refusals -- while the success line counted a smaller denominator and read
    "Approved 4 of 4". The page listed five. That is precisely the "somebody got there first" race
    this whole module exists to report, mis-reported.

    Resolving from the model manager keeps every row the admin was shown, so the service's own
    precondition can refuse it BY NAME. Safe here because neither ModelAdmin scopes rows per user --
    and the whole site is superusers only -- but a ModelAdmin that did would need its `get_queryset`
    honoured instead.
    """
    pks = request.POST.getlist('_selected_action')
    return modeladmin.model._default_manager.filter(pk__in=pks)


def _action_name(request):
    """The action being confirmed, read the way Django reads it.

    `POST['action']` is not enough: with `actions_on_bottom` the form renders TWO action selects, and
    Django picks between them with `POST['index']`. Taking the first would round-trip the empty top
    dropdown and the second submit would die as "No action selected" -- a silent no-op for anybody
    who turns that option on.
    """
    choices = request.POST.getlist('action')
    try:
        return choices[int(request.POST.get('index', 0))]
    except (IndexError, ValueError, TypeError):
        return choices[0] if choices else ''


def run_with_a_reason(modeladmin, request, queryset, *, title, verb, apply):
    """Either render the confirmation page, or do the work.

    `apply(obj, user, reason)` is the service function. It may raise `ModerationError` for a row
    somebody else already handled; that is reported, not raised, because it is an ordinary outcome
    rather than a failure of the sweep.
    """
    reason = (request.POST.get('reason') or '').strip()

    if request.POST.get(CONFIRM_FIELD) and len(reason) >= MIN_REASON_LENGTH:
        done, refused = 0, []
        try:
            for obj in _selected_rows(modeladmin, request):
                try:
                    apply(obj, request.user, reason)
                    done += 1
                except ModerationError as exc:
                    refused.append(f'{obj}: {exc}')
        finally:
            # In a `finally`, because the rows already processed STAY COMMITTED -- there is no
            # transaction around the loop, deliberately. Without this, an unexpected error partway
            # through gave the admin a 500 page and no idea how far the sweep had got.
            _report(modeladmin, request, verb, done, refused)
        return None

    # Either the first click, or a submission with no usable reason. Both land on the page; the
    # second says why it is still here rather than silently re-rendering.
    context = {
        **modeladmin.admin_site.each_context(request),
        'title': title,
        'verb': verb,
        'objects': queryset,
        'action_name': _action_name(request),
        'selected': queryset.values_list('pk', flat=True),
        'confirm_field': CONFIRM_FIELD,
        'reason': reason,
        'reason_was_too_short': bool(request.POST.get(CONFIRM_FIELD)),
        'opts': modeladmin.model._meta,
    }
    return TemplateResponse(request, 'admin/reasoned_action.html', context)


def _report(modeladmin, request, verb, done, refused):
    """What happened, in as few messages as will carry it."""
    total = done + len(refused)
    if total:
        modeladmin.message_user(
            request, f'{verb} {done} of {total}, logged against your name.',
            messages.SUCCESS if done else messages.WARNING)
    else:
        modeladmin.message_user(
            request, 'None of the selected rows are still there.', messages.WARNING)

    for note in refused[:MAX_NAMED_REFUSALS]:
        modeladmin.message_user(request, note, messages.WARNING)
    if len(refused) > MAX_NAMED_REFUSALS:
        modeladmin.message_user(
            request, f'...and {len(refused) - MAX_NAMED_REFUSALS} more already handled.',
            messages.WARNING)
