import logging
import time

from django.conf import settings
from django.contrib.staticfiles.finders import find
from django.http import HttpResponse
from django.views.generic import TemplateView, View

from trophies.mixins import StaffRequiredMixin
from trophies.util_modules.cache import redis_client
from core.services import home_service
from core.services.site_heartbeat import get_cached_heartbeat

logger = logging.getLogger('psn_api')


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


# ── Design workshops (staff-only) ──────────────────────────────────────────
# The 2026-08 staff/design strip-down removed the one-off exploration labs;
# these four survive as living references: the Frame prototype (canonical
# Earn Moment motion reference), the Horizon primitive workshop, the house
# style guide, and the rank-chrome preview of the production Pursuer Card.
# All are staff-gated now -- they were fully public (and indexable) before.


class DesignLabView(StaffRequiredMixin, TemplateView):
    """Staff-gated host for the static design workshop pages.

    No default template_name on purpose: each route supplies its own via
    `as_view(template_name=...)` in urls.py; a bare route would raise
    ImproperlyConfigured.
    """


class PursuerCardRanksPreviewView(StaffRequiredMixin, TemplateView):
    """Preview the *production* Pursuer Card at every rank tier (/design/pursuer-card-ranks/).

    The live card's chrome is driven by the viewer's real rank, so the high tiers are hard to
    see in normal use. This renders the real component partial with mock data, one card per
    rank, so the rank-chrome escalation can be eyeballed end to end.
    """
    template_name = 'design/pursuer_card_ranks.html'

    def get_context_data(self, **kwargs):
        from trophies.util_modules.leveling import PURSUER_RANKS, pursuer_rank_for_level
        ctx = super().get_context_data(**kwargs)
        families = [
            {'label': 'Combat', 'slug': 'combat', 'avg': 48, 'bar_pct': 100},
            {'label': 'Heart', 'slug': 'heart', 'avg': 41, 'bar_pct': 85},
            {'label': 'Mind', 'slug': 'mind', 'avg': 35, 'bar_pct': 73},
            {'label': 'Exploration', 'slug': 'exploration', 'avg': 22, 'bar_pct': 46},
            {'label': 'Finesse', 'slug': 'finesse', 'avg': 18, 'bar_pct': 38},
        ]
        showcase = [
            {'game_name': name, 'cover_url': '', 'earn_rate': rate,
             'np_communication_id': None, 'elements': []}
            for name, rate in [('Elden Ring', 0.8), ('Bloodborne', 1.1), ('Sekiro', 1.4),
                               ('Returnal', 2.1), ('Hollow Knight', 2.6)]
        ]
        cards = []
        for min_level, key, name, has_div in PURSUER_RANKS:
            rank = pursuer_rank_for_level(min_level)
            cards.append({
                'name': 'Nightfall', 'avatar_url': None,
                'rank': {'key': rank['key'], 'label': rank['label']},
                'level': min_level, 'active_title': 'The Completionist',
                'platinums': 287, 'avg_completion': 94.2, 'total_trophies': 18402,
                'rarest_pct': 0.8, 'families': families,
                'showcase': {'rarest': showcase, 'recent': showcase},
            })
        ctx['rank_cards'] = cards
        return ctx


