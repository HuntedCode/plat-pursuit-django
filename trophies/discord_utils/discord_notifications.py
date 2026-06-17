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
        description += f"\n\nWant to enjoy cool perks like ad-free web and 5 minutes refreshes, too?"
        description += f"\nConsider subscribing on our website: https://platpursuit.com/users/subscribe/"

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