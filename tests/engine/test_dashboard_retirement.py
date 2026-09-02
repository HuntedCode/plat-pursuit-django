"""The /dashboard/* and /my-pursuit/* URL prefixes dissolved in the personal-hub unify: those
pages now live at ROOT under the My Pursuit hub, with / as the Overview. Pins the hub-resolution
wiring + the url-name overrides for nested pages. (The old-path 301 redirects are pinned in
test_ia_hub_unify.py.)"""
from core.hub_subnav import MY_PURSUIT_HUB, resolve_hub_subnav


class _Req:
    def __init__(self, path, url_name=None):
        self.path = path
        self.resolver_match = type('M', (), {'url_name': url_name}) if url_name else None


def test_root_belongs_to_no_hub():
    """`/` is the LOBBY as of 2026-08: it sits above the four hubs rather than inside one, so it resolves
    to no hub and renders no sub-nav strip. My Pursuit's landing is Career."""
    assert resolve_hub_subnav(_Req('/')) is None
    assert resolve_hub_subnav(_Req('/career/', 'career'))['hub'].key == 'my_pursuit'


def test_root_personal_pages_resolve_under_my_pursuit():
    """Moved-to-root pages highlight their sub-nav item; nested pages use url-name overrides."""
    m = resolve_hub_subnav(_Req('/collection/', 'badge_collection'))
    assert m['hub'].key == 'my_pursuit' and m['active_slug'] == 'collection'
    m = resolve_hub_subnav(_Req('/shareables/platinums/', 'my_shareables_platinums'))
    assert m['hub'].key == 'my_pursuit' and m['active_slug'] == 'shareables'


def test_other_hubs_not_shadowed_and_fundraiser_in_support():
    """Other hubs aren't stolen by the personal hub, and the Fundraiser page now resolves under
    the Support hub (via its /fundraiser/ prefix)."""
    m = resolve_hub_subnav(_Req('/leaderboards/', 'overall_badge_leaderboards'))
    assert m['hub'].key == 'leaderboards'
    m = resolve_hub_subnav(_Req('/fundraiser/spring/', 'fundraiser'))
    assert m['hub'].key == 'support'


def test_my_pursuit_carries_the_expected_items():
    slugs = {i.slug for i in MY_PURSUIT_HUB.items}
    assert {'collection', 'career', 'milestones', 'titles',
            'shareables', 'recap'} <= slugs
    m = resolve_hub_subnav(_Req('/career/', 'career'))
    assert m['hub'].key == 'my_pursuit' and m['active_slug'] == 'career'
