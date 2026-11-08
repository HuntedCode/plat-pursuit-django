import queue
import time
import requests
import logging
import os
from datetime import timedelta
from django.conf import settings
from django.utils import timezone
from dotenv import load_dotenv
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

webhook_queue = queue.Queue()

load_dotenv()
PROXY_URL = os.getenv('PROXY_URL')
PROXIES = None
if PROXY_URL:
    # Parse the proxy URL to ensure it's correctly formatted
    parsed_proxy = urlparse(PROXY_URL)
    if not parsed_proxy.scheme or not parsed_proxy.hostname or not parsed_proxy.port:
        raise ValueError("Invalid PROXY_URL format in .env")
    PROXIES = {
        'http': PROXY_URL,
        'https': PROXY_URL
    }

def check_proxy_ip():
    if not PROXY_URL:
        logger.info("No proxy configured, skipping IP check.")
        return
    try:
        response = requests.get('https://api.ipify.org?format=text', proxies=PROXIES, timeout=10)
        response.raise_for_status()
        ip = response.text.strip()
        logger.info(f"Outbound IP via proxy: {ip}")
    except requests.RequestException as e:
        logger.error(f"Failed to check outbound IP via proxy: {e}")
        raise 

def webhook_sender_worker():
    check_proxy_ip()

    while True:
        payload, webhook_url = webhook_queue.get()
        max_retries = 5
        retry_count = 0
        while retry_count < max_retries:
            try:
                response = requests.post(webhook_url, json=payload, proxies=PROXIES)
                response.raise_for_status()
                logger.info(f"Successfully sent webhook payload to {webhook_url}")
                break
            except requests.exceptions.HTTPError as e:
                if response.status_code == 429:
                    retry_after = int(response.headers.get('Retry-After', 1))
                    logger.warning(f"Rate limited (429) on {webhook_url}. Retrying after {retry_after} seconds.")
                    time.sleep(retry_after + 0.5)
                    retry_count += 1
                else:
                    logger.error(f"Webhook send failed: {e}")
                    break
            except requests.RequestException as e:
                logger.error(f"Webhook send failed: {e}")
                time.sleep(1)
                retry_count += 1
        if retry_count >= max_retries:
            logger.error(f"Max retries exceeded for webhook to {webhook_url}. Dropping payload.")
        webhook_queue.task_done()
        time.sleep(1)

def queue_webhook_send(payload, webhook_url=settings.DISCORD_PLATINUM_WEBHOOK_URL):
    webhook_queue.put((payload, webhook_url))
    logger.info(f"Queued webhook to send to {webhook_url}")

#: Platinums a sync has earned and not yet announced (a Set of EarnedTrophy ids). Filled per game during
#: `sync_trophies`, drained ONCE by `sync_complete`, so a hunter who pops twelve platinums in one sync is
#: one ping, not twelve. A Set, not a List: a retried batch re-queues the same id and must not list it twice.
_PENDING_PLATS_KEY = 'pending_discord_plats:{profile_id}'
#: Outlives any real sync, so a sync_complete that re-queues games for a follow-up pass still finds them.
#: A sync that dies outright drops them after this, which is the right way round: a stale ping is worse
#: than a missed one.
_PENDING_PLATS_TTL = 86400

#: Lines in a multi-platinum embed. A platinum line carries a trophy name AND a game title (both PSN free
#: text), so it runs longer than a badge line; 10 keeps a typical batch readable.
_MAX_PLATINUM_LINES = 10
#: Discord 400s a longer description, and `webhook_sender_worker` drops any non-429 whole, which would
#: lose the entire batch. Both names are `max_length=255` and escaping can double them, so the line cap
#: alone cannot guarantee a fit; the builder also stops adding lines at this budget.
_DESCRIPTION_LIMIT = 4096
#: Room held back for the "…and N more" line.
_MORE_LINE_RESERVE = 32
#: How recent a platinum must be to be news. Checked again at flush, so ids left by a sync that died
#: before flushing cannot ping stale platinums on a later sync.
PLATINUM_NEWS_WINDOW = timedelta(days=2)


