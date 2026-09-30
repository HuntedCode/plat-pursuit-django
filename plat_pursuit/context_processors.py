import json
import logging

logger = logging.getLogger(__name__)

def site_links(request):
    """Site-wide external links. One source: the Discord invite used to be hardcoded in seven
    templates (with casing drift), while the setting the emails read was never defined."""
    from django.conf import settings
    return {'discord_invite_url': settings.DISCORD_INVITE_URL}


def whats_new_unread(request):
    """Whether this viewer has an unread What's New entry -- the avatar's attention dot.

    ZERO QUERIES, which is what makes a site-wide processor affordable here: `ui_flags` rides the user
    object authentication already loaded, and the entries are a module-level tuple. Nothing is fetched.
    Keep it that way -- this runs on every render of every page, including the Django admin.

    IT IS NOT REDUNDANT WITH THE MODAL, which is the objection I raised when this was first left out.
    The modal fires on the LOBBY, for SYNCED hunters only, so it reaches nobody who lands deep from a
    bookmark or a link, nobody signed in without a linked PSN, and nobody who closes it by reflex having
    read nothing. The dot is the signal for exactly those people.

    Fails closed, like `moderation_alert` below: a viewer loses a dot for one render, nobody gains one.
    """
    try:
        from core import whats_new
        unread = whats_new.is_due(getattr(request, 'user', None)) or whats_new.previewing(request)
        return {'whats_new_unread': unread}
    except Exception:
        logger.debug("Failed to resolve the What's New unread state", exc_info=True)
        return {}


def active_fundraiser(request):
    """
    Inject the currently active fundraiser for the site-wide banner.

    The banner is only shown to viewers who are logged in AND have a
    linked PSN profile: claiming badge artworks requires a profile, so
    the banner is noise for anonymous users and for users who haven't
    finished onboarding. Non-qualifying viewers get an empty context,
    which the banner partial treats as "don't render."

    Caches the fundraiser's PK for 60 seconds (model instances can't be
    JSON-serialized by django-redis's JSONSerializer, so we cache the ID
    and do a cheap PK lookup). A cached value of 0 means "no active
    fundraiser" to distinguish from a cache miss. The cache is shared
    across all users; the per-user gate lives at render time.
    """
    fundraiser = _active_fundraiser_or_none(request)
    return {'active_fundraiser': fundraiser} if fundraiser else {}


def _viewer_has_linked_profile(request):
    """True when the viewer is authenticated AND has a linked PSN profile."""
    user = getattr(request, 'user', None)
    if user is None or not user.is_authenticated:
        return False
    profile = getattr(user, 'profile', None)
    return bool(profile and profile.is_linked)


def high_sync_volume(request):
    """
    Check Redis for high sync volume flag and inject banner data into all templates.
    Single Redis GET per request (sub-millisecond).
    """
    try:
        from trophies.util_modules.cache import redis_client

        raw = redis_client.get('site:high_sync_volume')
        if not raw:
            return {}

        raw_str = raw.decode() if isinstance(raw, bytes) else raw
        parsed = json.loads(raw_str)
        return {
            'high_sync_volume': True,
            'high_sync_volume_count': parsed.get('heavy_count', 0),
            'high_sync_volume_activated_at': parsed.get('activated_at', 0),
        }
    except Exception:
        logger.debug("Failed to read high sync volume flag from Redis", exc_info=True)
        return {}


def psn_outage(request):
    """
    Check Redis for PSN outage flag and inject banner data into all templates.
    Single Redis GET per request (sub-millisecond).
    """
    try:
        from trophies.util_modules.cache import redis_client

        raw = redis_client.get('site:psn_outage')
        if not raw:
            return {}

        raw_str = raw.decode() if isinstance(raw, bytes) else raw
        parsed = json.loads(raw_str)
        return {
            'psn_outage': True,
            'psn_outage_activated_at': parsed.get('activated_at', 0),
        }
    except Exception:
        logger.debug("Failed to read PSN outage flag from Redis", exc_info=True)
        return {}


