"""The Challenge Optimizer: one page and six JSON doors (plan, alternatives, search, badges, start, apply).
Owner-only; nothing here is public.

A SEPARATE MODULE from `challenges/views.py` (already ~2,000 lines), built on its plumbing rather than
beside it: `_ChallengeJsonView` (401/403 JSON, never a redirect), `_json_when_limited` (a tripped limit
answers JSON 429 with an `error`), the shared write bucket, and `creation_is_open`.

THREE BUDGETS, because a plan has a cheap half and an expensive half (`services.planner`'s module docstring), and
Other options is neither:

- every plan and re-solve spends the SOLVE bucket (`SOLVE_RATE`);
- every "Other options" call spends the SHEET bucket (`SHEET_RATE`) instead: it never solves, only reads the
  cached pool, and the sheet asks for pages by itself as the hunter scrolls and switches tabs, so sharing the
  solve budget would leave their next real change refused;
- a call that misses the pool cache must ALSO pass the BUILD bucket (`BUILD_RATE`) before it builds one.

So a hunter can tweak a plan freely (lock, exclude, pin, "different games") against a cached pool, and only
changing their answers, or coming back after `planner.POOL_TTL`, costs a build. Apply spends the shared
write bucket once per press, however many squares it fills, and every square goes through
`challenge_service.assign`, which re-checks everything.

THE WIRE SPEAKS SLUGS, like the picker and `assign`. Ids stay server-side.
"""
import json
import logging
import re

from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Count
from django.db.models.functions import Lower
from django.http import JsonResponse
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.utils.decorators import method_decorator
from django.views.generic import TemplateView
from django_ratelimit.core import is_ratelimited
from django_ratelimit.decorators import ratelimit

from challenges.models import Challenge, short_label_for
from core.previews import previewing
from challenges.services import challenge_service as svc
from challenges.services import allowance, build_line, optimizer_drafts, picker, planner, slot_render
from challenges.views import (
    CHALLENGE_READ_RATELIMIT_GROUP,
    CHALLENGE_WRITE_RATELIMIT_GROUP,
    RATE_LIMITED_MESSAGE,
    MyChallengesView,
    _ChallengeJsonView,
    _LinkedProfileRequired,
    _cover_is_art,
    _cover_url,
    _json_when_limited,
    creation_is_open,
    start_from_request,
)
from trophies.constants import PLATFORM_LABELS
from trophies.models import Contract, GroupBadge, PlatformGroup
from trophies.services.badge_list_service import build_list_cards, live_editions
from trophies.services.badge_xp import edition_display_state
from trophies.services.collection_service import badge_type_sections
from trophies.util_modules.constants import ALL_PLATFORMS, MODERN_PLATFORMS

logger = logging.getLogger(__name__)

#: THE TEAM PREVIEW DOORS (`core.previews`: staff or moderator, and nothing real is written or spent), for the
#: states a team account (a member, with a quiet catalogue) never sees on its own:
#:
#: - `?preview=optimizer-free`: a free hunter with this week's plan unused. The banner, the "use your free plan?"
#:   pop-up, then a real plan; after that the week behaves as spent (other answers lock) until the page reloads.
#: - `?preview=optimizer-free-used`: a free hunter whose week is in use, on your saved plan's answers (or the
#:   wizard's defaults when nothing is saved), with Open my plan and the next free date a week out.
#: - `?preview=optimizer-line`: a pretend build line from the wizard's Plan it, `PREVIEW_LINE_START` places back
#:   and one place closer each re-ask, with Stop waiting; then the real plan. The real line is not touched.
#:
#: THE DOOR OPENS THE WHOLE THING (`core.previews`' rule): the page and the plan / Other options doors all read
#: it (the page's script carries `?preview=` on both). The pretend week lives in the team member's own SESSION,
#: never a real `OptimizerAllowance` row, and each page load starts it over. Previews skip the autosave too.
PREVIEW_FREE = 'optimizer-free'
PREVIEW_FREE_USED = 'optimizer-free-used'
PREVIEW_LINE = 'optimizer-line'
PREVIEW_DOORS = (PREVIEW_FREE, PREVIEW_FREE_USED, PREVIEW_LINE)
PREVIEW_WEEK_SESSION_KEY = 'optimizer_preview_week'
PREVIEW_LINE_START = 3
#: A pretend ticket names the place it was given: `preview-1` .. `preview-<PREVIEW_LINE_START>`.
PREVIEW_TICKET = re.compile(r'preview-([1-9][0-9]?)')
#: The pretend line moves one place per re-ask (the page re-asks every 2s), so this is its honest estimate.
PREVIEW_LINE_SECONDS_PER_PLACE = 2

OPTIMIZER_SOLVE_RATELIMIT_GROUP = 'challenges:optimizer-solve'
OPTIMIZER_BUILD_RATELIMIT_GROUP = 'challenges:optimizer-build'
#: A review session is a burst of clicks; 40 a minute is far above a hunter adjusting a plan by hand.
SOLVE_RATE = '40/m'
OPTIMIZER_SHEET_RATELIMIT_GROUP = 'challenges:optimizer-sheet'
SHEET_RATE = '120/m'
#: A build is the pool scan plus the badge evaluation. A hunter changing their answers a few times a
#: minute is normal; a loop is not.
BUILD_RATE = '6/m'

#: Ceilings on what one request may carry, so a crafted body cannot turn one call into a large one.
MAX_PINS = 20
MAX_EXCLUDED = 200
#: One line per square: 26 letters + 25 jobs, with headroom for a catalogue that gains a job.
MAX_LINES = 60
#: One page of an "Other options" tab. Every game is reachable (owner, 2026-10-17); this only bounds how many cards
#: one request decorates (covers, platforms), the sheet loading the next page as the hunter scrolls.
ALTERNATIVES_PAGE = 24
#: The sheet's tabs: the line's both squares, the opened square alone, its partner alone (owner, 2026-10-17).
ALTERNATIVES_GROUPS = ('both', 'rest', 'partner')
#: How many started badges step 4 shows as cards before "and N more".
STARTED_PREVIEW = 8
#: The badge picker's page, and how deep it may page (the live catalogue is a few hundred editions).
BADGE_PAGE_SIZE = 24
MAX_BADGE_PAGES = 40
#: The pin search shows this many games per page.
SEARCH_LIMIT = 12