def queue_platinum_announcement(profile_id, earned_trophy_id):
    """Hold a newly earned platinum for this sync's ONE consolidated Discord ping. Never raises: the
    announcement is the least important thing a trophy save does."""
    from trophies.util_modules.cache import redis_client

    key = _PENDING_PLATS_KEY.format(profile_id=profile_id)
    try:
        pipe = redis_client.pipeline()
        pipe.sadd(key, earned_trophy_id)
        pipe.expire(key, _PENDING_PLATS_TTL)
        pipe.execute()
    except Exception:
        logger.exception("Failed to queue platinum announcement: profile=%s et=%s", profile_id, earned_trophy_id)


def flush_platinum_announcements(profile):
    """Send everything `queue_platinum_announcement` held for this profile as ONE embed, and clear it.

    Read and delete in one MULTI, so a platinum queued between the two lands in the next flush rather than
    vanishing. Each id is re-checked against the DB at send time: still earned (a rolled-back batch can
    leave a queued id whose earn never committed) and still recent. Never raises.

    Shovelware is INCLUDED: one ping per sync means it can no longer flood the channel, and ordering
    rarest first sinks it (shovelware platinums run common) into the "…and N more" tail.
    """
    from django.db.models import Case, IntegerField, Value, When

    from trophies.models import EarnedTrophy
    from trophies.util_modules.cache import redis_client

    if not profile:
        return
    key = _PENDING_PLATS_KEY.format(profile_id=profile.id)
    try:
        pipe = redis_client.pipeline(transaction=True)
        pipe.smembers(key)
        pipe.delete(key)
        raw_ids, _ = pipe.execute()
        if not raw_ids or not profile.discord_id:
            return
        earned = list(
            EarnedTrophy.objects
            .filter(id__in=[int(i) for i in raw_ids], profile=profile, earned=True,
                    earned_date_time__gte=timezone.now() - PLATINUM_NEWS_WINDOW)
            # concept + igdb_match feed the headliner's art (`_game_art_url`: both chains read the match's
            # image-id columns); raw_response is the ~30 KB IGDB blob neither reads (project CLAUDE.md,
            # Image Styling Conventions).
            .select_related('trophy__game__concept__igdb_match')
            .defer('trophy__game__concept__igdb_match__raw_response')
            # Rarest PSN earn rate first. 0.0 is the model default for "PSN has not reported a rate yet",
            # not a real 0%, so those sort after every known rate rather than headlining the message.
            .annotate(rate_unknown=Case(When(trophy__trophy_earn_rate__lte=0, then=Value(1)),
                                        default=Value(0), output_field=IntegerField()))
            .order_by('rate_unknown', 'trophy__trophy_earn_rate', '-earned_date_time', '-id')
        )
        send_platinums_earned_notification(profile, earned)
    except Exception:
        logger.exception("Platinum announcement flush failed: profile=%s", profile.id)


def _game_art_url(game):
    """The headliner's art for the embed's large image: the site's shared landscape chain
    (`Concept.get_landscape_url`: trusted IGDB screenshots, then artworks, then PSN `bg_url`), which fills
    the embed's width, else the portrait cover chain (`display_image_url_large`): the same two chains the
    contract modal pairs, at the larger cover size. Never hand-roll either chain here. None when neither is an absolute URL, so the
    caller omits the image rather than sending a bad one. Never raises: art is decoration, and a failure
    here must not cost the ping."""
    try:
        url = (game.concept.get_landscape_url() if game.concept_id else None) or game.display_image_url_large
    except Exception:
        logger.exception("Platinum embed art lookup failed: game=%s", getattr(game, 'id', None))
        return None
    return url if url and str(url).startswith('http') else None


