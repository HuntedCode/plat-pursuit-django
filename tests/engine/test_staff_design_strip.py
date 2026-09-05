"""The 2026-08 staff/design strip-down (joint review; see rebuild-playbook.md).

Pins three things: the removed pages STAY removed (a route that quietly reappears would come back
ungated and unowned), the four surviving design labs are STAFF-GATED (they were fully public and
indexable before the strip -- an unauthenticated, uncached query surface), and the two locked keeps
plus the operational staff tools still answer for staff. The models behind every removed page
(CommentReport, ModerationLog, ReviewReport, GameFamily, Checklist) are retained on purpose.
"""
from pathlib import Path

import pytest
from django.conf import settings as django_settings
from django.urls import NoReverseMatch, reverse

from tests.factories import ProfileFactory

ROOT = Path(__file__).resolve().parents[2]

REMOVED_ROUTE_NAMES = [
    # Comment moderation cluster (mod ROLE users were already locked out post role-split).
    'comment_moderation', 'moderation_action', 'moderation_log',
    # Review moderation: a queue for the archived review system.
    'review_moderation', 'review_moderation_action', 'review_moderation_log',
    # Game-family management page (its 6 staff API endpoints died with it).
    'game_family_management',
    # CSP dashboard + ingest (report-uri directive removed from settings with them).
    'csp_report', 'staff_csp_violations', 'staff_csp_violations_clear',
    # Legacy-checklist browser (its wider-than-staff mixin was deleted too).
    'legacy_checklist_list', 'legacy_checklist_detail',
    # The 21 removed design labs.
    'design_frame_component_test', 'design_binder_preview', 'design_badge_collection_list',
    'design_badge_presentation', 'design_requirements_checklist', 'design_stage_cards',
    'design_game_card', 'design_badge_journey', 'design_chrome', 'design_recap_stage',
    'design_tally_preview', 'design_pursuer_card_preview',
    'design_pursuer_card_customization_preview', 'design_pursuer_card_workshop',
    'design_pursuer_card_spectral', 'design_pursuer_card_collection', 'design_jobs_preview',
    'design_lab_preview', 'design_research_panel_preview', 'design_mobile_subnav',
    'design_rank_colours_preview',
]

KEPT_LAB_URLS = [
    '/design/frame/',                 # the Frame prototype: canonical Earn Moment motion reference
    '/design/horizon/',               # Horizon primitive workshop (decisions locked)
    '/design/style-guide/',           # the house --pp-* style reference
    '/design/pursuer-card-ranks/',    # production card at every rank (covered in banner-retired tests too)
]

KEPT_STAFF_URLS = [
    '/staff/badge-create/',           # locked keep: badge authoring
    '/monitoring/tokens/',            # locked keep: TokenKeeper dashboard
    '/staff/subscriptions/',          # the operational view of subscription health
    '/staff/fundraiser/',             # claim-status transitions live here
    '/staff/badge-reveal/',           # the reveal spinner feeding the public event page
]


# A name-only guard can be dodged by re-adding the same PATH under a new name;
# these pin the addresses themselves. One representative per removed cluster.
REMOVED_PATHS = [
    '/staff/moderation/', '/staff/review-moderation/', '/staff/game-families/',
    '/csp-report/', '/staff/csp-violations/', '/staff/legacy-checklists/',
    '/design/binder/', '/design/tally/', '/design/pursuer-card/',
]


@pytest.fixture
def staff_client(client):
    profile = ProfileFactory(is_linked=True)
    profile.user.is_staff = True
    profile.user.save(update_fields=['is_staff'])
    client.force_login(profile.user)
    return client


@pytest.mark.parametrize('name', REMOVED_ROUTE_NAMES)
def test_removed_routes_do_not_reverse(name):
    """The URL names are gone, so no template {% url %} or reverse() can quietly resurrect one."""
    with pytest.raises(NoReverseMatch):
        reverse(name)


@pytest.mark.django_db
@pytest.mark.parametrize('path', REMOVED_PATHS)
def test_removed_paths_answer_404_even_for_staff(staff_client, path):
    """The paths themselves are dead, not just renamed."""
    assert staff_client.get(path).status_code == 404


@pytest.mark.django_db
@pytest.mark.parametrize('url', KEPT_LAB_URLS)
def test_surviving_labs_reject_anonymous_visitors(client, url):
    """Anonymous hits bounce to login -- the labs were fully public (and crawlable) before the strip."""
    resp = client.get(url)

    assert resp.status_code == 302
    assert 'login' in resp['Location']