def hub_subnav(request):
    """
    Resolve the active hub-of-hubs sub-navigation for the current request.

    Inspects ``request.path`` against the configured hub prefixes
    (``core.hub_subnav.HUB_SUBNAV_CONFIG``) and returns the active hub plus
    the active sub-nav slug. Pages that don't belong to any hub get
    ``hub_section=None`` so the ``hub_subnav.html`` template short-circuits
    and renders nothing.

    Dynamic behavior: the personal (My Pursuit) hub appends the viewer's Profile
    as a dynamic item (its URL needs their username), and the personal strip is
    auth-gated (hidden for anon). Viewing your OWN profile swaps that page's
    Community chrome for the personal strip.

    See ``docs/architecture/ia-and-subnav.md`` for the design rationale and
    the URL prefix matching algorithm.
    """
    try:
        from core.hub_subnav import build_rendered_items, resolve_hub_subnav

        match = resolve_hub_subnav(request)
        if match is None:
            return {'hub_section': None}

        hub = match['hub']
        active_slug = match['active_slug']

        # Ownership-aware Profile chrome was removed with the Profile strip-item (2026-08): the swap
        # existed to put your own profile under a personal strip WITH a Profile tab to highlight, and
        # with no such tab it would have rendered a strip highlighting nothing. Every profile page now
        # carries the same chrome whoever is looking, and the avatar menu is the single route to yours.

        is_auth = bool(getattr(request, 'user', None) and request.user.is_authenticated)

        # The personal ("My Pursuit") strip is a login-gated wayfinder: hide it entirely for
        # anonymous viewers so the Home (/) reads as a hero and public members (e.g. /milestones/)
        # don't sprout a personal strip.
        if hub.key == 'my_pursuit' and not is_auth:
            return {'hub_section': None}

        # No dynamic extras: Profile is reached from the avatar menu, and the fundraiser lives in the
        # Support hub.
        is_member = is_auth and bool(getattr(request.user, 'premium_tier', ''))
        items = build_rendered_items(hub, is_authenticated=is_auth, is_member=is_member,
                                     active_slug=active_slug, tags=_subnav_marks(request, hub.key))
        active_label = next((i.label for i in items if i.slug == active_slug), '')

        return {
            'hub_section': hub.key,
            # THE MARKED ITEMS, for the collapsed mobile bar. Its own list rather than a flag, because the bar
            # draws the marks themselves and needs their text, kind and spoken form -- and only the ATTENTION
            # marks: a config chip like 'Soon' is a label on one item, not something waiting for the hunter, and
            # summarising it on the bar would say "look in here" about a page that is not ready yet.
            'hub_subnav_marks': [i for i in items if i.tag_kind],
            'hub_subnav_label': hub.label,
            'hub_subnav_icon': hub.icon,
            'hub_subnav_items': items,
            'hub_subnav_active_slug': active_slug,
            'hub_subnav_active_label': active_label,   # current page, for the mobile collapse bar
        }
    except Exception:
        logger.debug("Failed to resolve hub_subnav for path %s", request.path, exc_info=True)
        return {'hub_section': None}


def _active_fundraiser_or_none(request=None):
    """The currently-live, banner-active Fundraiser (or None), gated to viewers with a linked
    profile (matches the site-wide banner audience). Shares the ``fundraiser:active_banner`` cache
    key (60s, PK-only) so it's a cache GET on the hot path."""
    if request is not None and not _viewer_has_linked_profile(request):
        return None
    try:
        from fundraiser.models import get_active_fundraiser
        return get_active_fundraiser()
    except Exception:
        logger.debug("Failed to resolve active fundraiser", exc_info=True)
        return None


def moderation_alert(request):
    """The Mod Center entry in the avatar menu, and whether it needs attention.

    Everyone who is not a moderator returns an empty dict before anything else happens: no cache
    read, no query, nothing. That gate is the whole cost of this processor for ~every visitor.

    The FLAG is separate from the COUNT on purpose. The entry has to appear for a moderator whose
    queues are empty -- a link that materialises only when there is work is a link nobody can find
    when they go looking for it. The count only decides whether it is wearing a marker.
    """
    try:
        # INSIDE the try, not above it. `is_mod_or_admin` reads `user.is_moderator` by bare attribute
        # access, deliberately, so that losing the property breaks loudly -- which was the right call
        # when its only caller was a /mod/ gate. This caller is every page render on the site,
        # including the Django admin, so "loudly" would mean a site-wide 500.
        from trophies.mixins import is_mod_or_admin
        if not is_mod_or_admin(getattr(request, 'user', None)):
            return {}
        from trophies.services import moderation_service
        return {'show_mod_center': True, 'mod_open_count': moderation_service.open_report_count()}
    except Exception:
        # Nothing, rather than a guess. The gate now lives inside the try, so a failure here can mean
        # "we could not establish whether this person is a moderator" -- and the safe answer to that
        # is no. A moderator loses a shortcut for one render; nobody gains one.
        logger.debug("Failed to resolve the moderation attention count", exc_info=True)
        return {}