def _platinum_line(platinum_emoji, earned_trophy):
    """One list line; `earned_trophy` is a stack's rarest member, so the rate shown is the stack's best."""
    trophy = earned_trophy.trophy
    return (f"{platinum_emoji} *{escape_md(trophy.trophy_name)}* in **{escape_md(trophy.game.title_name)}**"
            f" · 🌟 {trophy.trophy_earn_rate}%")


def _utf16_len(text):
    """Length as Discord may count it (UTF-16 code units): 🌟 and any emoji in a PSN title count as two,
    so measuring in Python code points could pass a description Discord then rejects."""
    return len(text.encode('utf-16-le')) // 2


def _stacks(earned_trophies):
    """Group platinums by concept, so the 4+ regional/platform stacks of one game a hunter pops together
    read as ONE line, not four near-identical ones. A game with no concept stands alone. Groups keep the
    input order, so a rarest-first list yields groups ordered by each one's rarest member."""
    groups = {}
    for et in earned_trophies:
        game = et.trophy.game
        groups.setdefault(game.concept_id or ('game', game.id), []).append(et)
    return list(groups.values())


def send_platinums_earned_notification(profile, earned_trophies):
    """ONE Discord embed for every platinum a profile earned this sync, in the order given (the flush passes
    them rarest first, so the first is the headliner: its trophy icon is the thumbnail and its game's art is
    the large image).

    A single platinum keeps the message hunters already know (its trophy icon, its PSN rarity, its date).
    Several become one list under a count, one line per game (stacks collapse to "×N", see `_stacks`), capped
    at `_MAX_PLATINUM_LINES` with the remaining PLATINUMS counted, so a shovelware-heavy whale's sync can
    neither flood the channel nor overflow the embed. The title and "…and N more" both count platinums, so
    the numbers add up however the lines are grouped.
    """
    if not profile or not earned_trophies or not profile.discord_id:
        return

    platinum_emoji = f"<:Platinum_Trophy:{settings.PLATINUM_EMOJI_ID}>" if settings.PLATINUM_EMOJI_ID else "🏆"
    plat_pursuit_emoji = f"<:PlatPursuit:{settings.PLAT_PURSUIT_EMOJI_ID}>" if settings.PLAT_PURSUIT_EMOJI_ID else "🏆"
    count = len(earned_trophies)
    headliner = earned_trophies[0]
    dates = sorted({et.earned_date_time.strftime('%Y-%m-%d') for et in earned_trophies if et.earned_date_time})
    earned_on = dates[0] if len(dates) == 1 else f"{dates[0]} to {dates[-1]}" if dates else ''

    if count == 1:
        trophy = headliner.trophy
        title = f"🎉 New Platinum for {profile.display_psn_username}!"
        description = (
            f"{plat_pursuit_emoji} <@{profile.discord_id}> has earned a shiny new platinum!\n"
            f"{platinum_emoji} *{escape_md(trophy.trophy_name)}* in **{escape_md(trophy.game.title_name)}**\n"
            f"🌟 {trophy.trophy_earn_rate}% (PSN)"
        )
    else:
        header = f"{plat_pursuit_emoji} <@{profile.discord_id}> has earned {count} shiny new platinums!\n\n"
        budget = _DESCRIPTION_LIMIT - _utf16_len(header) - _MORE_LINE_RESERVE
        lines, listed = [], 0
        for stack in _stacks(earned_trophies)[:_MAX_PLATINUM_LINES]:
            line = _platinum_line(platinum_emoji, stack[0])
            if len(stack) > 1:
                line += f" (×{len(stack)})"
            if _utf16_len(line) + 1 > budget:
                break
            lines.append(line)
            listed += len(stack)
            budget -= _utf16_len(line) + 1
        if count > listed:
            lines.append(f"…and {count - listed} more")
        title = f"🎉 {count} New Platinums for {profile.display_psn_username}!"
        description = header + "\n".join(lines)

    embed = {
        'title': title,
        'description': description,
        'color': 0x003791,
        'footer': {'text': "Powered by Plat Pursuit" + (f" | Earned: {earned_on}" if earned_on else '')},
    }
    # Omitted rather than `{'url': None}`, which Discord can reject, taking the whole batch with it.
    if headliner.trophy.trophy_icon_url:
        embed['thumbnail'] = {'url': headliner.trophy.trophy_icon_url}
    art = _game_art_url(headliner.trophy.game)
    if art:
        embed['image'] = {'url': art}
    payload = {'embeds': [embed],
        # Trophy names and game titles are PSN free text in a message that carries a live `<@id>`.
        'allowed_mentions': {'users': [str(profile.discord_id)]},
    }
    try:
        queue_webhook_send(payload)
        logger.info(f"Queued platinum notification ({count}) for {profile.psn_username}")
    except Exception as e:
        logger.error(f"Failed to queue platinum notification: {e}")

