"""The team preview door: `?preview=<slug>`, for looking at something that will not show itself.

WHY IT IS SHARED. Three surfaces need the same door now -- What's New (a one-shot that has been
spent), the Career new-contracts modal (gated on a per-hunter marker), and the My Pursuit nav markers
(which only appear when there is something to say). Each had, or would have had, its own copy of the
same four lines, and copies drift: the What's New door already had to be pulled out of the modal
because the avatar dot's half was missing, and the note it left behind is the rule here too -- a door
that opens only half of a thing is not a preview.

WHAT EVERY DOOR SHARES, and what a new one must not vary:

  STAFF OR MODERATOR, always. Several of these bypass the gate they exist to preview, so the
  querystring cannot be something anybody can type.
  NO STATE IS WRITTEN. Previewing never spends anything and never has to be undone -- which is the
  property that makes it safe to hand to someone and say "just add this to the URL".
  ON THE LIVE PAGE, not a harness. What you are looking at is the real render.
"""


def is_team(user) -> bool:
    """Staff or moderator -- `trophies.mixins.is_mod_or_admin`, which is the codebase's one answer.

    This started as a fourth hand-written copy of `is_staff or is_moderator`, complete with a
    docstring claiming bare attribute access while the code used `getattr(..., False)` -- the exact
    silently-falsey path it said it was avoiding, sitting in the module that had just been declared
    canonical. Deduplicating the preview doors while quietly forking the gate underneath them is not
    deduplicating anything, and `is_mod_or_admin`'s own docstring says why: "three hand-written
    copies of `is_staff or is_moderator` is how one of them ends up subtly different".

    Delegating also gains an `is_active` check the copies did not have. On a real request the auth
    backend already turns a deactivated user into AnonymousUser, so this is unreachable rather than a
    fix -- but revoking access is the moment a stale user object must not still say yes, and a
    preview door is exactly the sort of thing left open in a tab.

    Imported inside the function: `core` is imported early and `trophies.mixins` pulls in the rest of
    that app, so a module-level import here would be a load-order hazard for no gain.
    """
    from trophies.mixins import is_mod_or_admin

    return is_mod_or_admin(user)


def previewing(request, slug) -> bool:
    """Whether this request is a team preview of `slug`."""
    if request is None or getattr(request, 'GET', None) is None:
        return False
    if request.GET.get('preview') != slug:
        return False
    return is_team(getattr(request, 'user', None))