#: The XP mark's three parts. A CONSTANT because the aria clause is a literal SHARED with `navbar.html`,
#: `mobile_tabbar.html` and `challenge-detail.js` (which strips it from the accessible name after a claim) --
#: that coupling is pinned by a test, and a fourth loose copy here would have sat outside the pin.
_XP_MARK = ('XP', 'xp', 'Job XP waiting to be claimed')


def _count_mark(count):
    """The claimable-contract chip: `(text, kind, aria)`.

    THE SAME `9+` CAP THE NAV BADGE USES, and the same split -- the cap in the chip, the RAW number in the
    spoken label, because "9+ contracts" is a worse sentence than the count. A rail pill is
    `white-space: nowrap`, so a three-digit chip pushes its neighbours into the overflow sheet.

    Its own function so the preview door and the real path cannot format it differently.
    """
    return ('9+' if count > 9 else str(count), 'count',
            '%d contract%s ready to claim' % (count, '' if count == 1 else 's'))


def _subnav_marks(request, hub_key):
    """Attention chips for the My Pursuit strip: `{slug: (text, kind, aria)}`, or None.

    WHY THE STRIP NEEDS ITS OWN, when the parent nav item already carries them: the parent AGGREGATES. It says
    "something of yours is waiting" and, now that TWO pages pay job XP, it cannot say which -- the owner hit
    exactly that, reading a lit XP pill with no idea where to claim. So the strip disambiguates: Career carries
    the claimable-CONTRACT count, My Challenges the XP mark. Each item says its own thing (owner, 2026-09-30).

    IT SHARES THE NAV'S TWO CACHE KEYS, so the per-request total is the same whichever processor runs first --
    and `hub_subnav` is registered BEFORE `career_attention` in settings, so today THIS is the one that pays a
    cold miss and the nav collects the hits. (An earlier version had that backwards, which would mislead anyone
    profiling a slow first render of `/career/`.) On a warm cache it is two extra Redis GETs per My Pursuit
    render; there is no request-local memo.

    MY PURSUIT ONLY, gated before anything is read: this runs on every page of the site, and `/games/` has no
    business paying for a question about somebody's contracts.
    """
    if hub_key != 'my_pursuit':
        return None
    try:
        from trophies.services import career_attention as svc

        # THE PROFILE LOOKUP IS INSIDE THE TRY, which is a correction. `hasattr(user, 'profile')` is a
        # reverse-OneToOne DB query and `hasattr` swallows only `AttributeError` -- so a `DatabaseError` there
        # escaped to `hub_subnav`'s blanket handler, which returns `{'hub_section': None}` and takes away the
        # WHOLE STRIP rather than a chip. This processor runs first, so it is the earliest profile lookup on a
        # My Pursuit page and the likeliest one to raise.
        user = getattr(request, 'user', None)
        if not (user and user.is_authenticated and hasattr(user, 'profile')):
            return None

        count = svc.claimable_count(user.profile)
        unclaimed_xp = svc.has_unclaimed_challenge_xp(user.profile)

        # THE PREVIEW DOOR, honoured here too, and resolved the SAME WAY the nav's own processor resolves it --
        # `forced[0] if not None else (count or 3)`, then drop a zero. `?preview=career-markers` exists because
        # these marks only show when there is something to say, which makes them the hardest thing on the site
        # to look at deliberately; a door that lit the three NAV markers and left the strip bare would read as a
        # broken feature rather than as a preview.
        #
        # MIRRORED RATHER THAN REINVENTED: the first version branched early with its own default and rendered a
        # chip reading "0" at `&n=0` -- the one combination the door exists to produce, since that is how the XP
        # mark is seen on its own. Two code paths deciding what a preview means is how a door starts showing a
        # state the real page cannot.
        forced = svc.preview_counts(request)
        if forced is not None:
            count = forced[0] if forced[0] is not None else (count or 3)
            unclaimed_xp = forced[2]

        marks = {}
        if count:
            marks['career'] = _count_mark(count)
        if unclaimed_xp:
            marks['my_challenges'] = _XP_MARK
        return marks or None
    except Exception:
        # FAILS CLOSED like every other marker: a hunter loses a chip for one render, nobody gains one, and
        # the strip is wayfinding -- it must never be the reason a page 500s.
        logger.debug('Could not resolve sub-nav attention marks', exc_info=True)
        return None