_BADGE_TIER_LABELS = {1: 'Bronze', 2: 'Silver', 3: 'Gold', 4: 'Platinum'}


def send_badge_earned_notification(profile, badges):
    """Send ONE consolidated Discord embed listing the badges a profile just earned.

    The single badge-notification path (badge Discord ROLES were retired, and per-badge
    real-time pings were replaced by this batch). No-op unless the profile is Discord-linked
    (verified + discord_id) and at least one badge is given. Uses the first badge's image
    as the thumbnail.
    """
    if not profile or not badges:
        return
    if not profile.is_discord_verified or not profile.discord_id:
        return

    platinum_emoji = f"<:Platinum_Trophy:{settings.PLATINUM_EMOJI_ID}>" if settings.PLATINUM_EMOJI_ID else "🏆"
    plat_pursuit_emoji = f"<:PlatPursuit:{settings.PLAT_PURSUIT_EMOJI_ID}>" if settings.PLAT_PURSUIT_EMOJI_ID else "🏆"

    first_badge = badges[0]
    thumbnail_url = None
    if settings.DEBUG:
        thumbnail_url = 'https://platpursuit.com/static/images/badges/default.png'
    else:
        if first_badge.badge_image:
            thumbnail_url = first_badge.badge_image.url
        elif first_badge.base_badge and first_badge.base_badge.badge_image:
            thumbnail_url = first_badge.base_badge.badge_image.url
    if not thumbnail_url:
        thumbnail_url = 'https://platpursuit.com/static/images/badges/default.png'

    badge_lines = [
        f"{platinum_emoji} **{badge.effective_display_series or badge.name}** ({_BADGE_TIER_LABELS.get(badge.tier, 'Badge')})"
        for badge in badges
    ]
    count = len(badges)
    noun = 'badge' if count == 1 else 'badges'
    description = (
        f"{plat_pursuit_emoji} <@{profile.discord_id}>, you've earned {count} new {noun} on PlatPursuit!\n\n"
        + "\n".join(badge_lines)
        + "\n\nKeep up the hunt! 🎉"
    )

    embed_data = {
        'title': f"🎖️ {profile.display_psn_username} earned {count} new {noun}!",
        'description': description,
        'color': 0x674EA7,
        'footer': {'text': 'Powered by Plat Pursuit | No Trophy Can Hide From Us'},
    }
    if thumbnail_url:
        embed_data['thumbnail'] = {'url': thumbnail_url}

    payload = {'embeds': [embed_data]}
    try:
        queue_webhook_send(payload)
        logger.info(f"Queued badge-earned notification ({count}) for {profile.psn_username}")
    except Exception as e:
        logger.error(f"Failed to queue badge notification: {e}")


# Discord caps an embed description at 4096 characters. A line runs ~75-85 (an 18-digit emoji snowflake
# dominates it), so ~48 is the real ceiling; 15 leaves generous headroom and keeps the message readable --
# nobody scans a 40-line list anyway.
_MAX_BADGE_LINES = 15