#: The wizard's steps, in order. The template renders the rail from this and the script reads the panels'
#: `data-step` keys, so the two cannot drift.
OPTIMIZER_STEPS = (
    {'key': 'runs', 'label': 'Challenges'},
    {'key': 'platforms', 'label': 'Platform'},
    {'key': 'source', 'label': 'Games'},
    {'key': 'badges', 'label': 'Badges'},
    {'key': 'pins', 'label': 'Must-haves'},
)

BUILD_LIMITED_MESSAGE = 'That was a lot of fresh plans at once. Wait a minute and try again.'
#: Other options found every whole-catalogue slot taken (rare: it builds only when the cached pool expired).
CATALOGUE_BUSY_MESSAGE = 'Lots of hunters are optimizing right now. Try again in a moment.'


class PlanRequestError(ValueError):
    """A body the optimizer cannot read. Answered as 400 with the message."""


def run_option(profile, challenge_type):
    """One step-1 card's data: My Challenges' own card (`MyChallengesView.card_for`, so the two pages read the
    same state, progress and reward) plus what the optimizer needs on top of it. `run` is set only for a RUNNING
    challenge (the one a hunter can plan); `resumable` only for a hidden unfinished one (Start resumes it)."""
    card = MyChallengesView.card_for(profile, challenge_type, short_label_for(challenge_type))
    run = card['run'] if card['state'] == 'active' else None
    return {
        'type': challenge_type,
        'label': card['label'],
        'card': card,
        'run': run,
        'resumable': card['run'] if card['state'] == 'resumable' else None,
        # FROM THE RUN'S OWN COUNTS, not a slot query: the same definition the script uses after an Apply
        # (`total_slots - filled_count`), so the two cannot drift.
        'open': (run.total_slots - run.filled_count) if run else 0,
    }


def badge_cards(group_badges, profile):
    """Picker cards for a page of editions: the badge list's own batched cards (`build_list_cards`: art, edition,
    the hunter's per-edition progress, two bulk queries), with the medallion's state set from that progress by
    the Collection's shared rule (`edition_display_state`): earned is bright, in progress fills, unearned is grey.
    The list page shows everything full-colour because it is a showcase; a picker is about the hunter's own
    standing, so it uses the Collection's reading instead."""
    cards = build_list_cards(group_badges, profile)
    for card in cards:
        frame = card['frame']
        frame['state'], frame['progress_pct'] = edition_display_state(
            card['earned'], frame['stages_done'], frame['stages_total'])
    return cards


def badge_step_context(profile):
    """Step 4's seed: the started editions (shown as cards), each badge type with its live count, the catalogue
    total for "All badges", and the edition groups the picker filters by. A handful of bounded queries; the picker
    itself pages through `OptimizerBadgesView`."""
    started_ids = planner.started_edition_ids(profile)
    # ONLY THE CARDS SHOWN are built (the medallion work is per card); the count is the whole list.
    shown = list(live_editions().filter(pk__in=started_ids).select_related('series__submitted_by')
                 .order_by(Lower('series__name'), 'platform_group__sort_order', 'pk')[:STARTED_PREVIEW]) if started_ids else []
    started = badge_cards(shown, profile)
    by_type = dict(GroupBadge.objects.filter(is_live=True).values('series__badge_type')
                   .annotate(n=Count('pk')).values_list('series__badge_type', 'n'))
    return {
        'started_badges': started,
        'started_count': len(started_ids),
        'started_more': max(0, len(started_ids) - STARTED_PREVIEW),
        'badge_types': [{'key': key, 'label': label, 'count': by_type.get(key, 0)}
                        for key, label in badge_type_sections() if by_type.get(key)],
        'badges_total': sum(by_type.values()),
        'badge_groups': list(PlatformGroup.objects.filter(group_badges__is_live=True).distinct()
                             .order_by('sort_order', 'name').values('key', 'name', 'platforms')),
    }


def allowance_context(profile, user=None, state=None):
    """The weekly allowance as the page shows it: unlimited (members), available, or in use with its saved answers
    (picked badges carry their names, so the step can show them as chips) and the date the next one comes. `state`
    is a team preview's pretend week (`preview_week`); by default the hunter's own is read."""
    state = state or allowance.state_for(profile)
    saved = _with_pick_names(state.answers) if state.in_use else None
    nxt = allowance.when_context(state.next_free_at, user) if state.in_use else None
    if_now = allowance.when_context(timezone.now() + allowance.FREE_PLAN_WINDOW, user)
    return {
        'allowance_unlimited': state.unlimited,
        'allowance_in_use': state.in_use,
        'allowance_next': nxt,
        'allowance_next_label': nxt['label'] if nxt else '',
        'allowance_if_used_now': if_now,
        'allowance_if_used_now_label': if_now['label'],
        'allowance_saved': saved,
    }


def preview_door(request):
    """Which optimizer preview this request is (team only), or None."""
    return next((slug for slug in PREVIEW_DOORS if previewing(request, slug)), None)


def preview_week(request):
    """The pretend allowance week of a free-tier preview, from the team member's session; None when this request
    is not one. No answers kept yet means the week is unused."""
    if preview_door(request) not in (PREVIEW_FREE, PREVIEW_FREE_USED):
        return None
    week = request.session.get(PREVIEW_WEEK_SESSION_KEY)
    week = week if isinstance(week, dict) else {}
    used_at = parse_datetime(week.get('at') or '') if isinstance(week.get('at'), str) else None
    if not isinstance(week.get('answers'), dict) or used_at is None:
        return allowance.AllowanceState(unlimited=False)
    return allowance.AllowanceState(unlimited=False, used_at=used_at, answers=week['answers'])


def _spend_preview_week(request, options):
    request.session[PREVIEW_WEEK_SESSION_KEY] = {'answers': allowance.answers_of(options),
                                                 'at': timezone.now().isoformat()}


def _start_preview_week(request, door, context):
    """A preview page load starts its pretend week over: unused (`optimizer-free`), or spent just now on the saved
    plan's answers, or the wizard's defaults when nothing is saved (`optimizer-free-used`)."""
    if door == PREVIEW_FREE:
        request.session.pop(PREVIEW_WEEK_SESSION_KEY, None)
        return
    saved = context['saved_plan']
    answers = dict(saved['answers']) if saved else {
        'types': [r['type'] for r in context['runs'] if r['run']],
        'platforms': [p['code'] for p in context['platforms'] if p['checked']],
        'source': planner.SOURCE_BOTH, 'badge_started': bool(context['started_count']), 'badge_all': False,
        'badge_types': [], 'badge_picks': [], 'badge_weight': planner.WEIGHT_BONUS,
    }
    answers.pop('badge_pick_names', None)
    request.session[PREVIEW_WEEK_SESSION_KEY] = {'answers': answers, 'at': timezone.now().isoformat()}


