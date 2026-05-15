import json
import logging
import random
import time

from django.contrib.staticfiles.finders import find
from django.templatetags.static import static as static_url
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.template.loader import render_to_string
from django.urls import reverse_lazy
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.generic import TemplateView, View

from core.services.analytics_service import get_dashboard_data as get_analytics_dashboard_data
from core.services.community_hub_service import build_community_hub_context
from trophies.mixins import ProfileHotbarMixin, StaffRequiredMixin
from trophies.util_modules.cache import redis_client
from trophies.views.dashboard_views import build_dashboard_context, _get_site_heartbeat

logger = logging.getLogger('psn_api')


# Rotating "Did You Know?" facts shown on the syncing page so repeat visits stay
# fresh. Picked at random server-side per request. Keep these tight, fun, and
# focused on PlatPursuit features the user can look forward to once their sync
# finishes.
SYNCING_DID_YOU_KNOW = [
    "Every game in your library is auto-tagged with genres, themes, and engines so you can hunt by what you actually love.",
    "PlatPursuit awards over 100 unique badge series, each with its own tiers, XP, and tracking against your real PSN history.",
    "The A-to-Z, Calendar, and Genre Challenges turn your backlog into a structured pursuit, complete with progress tracking.",
    "Your dashboard is yours: rearrange modules, hide what you don't care about, and pin the stats that matter to you.",
    "Earned a platinum? You can generate a shareable card in seconds and post it to your favorite community.",
    "Our Monthly Recap is a Spotify-Wrapped-style trip through your trophy year, including your rarest grabs and biggest sessions.",
    "Roadmaps let you plan a platinum step by step, then watch your progress fill in as you sync.",
    "The community has flagged thousands of broken, unobtainable, or misbehaving trophies so you know what you're getting into.",
    "Reviews and ratings come from people who actually completed the game, not random voters, so they're worth reading.",
    "Game Families group prequels, sequels, and remasters together so your stats reflect the whole journey.",
    "Discord linking lets PlatBot deliver new platinums, badge unlocks, and challenge updates straight to your server.",
    "Every stat on the site updates from real PSN data. No fudging, no estimates, no fake leaderboards.",
    "The Platinum Grid view lets you visualize every plat you have ever earned in one beautiful wall.",
    "Premium themes change the entire site's vibe, including the navbar, cards, and your share images.",
    "Trophy hunting is more fun with friends. Browse profiles, compare stats, and challenge each other for the top spot.",
]


class AdsTxtView(View):
    def get(self, request):
        file_path = find('ads.txt')  # Finders search all STATICFILES_DIRS
        if file_path:
            try:
                with open(file_path, 'r') as f:
                    content = f.read()
                return HttpResponse(content, content_type='text/plain')
            except Exception:
                logger.exception("Error serving ads.txt")
                return HttpResponse("ads.txt not found", status=404)
        else:
            logger.warning("ads.txt not found in static files")
            return HttpResponse("ads.txt not found", status=404)


class RobotsTxtView(View):
    def get(self, request):
        file_path = find('robots.txt')
        if file_path:
            try:
                with open(file_path, 'r') as f:
                    content = f.read()
                return HttpResponse(content, content_type='text/plain')
            except Exception as e:
                logger.error(f"Error serving robots.txt: {e}")
                return HttpResponse("robots.txt not found", status=404)
        else:
            logger.warning("robots.txt not found in static files")
            return HttpResponse("robots.txt not found", status=404)


class PrivacyPolicyView(TemplateView):
    template_name = 'pages/privacy.html'


class TermsOfServiceView(TemplateView):
    template_name = 'pages/terms.html'


class AboutView(TemplateView):
    template_name = 'pages/about.html'


class ContactView(TemplateView):
    template_name = 'pages/contact.html'


