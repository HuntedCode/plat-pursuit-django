"""The Challenge Optimizer's weekly allowance: one free optimized plan a week, members any time (owner, 2026-10-16).

PREMIUM IS A DIAL, NOT A DOOR. Everyone can optimize; the dial is how often. A free hunter gets one plan per
rolling seven days, and "one plan" means ONE SET OF ANSWERS: every tweak, reroll, Apply and Plan again that keeps
those answers is free all week, and coming back later reopens it. Changing an answer is a new plan. Members are
unlimited (only the optimizer's rate limits apply).

THREE OUTCOMES for a plan request, decided here and nowhere else:

- `FREE`: a member, or this week's plan again (same answers);
- `SPEND`: no plan used this week yet; the hunter must say yes first (the page asks before it ever posts), and the
  plan door spends only once the plan has actually been BUILT, so a busy site or an error never costs the week;
- `LOCKED`: this week's plan was spent on different answers; the page says when the next one comes.

"THE SAME ANSWERS" IS JUDGED AGAINST WHAT STILL EXISTS. The saved answers are re-read with their challenges cut
to the runs that are active now and their badge types cut to the types with live badges now, and so are the new
ones. Otherwise a run that finished mid-week (its checkbox gone from step 1) or a badge type that went dormant
would lock a hunter out of the plan they already spent.

The spend is a WRITE, so it locks the hunter's profile row and re-decides under the lock: two tabs pressing
"Plan it" at once cannot spend two plans, or one plan twice.
"""
from dataclasses import dataclass
from datetime import timedelta

from django.db import transaction
from django.utils import timezone
from django.utils.formats import date_format

from challenges.models import OptimizerAllowance
from trophies.models import GroupBadge, Profile

#: A spent plan stays the hunter's for this long, then the next free one is available. Rolling, to the minute.
FREE_PLAN_WINDOW = timedelta(days=7)

FREE = 'free'
SPEND = 'spend'
LOCKED = 'locked'


class AllowanceLocked(Exception):
    """This week's free plan is already spent on other answers."""


@dataclass(frozen=True)
class AllowanceState:
    unlimited: bool
    used_at: object = None          # datetime of this week's spend, None when none is in use
    answers: dict = None

    @property
    def in_use(self):
        return self.used_at is not None

    @property
    def next_free_at(self):
        return self.used_at + FREE_PLAN_WINDOW if self.used_at else None


def is_unlimited(profile):
    """Members optimize any time. The same membership flag the Challenges beta gate reads."""
    return bool(getattr(profile, 'user_is_premium', False))


def state_for(profile, now=None):
    if is_unlimited(profile):
        return AllowanceState(unlimited=True)
    now = now or timezone.now()
    row = OptimizerAllowance.objects.filter(profile=profile).first()
    if row is None or row.used_at + FREE_PLAN_WINDOW <= now:
        return AllowanceState(unlimited=False)
    return AllowanceState(unlimited=False, used_at=row.used_at, answers=row.answers or {})


def decide(profile, options, active_types, now=None, state=None):
    """`active_types`: the plannable run types the hunter has running NOW (the plan door's `run_states`). `state`
    replaces the hunter's real one (a team preview's pretend week); by default it is read."""
    state = state or state_for(profile, now)
    if state.unlimited:
        return FREE
    if state.in_use:
        return FREE if same_plan(state.answers, answers_of(options), active_types) else LOCKED
    return SPEND


def same_plan(saved, current, active_types):
    """Are two sets of answers the same plan, judged against what still exists? See the module docstring."""
    live = live_badge_types()
    return _plan_key(saved, active_types, live) == _plan_key(current, active_types, live)


def live_badge_types():
    return set(GroupBadge.objects.filter(is_live=True).values_list('series__badge_type', flat=True).distinct())


def _plan_key(answers, active_types, live_types):
    from challenges.services.planner import PlannerOptions   # planner imports nothing from here; avoid a cycle risk
    a = answers or {}
    return PlannerOptions(
        types=tuple(t for t in a.get('types', ()) if t in set(active_types)),
        platforms=tuple(a.get('platforms', ())),
        source=a.get('source', 'both'),
        badge_started=bool(a.get('badge_started')),
        badge_all=bool(a.get('badge_all')),
        badge_types=tuple(t for t in a.get('badge_types', ()) if t in live_types),
        badge_picks=tuple(a.get('badge_picks', ())),
    ).digest()


@transaction.atomic
def spend(profile, options, active_types, now=None):
    """Spend this week's free plan on `options`. A no-op when it is already theirs; raises `AllowanceLocked` when
    another tab spent it on different answers first."""
    Profile.objects.select_for_update(no_key=True).filter(pk=profile.pk).first()
    now = now or timezone.now()
    outcome = decide(profile, options, active_types, now)
    if outcome == LOCKED:
        raise AllowanceLocked()
    if outcome == FREE:
        return
    OptimizerAllowance.objects.update_or_create(
        profile=profile, defaults={'used_at': now, 'digest': options.digest(), 'answers': answers_of(options)})


def answers_of(options):
    """The answers in the plan door's own body shape, so the page can restore them and post them back unchanged."""
    return {
        'types': list(options.types), 'platforms': list(options.platforms), 'source': options.source,
        'badge_started': options.badge_started, 'badge_all': options.badge_all,
        'badge_types': list(options.chosen_types), 'badge_picks': list(options.picked_badges),
        'badge_weight': options.badge_weight,
    }


def when_label(when, user=None):
    """'Thu, Oct 23 at 8:12 PM EDT' in the hunter's saved timezone (the middleware activates it per request) and
    clock (`use_24hr_clock`). WITH THE TIME, because the window is rolling to the minute: "ready Thu, Oct 23" alone
    reads as the start of that day, and the plan is still locked that morning. WITH THE ZONE, so a date is never
    ambiguous about whose clock it is on."""
    clock = 'H:i' if getattr(user, 'use_24hr_clock', False) else 'g:i A'
    return date_format(timezone.localtime(when), 'D, M j \\a\\t ' + clock + ' T')


def display_zone(user):
    """The zone a date is shown in (owner, 2026-10-17): the hunter's saved one when they ever chose it, or it is
    anything but the untouched UTC default. '' means "the device's own": a hunter who never chose sees their own
    clock, not UTC's."""
    zone = getattr(user, 'user_timezone', '') or 'UTC'
    if getattr(user, 'timezone_confirmed_at', None) is not None or zone != 'UTC':
        return zone
    return ''


def when_context(when, user=None):
    """One date as a page shows it (`partials/_when.html`): the moment (`iso`), the server's own text (`label`,
    the no-script fallback), and what the page's script re-renders it with: `tz` (`display_zone`) and `clock`."""
    return {'iso': when.isoformat(), 'label': when_label(when, user), 'tz': display_zone(user),
            'clock': '24' if getattr(user, 'use_24hr_clock', False) else '12'}