def _is_preview_ticket(ticket):
    return isinstance(ticket, str) and ticket.startswith('preview-')


def _preview_place(ticket):
    """The pretend line's next place for a re-ask carrying `ticket` (0 means: build now). Anything that is not a
    well-formed pretend ticket starts at the back."""
    match = PREVIEW_TICKET.fullmatch(ticket) if isinstance(ticket, str) else None
    if match is None:
        return PREVIEW_LINE_START
    return min(int(match.group(1)), PREVIEW_LINE_START + 1) - 1


def _with_pick_names(answers):
    """Saved answers with their picked badges named (`badge_pick_names`), so the badge step can show them as chips."""
    answers = dict(answers) if isinstance(answers, dict) else {}
    picks = answers.get('badge_picks')
    picks = [gid for gid in picks if isinstance(gid, int)] if isinstance(picks, list) else []
    if picks:
        names = {gid: f'{series} ({group})' for gid, series, group in GroupBadge.objects.filter(
            pk__in=picks).values_list('id', 'series__name', 'platform_group__name')}
        answers['badge_pick_names'] = {str(gid): names[gid] for gid in picks if gid in names}
    return answers


def saved_plan_context(profile, user, runs, spent_answers):
    """"Open my plan" for the page: the hunter's autosaved plan (or a free hunter's spent one), ready for the
    script to restore, with when it was saved. None when there is nothing to reopen."""
    active = {r['type'] for r in runs if r['run']}
    found = optimizer_drafts.to_reopen(profile, spent_answers=spent_answers, active_types=active)
    if found is None:
        return None
    return {
        'answers': _with_pick_names(found['answers']),
        'tweaks': found['tweaks'],
        'saved_label': allowance.when_label(found['updated_at'], user) if found['updated_at'] else '',
        'saved_when': allowance.when_context(found['updated_at'], user) if found['updated_at'] else None,
    }


class OptimizerView(LoginRequiredMixin, _LinkedProfileRequired, TemplateView):
    """The wizard page. Everything after the first paint is fetched, so this only seeds the choices."""

    template_name = 'challenges/optimizer.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        profile = self.request.user.profile
        runs = [run_option(profile, challenge_type) for challenge_type in planner.PLANNABLE_TYPES]
        context['runs'] = runs
        context['has_runs'] = any(r['run'] for r in runs)
        context['open_total'] = sum(r['open'] for r in runs)
        context['run_count'] = sum(1 for r in runs if r['run'])
        context['steps'] = OPTIMIZER_STEPS
        context['creation_is_open'] = creation_is_open(self.request, profile)
        # Can step 1 offer any door at all? A free hunter in the beta with nothing hidden has none, and the copy
        # must say so rather than invite them to "start one" beside two cards without a Start button.
        context['can_start_any'] = context['creation_is_open'] or any(r['resumable'] for r in runs)

        saved = ((self.request.user.browse_defaults or {}).get('games', {}) or {}).get('platform') or []
        chosen = {p for p in saved if p in ALL_PLATFORMS} or set(MODERN_PLATFORMS)
        context['platforms'] = [{'code': p, 'label': PLATFORM_LABELS.get(p, p), 'checked': p in chosen}
                                for p in ALL_PLATFORMS]

        context.update(badge_step_context(profile))
        context['limits'] = {'pins': MAX_PINS, 'badges': planner.MAX_PICKED_BADGES}
        door = preview_door(self.request)
        context['preview_door'] = door or ''
        context['preview_qs'] = f'?preview={door}' if door else ''
        context['when_tz'] = allowance.display_zone(self.request.user)
        context.update(allowance_context(profile, self.request.user))
        if door in (PREVIEW_FREE, PREVIEW_FREE_USED):
            # The real saved plan first (its answers are what a free-used week was "spent" on), then the pretend week.
            context['saved_plan'] = saved_plan_context(profile, self.request.user, runs, None)
            _start_preview_week(self.request, door, context)
            context.update(allowance_context(profile, self.request.user, state=preview_week(self.request)))
        context['saved_plan'] = saved_plan_context(profile, self.request.user, runs, context['allowance_saved'])
        context['breadcrumb'] = [
            {'text': 'My Challenges', 'url': reverse('my_challenges')},
            {'text': 'Optimizer'},
        ]
        return context


