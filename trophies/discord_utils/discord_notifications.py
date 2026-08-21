import queue
import time
import requests
import logging
import os
from django.conf import settings
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

def notify_new_platinum(profile, earned_trophy):
    """Send Discord webhook embed for new platinum."""
    try:
        platinum_emoji = f"<:Platinum_Trophy:{settings.PLATINUM_EMOJI_ID}>" if settings.PLATINUM_EMOJI_ID else "🏆"
        plat_pursuit_emoji = f"<:PlatPursuit:{settings.PLAT_PURSUIT_EMOJI_ID}>" if settings.PLAT_PURSUIT_EMOJI_ID else "🏆"
        embed_data = {
            'title': f"🎉 New Platinum for {profile.display_psn_username}!",
            'description': f"{plat_pursuit_emoji} <@{profile.discord_id}> has earned a shiny new platinum!\n{platinum_emoji} *{earned_trophy.trophy.trophy_name}* in **{earned_trophy.trophy.game.title_name}**\n🌟 {earned_trophy.trophy.trophy_earn_rate}% (PSN)",
            'color': 0x003791,
            'thumbnail': {'url': earned_trophy.trophy.trophy_icon_url},
            'footer': {'text': f"Powered by Plat Pursuit | Earned: {earned_trophy.earned_date_time.strftime('%Y-%m-%d')}"}
        }
        payload = {'embeds': [embed_data]}
        queue_webhook_send(payload)
        logger.info(f"Queued notification of new badge for {profile.psn_username}")
    except Exception as e:
        logger.error(f"Failed to queue badge notification: {e}")

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

#: Markdown characters that would let one admin-authored series name reformat the lines after it.
_MD_ESCAPE = str.maketrans({c: f'\\{c}' for c in '*_`~|[]()>'})


def _escape_md(text):
    return str(text or '').translate(_MD_ESCAPE)


def send_group_badges_earned_notification(profile, group_badges):
    """The rebuilt subsystem's badge announcement: ONE consolidated embed per sync.

    Sibling of `send_badge_earned_notification` above, which it replaces at cutover. Same gate, same shape,
    same voice -- hunters already recognise this message and the point is that earning a badge feels the
    same, not that the backend changed underneath.

    Two deliberate differences, both from the reframe:

    - A badge is named by SERIES and EDITION ("Soulsborne -- Ultra HD"). In this system those are separate
      badges with separate art and separate boards, so listing only the series would make finishing both
      editions read as the message repeating itself.
    - No tier label. There are no tiers; there is a platform group, which the edition already names.
    """
    if not profile or not group_badges:
        return
    if not profile.is_discord_verified or not profile.discord_id:
        return

    platinum_emoji = f"<:Platinum_Trophy:{settings.PLATINUM_EMOJI_ID}>" if settings.PLATINUM_EMOJI_ID else "🏆"
    plat_pursuit_emoji = f"<:PlatPursuit:{settings.PLAT_PURSUIT_EMOJI_ID}>" if settings.PLAT_PURSUIT_EMOJI_ID else "🏆"

    default_art = 'https://platpursuit.com/static/images/badges/default.png'
    thumbnail_url = default_art
    if not settings.DEBUG:
        # `art_layers()` is the single source of truth for a badge's art, and its subject chain has a rung
        # this used to miss: a `user` badge's subject is the SUBMITTER'S AVATAR. Hand-rolling
        # `badge_image_override or series.badge_image` showed the real artwork on the badge page and the
        # generic default in the announcement -- the two surfaces disagreeing about what a badge looks
        # like, in the one message that goes to the whole server.
        art = group_badges[0].art_layers()
        subject = art.get('main') if art.get('has_custom_image') else None
        if subject:
            # art_layers() returns a root-relative static path in a non-request context; Discord needs an
            # absolute URL, so a relative one falls back rather than shipping a broken thumbnail.
            thumbnail_url = subject if str(subject).startswith('http') else default_art

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