@pytest.mark.django_db
@pytest.mark.parametrize('url', KEPT_LAB_URLS)
def test_surviving_labs_reject_signed_in_non_staff(client, url):
    profile = ProfileFactory(is_linked=True)
    client.force_login(profile.user)

    resp = client.get(url)

    assert resp.status_code == 302
    assert resp['Location'] == reverse('home')


@pytest.mark.django_db
@pytest.mark.parametrize('url', KEPT_LAB_URLS + KEPT_STAFF_URLS)
def test_kept_pages_still_answer_for_staff(staff_client, url):
    assert staff_client.get(url).status_code == 200


def test_the_moderation_context_processor_is_unregistered():
    """The navbar grep below only proves no template READS the counts; this proves the settings
    entry (the piece a settings merge would silently restore) is gone too."""
    processors = django_settings.TEMPLATES[0]['OPTIONS']['context_processors']

    assert 'plat_pursuit.context_processors.moderation' not in processors


def test_robots_disallows_the_design_namespace():
    """The strip's gating is belt (StaffRequiredMixin) AND braces (crawl block)."""
    robots = (ROOT / 'static' / 'robots.txt').read_text(encoding='utf-8')

    assert 'Disallow: /design/' in robots


def test_the_navbar_carries_no_staff_links_except_the_mod_center():
    """The strip removed moderation + game families; the fundraiser link followed 2026-08-22
    (his call: unneeded for now). Staff pages are bookmark-reached and cross-link each other.

    ONE exception since 2026-09: the Mod Center. It is the only staff surface with a QUEUE, so it
    has something to say without being visited, and a bookmark cannot tell anyone three reports
    came in overnight. Gated on `is_mod_or_admin`, so the menu still reads identically for every
    ordinary hunter -- `test_mod_center.py` pins that in both directions. The bans below are
    unchanged, and the exception needs its own reason rather than this precedent.
    """
    navbar = (ROOT / 'templates' / 'partials' / 'navbar.html').read_text(encoding='utf-8')

    for marker in ('comment_moderation', 'game_family_management', 'fundraiser_admin',
                   'pending_reports_count', 'pending_proposals_count'):
        assert marker not in navbar, f'{marker} survived the strip'
    # The ANCHOR, not the bare string `mod_center` -- which is also a substring of
    # `show_mod_center` and of the comment above it, so deleting the link left this green.
    assert "{% url 'mod_center' %}" in navbar, 'the one deliberate exception went missing'


def test_robots_disallows_the_mod_namespace():
    """Same belt-and-braces as /design/: the gate is the real defence, the crawl block is cheap."""
    robots = (ROOT / 'static' / 'robots.txt').read_text(encoding='utf-8')

    assert 'Disallow: /mod/' in robots


def test_csp_no_longer_advertises_the_removed_report_endpoint(settings):
    directives = settings.CONTENT_SECURITY_POLICY['DIRECTIVES']

    assert 'report-uri' not in directives


def test_the_removed_files_stay_deleted():
    """The orphan pair especially: monitoring.html/monitoring.js were an unrouted duplicate of the
    live TokenKeeper dashboard, one `include` away from shipping stale."""
    for path in ('templates/monitoring.html', 'static/js/monitoring.js', 'static/js/binder.js',
                 'templates/components/_binder_page.html', 'puzzle-event-demo.html',
                 'puzzle-event-demo-v2.html', 'api/game_family_views.py',
                 'static/js/game-family-admin.js', 'templates/trophies/moderation',
                 'templates/core/csp_violations.html',
                 'templates/trophies/game_family_management.html',
                 'templates/trophies/staff/legacy_checklist_list.html',
                 'templates/trophies/staff/legacy_checklist_detail.html',
                 'templates/trophies/staff/_legacy_checklist_list_results.html'):
        assert not (ROOT / path).exists(), f'{path} is still in the tree'


def test_only_the_four_survivors_remain_in_the_design_directory():
    """A new lab template is fine -- once it gets a gated route and joins this list. What this guards
    against is a deleted workshop drifting back in through a merge."""
    files = sorted(p.name for p in (ROOT / 'templates' / 'design').iterdir() if p.is_file())

    assert files == ['frame_preview.html', 'horizon_preview.html',
                     'pursuer_card_ranks.html', 'style_guide_preview.html']