def career_attention(request):
    """The three markers on the My Pursuit nav item: a claim COUNT, an XP mark, and a NEW pill.

    A number for contracts waiting to be claimed; the word XP for Challenge job XP waiting to be
    claimed; the word NEW for news. Only the first is a count, because only the first is a quantity a
    hunter acts on one at a time -- a run with four paid-up squares is one press of Claim all.

    PRECEDENCE, when more than one applies (owner, 2026-09-29): count, then the XP mark, then New.
    Two kinds of waiting reward, then news: contracts first because the count is a QUANTITY worked
    through one at a time, Challenge XP second because it is one press however many squares are owed,
    and news last because it is the only one that is not theirs yet.

    The nav has room for all three only above 1280px; below that, and on the mobile tab bar, the CSS
    shows the first that applies -- see `chrome.css`, where the width budget is quantified.

    Anonymous and profile-less viewers return an empty dict before anything happens, which is the
    whole cost for them. For everyone else it is TWO cached per-user reads (the claim count and the
    challenge-XP flag) plus one cached SITE-WIDE value compared against a marker already on the user
    object -- see `trophies.services.career_attention` for why that last half is free. On a double
    miss that is three queries, not one: this paragraph said one for as long as there were two
    markers and one per-user read, and a stale cost figure in the one file that runs on every page
    is how the next marker gets budgeted against a number that is wrong by a factor.

    Fails closed, like `whats_new_unread` and `moderation_alert` above: a hunter loses a marker for
    one render, nobody gains one. A nav that 500s because a badge could not be counted would be a
    poor trade for a marker.
    """
    user = getattr(request, 'user', None)
    if not (user and user.is_authenticated and hasattr(user, 'profile')):
        return {}
    try:
        from trophies.services import career_attention as svc
        count = svc.claimable_count(user.profile)
        new = svc.has_new_contracts(user)
        # Job XP earned on a Challenge square and not yet claimed. A WORD rather than a number: a
        # hunter with four paid-up squares presses Claim all once, so "how many" is not the question,
        # and the count beside it stays the only quantity on the item.
        unclaimed_xp = svc.has_unclaimed_challenge_xp(user.profile)
        # `?preview=career-markers` (staff): the markers only show when there is something to say,
        # which makes them the hardest thing here to look at on purpose. See `svc.preview_counts`.
        forced = svc.preview_counts(request)
        if forced is not None:
            count = forced[0] if forced[0] is not None else (count or 3)
            new = forced[1]
            unclaimed_xp = forced[2]
        return {'career_claimable': count, 'career_new_contracts': new,
                'career_unclaimed_xp': unclaimed_xp}
    except Exception:
        logger.debug("Failed to resolve the My Pursuit attention markers", exc_info=True)
        return {}


def navsync(request):
    """Global profile sync state for the navbar's status-aware avatar + panel.

    The old hotbar was a per-view bar (ProfileHotbarMixin); the sync surface now
    lives in the always-present navbar, so its context must be global. Cheap: every
    value reads off the already-loaded ``request.user.profile`` (no new queries; the
    queue lookup only runs mid-sync). Anon / profile-less viewers get nothing, so the
    navbar renders the plain account avatar with no sync ring.
    """
    user = getattr(request, "user", None)
    if not (user and user.is_authenticated and hasattr(user, "profile")):
        return {}
    profile = user.profile
    data = {
        "profile": profile,
        "sync_status": profile.sync_status,
        "progress_percentage": profile.sync_percentage,
        "seconds_to_next_sync": profile.get_seconds_to_next_sync(),
    }
    if profile.sync_status == "syncing":
        try:
            from trophies.views.sync_views import _get_queue_position
            data["queue_position"] = _get_queue_position(profile.id)
        except Exception:
            logger.debug("Failed to resolve sync queue position", exc_info=True)
    return {"navsync": data}
