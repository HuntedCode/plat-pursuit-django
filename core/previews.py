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
    """Staff or moderator. Bare attribute access on purpose -- `is_moderator` is a property, so a
    missing one should be visible rather than silently falsey."""
    if user is None or not getattr(user, 'is_authenticated', False):
        return False
    return bool(getattr(user, 'is_staff', False) or getattr(user, 'is_moderator', False))


def previewing(request, slug) -> bool:
    """Whether this request is a team preview of `slug`."""
    if request is None or getattr(request, 'GET', None) is None:
        return False
    if request.GET.get('preview') != slug:
        return False
    return is_team(getattr(request, 'user', None))
