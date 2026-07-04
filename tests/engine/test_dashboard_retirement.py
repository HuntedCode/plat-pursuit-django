"""The /dashboard/* and /my-pursuit/* URL prefixes dissolved in the personal-hub unify: those
pages now live at ROOT under the My Pursuit hub, with / as the Overview. Pins the hub-resolution
wiring + the url-name overrides for nested pages. (The old-path 301 redirects are pinned in
test_ia_hub_unify.py.)"""
from core.hub_subnav import MY_PURSUIT_HUB, resolve_hub_subnav


class _Req:
    def __init__(self, path, url_name=None):
        self.path = path
        self.resolver_match = type('M', (), {'url_name': url_name}) if url_name else None


def test_root_resolves_to_my_pursuit_overview():
    """`/` is the personal hub's Overview now (it was the item-less Home before the unify)."""
    match = resolve_hub_subnav(_Req('/'))
    assert match['hub'].key == 'my_pursuit' and match['active_slug'] == 'overview'


def test_root_personal_pages_resolve_under_my_pursuit():
    """Moved-to-root pages highlight their sub-nav item; nested pages use url-name overrides."""
    m = resolve_hub_subnav(_Req('/stats/', 'my_stats'))
    assert m['hub'].key == 'my_pursuit' and m['active_slug'] == 'stats'
    m = resolve_hub_subnav(_Req('/shareables/platinums/', 'my_shareables_platinums'))
    assert m['hub'].key == 'my_pursuit' and m['active_slug'] == 'shareables'


def test_community_not_shadowed_and_fundraiser_in_support():
    """Other hubs aren't stolen by the personal hub, and the Fundraiser page now resolves under
    the Support hub (via its /fundraiser/ prefix)."""
    m = resolve_hub_subnav(_Req('/community/lists/', 'lists_browse'))
    assert m['hub'].key == 'community'
    m = resolve_hub_subnav(_Req('/fundraiser/spring/', 'fundraiser'))
    assert m['hub'].key == 'support'


def test_my_pursuit_carries_the_expected_items():
    slugs = {i.slug for i in MY_PURSUIT_HUB.items}
    assert {'overview', 'collection', 'career', 'milestones', 'titles',
            'stats', 'shareables', 'recap'} <= slugs
    m = resolve_hub_subnav(_Req('/career/', 'career'))
    assert m['hub'].key == 'my_pursuit' and m['active_slug'] == 'career'
