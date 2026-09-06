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
flags should not be undone by the one a colleague handled a minute ago. The page reports both
counts.
"""
from django.contrib import messages
from django.template.response import TemplateResponse

from trophies.services.moderation_service import ModerationError

#: The hidden field that says "this POST carries a reason", distinguishing the confirmed submission
#: from the first click that opened the page.
CONFIRM_FIELD = '_reasoned_confirm'


def run_with_a_reason(modeladmin, request, queryset, *, title, verb, apply):
    """Either render the confirmation page, or do the work.

    `apply(obj, user, reason)` is the service function. It may raise `ModerationError` for a row
    somebody else already handled; that is reported, not raised, because it is an ordinary outcome
    rather than a failure of the sweep.
    """
    reason = (request.POST.get('reason') or '').strip()

    if request.POST.get(CONFIRM_FIELD) and len(reason) >= 3:
        done, refused = 0, []
        for obj in queryset:
            try:
                apply(obj, request.user, reason)
                done += 1
            except ModerationError as exc:
                refused.append(f'{obj}: {exc}')

        if done:
            modeladmin.message_user(
                request, f'{verb} {done} of {done + len(refused)}, logged against your name.',
                messages.SUCCESS)
        for note in refused:
            modeladmin.message_user(request, note, messages.WARNING)
        if not done and not refused:
            modeladmin.message_user(request, 'Nothing was selected.', messages.WARNING)
        return None

    # Either the first click, or a submission with no usable reason. Both land on the page; the
    # second says why it is still here rather than silently re-rendering.
    context = {
        **modeladmin.admin_site.each_context(request),
        'title': title,
        'verb': verb,
        'objects': queryset,
        'action_name': request.POST.get('action', ''),
        'selected': queryset.values_list('pk', flat=True),
        'confirm_field': CONFIRM_FIELD,
        'reason': reason,
        'reason_was_too_short': bool(request.POST.get(CONFIRM_FIELD)),
        'opts': modeladmin.model._meta,
    }
    return TemplateResponse(request, 'admin/reasoned_action.html', context)