class _OptimizerJsonView(_ChallengeJsonView):
    """Body parsing and the metered pool, shared by the optimizer's JSON doors except Start."""

    def body(self, request):
        try:
            data = json.loads(request.body or b'{}')
        except (ValueError, UnicodeDecodeError):
            raise PlanRequestError('That request could not be read.')
        if not isinstance(data, dict):
            raise PlanRequestError('That request could not be read.')
        return data

    def options(self, data):
        types = tuple(t for t in planner.PLANNABLE_TYPES if t in _str_list(data.get('types')))
        if not types:
            raise PlanRequestError('Pick at least one challenge to plan.')
        source = data.get('source', planner.SOURCE_BOTH)
        weight = data.get('badge_weight', planner.WEIGHT_BONUS)
        if source not in planner.SOURCES or weight not in planner.BADGE_WEIGHTS:
            raise PlanRequestError('That request could not be read.')
        # Deduplicated BEFORE the cap, so repeated ids cannot push real picks out.
        picks = tuple(dict.fromkeys(_int_list(data.get('badge_picks'))))[:planner.MAX_PICKED_BADGES]
        return planner.PlannerOptions(
            types=types,
            platforms=tuple(picker.clean_platforms(_str_list(data.get('platforms')))),
            source=source,
            badge_started=data.get('badge_started') is True,
            badge_all=data.get('badge_all') is True,
            badge_types=tuple(t for t in _str_list(data.get('badge_types')) if t in planner.BADGE_TYPES),
            badge_picks=picks,
            badge_weight=weight,
        )

    def tweaks(self, data):
        return self.tweaks_and_slugs(data)[0]

    def tweaks_and_slugs(self, data):
        """The tweaks as the planner reads them (ids), and the same tweaks in the wire's slugs with anything that
        did not resolve dropped: the shape `optimizer_drafts` keeps, so a reopened plan posts back what was valid."""
        raw = data.get('tweaks') or {}
        if not isinstance(raw, dict):
            raise PlanRequestError('That request could not be read.')
        pins = _str_list(raw.get('pins'))[:MAX_PINS]
        excluded = _str_list(raw.get('excluded'))[:MAX_EXCLUDED]
        penalized = _str_list(raw.get('penalized'))[:MAX_EXCLUDED]
        square_pins = []
        raw_square_pins = raw.get('square_pins') or []
        if not isinstance(raw_square_pins, list):
            raise PlanRequestError('That request could not be read.')
        for entry in raw_square_pins[:MAX_LINES]:
            if isinstance(entry, dict) and all(isinstance(entry.get(f), str) for f in ('type', 'key', 'contract')):
                square_pins.append((entry['type'], entry['key'], entry['contract']))
        raw_left_open = raw.get('left_open') or []
        if not isinstance(raw_left_open, list):
            raise PlanRequestError('That request could not be read.')
        # Deduplicated BEFORE the cap, as the badge picks are, so repeats cannot crowd out real entries.
        left_open = list(dict.fromkeys(
            (e['type'], e['key']) for e in raw_left_open
            if isinstance(e, dict) and isinstance(e.get('type'), str) and isinstance(e.get('key'), str)))[:MAX_LINES]
        ids = _ids_for({*pins, *excluded, *penalized, *(s for _, _, s in square_pins)})
        tweaks = planner.Tweaks(
            pins=frozenset(ids[s] for s in pins if s in ids),
            square_pins={(t, k): ids[s] for t, k, s in square_pins if s in ids},
            excluded=frozenset(ids[s] for s in excluded if s in ids),
            penalized=frozenset(ids[s] for s in penalized if s in ids),
            left_open=frozenset(left_open),
        )
        slugs = {
            'pins': [s for s in dict.fromkeys(pins) if s in ids],
            'square_pins': [{'type': t, 'key': k, 'contract': s} for t, k, s in square_pins if s in ids],
            'excluded': [s for s in dict.fromkeys(excluded) if s in ids],
            'penalized': [s for s in dict.fromkeys(penalized) if s in ids],
            'left_open': [{'type': t, 'key': k} for t, k in left_open],
        }
        return tweaks, slugs

    def pool(self, request, profile, options, line_ticket=None, join_line=True):
        """The cached pool, or a freshly built one if the BUILD bucket allows it. None when it does not.

        A hunter WAITING IN LINE is not charged again: their build was counted when they joined, and re-asking every
        few seconds must not exhaust a limit meant for scripts. The waiver needs THEIR OWN ticket (tickets are bound
        to the user) on a whole-catalogue question (the only kind that waits), so a ticket cannot be used to build
        other answers past the limit. A ticket whose answer turns out to be cached leaves the line at once.

        A caller that will not join the line is not charged for a build the line would refuse anyway: it gets
        `LineBusy` before the bucket is touched."""
        pool, _ = planner.get_pool(profile, options, build_if_missing=False)
        if pool is not None:
            build_line.leave(line_ticket, request.user.id)
            return pool, False
        broad = options.badge_all or bool(options.chosen_types)
        if broad and not join_line and not build_line.has_room():
            raise build_line.LineBusy()
        waiting = broad and build_line.valid_ticket(line_ticket, request.user.id)
        if waiting:
            try:
                return planner.get_pool(profile, options, line_ticket=line_ticket, join_line=join_line)
            except build_line.TicketLost:
                # Claimed by a parallel request with the same ticket, or dropped since the check above: this
                # request is a newcomer again, and newcomers are charged.
                pass
        if self._build_limited(request):
            return None, False
        return planner.get_pool(profile, options, join_line=join_line)

    @staticmethod
    def _build_limited(request):
        return is_ratelimited(request, group=OPTIMIZER_BUILD_RATELIMIT_GROUP, key='user', rate=BUILD_RATE,
                              increment=True)


def _str_list(value):
    if not isinstance(value, list):
        return []
    return [v for v in value if isinstance(v, str) and v]


def _int_list(value):
    """Positive ints from a JSON list; anything else is dropped (a bool is an int in Python, so it is refused)."""
    if not isinstance(value, list):
        return []
    return [v for v in value if isinstance(v, int) and not isinstance(v, bool) and v > 0]


def _ids_for(slugs):
    if not slugs:
        return {}
    return dict(Contract.objects.filter(slug__in=slugs).values_list('slug', 'pk'))


def _allowance_check(request, profile, options, data, active_types):
    """(outcome, refusal): the allowance's outcome for this plan, and the reply to send instead when it may not be
    built. NOTHING IS SPENT HERE: the plan door spends only after the plan is built.

    409 `needs_confirmation` when it would spend this week's free plan and the hunter has not said yes (the page
    asks before posting, so this is the stale-tab and crafted-request path). 403 `plan_locked` when this week's
    plan is spent on other answers."""
    outcome = allowance.decide(profile, options, active_types, state=preview_week(request))
    if outcome == allowance.SPEND and data.get('use_free_plan') is not True:
        return outcome, JsonResponse({
            'needs_confirmation': True,
            'error': 'This would use your free optimized plan for the week.',
            **_next_free(timezone.now() + allowance.FREE_PLAN_WINDOW, request.user),
        }, status=409)
    if outcome == allowance.LOCKED:
        return outcome, _locked_response(request, profile)
    return outcome, None


def _next_free(when, user):
    """A reply's next-free date: the server's text and the moment, so the page can show it on the hunter's clock."""
    return {'next_free_label': allowance.when_label(when, user), 'next_free_at': when.isoformat()}


def _locked_response(request, profile):
    """403 with the next free date AND this week's saved answers, so a tab that never saw the spend (another tab
    made it) can show "Your plan this week" and offer Open my plan."""
    context = allowance_context(profile, request.user, state=preview_week(request))
    label = context['allowance_next_label']
    return JsonResponse({
        'plan_locked': True,
        'error': (f"This week's free plan uses different answers. Your next one is ready {label}." if label
                  else 'Plan it first to use your free plan for the week.'),
        'next_free_label': label,
        'next_free_at': context['allowance_next']['iso'] if context['allowance_next'] else '',
        'saved_answers': context['allowance_saved'],
    }, status=403)


