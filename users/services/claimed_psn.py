"""The PSN name a visitor claimed on the landing ("That's you? Sign up free to claim it."), carried in the session
from sign-up to Link PSN's first step, which fills it in.

allauth carries it too, as the sign-up's `next` (Link PSN with `?psn=`), but only on its pending login: that is
dropped after `ACCOUNT_LOGIN_TIMEOUT` (15 minutes, allauth's default), so an email confirmed later lands on the
sign-in page with no `next`, and "Wrong address? Fix it" clears it outright (`pending_signup.forget`). This
record is the session's own and outlives both. It is a prefill, nothing more: Link PSN validates it like a typed
name and forgets it once step 1 is submitted.

THE RECORD BELONGS TO THE ACCOUNT THAT CLAIMED IT. A browser can be shared: the hunter who claimed "alice" may
confirm on their phone and never sign in here, and the next person to sign up or sign in on this browser must not
find "alice" waiting. So the record carries the user it was made for, and only that user is shown it. "Fix it"
deletes that account and reopens sign-up, so it RELEASES the record (`release`), and the next sign-up on this
browser adopts it: the one hand-over, made on purpose rather than by any later sign-up inheriting it. Only the
claiming account's own Fix it releases it: a different person's Fix it on the same browser leaves it alone.

Not part of `pending_signup`'s record on purpose: that one is forgotten at sign-in, and this one is needed after.
"""
from urllib.parse import parse_qs, urlparse

from django.urls import reverse

SESSION_KEY = 'pp_claimed_psn'
MAX_LENGTH = 16   # a PSN Online ID's own limit; anything longer cannot be one


def _name_from_next(request):
    nxt = request.POST.get('next') or request.GET.get('next') or ''
    parsed = urlparse(nxt)
    if parsed.netloc or parsed.path != reverse('link_psn'):
        return ''
    name = (parse_qs(parsed.query).get('psn') or [''])[0].strip()
    return name if len(name) <= MAX_LENGTH else ''


def remember(request, user):
    """At sign-up. A `next` pointing at Link PSN with a `?psn=` makes a record for this new account; with no such
    `next`, a RELEASED record (Fix it) is adopted by this account. Any other record is left alone, and stays
    invisible to this account."""
    name = _name_from_next(request)
    if name:
        request.session[SESSION_KEY] = {'name': name, 'user_id': user.pk}
        return
    record = request.session.get(SESSION_KEY)
    if isinstance(record, dict) and record.get('user_id') is None and record.get('name'):
        request.session[SESSION_KEY] = {'name': record['name'], 'user_id': user.pk}


def release(request, deleted_user_id):
    """Fix it deleted `deleted_user_id`. If this record was made for that account, let the next sign-up on this
    browser adopt it. A record made for anyone else stays theirs: on a shared browser, C fixing C's own typo must
    not hand A's claim to C's next sign-up (audit-caught)."""
    record = request.session.get(SESSION_KEY)
    if isinstance(record, dict) and record.get('name') and record.get('user_id') == deleted_user_id:
        request.session[SESSION_KEY] = {'name': record['name'], 'user_id': None}


def claimed_name(request):
    """The name, for the account that claimed it only."""
    record = request.session.get(SESSION_KEY)
    user = getattr(request, 'user', None)
    if not isinstance(record, dict) or not user or not user.is_authenticated:
        return ''
    return record.get('name', '') if record.get('user_id') == user.pk else ''


def forget(request):
    request.session.pop(SESSION_KEY, None)
