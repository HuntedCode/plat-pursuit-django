"""Phase 3 of the gamification Home: the Dashboard hub dissolved into a Home root, with its
items (Stats / Shareables / Recap + the dynamic Fundraiser) re-homed under My Pursuit and
the orphaned /dashboard/* URLs resolving there. Pins the hub-resolution wiring."""
from core.hub_subnav import (
    HOME_HUB,
    MY_PURSUIT_HUB,
    build_rendered_items,
    resolve_hub_subnav,
)


class _Req:
    def __init__(self, path, url_name=None):
        self.path = path
        self.resolver_match = type('M', (), {'url_name': url_name}) if url_name else None


def test_root_resolves_to_itemless_home_hub():
    """`/` maps to the Home hub so the navbar highlights Home, but it has no items so the
    sub-nav strip stays hidden (the template guards on hub_subnav_items)."""
    match = resolve_hub_subnav(_Req('/'))
    assert match['hub'].key == 'home' and match['active_slug'] is None
    assert build_rendered_items(HOME_HUB, is_authenticated=True) == ()


def test_dashboard_urls_rehome_under_my_pursuit():
    """The not-yet-moved /dashboard/* URLs now resolve under My Pursuit (the inherited
    prefix), highlighting the moved sub-nav items."""
    m = resolve_hub_subnav(_Req('/dashboard/stats/', 'my_stats'))
    assert m['hub'].key == 'my_pursuit' and m['active_slug'] == 'stats'
    # A nested shareables page highlights Shareables via the url-name override.
    m = resolve_hub_subnav(_Req('/dashboard/shareables/platinums/', 'my_shareables_platinums'))
    assert m['hub'].key == 'my_pursuit' and m['active_slug'] == 'shareables'


def test_community_not_shadowed_and_fundraiser_rehomed():
    """The inherited /dashboard/ prefix must NOT shadow other hubs, and the Fundraiser page
    (a url-name override) resolves under My Pursuit now."""
    m = resolve_hub_subnav(_Req('/community/lists/', 'lists_browse'))
    assert m['hub'].key == 'community'          # not stolen by /dashboard/ or the bare-root case
    m = resolve_hub_subnav(_Req('/fundraiser/spring/', 'fundraiser'))
    assert m['hub'].key == 'my_pursuit' and m['active_slug'] == 'fundraiser'


def test_my_pursuit_carries_the_moved_items():
    slugs = {i.slug for i in MY_PURSUIT_HUB.items}
    assert {'stats', 'shareables', 'recap'} <= slugs          # moved in
    assert {'lab', 'collection', 'milestones', 'titles'} <= slugs  # originals intact
    # My Pursuit's own pages still resolve.
    m = resolve_hub_subnav(_Req('/my-pursuit/lab/', 'lab'))
    assert m['hub'].key == 'my_pursuit' and m['active_slug'] == 'lab'