class OptimizerPlanView(_OptimizerJsonView):
    """Build a plan, or re-solve one with the hunter's tweaks. POST because the body is structured. Its only
    writes follow a plan that was built: a free hunter's weekly spend, and the autosaved draft that "Open my plan"
    reopens (`optimizer_drafts`)."""

    @method_decorator(ratelimit(group=OPTIMIZER_SOLVE_RATELIMIT_GROUP, key='user', rate=SOLVE_RATE,
                                method='POST', block=False))
    @_json_when_limited
    def post(self, request):
        profile = self._profile(request)
        try:
            data = self.body(request)
            options = self.options(data)
            tweaks, tweak_slugs = self.tweaks_and_slugs(data)
        except PlanRequestError as exc:
            return self.fail(exc)
        # RUNS FIRST: a hunter with nothing to plan must not spend a build (and the pool scan) to be told so.
        states = planner.run_states(profile, options.types)
        if not states:
            build_line.leave(data.get('line_ticket'), request.user.id)   # refused while waiting: out of line
            return self.fail(PlanRequestError('Start a challenge to plan it.'))
        # THE WEEKLY ALLOWANCE, before any build: a free hunter's plan needs their yes (`use_free_plan`), and a plan
        # on other answers waits for next week's. The spend itself waits until the plan EXISTS (below), so a busy
        # site, a rate limit or an error never costs a hunter their week.
        outcome, refusal = _allowance_check(request, profile, options, data, set(states))
        if refusal is not None:
            build_line.leave(data.get('line_ticket'), request.user.id)
            return refusal
        door = preview_door(request)
        line_ticket = data.get('line_ticket')
        if door == PREVIEW_LINE and (line_ticket is None or _is_preview_ticket(line_ticket)):
            # THE PRETEND LINE, for a plan from the wizard only (a tweak re-solve never waits in the real one either,
            # since its pool is cached). Its tickets are never real ones, and a REAL ticket (the real line was full
            # when the pretend one ended) passes straight through, so it keeps its real place.
            place = _preview_place(line_ticket) if data.get('from_wizard') is True else 0
            if place:
                return JsonResponse({'queued': True, 'ticket': f'preview-{place}', 'position': place,
                                     'eta_seconds': place * PREVIEW_LINE_SECONDS_PER_PLACE}, status=202)
            line_ticket = None
        try:
            pool, built = self.pool(request, profile, options, line_ticket=line_ticket)
        except build_line.LineWait as wait:
            # IN LINE, NOT REFUSED (owner, 2026-10-16): 202 with the hunter's place and an estimate. The page
            # re-asks with the ticket. Nothing is spent: the week's plan is spent only on a plan that is built.
            return JsonResponse({
                'queued': True, 'ticket': wait.ticket, 'position': wait.position, 'eta_seconds': wait.eta_seconds,
            }, status=202)
        if pool is None:
            return JsonResponse({'error': BUILD_LIMITED_MESSAGE}, status=429)
        plan = planner.make_plan(profile, options, pool, states, tweaks)
        # A square left open that the hunter has since filled by hand is forgotten, not saved for ever.
        tweak_slugs = {**tweak_slugs, 'left_open': [{'type': t, 'key': k} for t, k in plan.left_open]}
        body = _plan_json(plan, pool, states, tweaks, built, badges_only=options.badges_only)
        if outcome == allowance.SPEND:
            if preview_week(request) is not None:
                _spend_preview_week(request, options)   # the pretend week, never a real row
            else:
                try:
                    allowance.spend(profile, options, set(states))
                except allowance.AllowanceLocked:   # another tab spent it on other answers while this one built
                    return _locked_response(request, profile)
            # THE WEEK'S OWN DATE: a spend that was a no-op (another tab had just spent it on these answers) keeps
            # the earlier `used_at`, and "now + 7 days" would overstate the wait.
            # The SAME test that chose a pretend spend above: the line door is a preview with a real week.
            week = preview_week(request)
            spent_at = week if week is not None else allowance.state_for(profile)
            next_at = (spent_at.next_free_at if spent_at is not None and spent_at.next_free_at
                       else timezone.now() + allowance.FREE_PLAN_WINDOW)
            body['allowance'] = {'spent': True, **_next_free(next_at, request.user)}
        if door is None:
            _autosave(profile, options, tweak_slugs)
        return JsonResponse(body)


def _autosave(profile, options, tweak_slugs):
    """Keep the plan just built for "Open my plan". A convenience: a failed save is logged, never a lost plan."""
    try:
        optimizer_drafts.save(profile, options, tweak_slugs)
    except Exception:
        logger.exception('optimizer draft could not be saved for profile %s', profile.pk)


class OptimizerAlternativesView(_OptimizerJsonView):
    """ "Other options" for one square, from the cached pool, a page at a time."""

    def post(self, request):
        profile = self._profile(request)
        try:
            data = self.body(request)
        except PlanRequestError as exc:
            return self.fail(exc)
        offset = data.get('offset')
        if not isinstance(offset, int) or isinstance(offset, bool) or offset < 0:
            offset = 0
        # THE SHEET BUCKET, every page and tab alike (module docstring). Read at call time, not baked into a
        # decorator, so the rate is one module constant the tests can turn.
        if is_ratelimited(request, group=OPTIMIZER_SHEET_RATELIMIT_GROUP, key='user', rate=SHEET_RATE,
                          method='POST', increment=True):
            return JsonResponse({'error': RATE_LIMITED_MESSAGE}, status=429)
        try:
            options = self.options(data)
            tweaks = self.tweaks(data)
        except PlanRequestError as exc:
            return self.fail(exc)
        square = (data.get('type'), data.get('key'))
        if not all(isinstance(part, str) for part in square):
            return self.fail(PlanRequestError('That request could not be read.'))
        partners = _partner_squares(data.get('partners'))
        states = planner.run_states(profile, options.types)
        if square[0] not in states:
            return self.not_found()
        # Other options belongs to a plan the hunter HAS: never a way to look at a plan they have not spent on.
        if allowance.decide(profile, options, set(states), state=preview_week(request)) != allowance.FREE:
            return _locked_response(request, profile)
        try:
            # NOT JOINING the line: Other options is a one-off, and a ticket nobody re-asks for would stall everyone
            # behind it until it lapsed. It builds only when the cached plan expired, which is rare.
            pool, _ = self.pool(request, profile, options, join_line=False)
        except build_line.LineBusy:
            return JsonResponse({'error': CATALOGUE_BUSY_MESSAGE}, status=429)
        if pool is None:
            return JsonResponse({'error': BUILD_LIMITED_MESSAGE}, status=429)
        # THE LINE'S OWN GAME is left out like an excluded one: it fits both squares by definition, and the sheet
        # never offers it (counted, it would make the "C + Gunslinger" tab read one game more than it can show).
        current = data.get('current')
        skip = set(tweaks.excluded)
        if isinstance(current, str) and current:
            skip.update(Contract.objects.filter(slug=current).values_list('pk', flat=True))
        halves = planner.alternatives(pool, states, square, frozenset(skip), options=options, partners=partners,
                                      left_open=tweaks.left_open, badge_only=data.get('badge_only') is True)
        # THE TAB: the one asked for, else the first with games (both, then the square alone, then the partner
        # alone), else the square alone. `fallback` (the badge chip) also moves off an asked-for tab the filter
        # left empty. `offset` pages it.
        first_with_games = next((g for g in ALTERNATIVES_GROUPS if halves[g]), 'rest')
        group = data.get('group')
        if not isinstance(group, str) or group not in ALTERNATIVES_GROUPS:
            group = first_with_games
        elif data.get('fallback') is True and not halves[group]:
            group = first_with_games
        found = halves[group][offset:offset + ALTERNATIVES_PAGE]
        rows = _rows_by_id({cand['id'] for cand, _ in found})
        labels = _labels(states)
        return JsonResponse({
            'square': _square(square, labels),
            'group': group,
            'counts': {g: len(halves[g]) for g in ALTERNATIVES_GROUPS},
            'rows': [{**rows[cand['id']], 'stages': cand['stages'], 'progress': cand['progress'],
                      'also': [_square(sq, labels) for sq in also]}
                     for cand, also in found if cand['id'] in rows],
            'offset': offset,
            # Counted on the page TAKEN, not the rows drawn, so a row whose contract vanished mid-read never
            # shifts the next page's start.
            'showing': len(found),
            'more': offset + len(found) < len(halves[group]),
        })


