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
from trophies.views.dashboard_views import _get_site_heartbeat
from core.services import home_service

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


class BinderPreviewView(TemplateView):
    """Workshop page for the Binder primitive at /design/binder/.

    The Binder is the display surface that holds Frames -- physical card
    binder metaphor: paper-stock pages with binder rings, transparent
    sleeve pockets, page numbers, all to a 4x4 desktop grid (4 series x
    4 tiers per page = 16 badges per page). Mobile preserves the page
    concept but elongates to 2-wide flow.

    Sample data follows the real badge rules so the visual reads as a
    plausible collection:
      - Stages: bronze <= silver; gold == platinum >= silver
      - Progress: all 4 tiers progress in lockstep (small per-tier
        variation, bronze tends to lead silver, gold tends to lead
        platinum, never massive gaps)
      - Maintenance: if one tier of a series is in maintenance, ALL four
        tiers of that series are
      - Earn order: bronze first, platinum last; silver typically before
        gold but gold can rarely jump ahead

    Each card carries a set_number used as the canonical engraved sort
    key (stable regardless of user sort).
    """
    template_name = 'design/binder_preview.html'

    TIERS = ['bronze', 'silver', 'gold', 'platinum']
    TIER_NEXT = {'bronze': 'Silver', 'silver': 'Gold', 'gold': 'Platinum', 'platinum': 'Maxed'}
    BG_INDEX_BY_TIER = {'bronze': '1', 'silver': '2', 'gold': '3', 'platinum': '4'}
    RARITY_BY_TIER = {
        'bronze':   {'class': 'common',   'pct': 47,   'rank': 14802},
        'silver':   {'class': 'uncommon', 'pct': 12,   'rank': 1902},
        'gold':     {'class': 'rare',     'pct': 3,    'rank': 247},
        'platinum': {'class': 'mythic',   'pct': 0.3,  'rank': 11},
    }

    # 16 series x 4 tiers = 64 cards across 4 pages of 16 (= 2 full
    # spreads). Each page carries its own THEME (like a binder tab
    # divider) -- "PlayStation Classics", "Open World Epics", etc.
    # The tab UI sticks at top of viewport while the user scrolls
    # through that page's 16 cards, then gets pushed out by the
    # next page's tab.
    #
    # Color is picked from a curated PALETTE (cobalt / amber / emerald
    # / violet / crimson / teal / mustard / slate). At production
    # scale (25+ themes) themes repeat palette colors when they're
    # conceptually related -- users rarely see more than ~3 tabs at
    # once during scroll, so repeats are fine. The mapping is
    # centralized in CSS via the [data-palette] attribute.
    PAGES_DATA = [
        {
            'theme':   'PlayStation Classics',
            'palette': 'cobalt',
            'series':  ['Resident Evil', 'Final Fantasy', 'Metal Gear', 'Dark Souls'],
        },
        {
            'theme':   'Cinematic Adventures',
            'palette': 'amber',
            'series':  ['Marvel Universe', 'Persona', 'Yakuza', 'The Last of Us'],
        },
        {
            'theme':   'Open World Epics',
            'palette': 'emerald',
            'series':  ['God of War', 'Uncharted', 'PSVR Catalog', 'FromSoftware'],
        },
        {
            'theme':   'Heritage Studios',
            'palette': 'violet',
            'series':  ['Spider-Man', 'Horizon', 'Gran Turismo', 'Tekken'],
        },
    ]

    # Per-series "stories" -- each entry resolves to all four tiers of
    # one row, honoring the badge rules above. Page 1 leans complete,
    # page 2 mixes states (including a maintenance row + a rare
    # gold-before-silver case), page 3 leans frontier, page 4 brings
    # back more complete + a second maintenance row.
    SERIES_STORIES = [
        # Page 1 (mature zone)
        {'kind': 'complete',         'earned_date': 'Mar 04, 2024'},
        {'kind': 'complete',         'earned_date': 'Apr 18, 2024'},
        {'kind': 'gold_jumped',      'earned_date': 'May 22, 2024'},
        {'kind': 'near_complete',    'earned_date': 'Jul 11, 2024', 'pct': 80},
        # Page 2 (working edge)
        {'kind': 'complete',         'earned_date': 'Aug 30, 2024'},
        {'kind': 'mostly_done',      'earned_date': 'Sep 14, 2024', 'pct': 88},
        {'kind': 'maintenance',      'earned_date': 'Oct 02, 2024', 'pct': 75},
        {'kind': 'mid_progress',     'pct': 55},
        # Page 3 (frontier)
        {'kind': 'mid_progress',     'pct': 42},
        {'kind': 'bronze_only',      'earned_date': 'Jan 21, 2025'},
        {'kind': 'started',          'pct': 18},
        {'kind': 'untouched'},
        # Page 4 (continued frontier / mature mix)
        {'kind': 'complete',         'earned_date': 'Dec 03, 2023'},
        {'kind': 'maintenance',      'earned_date': 'Nov 15, 2024', 'pct': 60},
        {'kind': 'near_complete',    'earned_date': 'Feb 14, 2024', 'pct': 70},
        {'kind': 'started',          'pct': 28},
    ]

    # Per-series stage profiles. bronze <= silver; gold == platinum
    # >= silver. Cycled by series index so each row has its own count.
    STAGE_PROFILES = [
        {'bronze': 5, 'silver': 5, 'gold': 8,  'platinum': 8},
        {'bronze': 4, 'silver': 4, 'gold': 7,  'platinum': 7},
        {'bronze': 5, 'silver': 6, 'gold': 9,  'platinum': 9},
        {'bronze': 6, 'silver': 6, 'gold': 10, 'platinum': 10},
        {'bronze': 4, 'silver': 5, 'gold': 8,  'platinum': 8},
        {'bronze': 7, 'silver': 7, 'gold': 11, 'platinum': 11},
        {'bronze': 5, 'silver': 5, 'gold': 8,  'platinum': 8},
        {'bronze': 4, 'silver': 4, 'gold': 6,  'platinum': 6},
        {'bronze': 6, 'silver': 7, 'gold': 10, 'platinum': 10},
        {'bronze': 5, 'silver': 5, 'gold': 9,  'platinum': 9},
        {'bronze': 4, 'silver': 5, 'gold': 7,  'platinum': 7},
        {'bronze': 8, 'silver': 8, 'gold': 12, 'platinum': 12},
        {'bronze': 5, 'silver': 5, 'gold': 9,  'platinum': 9},
        {'bronze': 6, 'silver': 6, 'gold': 10, 'platinum': 10},
        {'bronze': 5, 'silver': 6, 'gold': 9,  'platinum': 9},
        {'bronze': 4, 'silver': 4, 'gold': 7,  'platinum': 7},
    ]

    def _art_layers(self, tier):
        i = self.BG_INDEX_BY_TIER[tier]
        return [
            static_url(f'images/badges/backdrops/{i}_backdrop.png'),
            static_url('images/badges/default.png'),
            static_url(f'images/badges/foregrounds/{i}_foreground.png'),
        ]

    def _resolve_tier_state(self, story, tier, stages_total):
        """Given a story kind + tier + stage count, return
        (state, stages_done, progress_pct). Honors the badge rules."""
        kind = story['kind']

        if kind == 'complete':
            return ('earned', stages_total, None)

        if kind == 'maintenance':
            # All 4 tiers in maintenance, all with consistent repair
            # progress. Bronze leads slightly (repaired faster).
            base = story.get('pct', 75)
            offsets = {'bronze': 10, 'silver': 0, 'gold': 0, 'platinum': -10}
            pct = max(20, min(90, base + offsets[tier]))
            repaired = max(1, min(stages_total - 1, int(round(stages_total * pct / 100))))
            return ('maintenance', repaired, pct)

        if kind == 'near_complete':
            # B/S/G earned, platinum in progress at high pct.
            if tier == 'platinum':
                pct = story.get('pct', 80)
                return ('in_progress', int(stages_total * pct / 100), pct)
            return ('earned', stages_total, None)

        if kind == 'mostly_done':
            # Bronze earned, silver/gold/platinum close to done.
            if tier == 'bronze':
                return ('earned', stages_total, None)
            base = story.get('pct', 88)
            offsets = {'silver': 4, 'gold': 0, 'platinum': -5}
            pct = max(5, min(95, base + offsets[tier]))
            return ('in_progress', int(stages_total * pct / 100), pct)

        if kind == 'gold_jumped':
            # Rare exception: bronze + gold earned, silver still in
            # progress, platinum unearned. Honors "platinum last."
            if tier == 'bronze' or tier == 'gold':
                return ('earned', stages_total, None)
            if tier == 'silver':
                pct = 70
                return ('in_progress', int(stages_total * pct / 100), pct)
            return ('unearned', 0, 0)

        if kind == 'mid_progress':
            # All 4 in progress at similar pct. Bronze leads silver,
            # gold leads platinum, small gaps.
            base = story.get('pct', 50)
            offsets = {'bronze': 5, 'silver': 0, 'gold': 0, 'platinum': -5}
            pct = max(5, min(95, base + offsets[tier]))
            return ('in_progress', int(stages_total * pct / 100), pct)

        if kind == 'started':
            # All 4 just starting, low pct, small bronze lead.
            base = story.get('pct', 18)
            offsets = {'bronze': 6, 'silver': 0, 'gold': 0, 'platinum': -4}
            pct = max(2, min(95, base + offsets[tier]))
            return ('in_progress', int(stages_total * pct / 100), pct)

        if kind == 'bronze_only':
            # Bronze earned, others untouched.
            if tier == 'bronze':
                return ('earned', stages_total, None)
            return ('unearned', 0, 0)

        # 'untouched' or unknown -> all unearned.
        return ('unearned', 0, 0)

    def _build_frame(self, series_name, tier, set_number, story, stages_total):
        state, stages_done, progress_pct = self._resolve_tier_state(story, tier, stages_total)
        rarity = self.RARITY_BY_TIER[tier]
        ctx = {
            'tier': tier,
            'state': state,
            'size': 'default',
            'series_name': series_name,
            'badge_name': f'{series_name} Platinum' if tier == 'platinum' else f'{tier.title()} Trophy',
            'art_layers': self._art_layers(tier),
            'stages_total': stages_total,
            'stages_done': stages_done if stages_done is not None else 0,
            'rarity_pct': rarity['pct'],
            'rarity_rank': rarity['rank'],
            'rarity_class': rarity['class'],
            'next_tier_label': self.TIER_NEXT[tier],
            'set_number': set_number,
        }
        if state == 'earned':
            ctx['engraving_rank'] = rarity['rank']
            ctx['earned_date'] = story.get('earned_date', 'Jan 21, 2025')
        elif state == 'maintenance':
            ctx['engraving_rank'] = rarity['rank']
            ctx['progress_pct'] = progress_pct
            ctx['maintenance_label'] = 'Reactivate'
            ctx['repair_pips'] = [i < stages_done for i in range(stages_total)]
        elif state == 'in_progress':
            ctx['progress_pct'] = progress_pct
        else:  # unearned
            ctx['progress_pct'] = 0
        return ctx

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        pages = []
        set_num = 1
        series_idx = 0
        for page_idx, page_data in enumerate(self.PAGES_DATA):
            frames = []
            for series_name in page_data['series']:
                story = self.SERIES_STORIES[series_idx]
                stages = self.STAGE_PROFILES[series_idx]
                for tier in self.TIERS:
                    frames.append(self._build_frame(series_name, tier, set_num, story, stages[tier]))
                    set_num += 1
                series_idx += 1
            pages.append({
                'number':  page_idx + 1,
                'theme':   page_data['theme'],
                'palette': page_data['palette'],
                'frames':  frames,
            })

        # Group pages into spreads (pairs) for the desktop spread view.
        # If the last spread has no facing page, the template renders an
        # empty back-cover placeholder on the right.
        spreads = []
        for i in range(0, len(pages), 2):
            spreads.append({
                'number': i // 2 + 1,
                'left':  pages[i],
                'right': pages[i + 1] if i + 1 < len(pages) else None,
            })

        ctx['pages'] = pages
        ctx['spreads'] = spreads
        ctx['total_pages'] = len(pages)
        ctx['total_spreads'] = len(spreads)
        ctx['total_cards'] = set_num - 1
        return ctx


