"""The Admin Hub: what needs a human today, what was just done, and the way into every staff tool.

Lives in `core` for the same reason `AdminAction` does -- it reads moderation (trophies), badge
claims (fundraiser) and the worker queues (redis), and belongs to none of them.

`/staff/`, not `/admin/` (Django's) and not a fresh namespace: four of the five existing staff tools
already live under `/staff/`, `static/robots.txt` already blocks it, and `test_staff_design_strip.py`
asserts those four still answer 200 at their current paths. Choosing this prefix means the hub
appears without moving anything and without editing a single pinned test.

THE LANDING IS NOT A LINK FARM. It leads with the numbers that mean work, because the question an
admin arrives with is "is there anything for me", and only then offers the doors.
"""
import logging

from django.db.models import Q
from django.shortcuts import get_object_or_404
from django.urls import reverse_lazy
from django.views.generic import TemplateView, View

from core.models import AdminAction
from fundraiser.models import DonationBadgeClaim
from users.models import CustomUser
from trophies.mixins import PostActionMixin, StaffRequiredMixin
from trophies.models import ModerationAction, UserConceptRating
from trophies.services import moderation_service
from trophies.util_modules.cache import redis_client
from trophies.views.admin_views import WORKER_QUEUES

logger = logging.getLogger(__name__)

#: How many entries the landing's activity rail shows, per log and after merging.
RECENT_LIMIT = 12

#: How much of one person's history the person page shows, per list. Bounded because a prolific
#: reporter can accrue hundreds of entries and this page is a judgement aid, not an archive -- the
#: full record is the decision log, which pages.
HISTORY_LIMIT = 25


def _worker_backlog():
    """The deepest worker queue right now, or None if Redis cannot be reached.

    None rather than 0: an unreachable Redis and an empty queue are opposite facts, and rendering
    both as "0 waiting" tells an admin the workers are idle when they may be unreachable. The
    template says so in words.

    Reads `WORKER_QUEUES` from the monitoring view rather than listing the queues again -- which
    queues exist is one fact, and a hub quietly watching four of five is worse than not watching.
    """
    try:
        depths = {name: redis_client.llen(name) for name in WORKER_QUEUES}
    except Exception:
        logger.debug('Admin hub: could not read worker queue depths', exc_info=True)
        return None
    if not depths:
        return None
    deepest = max(depths, key=depths.get)
    return {'queue': deepest, 'depth': depths[deepest], 'total': sum(depths.values())}


def recent_activity(limit=RECENT_LIMIT):
    """The two audit logs, interleaved by time, newest first.

    Both logs on one rail rather than two panels: "what has been happening here" is one question,
    and an admin who has to check two lists to answer it will check one.

    Two BOUNDED slices merged in Python, which is the shape this project allows (`[:N]`) rather than
    the one it forbids (iterating a profile-scoped queryset). A UNION view or a merged table would be
    a third representation of facts two tables already hold correctly.

    `prefetch_related('reversed_by_action')` on both because the rail badges reversed entries, and
    `is_reversed` is a query per row without it.
    """
    moderation = ModerationAction.objects.prefetch_related('reversed_by_action')[:limit]
    administrative = AdminAction.objects.prefetch_related('reversed_by_action')[:limit]
    merged = (
        [{'source': 'Moderation', 'entry': entry} for entry in moderation]
        + [{'source': 'Admin', 'entry': entry} for entry in administrative]
    )
    # `-id` breaks the tie for the same reason both models order that way: `created_at` is
    # auto_now_add, so a bulk write lands several rows on one timestamp.
    merged.sort(key=lambda row: (row['entry'].created_at, row['entry'].id), reverse=True)
    return merged[:limit]