class OptimizerSearchView(_OptimizerJsonView):
    """The pin search: the picker's own contract-first search, once per active run, merged by game.

    REUSED, NOT REWRITTEN: `picker.search_panel` already owns the term scrubbing, the minimum length, the
    bounded slice and the per-run open keys. Two runs cost two of its calls."""

    @method_decorator(ratelimit(group=CHALLENGE_READ_RATELIMIT_GROUP, key='user', rate='90/m',
                                method=('GET', 'HEAD'), block=False))
    @_json_when_limited
    def get(self, request):
        profile = self._profile(request)
        query = request.GET.get('q', '')
        types = [t for t in planner.PLANNABLE_TYPES if t in request.GET.getlist('type')] or list(planner.PLANNABLE_TYPES)
        runs = {t: svc.active_run(profile, t) for t in types}
        runs = {t: r for t, r in runs.items() if r is not None}
        if not runs:
            return JsonResponse({'query': query, 'too_short': False, 'no_runs': True, 'rows': []})
        merged, order, too_short = {}, [], True
        for run_type, run in runs.items():
            panel = picker.search_panel(profile, run, query, limit=SEARCH_LIMIT)
            too_short = panel['too_short']
            for row in panel['rows']:
                entry = merged.get(row['slug'])
                if entry is None:
                    entry = merged[row['slug']] = {
                        **_wire({k: row[k] for k in ('slug', 'name', 'cover', 'platforms', 'availability')}),
                        'is_completed_by_you': row['is_completed_by_you'], 'fits': {}, 'in_run': {},
                    }
                    order.append(row['slug'])
                entry['fits'][run_type] = [{'key': k, 'label': panel['key_labels'].get(k, k)} for k in row['keys']]
                entry['in_run'][run_type] = row['already_in_run']
        return JsonResponse({'query': query, 'too_short': too_short, 'rows': [merged[s] for s in order]})


class OptimizerBadgesView(_OptimizerJsonView):
    """Step 4's badge picker: a page of edition cards, server-rendered (the medallion is a template component),
    filtered exactly as the Browse Gallery filters (`badge_list_service.live_editions`).

    GET `?q=` `?type=` (repeatable) `?group=` (repeatable, edition keys) `?page=`. Answers `{html, more, total,
    page}`. The cards come back unpressed; the script marks the ones in its selection. A read on the picker's own
    read bucket, like the must-have search."""

    @method_decorator(ratelimit(group=CHALLENGE_READ_RATELIMIT_GROUP, key='user', rate='90/m',
                                method=('GET', 'HEAD'), block=False))
    @_json_when_limited
    def get(self, request):
        profile = self._profile(request)
        qs = live_editions(
            groups=request.GET.getlist('group'),
            badge_types=[t for t in request.GET.getlist('type') if t in planner.BADGE_TYPES],
            query=picker.clean_term(request.GET.get('q', '')),
        ).select_related('series__submitted_by').order_by(Lower('series__name'), 'platform_group__sort_order', 'pk')
        try:
            page = max(1, min(int(request.GET.get('page', 1)), MAX_BADGE_PAGES))
        except (TypeError, ValueError):
            page = 1
        start = (page - 1) * BADGE_PAGE_SIZE
        total = qs.count()
        cards = badge_cards(qs[start:start + BADGE_PAGE_SIZE], profile)
        html = render_to_string('challenges/partials/_optimizer_badge_cards.html', {'cards': cards}, request=request)
        return JsonResponse({'html': html, 'total': total, 'page': page,
                             'more': start + len(cards) < total and page < MAX_BADGE_PAGES})


class OptimizerStartView(_ChallengeJsonView):
    """Start (or resume) a run from the optimizer's step 1 WITHOUT leaving the page: the JSON twin of
    `challenge_start`, which stays as the no-JavaScript fallback the same form posts to.

    THE RULES ARE THE FORM DOOR'S, called rather than copied (`views.start_from_request`), and so is the rate
    budget: the same group and rate, so `django_ratelimit` buckets the two doors together."""

    @method_decorator(ratelimit(group=CHALLENGE_WRITE_RATELIMIT_GROUP, key='user', rate='30/m',
                                method='POST', block=False))
    @_json_when_limited
    def post(self, request, challenge_type):
        if challenge_type not in planner.PLANNABLE_TYPES:
            return self.not_found()
        challenge, outcome, error = start_from_request(request, challenge_type)
        if error:
            return self.fail(PlanRequestError(error))
        message = {svc.CREATED: f'{challenge.name} is ready to plan.',
                   svc.RESUMED: f'{challenge.name} is back where you left it.'}.get(outcome, '')
        option = run_option(request.user.profile, challenge_type)
        # THE CARD, RENDERED BY THE SERVER from the same partial the page uses, so the card swapped in without a
        # reload is the card a reload would draw. Auto-escaped by the template engine like any other page.
        html = render_to_string('challenges/partials/_optimizer_run_option.html',
                                {'r': option, 'creation_is_open': creation_is_open(request, request.user.profile)},
                                request=request)
        return JsonResponse({'type': challenge_type, 'outcome': outcome, 'open': option['open'],
                             'message': message, 'html': html})