class HomeView(TemplateView):
    """
    Site home page router.

    A single entry point at / that branches the response based on user state. Since the gates
    merge (2026-08), every PRE-SYNCED state renders the same template -- home/landing.html, the
    gates surface -- with `home_state` driving a per-state hero, close, and SEO blocks:

    - Anonymous visitors                  -> the search hero (the marketing front door)
    - Logged in, no/unlinked Profile      -> the link hero (home/_hero_no_psn.html)
    - Linked, sync_status != 'synced'     -> the live status hero (home/_hero_syncing.html;
        'error' surfaces in-page rather than throwing the user into a half-empty home)
    - Linked, sync_status == 'synced'     -> trophies/home.html (the gamification Home)

    navsync.js owns the sync poll and dispatches the CustomEvents the syncing hero rides;
    syncing.js turns the 'synced' transition into the enter finale on first syncs and a
    reload on quick refreshes.
    """
    # template_name is set per-state in get_template_names below.

    def get_template_names(self):
        state = self._resolve_state()
        # The gates merge (2026-08): every pre-synced state renders the landing surface;
        # home_state drives the hero/close/SEO branches inside the one template.
        return {
            'anonymous': 'home/landing.html',
            'no_psn':    'home/landing.html',
            'syncing':   'home/landing.html',
            'synced':    'trophies/home.html',
        }[state]

    def _team_previewing_landing(self):
        """True when a signed-in team member (staff or moderator) asks to SEE the anonymous
        landing (`/?preview=landing`) -- the page only ever routes to logged-out visitors, and
        the beta requires logging in, so reviewers need a door. Team-gated: the param is
        harmless (the landing is cached community data), but the front door should not grow an
        undocumented public mode."""
        return (
            self.request.GET.get('preview') == 'landing'
            and self.request.user.is_authenticated
            and (self.request.user.is_staff or getattr(self.request.user, 'is_moderator', False))
        )

    def _team_previewing_syncing(self):
        """True when a team member asks to SEE the first-sync waiting room
        (`/?preview=syncing`). A synced team account can otherwise never reach it, and the
        syncing hero is an onboarding surface worth reviewing. Note: a synced
        previewer has trophies, so `is_initial_sync` renders False -- the DEBUG dev panel on
        the page covers the initial-sync copy path."""
        return (
            self.request.GET.get('preview') == 'syncing'
            and self.request.user.is_authenticated
            and (self.request.user.is_staff or getattr(self.request.user, 'is_moderator', False))
        )

    def _resolve_state(self):
        """Compute the home-page state for the current request user."""
        request = self.request
        if not request.user.is_authenticated:
            return 'anonymous'
        if self._team_previewing_landing():
            return 'anonymous'
        profile = getattr(request.user, 'profile', None)
        if profile is None or not profile.is_linked:
            return 'no_psn'
        if self._team_previewing_syncing():
            return 'syncing'
        if profile.sync_status != 'synced':
            # Both 'syncing' and 'error' get the in-progress shell rather than
            # an empty dashboard. The shell shows the relevant status messaging.
            return 'syncing'
        return 'synced'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        state = self._resolve_state()
        context['home_state'] = state

        if state == 'synced':
            # The gamification Home: a glanceable Pursuer landing that routes into the
            # functional My Pursuit pages (replaces the retired dashboard).
            profile = self.request.user.profile
            context['profile'] = profile
            context.update(home_service.build_home_context(profile))
            return context

        # All pre-synced states share the cached site heartbeat for their
        # community-stats card. Reused directly so we don't recompute on render.
        context['site_heartbeat'] = get_cached_heartbeat()

        # The gates merge: the landing's cached sections render for EVERY pre-synced state.
        # Cached community reads + cron-rendered artifacts ONLY (the service's module rule --
        # no per-user provider may enter it); the per-state hero extras stay in the branches
        # below, so the anon page keeps its ~free render.
        from core.services import landing_service
        context.update(landing_service.build_landing_context())

        if state == 'anonymous':
            return context

        if state == 'syncing':
            profile = self.request.user.profile
            context['profile'] = profile

            # First-time sync detection: a profile that has never completed a
            # sync has total_trophies == 0. This is the simplest and cheapest
            # signal we can give to the template to tailor copy ("first time"
            # vs "quick refresh"). Holds up after unlink/relink because
            # total_trophies is reset to 0 on relink.
            context['is_initial_sync'] = (profile.total_trophies == 0)

            # ?preview=syncing renders from a SYNCED team account, which would otherwise fall
            # into the quick-refresh copy with no progress bar, no greeting, and no finale --
            # previewing almost nothing. Force the first-sync view: the greeting, the
            # syncing-only blocks (via preview_syncing in the template's status gates), and
            # the finale all render, and the DEBUG simulate panel drives the state machine.
            context['preview_syncing'] = self._team_previewing_syncing()
            if context['preview_syncing']:
                context['is_initial_sync'] = True

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

            # PSN's own totals land on the profile within seconds of sync start
            # (psn_api_service.update_profile_from_legacy), long before our per-game walk
            # finishes -- so the waiting page can greet a first-timer with their real
            # numbers. None (not a zeroed dict) when the summary hasn't landed yet: the
            # template branches to generic copy and the poll payload upgrades it live.
            # get_total_trophies_from_summary() returns None on an empty summary.
            summary = profile.earned_trophy_summary or {}
            context['psn_found'] = {
                'total': profile.get_total_trophies_from_summary() or 0,
                'plats': summary.get('platinum', 0),
                'level': profile.trophy_level or 0,
            } if summary else None

            # DEBUG-only: the syncing hero replay harness (canned event payloads, no
            # real sync) lives in the template behind this flag.
            context['sync_dev'] = settings.DEBUG

        return context