class BadgeCollectionListView(BinderPreviewView):
    """Workshop page for the personal Badge Collection list view at
    /design/badge-collection/.

    The companion to the Binder primitive: same dataset (reuses the
    BinderPreviewView's class attributes), completely different shape.

    The Binder is the DISPLAY PIECE -- emotional, immersive, the place
    users browse to feel good about their collection. This list view is
    the HUNTING TOOL -- flat, sortable, filterable, the place users go
    to answer "where's my X" or "what should I work on next." The two
    are complementary, not competing. Each cards links back to its
    spot in the binder via #card-NNNN URL anchor.
    """
    template_name = 'design/badge_collection_list.html'

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        # Flatten the binder's page-grouped data into a single per-card
        # list with theme + page context attached to each badge.
        badges = []
        for page in ctx['pages']:
            for frame in page['frames']:
                badge = dict(frame)
                badge['theme']       = page['theme']
                badge['palette']     = page['palette']
                badge['page_number'] = page['number']
                badges.append(badge)
        ctx['badges'] = badges
        # Pre-compute a list of distinct themes (in page order) so the
        # filter UI can render its chips without scanning the badges.
        ctx['themes'] = [{'name': p['theme'], 'palette': p['palette']} for p in ctx['pages']]
        # Drop the heavy nested 'pages' / 'spreads' from the context --
        # this view doesn't render them and they'd just bloat the
        # template context for no reason.
        ctx.pop('pages', None)
        ctx.pop('spreads', None)
        ctx.pop('total_spreads', None)
        return ctx