class OptimizerApplyView(_OptimizerJsonView):
    """Fill the accepted lines, one `assign` each. Lines are independent: one refusal never stops the rest.

    A LINE SAYS WHAT IT EXPECTS TO REPLACE (`replacing`, '' for an open square), and a square whose contents
    changed since the plan was drawn is SKIPPED, never overwritten: an open-square line must not land on a
    game the hunter placed in another tab, and a swap must replace the game it was offered against.

    THE CHECK IS A SNAPSHOT, NOT A LOCK: the slots are read before the loop and `assign` locks per line, so a
    write from another tab in that window is not caught here. `assign` still refuses everything it would
    refuse anyway; what slips through is only "replaced a game the hunter placed a moment ago", which the
    square can undo.

    NEVER A LOCKING PLACEMENT. `acknowledge_lock` is always False, so a game the hunter finished since
    planning (which would complete and lock the square) comes back skipped with a pointer to the square,
    whose picker asks first. The optimizer never auto-applies a lock-in (owner, 2026-10-16)."""

    @method_decorator(ratelimit(group=CHALLENGE_WRITE_RATELIMIT_GROUP, key='user', rate='30/m',
                                method='POST', block=False))
    @_json_when_limited
    def post(self, request):
        profile = self._profile(request)
        try:
            data = self.body(request)
            lines = _apply_lines(data.get('lines'))
        except PlanRequestError as exc:
            return self.fail(exc)

        types = {t for t, _, _, _ in lines}
        runs = {t: svc.active_run(profile, t) for t in types}
        slots = {t: {s.key: s for s in run.slots.all()} for t, run in runs.items() if run is not None}
        contracts = {c.slug: c for c in Contract.objects.filter(slug__in={s for _, _, s, _ in lines})}

        labels = {t: picker.square_labels(run) for t, run in runs.items() if run is not None}
        results = []
        for run_type, key, slug, replacing in lines:
            contract = contracts.get(slug)
            outcome = _apply_one(profile, runs.get(run_type), slots.get(run_type, {}).get(key),
                                 contract, key, replacing)
            # NAMED, so a skipped line reads "Brawler: Bloodborne ..." rather than a raw job slug.
            results.append({'type': run_type, 'key': key, 'contract': slug,
                            'label': labels.get(run_type, {}).get(key, {}).get('label', key),
                            'name': contract.name if contract else '', **outcome})

        # THE SAVED PLAN STAYS (owner, 2026-10-16), minus what just went on the board. Never a reason to report a
        # done Apply as failed: the squares are already filled.
        try:
            optimizer_drafts.forget_applied(profile, {r['contract'] for r in results if r['status'] == 'applied'})
        except Exception:
            logger.exception('optimizer draft could not forget applied games for profile %s', profile.pk)
        fresh = Challenge.objects.filter(pk__in=[r.pk for r in runs.values() if r is not None])
        is_open = creation_is_open(request, profile)
        return JsonResponse({
            # STEP 1'S CARDS, re-rendered: "X done, N planned" just changed, and Edit answers would show it stale.
            'cards': {t: render_to_string('challenges/partials/_optimizer_run_option.html',
                                          {'r': run_option(profile, t), 'creation_is_open': is_open}, request=request)
                      for t in runs},
            'results': results,
            'applied': sum(1 for r in results if r['status'] == 'applied'),
            'runs': {run.challenge_type: {'filled_count': run.filled_count, 'completed_count': run.completed_count,
                                          'total_slots': run.total_slots,
                                          'url': reverse('challenge_detail', args=[run.pk])}
                     for run in fresh},
        })


def _apply_lines(raw):
    if not isinstance(raw, list) or not raw:
        raise PlanRequestError('There is nothing to apply.')
    if len(raw) > MAX_LINES:
        raise PlanRequestError('That plan is too long to apply at once.')
    lines, seen = [], set()
    for entry in raw:
        if not isinstance(entry, dict):
            raise PlanRequestError('That request could not be read.')
        run_type, key, slug = entry.get('type'), entry.get('key'), entry.get('contract')
        replacing = entry.get('replacing') or ''
        if not all(isinstance(v, str) and v for v in (run_type, key, slug)) or not isinstance(replacing, str):
            raise PlanRequestError('That request could not be read.')
        if run_type not in planner.PLANNABLE_TYPES or (run_type, key) in seen:
            raise PlanRequestError('That request could not be read.')
        seen.add((run_type, key))
        lines.append((run_type, key, slug, replacing))
    return lines


def _apply_one(profile, run, slot, contract, key, replacing):
    if run is None or slot is None:
        return {'status': 'skipped', 'reason': 'That challenge is not running any more.'}
    if slot.is_completed or slot.contract_slug != replacing:
        return {'status': 'skipped', 'reason': 'That square changed since you planned it.'}
    if contract is None:
        return {'status': 'skipped', 'reason': 'That game is not on the Job Board.'}
    try:
        svc.assign(run, profile, key, contract, acknowledge_lock=False)
    except svc.ConfirmationRequired:
        return {'status': 'skipped',
                'reason': 'You finished that one since planning. Place it from its square to confirm.'}
    except svc.ChallengeError as exc:
        return {'status': 'skipped', 'reason': str(exc)}
    return {'status': 'applied'}


# --- plan JSON --------------------------------------------------------------------------------------------

def _wire(row):
    """A picker row as the browser receives it: the cover as a URL plus whether it is real art, exactly as
    the picker's own doors send it."""
    return {**row, 'cover': _cover_url(row['cover']), 'cover_is_art': _cover_is_art(row['cover'])}


def _rows_by_id(ids):
    if not ids:
        return {}
    return {cid: _wire(row) for cid, row in picker.contract_rows(list(Contract.objects.filter(pk__in=ids))).items()}


#: A plan line holds at most one square per run, so it has at most this many squares besides the one opened.
MAX_PARTNER_SQUARES = len(planner.PLANNABLE_TYPES) - 1


def _partner_squares(raw):
    """The other squares of the line Other options was opened from, as (type, key) pairs. Hunter-supplied, so
    anything malformed is dropped rather than refused: the sheet still answers, just without the tabs about
    the partner ("C + Gunslinger", "Gunslinger only"). `alternatives` itself ignores a partner in a run with
    no active run, or in the opened square's own run."""
    if not isinstance(raw, list):
        return []
    pairs = [(p.get('type'), p.get('key')) for p in raw[:MAX_PARTNER_SQUARES] if isinstance(p, dict)]
    return [(t, k) for t, k in pairs if isinstance(t, str) and isinstance(k, str)]


def _labels(states):
    return {t: picker.square_labels(s.challenge) for t, s in states.items()}


