"""Who the Discord bot is.

PlatBot calls PlatPursuit's API with one DRF token whose key is BOT_API_KEY. `is_discord_bot` is
the one answer to "is this the bot?", shared by `api.permissions.IsDiscordBot`, the django-ratelimit
exemption (`api.views.bot_exempt_rate`) and the DRF throttle's bot ceiling (`core.throttling`).
It lives in `core` so the throttle can use it without `core` importing from `api`.
"""
import hmac

from django.conf import settings


def is_discord_bot(request) -> bool:
    """Whether the request carries the bot's token. Never true when BOT_API_KEY is unset."""
    key = getattr(getattr(request, 'auth', None), 'key', None)
    expected = settings.BOT_API_KEY
    if not key or not expected:
        return False
    # Bytes, not str: compare_digest refuses a non-ASCII str, which would turn a bad BOT_API_KEY
    # value into a 500 on every bot request instead of a plain mismatch.
    return hmac.compare_digest(str(key).encode(), str(expected).encode())