class AdminHubView(StaffRequiredMixin, TemplateView):
    """The landing. Admins only -- `is_staff`, which the `CustomUser.save()` lockstep keeps false for
    a moderator, so the Mod Center's audience cannot reach this."""
    template_name = 'staff/admin_hub.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        # Read verbatim from the service the Mod Center reads, so the hub cannot claim a different
        # amount of work from the page it points at.
        queues = moderation_service.queue_counts()
        context['reports_waiting'] = sum(counts['open'] for counts in queues.values())

        # `claimed` is the status a claim lands in and sits at until somebody starts the artwork, so
        # it is the one that means "waiting for us".
        context['claims_waiting'] = DonationBadgeClaim.objects.filter(status='claimed').count()

        context['worker_backlog'] = _worker_backlog()
        context['recent'] = recent_activity()
        context['breadcrumb'] = [{'text': 'Home', 'url': reverse_lazy('home')}, {'text': 'Admin'}]
        context['page_name'] = 'Admin'
        # The shell's back-button row is for pages BELOW the hub. On the hub itself it would offer a
        # link to the page you are already on.
        context['is_hub'] = True
        context['seo_title'] = 'Admin - Platinum Pursuit'
        return context


#: One page of the decision log. It only grows, so it pages.
PER_PAGE = 25

#: The filters offered over the decision log. `reversible` leads nothing -- `all` does -- because
#: unlike a queue this page is a RECORD first and a workbench second: an admin arrives to read what
#: happened far more often than to undo it.
DECISION_FILTERS = ['all', 'reversible', 'reversed']


class DecisionLogView(StaffRequiredMixin, TemplateView):
    """Every moderation decision, with the power to undo one.

    Admin-only, and that is the whole difference from the Mod Center's own rail: moderators can see
    what they and their colleagues decided, admins can take it back. `reverse_action` has existed
    since the log was built and has been unreachable from any page until now.
    """
    template_name = 'staff/decisions.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        requested = self.request.GET.get('show', 'all')
        show = requested if requested in DECISION_FILTERS else 'all'

        entries = (ModerationAction.objects
                   # The rail renders `actor_label` and `subject_label`, both frozen at write time,
                   # so neither FK is touched. The prefetch is for `is_reversed`, which is a query
                   # per row without it.
                   .prefetch_related('reversed_by_action'))
        if show == 'reversed':
            entries = entries.filter(reversed_by_action__isnull=False)
        elif show == 'reversible':
            # What the SERVICE can undo, asked of the service rather than restated here -- otherwise
            # the page offers a button the service refuses, which is the worst of both.
            entries = entries.filter(action__in=list(moderation_service.UNDOABLE_ACTIONS),
                                     reversed_by_action__isnull=True, reverses__isnull=True)

        try:
            page = max(1, int(self.request.GET.get('page', 1)))
        except (TypeError, ValueError):
            page = 1
        start = (page - 1) * PER_PAGE
        # One past the page, so "is there more" needs no COUNT over a table that only grows.
        rows = list(entries[start:start + PER_PAGE + 1])

        context['rows'] = rows[:PER_PAGE]
        context['has_next'] = len(rows) > PER_PAGE
        context['page'] = page
        context['show'] = show
        context['show_filters'] = DECISION_FILTERS
        context['undoable'] = moderation_service.UNDOABLE_ACTIONS
        context['page_name'] = 'Decisions'
        context['breadcrumb'] = [{'text': 'Home', 'url': reverse_lazy('home')},
                                 {'text': 'Admin', 'url': reverse_lazy('admin_hub')},
                                 {'text': 'Decisions'}]
        context['seo_title'] = 'Decisions - Admin'
        return context


class ReverseDecisionView(StaffRequiredMixin, PostActionMixin, View):
    """Undo one moderation decision. POST-only, admin-only, reason required.

    The gate is the difference that matters: `StaffRequiredMixin`, not the Mod Center's
    `ModeratorRequiredMixin`. A moderator can decide; taking a colleague's decision back is an
    admin's call.
    """
    error_class = moderation_service.ModerationError
    success_message = 'Decision reversed.'

    def act(self, pk, user, reason):
        moderation_service.reverse_action(
            get_object_or_404(ModerationAction, pk=pk), user, reason)

    def default_redirect(self):
        return reverse_lazy('admin_decisions')


# ── people ───────────────────────────────────────────────────────────────────────────────────────

#: How many search results to show. A staff lookup is a targeted question ("this hunter"), not a
#: browse, so a short list that arrives instantly beats a long one behind a paginator.
PEOPLE_LIMIT = 20