def _badge_blocked(plan, pool, states):
    """The empty squares a game that clears no stage could have filled, in board order of `plan.empty`."""
    if not plan.empty:
        return []
    fillable = {(t, k) for c in pool['candidates'] if not c['stages']
                for t, keys in c['fits'].items() if t in states and c['id'] not in states[t].in_run for k in keys}
    return [sq for sq in plan.empty if sq in fillable]


def _job_groups(labels):
    """The Job view's sections (owner, 2026-10-17): the jobs run's squares bucketed by discipline, in the run
    page's own order (`slot_render.discipline_sequence`), keys in board order. Empty with no jobs run."""
    by_discipline = {}
    for key, info in labels.get(planner.CHALLENGE_TYPE_JOBS, {}).items():
        by_discipline.setdefault((info['look'] or {}).get('disc_slug') or '', []).append(key)
    return [{**slot_render.discipline_head(slug), 'keys': by_discipline[slug]}
            for slug in slot_render.discipline_sequence(by_discipline)]


def _square(square, labels):
    run_type, key = square
    info = labels.get(run_type, {}).get(key, {'label': key, 'look': None})
    return {'type': run_type, 'key': key, 'label': info['label'], 'look': info['look']}


def _square_order(labels):
    order = {}
    for run_type in planner.PLANNABLE_TYPES:
        for position, key in enumerate(labels.get(run_type, {})):
            order[(run_type, key)] = (planner.PLANNABLE_TYPES.index(run_type), position)
    return order


def _plan_json(plan, pool, states, tweaks, built, badges_only=False):
    labels = _labels(states)
    order = _square_order(labels)
    # A game the answers would not have chosen is "outside your filters": not in the pool, or (badge games only)
    # in it but clearing no stage, so a must-have the hunter forced in is tagged either way.
    # An UNOBTAINABLE must-have that PASSED the filters is not "outside your filters" either: the pool left it out
    # for being unfinishable, which its own chip already says (`pool['unobtainable']`, kept by the build).
    pool_ids = ({c['id'] for c in pool['candidates'] if c['stages'] or not badges_only}
                | {cid for cid in pool.get('unobtainable', ()) if cid in pool['stages'] or not badges_only})
    pinned = set(tweaks.pins) | set(tweaks.square_pins.values())
    ids = (set(plan.candidates) | {cur for cur, _, _ in plan.swaps.values()} | set(plan.unplaced_pins))
    rows = _rows_by_id(ids)

    by_contract = {}
    for square in sorted(plan.assignments, key=order.__getitem__):
        by_contract.setdefault(plan.assignments[square], []).append(square)
    lines = []
    for cid, squares in by_contract.items():
        cand = plan.candidates.get(cid, {})
        lines.append({
            **rows.get(cid, {}),
            'squares': [_square(sq, labels) for sq in squares],
            # THE SLOT'S OWN SNAPSHOT, which is what Apply compares: a staff rename leaves it stale, and the
            # live slug would then never match.
            'replacing': {sq[0]: states[sq[0]].snapshots[sq[1]]
                          for sq in squares if sq[1] in states[sq[0]].snapshots},
            # What an accepted swap will overwrite, said on the line itself: the swap row is gone once accepted.
            'replaces': [{**_square(sq, labels), 'name': states[sq[0]].names.get(sq[1], '')}
                         for sq in squares if sq[1] in states[sq[0]].snapshots],
            'stages': cand.get('stages', []),
            'progress': cand.get('progress', 0),
            'pinned': cid in pinned,
            'outside_filters': cid not in pool_ids,
            # THE SORT TOGGLE'S KEYS: where this game's square sits on each board it fills (the board's own
            # order, as its run page shows it). A game takes one square per run, so there is one per type.
            'sort': {sq[0]: order[sq][1] for sq in squares},
        })
    # Games that cover both runs lead, then the board order of their first square.
    lines.sort(key=lambda line: (-len(line['squares']), order[(line['squares'][0]['type'], line['squares'][0]['key'])]))

    # ONE CARD PER SUGGESTED GAME (owner, 2026-10-17). The planner suggests per square, so a game that would
    # replace unfinished games in BOTH runs arrives as two swaps, each naming the other square as "also covers".
    # Merged, the card names every game it replaces and the hunter chooses both or one. `also` drops every square
    # it would replace, and every square the game ALREADY holds unfinished (it is staying there, so it covers
    # nothing new); what is left is the game's plan squares, or an open square the swap could fill.
    by_game = {}
    for square in sorted(plan.swaps, key=order.__getitem__):
        current, suggested, also = plan.swaps[square]
        by_game.setdefault(suggested, []).append((square, current, also))
    swaps = []
    for suggested, items in by_game.items():
        replaced = {square for square, _, _ in items}
        also = {sq for _, _, extra in items for sq in extra
                if sq not in replaced and states[sq[0]].unfinished.get(sq[1]) != suggested}
        cand = plan.candidates.get(suggested, {})
        swaps.append({
            'replaces': [{'square': _square(square, labels),
                          'current': {k: rows.get(current, {}).get(k, '') for k in ('slug', 'name')}}
                         for square, current, _ in items],
            'suggested': {**rows.get(suggested, {}), 'stages': cand.get('stages', []),
                          'progress': cand.get('progress', 0)},
            'also': [_square(sq, labels) for sq in sorted(also, key=order.__getitem__)],
        })

    return {
        'built': built,
        'runs': {t: {'id': s.challenge.pk, 'name': s.challenge.name,
                     'url': reverse('challenge_detail', args=[s.challenge.pk]),
                     'open': len(s.open_keys), 'unfinished': len(s.unfinished), 'total': s.challenge.total_slots}
                 for t, s in states.items()},
        'lines': lines,
        'swaps': swaps,
        'unplaced': [{**{k: rows.get(cid, {}).get(k, '') for k in ('slug', 'name')}, 'reason': reason}
                     for cid, reason in plan.unplaced_pins.items()],
        'empty': [_square(sq, labels) for sq in sorted(plan.empty, key=order.__getitem__)],
        'left_open': [_square(sq, labels) for sq in sorted(plan.left_open, key=order.__getitem__)],
        'job_groups': _job_groups(labels),
        'notes': {
            'hatch': {t: [_square((t, k), labels) for k in keys] for t, keys in plan.hatch_keys.items() if keys},
            'importer': plan.importer_note,
            # So "Still open" can say WHY: the open squares that only "badge games only" left open (some other
            # game fits them), and nothing for a square no game fits at all.
            'badge_blocked': [_square(sq, labels) for sq in _badge_blocked(plan, pool, states) if badges_only],
        },
    }