#: Markdown characters that would let one admin-authored series name, or one PSN trophy/game name, reformat
#: the lines after it. `\` so a trailing backslash cannot escape the closing `*` around it; `<` so text
#: shaped like `<#id>` stays text instead of rendering as a channel link.
_MD_ESCAPE = str.maketrans({c: f'\\{c}' for c in '\\*_`~|[]()<>'})


def escape_md(text):
    """Escape Discord markdown in free text bound for an embed (staff/curator-authored names, PSN trophy
    and game names). PUBLIC because
    every webhook sender needs it: the contract announcer reimplemented this before noticing it was
    already here, which is one definition of the escape set too many."""
    return str(text or '').translate(_MD_ESCAPE)


_escape_md = escape_md   # the original private name, for this module's own callers


class WebhookError(Exception):
    """Raised by `post_webhook_sync`. Callers in management commands re-raise as CommandError; a
    dedicated type keeps this module free of a django.core.management import."""


def post_webhook_sync(payload, webhook_url, *, label="Webhook", timeout=10):
    """Synchronous direct POST to a Discord webhook. For MANAGEMENT COMMANDS, which must not use
    the queue/worker above: a one-shot command's daemon worker thread dies when the process exits,
    which can drop a message mid-flight, and the worker swallows HTTP errors into a logger where a
    cron failure is invisible. Returns the response; raises `WebhookError` on transport failure or
    any 4xx/5xx, so the caller can leave its idempotency stamp unset and retry next run.

    THE URL IS NEVER IN THE RAISED MESSAGE. A `requests` connection or timeout exception embeds the
    full URL it was calling ("Max retries exceeded with url: /api/webhooks/<id>/<token>"), so
    interpolating the exception verbatim -- which both callers of this pattern did -- printed the
    webhook SECRET to stdout and into Render's job log on every transport failure.
    """
    try:
        response = requests.post(webhook_url, json=payload, proxies=PROXIES, timeout=timeout)
    except requests.RequestException as e:
        # NOT `logger.exception`. The raised message below is redacted, but a requests exception's own
        # message embeds the full URL ("Max retries exceeded with url: /api/webhooks/<id>/<token>"),
        # so attaching the traceback put the webhook token in the log stream on every transport
        # failure -- the same secret the line below is careful not to print. The type is the part
        # that helps; the URL is the part that must not travel.
        logger.error("%s direct POST raised: %s (URL redacted)", label, type(e).__name__)
        raise WebhookError(f"{label} POST failed: {type(e).__name__} (see logs; URL redacted)")
    if response.status_code >= 400:
        raise WebhookError(f"{label} returned HTTP {response.status_code}: {response.text[:500]}")
    return response


def _badge_thumbnail(group_badge):
    """A badge's subject art as an absolute URL for an embed thumbnail, else the default badge."""
    default_art = 'https://platpursuit.com/static/images/badges/default.png'
    if settings.DEBUG or group_badge is None:
        return default_art
    # `art_layers()` is the single source of truth for a badge's art, and its subject chain has a rung
    # this used to miss: a `user` badge's subject is the SUBMITTER'S AVATAR. Hand-rolling
    # `badge_image_override or series.badge_image` showed the real artwork on the badge page and the
    # generic default in the announcement -- the two surfaces disagreeing about what a badge looks
    # like, in the one message that goes to the whole server.
    art = group_badge.art_layers()
    # `main_full`: the upload itself, not the 512px display copy medallions draw (SW-06).
    subject = art.get('main_full') if art.get('has_custom_image') else None
    # art_layers() returns a root-relative static path in a non-request context; Discord needs an
    # absolute URL, so a relative one falls back rather than shipping a broken thumbnail.
    return subject if subject and str(subject).startswith('http') else default_art