class PeopleSearchView(StaffRequiredMixin, TemplateView):
    """Find one hunter by PSN handle or email address.

    Deliberately search-ONLY: there is no "all users" listing, and an empty query shows nothing
    rather than everybody. A staff tool that renders a scrollable list of every account invites
    browsing through people, and the reason to open this page is always a specific person.
    """
    template_name = 'staff/people.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        query = (self.request.GET.get('q') or '').strip()

        results = []
        if query:
            # `select_related('profile')`: every row renders `display_name`, which reaches through
            # to the profile. Without it this is a query per result.
            matches = (CustomUser.objects
                       .select_related('profile')
                       .filter(Q(profile__psn_username__icontains=query)
                               | Q(profile__display_psn_username__icontains=query)
                               | Q(email__icontains=query))
                       .order_by('profile__psn_username', 'email')[:PEOPLE_LIMIT + 1])
            found = list(matches)
            context['more_than_shown'] = len(found) > PEOPLE_LIMIT
            results = found[:PEOPLE_LIMIT]

        context['results'] = results
        context['query'] = query
        context['page_name'] = 'People'
        context['breadcrumb'] = [{'text': 'Home', 'url': reverse_lazy('home')},
                                 {'text': 'Admin', 'url': reverse_lazy('admin_hub')},
                                 {'text': 'People'}]
        context['seo_title'] = 'People - Admin'
        return context


class PersonView(StaffRequiredMixin, TemplateView):
    """Everything this site has decided about one hunter, on one page.

    The question an admin actually arrives with is "what is the story with this person", and before
    `subject_user` existed it could not be asked: the log pointed at ratings and games, and reached a
    person only through a report FK that goes null the moment the report is purged.

    Both logs, because an account can be on the receiving end of a moderation decision and an admin
    action, and having to check two pages to judge a repeat offender is how the second one gets
    skipped.
    """
    template_name = 'staff/person.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        person = get_object_or_404(
            CustomUser.objects.select_related('profile'), pk=self.kwargs['user_id'])

        context['person'] = person
        context['profile'] = getattr(person, 'profile', None)

        # Bounded slices, newest first. Both logs order that way already.
        context['decisions'] = list(
            ModerationAction.objects.filter(subject_user=person)
            .prefetch_related('reversed_by_action')[:HISTORY_LIMIT])
        context['admin_actions'] = list(
            AdminAction.objects.filter(subject_user=person)
            .prefetch_related('reversed_by_action')[:HISTORY_LIMIT])

        # Their own words, which is the other half of judging a pattern: the log says what was done
        # about them, this says what they wrote. Hidden ones included and marked -- the point of the
        # page is the pattern, and omitting the hidden ones would hide exactly the evidence.
        profile = context['profile']
        context['takes'] = list(
            UserConceptRating.objects
            .filter(profile=profile).exclude(blurb='')
            .select_related('concept')
            .order_by('-id')[:HISTORY_LIMIT]) if profile else []

        context['page_name'] = person.display_name
        context['breadcrumb'] = [{'text': 'Home', 'url': reverse_lazy('home')},
                                 {'text': 'Admin', 'url': reverse_lazy('admin_hub')},
                                 {'text': 'People', 'url': reverse_lazy('admin_people')},
                                 {'text': person.display_name}]
        context['seo_title'] = f'{person.display_name} - Admin'
        return context


class HideTakeView(StaffRequiredMixin, PostActionMixin, View):
    """Hide a quick take nobody reported.

    Admin-only, unlike the queue's own Hide. The reactive queue only ever sees what a hunter
    objected to; acting without a report means acting on your own judgement, with nobody having
    raised it -- so it carries the same reason requirement and lands in the same log, under its own
    action name.
    """
    error_class = moderation_service.ModerationError
    success_message = 'Quick take hidden.'

    def act(self, pk, user, reason):
        moderation_service.hide_blurb_without_a_report(
            get_object_or_404(UserConceptRating, pk=pk), user, reason)

    def default_redirect(self):
        return reverse_lazy('admin_people')