class PursuerCardRanksPreviewView(TemplateView):
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
            {'game_name': name, 'cover_url': '', 'has_cover': False, 'earn_rate': rate,
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


class PursuerCardPreviewView(TemplateView):
    """Workshop page for the Pursuer Card primitive at /design/pursuer-card/.

    The Pursuer Card composes the rest of the kit: Tally for the Master
    Level, Horizon for the XP-to-next-tier bar, and embeds the Frame
    partial at mini variant for the recent-badge peek row. This view
    builds out a small set of sample mini-Frame contexts the template
    can include via `{% include "components/frame.html" %}` to show
    real Frame chrome at workshop scale -- the prior version of the
    workshop used CSS-only placeholder boxes that didn't accurately
    represent the production sizing or visual weight.
    """
    template_name = 'design/pursuer_card_preview.html'

    BG_INDEX_BY_TIER = {'bronze': '1', 'silver': '2', 'gold': '3', 'platinum': '4'}
    SERIES_BY_TIER = {
        'bronze':   'Resident Evil',
        'silver':   'FromSoftware',
        'gold':     'Marvel Universe',
        'platinum': 'PSVR Catalogue',
    }
    RARITY_BY_TIER = {
        'bronze':   {'class': 'common',   'pct': 47,  'rank': 14802},
        'silver':   {'class': 'uncommon', 'pct': 12,  'rank': 1902},
        'gold':     {'class': 'rare',     'pct': 3,   'rank': 247},
        'platinum': {'class': 'mythic',   'pct': 0.3, 'rank': 11},
    }

    def _mini_frame(self, tier, set_number=247):
        """Build a single mini-variant Frame context for the badge peek.

        The mini variant hides plinth-meta + plinth-name-row, so most
        of these fields don't render visibly. They're still passed for
        completeness in case the template chooses a larger variant later
        (compact, default).
        """
        i = self.BG_INDEX_BY_TIER[tier]
        rarity = self.RARITY_BY_TIER[tier]
        return {
            'tier': tier,
            'state': 'earned',
            'size': 'mini',
            'series_name': self.SERIES_BY_TIER[tier],
            'badge_name': 'Default Badge',
            'art_layers': [
                static_url(f'images/badges/backdrops/{i}_backdrop.png'),
                static_url('images/badges/default.png'),
                static_url(f'images/badges/foregrounds/{i}_foreground.png'),
            ],
            'engraving_rank': rarity['rank'],
            'set_number': set_number,
            'rarity_pct': rarity['pct'],
            'rarity_rank': rarity['rank'],
            'rarity_class': rarity['class'],
            'earned_date': 'Jan 21, 2025',
            'stages_done': 10,
            'stages_total': 10,
            # Workshop badges don't flip -- they're decorative peeks,
            # not interactive Frames. Default partial behavior is to
            # add the flippable structure for earned cards, which adds
            # DOM weight + a click handler we don't need here.
            'allow_flip': False,
        }

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        # Four reusable mini-Frame contexts (one per tier) the template
        # mixes and matches across the canonical card, the four tier
        # cards, the Share size, the composition section, and the
        # customization diagram. Set numbers vary per tier so the
        # set-mark in the bottom-right of each mini Frame reads as a
        # different print-run.
        ctx['mini_bronze']   = self._mini_frame('bronze',   14)
        ctx['mini_silver']   = self._mini_frame('silver',   89)
        ctx['mini_gold']     = self._mini_frame('gold',    247)
        ctx['mini_platinum'] = self._mini_frame('platinum',  3)
        return ctx


class PursuerCardCustomizationPreviewView(PursuerCardPreviewView):
    """Workshop deep-dive into the Pursuer Card's five customization
    slots (background, frame overlay, particle, title plate, showcase
    config). Shows variants per slot so the team can pick what ships
    free vs what ships premium. Inherits PursuerCardPreviewView's
    mini-Frame contexts for the showcase-layout demos.
    """
    template_name = 'design/pursuer_card_customization_preview.html'


class JobsWorkshopView(TemplateView):
    """Workshop for the gamification Jobs visual system at /design/jobs/.

    Explores the 5 disciplines (Combat / Exploration / Mind / Heart / Finesse) and the
    seeded 24 + 1 jobs: per-discipline colors, the 5-axis radar, and job-card treatment.
    Public direct link, not navigated to. See docs/design/rebuild/job-board-contracts.md
    + docs/design/visual-identity.md.
    """
    template_name = 'design/jobs_preview.html'

    DISCIPLINE_LABELS = {
        'combat': 'Combat', 'exploration': 'Exploration', 'mind': 'Mind',
        'heart': 'Heart', 'finesse': 'Finesse',
    }
    DISCIPLINE_TAGLINE = {
        'combat': 'You fight.', 'exploration': 'You discover.', 'mind': 'You outwit.',
        'heart': 'You feel.', 'finesse': 'You perform.',
    }
    # A sample pursuer's overall discipline levels, purely to show the radar's shape.
    SAMPLE_RADAR = {'combat': 9, 'exploration': 5, 'mind': 7, 'heart': 3, 'finesse': 6}
    # Per-job sample levels (same order as the seed) for the per-discipline radars
    # and the card XP bars. Placeholder data, varied to show shape.
    DISCIPLINE_SAMPLE = {
        'combat':      [9, 6, 7, 4, 8],
        'exploration': [5, 8, 3, 7, 6],
        'mind':        [7, 5, 9, 4, 6],
        'heart':       [6, 8, 5, 3, 7],
        'finesse':     [8, 5, 7, 6, 4],
    }
    # Sample "progress to next level" per card slot (0-100), independent of level.
    PROGRESS_CYCLE = [72, 38, 90, 21, 55]
    # Atom shape per family slot: color = family, shape = element (slot within family).
    SHAPES = ['circle', 'triangle', 'square', 'pentagon', 'hexagon']
    # Sample projects (game -> its elements) to demo the Compound generator across sizes.
    SAMPLE_PROJECTS = [
        {'name': 'Tetris Effect', 'slug': 'tetris-effect', 'elements': ['maestro']},
        {'name': 'DOOM Eternal', 'slug': 'doom-eternal', 'elements': ['gunslinger', 'slayer']},
        {'name': 'Stardew Valley', 'slug': 'stardew-valley', 'elements': ['architect', 'tycoon', 'cartographer']},
        {'name': 'The Witcher 3', 'slug': 'witcher-3', 'elements': ['mage', 'slayer', 'cartographer', 'exorcist']},
        {'name': 'Persona 5 Royal', 'slug': 'persona-5-royal', 'elements': ['mastermind', 'mage', 'champion', 'librarian', 'infiltrator']},
    ]
    # Curated periodic-table-style symbols (cap + lowercase, all unique) used in
    # place of icons -- a designed mark, not an auto-derived code. Workshop proposal.
    SYMBOLS = {
        'slayer': 'Sl', 'gunslinger': 'Gn', 'vanguard': 'Vg', 'outlaw': 'Ol', 'warrior': 'Wr',
        'pathfinder': 'Pf', 'infiltrator': 'If', 'cartographer': 'Ca', 'mascot': 'Ms', 'survivalist': 'Sv',
        'mastermind': 'Mm', 'tactician': 'Tc', 'architect': 'Ar', 'tycoon': 'Ty', 'card-shark': 'Cs',
        'mage': 'Mg', 'champion': 'Ch', 'librarian': 'Lb', 'jester': 'Js', 'exorcist': 'Ex',
        'gamer': 'Gm', 'driver': 'Dr', 'athlete': 'At', 'maestro': 'Mo', 'freelancer': 'Fl',
    }
    # Draft, flavor-forward job copy: evokes the genre/theme WITHOUT naming the IGDB
    # tags. Recommended voice; react and tune in the workshop.
    DESCRIPTIONS = {
        'slayer':       "Crowds of enemies are just a to-do list.",
        'gunslinger':   "If it moves, it's already in your sights.",
        'vanguard':     "First through the door, last to fall back.",
        'outlaw':       "Out here, the rules are more of a suggestion.",
        'warrior':      "One on one, fists up. Settle it in the ring.",
        'pathfinder':   "Every ledge is a question. You answer all of them.",
        'infiltrator':  "They never knew you were there. That's the point.",
        'cartographer': "The map fills in behind you, one horizon at a time.",
        'mascot':       "Bright worlds, big jumps, a grin the whole way.",
        'survivalist':  "Cold, hungry, hunted, still standing.",
        'mastermind':   "The solution was obvious. Eventually.",
        'tactician':    "You saw the win three moves ago.",
        'architect':    "You don't play the world. You build it.",
        'tycoon':       "Buy low, plat high.",
        'card-shark':   "The house doesn't always win.",
        'mage':         "Spellbook in hand, fate in flux.",
        'champion':     "Glory, measured in trophies. Naturally.",
        'librarian':    "Every page turned is a story finished.",
        'jester':       "You came for the story, stayed for the laughs.",
        'exorcist':     "You walk toward the thing everyone else runs from.",
        'gamer':        "High score isn't a goal, it's a personality.",
        'driver':       "The apex belongs to you.",
        'athlete':      "Reflexes, timing, and a podium with your name on it.",
        'maestro':      "Every beat, right on time.",
        'freelancer':   "A little of everything. A specialist in showing up.",
    }

    @staticmethod
    def _build_compound(atoms, seed):
        """Workshop delegate to the productionized generator in element_render
        (single source). Kept so the /design/jobs/ sandbox + its call sites are unchanged.
        """
        from trophies.services.element_render import build_compound
        return build_compound(atoms, seed)

    @staticmethod
    def _build_spectrum(elements, levels=None):
        """Emission-spectrum fingerprint: each element emits a fixed set of colored
        lines (seeded by the element itself, so it's a true composition fingerprint),
        each in its own SHADE within its family. If `levels` (a {slug: level 0-10} map)
        is given, line brightness reflects your level in each element (brighter = more
        leveled) -- used for the whole-profile signature; otherwise intensity is a
        per-line identity flourish (used for composition fingerprints). Returns line
        dicts with an x position in a 300-wide band, a CSS color, and shape.
        """
        import random
        import zlib
        lines = []
        for el in elements:
            er = random.Random(zlib.crc32(('line:' + el['slug']).encode()))
            keep = 100 - er.randint(0, 40)  # per-element shade toward white
            lc = "color-mix(in oklab, var(--disc-%s) %d%%, white)" % (el['disc_slug'], keep)
            lvl_intensity = None
            if levels is not None:
                lvl_intensity = round(0.3 + 0.65 * (levels.get(el['slug'], 0) / 10.0), 2)
            for _ in range(er.randint(2, 4)):
                x = round(2 + er.uniform(0.04, 0.96) * 296, 1)
                lines.append({
                    'x': x,
                    'fx': round(x - 6, 1),
                    'lc': lc,
                    'shape': el['shape'],
                    'intensity': lvl_intensity if lvl_intensity is not None else round(er.uniform(0.65, 1.0), 2),
                })
        return lines

    @staticmethod
    def _build_helix(elements, seed):
        """DNA double-helix whose highlighted rungs are the elements (in sequence) over
        a neutral backbone. Seeded twist phase varies the strand per contract. Returns
        the two strands (point lists) + rungs in a w x h viewBox.
        """
        import math
        import random
        rng = random.Random(seed)
        w, h = 120.0, 212.0
        cxh, amp = w / 2, 34.0
        top, bot = 16.0, h - 16.0
        span = bot - top
        turns = 2.3
        phase = rng.uniform(0, 2 * math.pi)

        def strand(offset):
            return [
                (round(cxh + amp * math.sin(phase + (s / 48) * turns * 2 * math.pi + offset), 1),
                 round(top + (s / 48) * span, 1))
                for s in range(49)
            ]

        n = len(elements)
        rungs_total = min(max(n + 3, 6), 9)
        if n == 1:
            elem_rungs = [rungs_total // 2]
        else:
            elem_rungs = sorted({round(k * (rungs_total - 1) / (n - 1)) for k in range(n)})
        rung_elem = {ri: elements[i] for i, ri in enumerate(elem_rungs) if i < n}

        rungs = []
        for ri in range(rungs_total):
            u = (ri + 0.5) / rungs_total
            y = top + u * span
            a = phase + u * turns * 2 * math.pi
            el = rung_elem.get(ri)
            rungs.append({
                'y': round(y, 1),
                'xA': round(cxh + amp * math.sin(a), 1),
                'xB': round(cxh - amp * math.sin(a), 1),
                'cx': round(cxh, 1),
                'element': bool(el),
                'symbol': el['symbol'] if el else '',
                'disc_slug': el['disc_slug'] if el else '',
            })
        return {'w': w, 'h': h, 'strandA': strand(0), 'strandB': strand(math.pi), 'rungs': rungs}

    def get_context_data(self, **kwargs):
        import json
        from trophies.models import Job, Contract
        ctx = super().get_context_data(**kwargs)

        def _xp(level, progress):
            """Sample XP into the current level and the span needed for the next."""
            nxt = (level + 1) * 1200
            return f"{round(nxt * progress / 100):,}", f"{nxt:,}"

        by_disc = {d: [] for d in self.DISCIPLINE_LABELS}
        for job in Job.objects.all():
            by_disc.setdefault(job.discipline, []).append(job)

        disciplines = []
        atomic = 0  # running atomic number across the whole table (1-25)
        for slug, label in self.DISCIPLINE_LABELS.items():
            samples = self.DISCIPLINE_SAMPLE.get(slug, [])
            job_dicts = []
            for i, job in enumerate(by_disc.get(slug, [])):
                atomic += 1
                level = samples[i] if i < len(samples) else 5
                progress = self.PROGRESS_CYCLE[i % len(self.PROGRESS_CYCLE)]
                xp_current, xp_next = _xp(level, progress)
                shape = self.SHAPES[i % len(self.SHAPES)]
                job_dicts.append({
                    'number': atomic,
                    'name': job.name,
                    'slug': job.slug,
                    'disc_slug': slug,
                    'shape': shape,
                    'symbol': self.SYMBOLS.get(job.slug, job.name[:2]),
                    'description': self.DESCRIPTIONS.get(job.slug, ''),
                    'level': level,
                    'progress': progress,
                    'xp_current': xp_current,
                    'xp_next': xp_next,
                    'state': 'active',
                    'spectrum': self._build_spectrum([{'slug': job.slug, 'disc_slug': slug, 'shape': shape}]),
                })
            disciplines.append({
                'slug': slug,
                'label': label,
                'tagline': self.DISCIPLINE_TAGLINE[slug],
                'jobs': job_dicts,
                'radar_labels_json': json.dumps([j['name'] for j in job_dicts]),
                'radar_data_json': json.dumps([j['level'] for j in job_dicts]),
            })
        ctx['disciplines'] = disciplines

        # Compound generator: synthesize each sample project's molecule from its elements.
        import zlib
        element_by_slug = {
            j['slug']: {'slug': j['slug'], 'symbol': j['symbol'], 'shape': j['shape'], 'disc_slug': j['disc_slug'], 'name': j['name']}
            for d in disciplines for j in d['jobs']
        }
        projects = []
        for proj in self.SAMPLE_PROJECTS:
            els = [element_by_slug[s] for s in proj['elements'] if s in element_by_slug]
            seed = zlib.crc32(proj['slug'].encode())
            projects.append({
                'name': proj['name'],
                'elements': els,
                'compound': self._build_compound(els, seed),
                'spectrum': self._build_spectrum(els),
                'helix': self._build_helix(els, seed),
            })
        ctx['projects'] = projects

        # Neon "same jobs, different molecules" demo: ONE fixed element set, many seeds --
        # shows that two Contracts with identical jobs still synthesize distinct molecules.
        demo_els = [element_by_slug[s] for s in ('gunslinger', 'cartographer', 'mage') if s in element_by_slug]
        ctx['neon_variants'] = [
            self._build_compound(demo_els, zlib.crc32(('variant-%d' % i).encode())) for i in range(8)
        ]

        # Real compounds: generate molecules from the actual Contracts on this server.
        # A Contract's assigned jobs are its elements (family = discipline, shape =
        # position within family). Add/edit a Contract in admin and refresh to update.
        contracts = []
        for contract in Contract.objects.prefetch_related('jobs').order_by('name')[:24]:
            els = [
                {
                    'slug': j.slug,
                    'symbol': self.SYMBOLS.get(j.slug) or j.icon or j.name[:2],
                    'shape': self.SHAPES[j.display_order % len(self.SHAPES)],
                    'disc_slug': j.discipline,
                    'name': j.name,
                }
                for j in contract.jobs.all()
            ]
            if not els:
                continue
            seed = zlib.crc32(contract.slug.encode())
            contracts.append({
                'name': contract.name,
                'elements': els,
                'compound': self._build_compound(els, seed),
                'spectrum': self._build_spectrum(els),
                'helix': self._build_helix(els, seed),
            })
        ctx['contracts'] = contracts

        # State showcase: one job (Slayer) in each of the three card states.
        def _demo(state, level, progress):
            xc, xn = _xp(level, progress)
            return {
                'number': 1, 'name': 'Slayer', 'slug': 'slayer', 'disc_slug': 'combat',
                'shape': 'circle',
                'symbol': self.SYMBOLS['slayer'], 'description': self.DESCRIPTIONS['slayer'],
                'level': level, 'progress': progress, 'xp_current': xc, 'xp_next': xn,
                'state': state,
                'spectrum': self._build_spectrum([{'slug': 'slayer', 'disc_slug': 'combat', 'shape': 'circle'}]),
            }
        ctx['demo_states'] = [
            _demo('locked', 0, 0),
            _demo('active', 4, 60),
            _demo('mastered', 10, 100),
        ]

        ctx['radar_labels_json'] = json.dumps(list(self.DISCIPLINE_LABELS.values()))
        ctx['radar_data_json'] = json.dumps([self.SAMPLE_RADAR[s] for s in self.DISCIPLINE_LABELS])
        return ctx


class LabWorkshopView(JobsWorkshopView):
    """Workshop for The Lab at /design/lab/ -- the element identity home: the Platinum
    DNA radar (fed by your element levels), the periodic table of all 25 elements in a
    realistic mix of states, your whole-profile spectral signature, and a composition
    summary. Reuses JobsWorkshopView's catalog constants + spectrum generator.
    """
    template_name = 'design/lab_preview.html'

    # A sample in-progress collection: some locked (0), some mastered (10), most active.
    LAB_LEVELS = {
        'combat':      [10, 7, 4, 0, 8],
        'exploration': [5, 9, 0, 6, 3],
        'mind':        [7, 0, 10, 4, 6],
        'heart':       [6, 8, 0, 2, 5],
        'finesse':     [9, 4, 7, 0, 10],
    }

    def get_context_data(self, **kwargs):
        import json
        from trophies.models import Job
        # Skip JobsWorkshopView's heavy catalog build; start from the base TemplateView.
        ctx = super(JobsWorkshopView, self).get_context_data(**kwargs)

        def _xp(level, progress):
            nxt = (level + 1) * 1200
            return f"{round(nxt * progress / 100):,}", f"{nxt:,}"

        by_disc = {d: [] for d in self.DISCIPLINE_LABELS}
        for job in Job.objects.all():
            by_disc.setdefault(job.discipline, []).append(job)

        disciplines, radar_vals, all_elements, all_tiles = [], [], [], []
        level_by_slug = {}
        atomic = total_level = total_xp = 0
        for slug, label in self.DISCIPLINE_LABELS.items():
            fam_levels = self.LAB_LEVELS.get(slug, [5] * 5)
            tiles = []
            for i, job in enumerate(by_disc.get(slug, [])):
                atomic += 1
                lvl = fam_levels[i] if i < len(fam_levels) else 5
                state = 'locked' if lvl == 0 else ('mastered' if lvl >= 10 else 'active')
                progress = 0 if state == 'locked' else (100 if state == 'mastered' else self.PROGRESS_CYCLE[i % len(self.PROGRESS_CYCLE)])
                xc, xn = _xp(lvl, progress)
                cum = 1200 * lvl * (lvl + 1) // 2 + round((lvl + 1) * 1200 * progress / 100)
                shape = self.SHAPES[i % len(self.SHAPES)]
                tile = {
                    'number': atomic, 'name': job.name, 'slug': job.slug, 'disc_slug': slug,
                    'shape': shape, 'symbol': self.SYMBOLS.get(job.slug, job.name[:2]),
                    'level': lvl, 'progress': progress, 'xp_current': xc, 'xp_next': xn, 'state': state,
                    'description': self.DESCRIPTIONS.get(job.slug, ''),
                    'xp_total': "{:,}".format(cum),
                }
                tiles.append(tile)
                all_tiles.append(tile)
                all_elements.append({'slug': job.slug, 'disc_slug': slug, 'shape': shape})
                level_by_slug[job.slug] = lvl
                total_level += lvl
                total_xp += cum
            avg = round(sum(fam_levels) / len(fam_levels), 1) if fam_levels else 0
            radar_vals.append(avg)
            disciplines.append({
                'slug': slug, 'label': label, 'tagline': self.DISCIPLINE_TAGLINE[slug],
                'jobs': tiles, 'avg': avg,
                'radar_labels_json': json.dumps([t['name'] for t in tiles]),
                'radar_data_json': json.dumps([t['level'] for t in tiles]),
            })

        ctx['disciplines'] = disciplines
        ctx['radar_labels_json'] = json.dumps(list(self.DISCIPLINE_LABELS.values()))
        ctx['radar_data_json'] = json.dumps(radar_vals)
        ctx['profile_spectrum'] = self._build_spectrum(all_elements, level_by_slug)
        ctx['dominant'] = max(disciplines, key=lambda d: d['avg']) if disciplines else None
        ctx['top_element'] = max(all_tiles, key=lambda t: (t['level'], t['progress'])) if all_tiles else None
        ctx['total_level'] = total_level
        ctx['total_xp'] = "{:,}".format(total_xp)
        ctx['total'] = atomic
        return ctx


class ResearchPanelView(JobsWorkshopView):
    """Workshop for the Research Panel at /design/research-panel/ -- the browse list of
    Projects (the user-facing skin for curated Contracts) to pursue. Each Project shows
    its spectral fingerprint + element composition + a fixed XP reward (the global T,
    split evenly among its elements). Pulls real Contracts from the DB alongside samples.
    """
    template_name = 'design/research_panel_preview.html'
    T_TOTAL = 5000  # every Project pays the same global total, split among its elements
    SAMPLE_STATUS = {
        'tetris-effect': ('pursuing', 45),
        'doom-eternal': ('completed', 100),
        'stardew-valley': ('available', 0),
        'witcher-3': ('pursuing', 70),
        'persona-5-royal': ('available', 0),
    }

    def _element_index(self):
        from trophies.models import Job
        return {
            job.slug: {
                'slug': job.slug, 'symbol': self.SYMBOLS.get(job.slug, job.name[:2]),
                'shape': self.SHAPES[job.display_order % len(self.SHAPES)],
                'disc_slug': job.discipline, 'name': job.name,
            }
            for job in Job.objects.all()
        }

    def _project(self, name, element_slugs, status, progress, index, cover=None):
        els = [index[s] for s in element_slugs if s in index]
        n = len(els) or 1
        return {
            'name': name, 'elements': els, 'spectrum': self._build_spectrum(els),
            'xp_total': "{:,}".format(self.T_TOTAL), 'xp_each': "{:,}".format(self.T_TOTAL // n),
            'status': status, 'progress': progress, 'cover': cover,
        }

    def get_context_data(self, **kwargs):
        from trophies.models import Contract
        ctx = super(JobsWorkshopView, self).get_context_data(**kwargs)
        index = self._element_index()

        ctx['projects'] = [
            self._project(p['name'], p['elements'], *self.SAMPLE_STATUS.get(p['slug'], ('available', 0)), index)
            for p in self.SAMPLE_PROJECTS
        ]

        # Real Projects from DB Contracts -- pull the game (Concept) title + cover.
        contracts = []
        for contract in Contract.objects.prefetch_related('jobs', 'memberships__concept').order_by('name')[:24]:
            slugs = [j.slug for j in contract.jobs.all()]
            if not any(s in index for s in slugs):
                continue
            cover, title = None, contract.name
            memberships = list(contract.memberships.all())
            if memberships:
                concept = memberships[0].concept
                title = concept.unified_title or contract.name
                cover = concept.concept_icon_url or None
            contracts.append(self._project(title, slugs, 'available', 0, index, cover))
        ctx['contracts'] = contracts
        ctx['t_total'] = "{:,}".format(self.T_TOTAL)
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
    - Linked, sync_status == 'synced'     -> trophies/home.html (the gamification Home)
    - Linked, sync_status == 'error'      -> home/syncing.html (we surface the
        error in-page rather than throwing the user into a half-empty home)

    The hotbar polls /api/profile-sync-status/ every 2s while syncing; the
    syncing page listens for a 'platpursuit:sync-status-changed' CustomEvent
    dispatched by hotbar.js and reloads the page when sync transitions to
    'synced', so users automatically advance to the home.
    """
    # template_name is set per-state in get_template_names below.

    def get_template_names(self):
        state = self._resolve_state()
        return {
            'anonymous': 'home/landing.html',
            'no_psn':    'home/link_psn.html',
            'syncing':   'home/syncing.html',
            'synced':    'trophies/home.html',
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
            # The gamification Home: a glanceable Pursuer landing that routes into the
            # functional My Pursuit pages (replaces the retired dashboard).
            profile = self.request.user.profile
            context['profile'] = profile
            context.update(home_service.build_home_context(profile))
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