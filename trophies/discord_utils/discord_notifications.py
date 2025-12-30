import queue
import time
import requests
import logging
from django.conf import settings

logger = logging.getLogger(__name__)

webhook_queue = queue.Queue()

def webhook_sender_worker():
    while True:
        payload, webhook_url = webhook_queue.get()
        max_retries = 5
        retry_count = 0
        while retry_count < max_retries:
            try:
                response = requests.post(webhook_url, json=payload)
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

def notify_new_badge(profile, badge):
    """Send Discord webhook embed for new badge."""
    try:
        platinum_emoji = f"<:Platinum_Trophy:{settings.PLATINUM_EMOJI_ID}>" if settings.PLATINUM_EMOJI_ID else "🏆"
        plat_pursuit_emoji = f"<:PlatPursuit:{settings.PLAT_PURSUIT_EMOJI_ID}>" if settings.PLAT_PURSUIT_EMOJI_ID else "🏆"

        thumbnail_url = ''
        if badge.badge_image or badge.base_badge:
            if settings.DEBUG:
                thumbnail_url = 'https://psnobj.prod.dl.playstation.net/psnobj/NPWR20813_00/19515081-883c-41e2-9c49-8a8706c59efc.png'
            else:
                if badge.badge_image:
                    thumbnail_url = badge.badge_image.url
                else:
                    thumbnail_url = badge.base_badge.badge_image.url
            
            if not thumbnail_url:
                thumbnail_url = 'images/badges/default.png'

        description = f"{plat_pursuit_emoji} <@{profile.discord_id}> has earned a brand new role!\n{platinum_emoji} **{badge.display_series}**"
        if badge.discord_role_id:
            description += f"\nYou've earned the <@&{badge.discord_role_id}> role! Congrats! 🎉"

        embed_data = {
            'title': f"🚨 New Badge for {profile.display_psn_username}! 🚨",
            'description': description,
            'color': 0x674EA7,
            'thumbnail': {'url': thumbnail_url},
            'footer': {'text': f"Powered by Plat Pursuit | No Trophy Can Hide From Us"},
        }
        payload = {'embeds': [embed_data]}
        queue_webhook_send(payload)
        logger.info(f"Queued notification of new badge for {profile.psn_username}")
    except Exception as e:
        logger.error(f"Failed to queue badge notification: {e}")

def send_batch_role_notification(profile, badges):
    """
    Sends a single Discord embed listing ONLY the badges that grant a Discord role.
    Uses the first such badge's image as thumbnail.
    """
    if not badges:
        return

    platinum_emoji = f"<:Platinum_Trophy:{settings.PLATINUM_EMOJI_ID}>" if settings.PLATINUM_EMOJI_ID else "🏆"
    plat_pursuit_emoji = f"<:PlatPursuit:{settings.PLAT_PURSUIT_EMOJI_ID}>" if settings.PLAT_PURSUIT_EMOJI_ID else "🏆"

    role_badges = [b for b in badges if b.discord_role_id]

    if not role_badges:
        logger.info(f"No role-granting badges for {profile.psn_username} — skipping notification")
        return

    first_badge = role_badges[0]
    thumbnail_url = None
    if settings.DEBUG:
        thumbnail_url = 'https://psnobj.prod.dl.playstation.net/psnobj/NPWR20813_00/19515081-883c-41e2-9c49-8a8706c59efc.png'
    else:
        if first_badge.badge_image:
            thumbnail_url = first_badge.badge_image.url
        elif first_badge.base_badge and first_badge.base_badge.badge_image:
            thumbnail_url = first_badge.base_badge.badge_image.url

        if not thumbnail_url:
                thumbnail_url = 'images/badges/default.png'

    badge_lines = []
    for badge in role_badges:
        badge_lines.append(f"{platinum_emoji} **{badge.display_series}** <@&{badge.discord_role_id}>")

    description = (
        f"{plat_pursuit_emoji} <@{profile.discord_id}> — here are the Discord roles you've earned on PlatPursuit!\n\n"
        + "\n".join(badge_lines)
        + "\n\nThank you for being part of the community! 🎉"
    )

    embed_data = {
        'title': f"🎖️ Your Plat Pursuit Discord Roles ({len(role_badges)} total)",
        'description': description,
        'color': 0x674EA7,
        'footer': {'text': 'Powered by Plat Pursuit | No Trophy Can Hide From Us'},
    }
    if thumbnail_url:
        embed_data['thumbnail'] = {'url': thumbnail_url}

    payload = {'embeds': [embed_data]}
    try:
        queue_webhook_send(payload)
        logger.info(f"Queued notification of new badge for {profile.psn_username}")
    except Exception as e:
        logger.error(f"Failed to queue badge notification: {e}")