class FrameComponentTestView(TemplateView):
    """Test harness for the Frame component partial.

    Renders every (state x size x tier x is_pinned) variation through
    `templates/components/frame.html` so the team can verify visual
    parity after CSS / JS changes. Public direct link, not navigated to
    from anywhere in the product.
    """
    template_name = 'design/frame_component_test.html'

    TIERS = ['bronze', 'silver', 'gold', 'platinum']
    TIER_LABEL = {'bronze': 'Bronze', 'silver': 'Silver', 'gold': 'Gold', 'platinum': 'Platinum'}
    TIER_NEXT = {'bronze': 'Silver', 'silver': 'Gold', 'gold': 'Platinum', 'platinum': 'Maxed'}
    SERIES_BY_TIER = {
        'bronze': 'Resident Evil',
        'silver': 'FromSoftware',
        'gold': 'Marvel Universe',
        'platinum': 'PSVR Catalogue',
    }
    BG_INDEX_BY_TIER = {'bronze': '1', 'silver': '2', 'gold': '3', 'platinum': '4'}
    RARITY_BY_TIER = {
        'bronze':   {'class': 'common',   'pct': 47,   'rank': 14802},
        'silver':   {'class': 'uncommon', 'pct': 12,   'rank': 1902},
        'gold':     {'class': 'rare',     'pct': 3,    'rank': 247},
        'platinum': {'class': 'mythic',   'pct': 0.3,  'rank': 11},
    }

    def _art_layers(self, tier):
        i = self.BG_INDEX_BY_TIER[tier]
        return [
            static_url(f'images/badges/backdrops/{i}_backdrop.png'),
            static_url('images/badges/default.png'),
            static_url(f'images/badges/foregrounds/{i}_foreground.png'),
        ]

    def _base_ctx(self, tier, state, size='default', is_pinned=False, **extra):
        rarity = self.RARITY_BY_TIER[tier]
        ctx = {
            'tier': tier,
            'state': state,
            'size': size,
            'series_name': self.SERIES_BY_TIER[tier],
            'badge_name': 'Default Badge',
            'art_layers': self._art_layers(tier),
            'description': f'Awarded for completing the platinum on a {self.SERIES_BY_TIER[tier]} entry.',
            'earned_date': 'Jan 21, 2025',
            'stages_done': 8,
            'stages_total': 10,
            'rarity_pct': rarity['pct'],
            'rarity_rank': rarity['rank'],
            'rarity_class': rarity['class'],
            'next_tier_label': self.TIER_NEXT[tier],
            'is_pinned': is_pinned,
        }
        if state == 'earned':
            ctx['engraving_rank'] = rarity['rank']
            ctx['stages_done'] = ctx['stages_total']
        elif state == 'maintenance':
            # Mint is permanent — survives into the dormant state.
            # stages_done/total now represent repair progress; caller
            # can override (e.g. 4 of 5 repaired).
            ctx['engraving_rank'] = rarity['rank']
        elif state == 'in_progress':
            ctx['progress_pct'] = 80
        elif state == 'unearned':
            ctx['stages_done'] = 0
        ctx.update(extra)
        return ctx

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)

        # Section 1: tier matrix at default size (earned).
        ctx['tiers_earned'] = [self._base_ctx(t, 'earned') for t in self.TIERS]

        # Section 2: size matrix at Gold tier.
        ctx['sizes_gold'] = [
            self._base_ctx('gold', 'earned', size='large'),
            self._base_ctx('gold', 'earned', size='default'),
            self._base_ctx('gold', 'earned', size='compact'),
            self._base_ctx('gold', 'earned', size='mini'),
        ]

        # Section 3: state x tier grid (non-earned states only).
        ctx['state_grid'] = []
        for state in ('in_progress', 'unearned'):
            ctx['state_grid'].append({
                'state': state,
                'label': state.replace('_', ' ').title(),
                'cards': [self._base_ctx(t, state) for t in self.TIERS],
            })

        # Section 4: pinned cards across states (all gold for clarity).
        ctx['pinned_examples'] = [
            self._base_ctx('gold', 'earned', is_pinned=True),
            self._base_ctx('gold', 'in_progress', is_pinned=True),
            self._base_ctx('gold', 'unearned', is_pinned=True),
        ]

        # Section 5: title marquee — series name long enough to overflow.
        long_series_ctx = self._base_ctx('platinum', 'earned')
        long_series_ctx['series_name'] = 'The Witcher 3: Wild Hunt – Complete Edition Trophies Collection'
        long_series_ctx['badge_name'] = 'Long Title Test'
        ctx['marquee_examples'] = [long_series_ctx]

        # Section 5.5: Set engraving + current-cycle print line.
        # Each card carries:
        #   - mint engraving (permanent, etched into chrome)
        #   - set engraving in bottom-right (permanent, shared across all
        #     badges of the same series + tier — like a print-run stamp)
        #   - optional current-cycle PRINT line (refreshed each repair cycle
        #     along with name / dates / rarity; ink on chrome, not etched)
        # Two columns per tier: pristine (no maintenance cycle yet) vs.
        # post-repair (current rank + cycle 3 reprinted onto the chrome).
        set_numbers = {'bronze': 14, 'silver': 89, 'gold': 247, 'platinum': 3}
        ctx['print_examples'] = []
        for t in self.TIERS:
            base = self._base_ctx(t, 'earned')
            base['set_number'] = set_numbers[t]
            pristine = dict(base)
            reprinted = dict(base)
            reprinted['current_rank'] = max(1, self.RARITY_BY_TIER[t]['rank'] // 8)
            reprinted['current_cycle'] = 3
            ctx['print_examples'].append({
                'tier': t,
                'pristine': pristine,
                'reprinted': reprinted,
            })

        # Section 5c: Maintenance state — canonical .pp-frame--maintenance.
        # Earned badges enter MAINTENANCE when a new game ships in the
        # series. The achievement still happened (mint + set engravings
        # permanent), but the badge is dormant and the user needs to
        # re-earn it. Cool-dormant badge layer + heavy amber band riveted
        # across the center + warm REACTIVATE label + hatched warning
        # overlay on the unrepaired portion + amber repair line at the
        # build height + stage pips below the meta line.
        maint_base = self._base_ctx('gold', 'maintenance')
        maint_base['set_number'] = set_numbers['gold']
        maint_base['stages_done'] = 4
        maint_base['stages_total'] = 5
        maint_base['progress_pct'] = 80  # 4/5 = 80% repaired
        maint_base['repair_pips'] = [i < 4 for i in range(5)]  # 4 done, 1 remaining
        ctx['maintenance_example'] = maint_base

        # Section 5d: Active→Maintenance choreography harness. Card
        # renders as state=earned with is_maint_staged=True, holding it
        # in the pre-animation "still active" appearance with the
        # maintenance DOM pre-staged hidden. Play triggers the ~2.55s
        # transition; Reset returns to staged-active.
        staged_maint = self._base_ctx('gold', 'earned')
        staged_maint['set_number'] = set_numbers['gold']
        staged_maint['stages_done'] = 4
        staged_maint['stages_total'] = 5
        staged_maint['progress_pct'] = 80
        staged_maint['repair_pips'] = [i < 4 for i in range(5)]
        staged_maint['is_maint_staged'] = True
        staged_maint['dom_id'] = 'pp-maint-test-gold'
        ctx['maintenance_staged'] = staged_maint

        # Section 6: earn moment harness — one card per tier with a play
        # button. Cards are state=in_progress at 90% build, staged for the
        # earn moment (is_earn_staged=True). The engraving_rank is the FINAL
        # earned rank; the partial renders it under the placeholder class so
        # the etch phase reveals the proper "#247 of all time" text. The
        # back face is also pre-rendered, hidden via .pp-earn-back-staged
        # until phase 10's back-scan reveals it.
        ctx['earn_harness'] = []
        for t in self.TIERS:
            staged_ctx = self._base_ctx(t, 'in_progress')
            staged_ctx['progress_pct'] = 90
            staged_ctx['stages_done'] = 9
            staged_ctx['dom_id'] = f'pp-earn-test-{t}'
            staged_ctx['is_earn_staged'] = True
            staged_ctx['engraving_rank'] = self.RARITY_BY_TIER[t]['rank']
            ctx['earn_harness'].append({'tier': t, 'frame': staged_ctx})

        return ctx


class CommunityHubView(ProfileHotbarMixin, TemplateView):
    """The Community Hub destination page at /community/.

    A fixed-layout **Feature Spotlight** page (NOT an aggregator) composed
    of: site heartbeat hero, conditional active fundraiser banner, Pursuit
    Feed Spotlight promo block (3 sample events + CTA), 2x2 feature grid
    (Reviews / Challenges / Lists / Leaderboards), and a permanent Discord
    callout. Each card is part-marketing (icon, tagline, CTA) and
    part-preview (3-5 items of real signal); the hub is a wayfinder, not
    a feed-of-feeds.

    Unlike the dashboard, this page is NOT customizable: no drag-and-drop,
    no module library, no per-user hidden modules. The hub is curated;
    the dashboard is personal. See docs/features/community-hub.md for the
    page anatomy and the rationale for the Feature Spotlight design.

    Public access — no auth required. Anonymous visitors get the same
    layout; the only viewer-specific touch is the rank highlight on the
    Leaderboards card when logged in.
    """
    template_name = 'community/hub.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        # Pull viewer profile if logged in AND linked. Personal-section
        # helpers in build_community_hub_context all branch on a None
        # `viewer_profile` to render the sign-in / link-PSN CTA, so we
        # collapse "anonymous", "logged in but no profile", and "logged
        # in with an unlinked profile" into a single None signal here.
        # Authenticated unlinked users get a "Link your PSN to see your
        # stats" CTA in each card's bottom half via the same template
        # branch as anonymous users — both states see the same CTA copy
        # because both fixes route through `link_psn`.
        viewer_profile = None
        if self.request.user.is_authenticated:
            profile = getattr(self.request.user, 'profile', None)
            if profile is not None and profile.is_linked:
                viewer_profile = profile

        # Pass the auth + linked-state signals through to the template so
        # it can pick the right CTA copy for the personal half empty state
        # ("sign in" vs. "link your PSN").
        context['viewer_is_authenticated'] = self.request.user.is_authenticated
        context['viewer_has_linked_profile'] = viewer_profile is not None

        # All module data lives under top-level context keys keyed by module
        # slug. Each module's template can render or skip independently.
        context.update(build_community_hub_context(viewer_profile=viewer_profile))

        # SEO + breadcrumb context. The base.html blocks pick these up via
        # the seo_title / seo_description / breadcrumb context vars and the
        # jsonld_breadcrumbs templatetag.
        context['seo_title'] = 'Community Hub - Platinum Pursuit'
        context['seo_description'] = (
            "What's happening across PlatPursuit right now: the Pursuit Feed, "
            "global leaderboards, top reviewers, active challenges, and more."
        )
        context['breadcrumb'] = [
            {'text': 'Home', 'url': reverse_lazy('home')},
            {'text': 'Community Hub'},
        ]
        return context


class AnalyticsDashboardView(StaffRequiredMixin, TemplateView):
    """Staff-only analytics dashboard at /staff/analytics/.

    Bookmark-only (not in nav). Reads existing AnalyticsSession / PageView /
    SiteEvent data, no schema changes. Date window via ?range= (7d, 30d, 90d,
    all); page-type filter via ?page_type= for the Top Pages table.
    """
    template_name = 'core/analytics_dashboard.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        range_key = self.request.GET.get('range', '30d')
        page_type_filter = self.request.GET.get('page_type') or None
        include_bots = self.request.GET.get('include_bots') == '1'
        exclude_recent_hours = self.request.GET.get('exclude_recent_hours')
        force_refresh = self.request.GET.get('refresh') == '1'

        kwargs_for_data = dict(
            range_key=range_key,
            page_type_filter=page_type_filter,
            include_bots=include_bots,
            force_refresh=force_refresh,
        )
        if exclude_recent_hours is not None:
            kwargs_for_data['exclude_recent_hours'] = exclude_recent_hours

        data = get_analytics_dashboard_data(**kwargs_for_data)
        context.update(data)
        return context


class AnalyticsReportView(StaffRequiredMixin, View):
    """Markdown report download for the staff analytics dashboard.

    Renders the same payload as AnalyticsDashboardView via the cached
    get_dashboard_data helper, but formats it as a markdown document that
    downloads as a .md file. Useful for sharing the snapshot in chat / Discord
    / docs without having to copy-paste each panel by hand.
    """

    def get(self, request, *args, **kwargs):
        range_key = request.GET.get('range', '30d')
        page_type_filter = request.GET.get('page_type') or None
        include_bots = request.GET.get('include_bots') == '1'
        exclude_recent_hours = request.GET.get('exclude_recent_hours')
        force_refresh = request.GET.get('refresh') == '1'

        kwargs_for_data = dict(
            range_key=range_key,
            page_type_filter=page_type_filter,
            include_bots=include_bots,
            force_refresh=force_refresh,
        )
        if exclude_recent_hours is not None:
            kwargs_for_data['exclude_recent_hours'] = exclude_recent_hours

        data = get_analytics_dashboard_data(**kwargs_for_data)
        context = {**data, 'generated_at': timezone.now()}
        body = render_to_string('core/analytics_report.md', context)

        now = timezone.now()
        bots_tag = 'with-bots' if include_bots else 'humans'
        filter_tag = f'-{page_type_filter}' if page_type_filter else ''
        lag_h = data.get('window', {}).get('exclude_recent_hours', 0) or 0
        lag_tag = f'-lag{lag_h}h' if lag_h else ''
        filename = f"platpursuit-analytics-{range_key}{filter_tag}-{bots_tag}{lag_tag}-{now.strftime('%Y%m%d-%H%M')}.md"

        response = HttpResponse(body, content_type='text/markdown; charset=utf-8')
        response['Content-Disposition'] = f'attachment; filename="{filename}"'
        return response


class HomeView(ProfileHotbarMixin, TemplateView):
    """
    Site home page router.

    A single entry point at / that branches the response based on user state:

    - Anonymous visitors                  -> home/landing.html (marketing pitch)
    - Logged in, no Profile               -> home/link_psn.html (link your PSN)
    - Logged in, Profile exists, !linked  -> home/link_psn.html
    - Linked, sync_status == 'syncing'    -> home/syncing.html (in-progress page)
    - Linked, sync_status == 'synced'     -> trophies/dashboard.html (full dashboard)
    - Linked, sync_status == 'error'      -> home/syncing.html (we surface the
        error in-page rather than throwing the user into a half-empty dashboard)

    The hotbar polls /api/profile-sync-status/ every 2s while syncing; the
    syncing page listens for a 'platpursuit:sync-status-changed' CustomEvent
    dispatched by hotbar.js and reloads the page when sync transitions to
    'synced', so users automatically advance to the dashboard.
    """
    # template_name is set per-state in get_template_names below.

    def get_template_names(self):
        state = self._resolve_state()
        return {
            'anonymous': 'home/landing.html',
            'no_psn':    'home/link_psn.html',
            'syncing':   'home/syncing.html',
            'synced':    'trophies/dashboard.html',
        }[state]

    def _resolve_state(self):
        """Compute the home-page state for the current request user."""
        request = self.request
        if not request.user.is_authenticated:
            return 'anonymous'
        profile = getattr(request.user, 'profile', None)
        if profile is None or not profile.is_linked:
            return 'no_psn'
        if profile.sync_status != 'synced':
            # Both 'syncing' and 'error' get the in-progress shell rather than
            # an empty dashboard. The shell shows the relevant status messaging.
            return 'syncing'
        return 'synced'

    def get_context_data(self, **kwargs):
        # ProfileHotbarMixin attaches the hotbar context for any logged-in user
        # with a profile, so all four states get a working hotbar where applicable.
        context = super().get_context_data(**kwargs)
        state = self._resolve_state()
        context['home_state'] = state

        if state == 'synced':
            # Render the dashboard exactly as DashboardView would.
            profile = self.request.user.profile
            context.update(build_dashboard_context(self.request, profile))
            # Welcome Tour: auto-show once for users who haven't completed it
            context['show_welcome_tour'] = getattr(profile, 'tour_completed_at', None) is None
            return context

        # All non-dashboard states share the cached site heartbeat for their
        # community-stats card. Reused directly so we don't recompute on render.
        context['site_heartbeat'] = _get_site_heartbeat()

        if state == 'syncing':
            profile = self.request.user.profile
            context['profile'] = profile

            # First-time sync detection: a profile that has never completed a
            # sync has total_trophies == 0. This is the simplest and cheapest
            # signal we can give to the template to tailor copy ("first time"
            # vs "quick refresh"). Holds up after unlink/relink because
            # total_trophies is reset to 0 on relink.
            context['is_initial_sync'] = (profile.total_trophies == 0)

            # Elapsed time: read sync_started_at:{profile_id} from Redis. The
            # API endpoint also exposes this so the JS can keep counting up
            # without re-fetching, but rendering it server-side ensures the
            # initial paint shows the correct value (no flash of "0 seconds").
            sync_started_at_raw = redis_client.get(f'sync_started_at:{profile.id}')
            elapsed_seconds = 0
            if sync_started_at_raw:
                try:
                    started_at = float(
                        sync_started_at_raw.decode()
                        if isinstance(sync_started_at_raw, bytes)
                        else sync_started_at_raw
                    )
                    elapsed_seconds = max(0, int(time.time() - started_at))
                except (ValueError, TypeError):
                    elapsed_seconds = 0
            context['sync_elapsed_seconds'] = elapsed_seconds

            # D2: send the full fact list (instead of one randomly chosen) so
            # the template can rotate them client-side. Shuffle server-side so
            # different page loads start from a different fact.
            facts = list(SYNCING_DID_YOU_KNOW)
            random.shuffle(facts)
            context['did_you_know_facts'] = facts
            # Backwards-compat: keep `did_you_know` for the initial render so
            # the template doesn't need a special "first fact" path.
            context['did_you_know'] = facts[0]

        return context


# ── CSP violation reporting ────────────────────────────────────────────────
# Browsers POST violations to the report-uri configured in settings.py. We
# buffer them in a capped Redis list and expose a staff dashboard so we can
# react to CSP misconfigurations without staring at production browser
# DevTools. No DB model: violations are debug telemetry, not durable data.
_CSP_REDIS_KEY = 'csp:violations'
_CSP_REDIS_CAP = 1000
_CSP_REDIS_TTL_SECONDS = 30 * 24 * 3600


def _normalize_csp_reports(payload):
    """Yield normalized violation dicts from a raw CSP report payload.

    Handles both wire formats:
      * Legacy `application/csp-report`: a single object wrapped in
        `{"csp-report": {...}}` with kebab-case keys.
      * Reporting API `application/reports+json`: a JSON array of report
        objects, each carrying a `body` dict with camelCase keys.
    """
    if isinstance(payload, dict) and 'csp-report' in payload:
        report = payload['csp-report'] or {}
        if isinstance(report, dict):
            yield {
                'directive': (
                    report.get('effective-directive')
                    or report.get('violated-directive')
                    or 'unknown'
                ),
                'blocked': report.get('blocked-uri') or '',
                'document': report.get('document-uri') or '',
                'source_file': report.get('source-file') or '',
                'line': report.get('line-number') or 0,
            }
        return

    if isinstance(payload, list):
        for item in payload:
            if not isinstance(item, dict):
                continue
            body = item.get('body') or {}
            if not isinstance(body, dict):
                continue
            yield {
                'directive': (
                    body.get('effectiveDirective')
                    or body.get('violatedDirective')
                    or 'unknown'
                ),
                'blocked': body.get('blockedURL') or body.get('blocked-uri') or '',
                'document': body.get('documentURL') or item.get('url') or '',
                'source_file': body.get('sourceFile') or '',
                'line': body.get('lineNumber') or 0,
            }


@csrf_exempt
def csp_report_ingest(request):
    """Public POST endpoint for CSP violation reports.

    Browsers send reports as background POSTs without CSRF tokens (and
    sometimes without credentials), so this view is exempt from CSRF and
    open to anonymous traffic. The LTRIM cap protects Redis from runaway
    misconfigurations; rate-limiting per-IP isn't worth the complexity for
    a debug surface.
    """
    if request.method != 'POST':
        return HttpResponse(status=405)

    try:
        payload = json.loads((request.body or b'{}').decode('utf-8'))
    except (ValueError, UnicodeDecodeError):
        logger.warning("CSP report ingest: invalid JSON body")
        return HttpResponse(status=204)

    now_ms = int(time.time() * 1000)
    pipe = redis_client.pipeline()
    pushed = 0
    for report in _normalize_csp_reports(payload):
        report['ts'] = now_ms
        pipe.lpush(_CSP_REDIS_KEY, json.dumps(report))
        pushed += 1

    if pushed:
        pipe.ltrim(_CSP_REDIS_KEY, 0, _CSP_REDIS_CAP - 1)
        pipe.expire(_CSP_REDIS_KEY, _CSP_REDIS_TTL_SECONDS)
        try:
            pipe.execute()
        except Exception:
            logger.exception("CSP report ingest: Redis write failed")

    # 204 No Content is the conventional response for report endpoints;
    # browsers don't render or otherwise act on the body.
    return HttpResponse(status=204)


class CspViolationsView(StaffRequiredMixin, TemplateView):
    """Staff-only dashboard at /staff/csp-violations/.

    Reads the rolling Redis buffer, aggregates by (directive, blocked URI,
    document URI), and surfaces both the grouped view (what's hitting) and
    the recent raw entries (where + when). Not linked from nav: bookmark.
    """
    template_name = 'core/csp_violations.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        try:
            raw_entries = redis_client.lrange(_CSP_REDIS_KEY, 0, _CSP_REDIS_CAP - 1)
        except Exception:
            logger.exception("CSP dashboard: Redis read failed")
            raw_entries = []

        entries = []
        for raw in raw_entries:
            try:
                if isinstance(raw, bytes):
                    raw = raw.decode('utf-8')
                entries.append(json.loads(raw))
            except (ValueError, UnicodeDecodeError):
                continue

        aggregates = {}
        for entry in entries:
            key = (
                entry.get('directive', ''),
                entry.get('blocked', ''),
                entry.get('document', ''),
            )
            agg = aggregates.setdefault(key, {
                'directive': key[0],
                'blocked': key[1],
                'document': key[2],
                'count': 0,
                'last_seen_ts': 0,
            })
            agg['count'] += 1
            ts = entry.get('ts', 0)
            if ts > agg['last_seen_ts']:
                agg['last_seen_ts'] = ts

        context['aggregates'] = sorted(
            aggregates.values(), key=lambda a: a['last_seen_ts'], reverse=True
        )
        context['recent'] = entries[:50]
        context['total'] = len(entries)
        context['cap'] = _CSP_REDIS_CAP
        return context


class CspViolationsClearView(StaffRequiredMixin, View):
    """POST-only: deletes the Redis CSP violations buffer.

    Useful for the "deploy a CSP change, clear the buffer, reload pages,
    see what's still tripping" workflow. CSRF-protected via the standard
    middleware; only reachable by staff.
    """

    def post(self, request, *args, **kwargs):
        try:
            redis_client.delete(_CSP_REDIS_KEY)
        except Exception:
            logger.exception("CSP dashboard: Redis clear failed")
        return redirect('staff_csp_violations')