"""
Centralized bot detection from User-Agent strings.

Sole remaining caller is track_site_event, which uses it to keep scripted
traffic out of the SiteEvent funnel. The analytics session/page-view system
that was its other consumer was removed in 2026-08 (see core.services.tracking).

Catches three classes:
1. Self-identifying bots: UA contains 'bot'/'spider'/'crawler' or a known crawler name.
2. Empty UA: legitimate browsers always send one; an empty UA is almost always scripted.
3. Very short UA (< 20 chars): too short to be any real browser.

Cannot catch UA spoofers (bots claiming to be Chrome) -- the 2026-08 scraper was
exactly that, and sailed straight through this check. Anything that needs to stop
spoofers belongs at the Cloudflare edge, not here: this is a cheap funnel filter,
not a defence.
"""
import re

# Case-insensitive match. Order doesn't affect correctness here, only readability.
_BOT_REGEX = re.compile(
    # Generic bot self-identifiers
    r"bot|spider|crawler|slurp|scrape|fetch|preview|lighthouse|pingdom|monitor|"
    # SEO / analytics crawlers
    r"ahrefs|semrush|mj12|dotbot|petalbot|amazonbot|bytespider|seekport|"
    # AI training / RAG crawlers
    r"claude(bot|web)|gptbot|google-extended|google-inspectiontool|"
    r"perplexity|chatgpt|cohere|anthropic|cccbot|"
    # Search engine crawlers
    r"applebot|yandex|bingbot|duckduckbot|baiduspider|sogou|"
    # Social link previewers
    r"facebookexternalhit|facebookbot|twitterbot|linkedinbot|discordbot|"
    r"telegrambot|whatsapp|skypeuripreview|mastodon|pinterestbot|slackbot|"
    # Scripted HTTP clients
    r"curl|wget|python-|http-client|java/|go-http-client|httpx|aiohttp|"
    r"node-fetch|axios/|okhttp|libwww|lwp::simple|requests/|urllib|guzzle|"
    # Headless browsers / automation
    r"headlesschrome|phantomjs|puppeteer|playwright|selenium|webdriver|electron",
    re.IGNORECASE,
)

# Below this length, no legitimate browser would match. Real browser UAs are
# typically 80-200 chars. A UA under 20 chars is essentially always scripted.
_SUSPICIOUSLY_SHORT_LEN = 20


def is_bot_user_agent(ua):
    """Return True if the UA looks like a bot/scripted client."""
    if not ua:
        return True
    if len(ua) < _SUSPICIOUSLY_SHORT_LEN:
        return True
    return bool(_BOT_REGEX.search(ua))