def send_group_badges_earned_notification(profile, group_badges, rewards=()):
    """The rebuilt subsystem's badge announcement: ONE consolidated embed per sync.

    Sibling of `send_badge_earned_notification` above, which it replaces at cutover. Same gate, same shape,
    same voice -- hunters already recognise this message and the point is that earning a badge feels the
    same, not that the backend changed underneath.

    Two deliberate differences, both from the reframe:

    - A badge is named by SERIES and EDITION ("Soulsborne -- Ultra HD"). In this system those are separate
      badges with separate art and separate boards, so listing only the series would make finishing both
      editions read as the message repeating itself.
    - No tier label. There are no tiers; there is a platform group, which the edition already names.

    `rewards` (phase 4): masteries / Full Sets / Series Masters gained in the SAME run ride this embed as extra
    lines, so finishing a series is still one ping (`send_badge_rewards_notification` covers a run with none).
    """
    if not profile or not group_badges:
        return
    if not profile.is_discord_verified or not profile.discord_id:
        return

    platinum_emoji = f"<:Platinum_Trophy:{settings.PLATINUM_EMOJI_ID}>" if settings.PLATINUM_EMOJI_ID else "🏆"
    plat_pursuit_emoji = f"<:PlatPursuit:{settings.PLAT_PURSUIT_EMOJI_ID}>" if settings.PLAT_PURSUIT_EMOJI_ID else "🏆"

    thumbnail_url = _badge_thumbnail(group_badges[0])

    count = len(group_badges)
    noun = 'badge' if count == 1 else 'badges'
    header = f"{plat_pursuit_emoji} <@{profile.discord_id}>, you've earned {count} new {noun} on PlatPursuit!\n\n"
    footer = "\n\nKeep up the hunt! 🎉"

    # Discord rejects a description over 4096 characters with a 400, and `webhook_sender_worker` treats any
    # non-429 as terminal -- so an over-long embed is dropped entirely, with a log line that does not name
    # the profile. That is not hypothetical: at cutover a hunter's first sync awards every badge the engine
    # agrees with at once, and a badge is per (series x edition), so one series can contribute two.
    # Listing a bounded number and counting the rest keeps the message intact.
    lines, shown = [], 0
    for gb in group_badges:
        if shown >= _MAX_BADGE_LINES:
            break
        # Series names are admin-authored free text; escaping the markdown characters keeps one badge from
        # reformatting every line after it.
        lines.append(f"{platinum_emoji} **{_escape_md(gb.series.name)}** ({gb.platform_group.name})")
        shown += 1
    if count > shown:
        lines.append(f"…and {count - shown} more")
    lines += _reward_lines(list(rewards), _MAX_BADGE_LINES - shown)   # one budget for both: the 4096 limit

    payload = {'embeds': [{
        'title': f"🎖️ {profile.display_psn_username} earned {count} new {noun}!",
        'description': header + "\n".join(lines) + footer,
        'color': 0x674EA7,
        'footer': {'text': 'Powered by Plat Pursuit | No Trophy Can Hide From Us'},
        'thumbnail': {'url': thumbnail_url},
    }],
        # Scope the ping to the one hunter this is about. Without it, an `@everyone` or a role mention in an
        # admin-authored series name would resolve, because the embed's own `<@id>` proves mentions are live
        # in this payload.
        'allowed_mentions': {'users': [str(profile.discord_id)]},
    }
    try:
        queue_webhook_send(payload)
        logger.info(f"Queued group-badge notification ({count}) for {profile.psn_username}")
    except Exception as e:
        logger.error(f"Failed to queue group-badge notification: {e}")


#: How each badge reward reads in its announcement line.
_REWARD_LABELS = {'mastered': 'Mastered', 'full_set': 'Full Set', 'series_master': 'Series Master'}


def _reward_lines(rewards, limit=_MAX_BADGE_LINES):
    """One line per reward, at most `limit` (the embed's line budget left), the rest counted: a mastery names
    its edition, a series reward does not."""
    if not rewards:
        return []
    lines = []
    for kind, gb, series in rewards[:max(limit, 0)]:
        name = f"**{_escape_md(series.name)}**" + (f" ({gb.platform_group.name})" if gb else '')
        lines.append(f"👑 {_REWARD_LABELS[kind]}: {name}")
    if len(rewards) > len(lines):
        lines.append(f"…and {len(rewards) - len(lines)} more")
    return lines


def send_badge_rewards_notification(profile, rewards):
    """Badge rewards (phase 4): masteries, Full Sets and Series Masters, ONE consolidated embed per run, in
    the badge announcement's voice. `rewards`: [(kind, group_badge or None, series)], kind one of
    `_REWARD_LABELS`. A mastery names its edition; a series reward is the series' own."""
    if not profile or not rewards:
        return
    if not profile.is_discord_verified or not profile.discord_id:
        return

    plat_pursuit_emoji = f"<:PlatPursuit:{settings.PLAT_PURSUIT_EMOJI_ID}>" if settings.PLAT_PURSUIT_EMOJI_ID else "🏆"
    count = len(rewards)
    lines = _reward_lines(rewards)
    title = (f"👑 {profile.display_psn_username}: {_REWARD_LABELS[rewards[0][0]]}!" if count == 1
             else f"👑 {profile.display_psn_username} earned {count} badge rewards!")

    payload = {'embeds': [{
        'title': title,
        'description': (f"{plat_pursuit_emoji} <@{profile.discord_id}>, you've finished more than the badge on "
                        f"PlatPursuit!\n\n" + "\n".join(lines) + "\n\nKeep up the hunt! 🎉"),
        'color': 0xE7C25C,
        'footer': {'text': 'Powered by Plat Pursuit | No Trophy Can Hide From Us'},
        'thumbnail': {'url': _badge_thumbnail(next((gb for _, gb, _ in rewards if gb), None))},
    }],
        'allowed_mentions': {'users': [str(profile.discord_id)]},
    }
    try:
        queue_webhook_send(payload)
        logger.info(f"Queued badge-rewards notification ({count}) for {profile.psn_username}")
    except Exception as e:
        logger.error(f"Failed to queue badge-rewards notification: {e}")


def send_subscription_notification(user):
    if not user or not hasattr(user, 'profile'):
        return

    profile = user.profile

    if not profile.is_discord_verified or not profile.discord_id:
        return
    
    try:
        platinum_emoji = f"<:Platinum_Trophy:{settings.PLATINUM_EMOJI_ID}>" if settings.PLATINUM_EMOJI_ID else "🏆"
        plat_pursuit_emoji = f"<:PlatPursuit:{settings.PLAT_PURSUIT_EMOJI_ID}>" if settings.PLAT_PURSUIT_EMOJI_ID else "🏆"

        thumbnail_url = 'https://platpursuit.com/static/images/badges/default.png'

        description = f"{plat_pursuit_emoji} <@{profile.discord_id}> has just subscribed!\n{platinum_emoji} Our latest **{user.get_premium_tier()}** subscriber!"
        description += f"\nEnjoy your new perks and thank you for being an amazing part of this community! 🎉"
        # Audit fix: this advertised "custom themes" (retired 2026-08) in every new-subscriber
        # announcement. Only claims that exist in PREMIUM_PERKS belong here.
        description += f"\n\nWant 5-minute refreshes and a supporter mark of your own?"
        description += f"\nConsider subscribing on our website: https://platpursuit.com/support/"

        embed_data = {
            'title': f"⚡ {profile.display_psn_username} Just Subscribed! ⚡",
            'description': description,
            'color': 0x674EA7,
            'thumbnail': {'url': thumbnail_url},
            'footer': {'text': f"Powered by Plat Pursuit | No Trophy Can Hide From Us"},
        }
        payload = {'embeds': [embed_data]}
        if settings.STRIPE_MODE == 'live':
            webhook_url = settings.DISCORD_PLATINUM_WEBHOOK_URL
        else:
            webhook_url = settings.DISCORD_TEST_WEBHOOK_URL
        queue_webhook_send(payload, webhook_url=webhook_url)
        logger.info(f"Queued notification of new badge for {profile.psn_username}")
    except Exception as e:
        logger.error(f"Failed to queue badge notification: {e}")