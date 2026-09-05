"""The Mod Center: the two report queues, and the actions on them.

Views are deliberately thin. Every decision goes through `moderation_service`, which applies the
change and writes the audit entry in one transaction and refuses an action whose target somebody has
already handled -- so a view cannot mutate anything without a log entry, and a stale queue page
cannot produce a second, false record.

Gated by `ModeratorRequiredMixin` (moderators AND admins). The gate REDIRECTS rather than 403s, so a
hunter who guesses a URL gets the home page rather than confirmation that something is here.
"""
from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect
from django.utils.http import url_has_allowed_host_and_scheme
from django.urls import reverse_lazy
from django.views.generic import TemplateView, View

from trophies.mixins import ModeratorRequiredMixin
from trophies.models import BlurbReport, GameFlag, ModerationAction
from trophies.services import moderation_service
from trophies.services.game_flag_service import GameFlagService

#: One page of a queue. Reports accumulate forever, so every queue view pages -- a moderator who
#: comes back after a quiet month should not be handed six hundred rows in one response.
PER_PAGE = 25

#: The status filter offered on both queues. 'pending' leads because it is the only one that is work.
STATUS_FILTERS = ['pending', 'actioned', 'dismissed', 'all']


def queue_summaries():
    """Every queue, named and linked, with how much is waiting in it. ONE definition, read by the
    landing and by each queue's own header.

    The COUNTS come from `moderation_service.queue_counts()` rather than being aggregated here: the
    navbar's attention marker reads the same function, and a marker that counts differently from the
    page it points at is worse than no marker at all. This function owns only the presentation --
    what each queue is called and where it lives.
    """
    counts = moderation_service.queue_counts()
    return [
        # The spread goes FIRST so the presentation keys below it always win. Harmless today (the
        # counts carry only `open`/`total`), but a future count key named `name` or `url` would
        # otherwise silently replace the queue's own.
        {**counts['quick-takes'],
         'slug': 'quick-takes', 'name': 'Quick Takes',
         'url': reverse_lazy('mod_quick_takes'),
         'blurb': 'Reported quick takes, the only free text a hunter can write on the site.'},
        {**counts['game-flags'],
         'slug': 'game-flags', 'name': 'Game Flags',
         'url': reverse_lazy('mod_game_flags'),
         'blurb': 'Reported problems with a game: delisted, unobtainable, shovelware, buggy.'},
    ]


def _breadcrumb(*tail):
    items = [{'text': 'Home', 'url': reverse_lazy('home')},
             {'text': 'Mod Center', 'url': reverse_lazy('mod_center')}]
    items.extend(tail)
    items[-1].pop('url', None)      # the last crumb is where you are
    return items


class ModCenterView(ModeratorRequiredMixin, TemplateView):
    """The landing: what is waiting, and what has just been decided."""
    template_name = 'moderation/mod_center.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['queues'] = queue_summaries()
        context['open_total'] = sum(q['open'] for q in context['queues'])
        # `select_related('actor')` because the list renders the actor on every row, and
        # `actor_label` is only the fallback for a deleted account.
        context['recent'] = (ModerationAction.objects.select_related('actor')
                             .prefetch_related('reversed_by_action')[:12])
        context['breadcrumb'] = _breadcrumb({'text': 'Mod Center'})
        context['seo_title'] = 'Mod Center - Platinum Pursuit'
        return context


class _QueueView(ModeratorRequiredMixin, TemplateView):
    """Shared shape for both queues: filter by status, page, render.

    One base because the two queues differ only in their model, their filter vocabulary and what a
    row shows. Two hand-written copies would drift on the parts that matter least and are most
    tedious to keep in step -- paging, the status filter, the empty state.
    """
    status_map = {}          # our filter value -> the model's status values
    queue_name = ''
    queue_slug = ''

    def get_queryset(self):
        raise NotImplementedError

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        requested = self.request.GET.get('status', 'pending')
        status = requested if requested in STATUS_FILTERS else 'pending'

        qs = self.get_queryset()
        if status != 'all':
            qs = qs.filter(status__in=self.status_map[status])

        try:
            page = max(1, int(self.request.GET.get('page', 1)))
        except (TypeError, ValueError):
            page = 1
        start = (page - 1) * PER_PAGE
        # One row past the page, so "is there a next page" needs no COUNT over a table that only
        # grows. Same probe the browse walls use.
        rows = list(qs[start:start + PER_PAGE + 1])
        context['rows'] = rows[:PER_PAGE]
        context['has_next'] = len(rows) > PER_PAGE
        context['page'] = page
        context['status'] = status
        context['status_filters'] = STATUS_FILTERS
        context['queue_name'] = self.queue_name
        context['queue_slug'] = self.queue_slug
        context['reasons'] = getattr(self, 'reasons', [])
        # The OTHER queues, with their open counts. A breadcrumb is the smallest target on the page
        # and the only way back, and hopping between two queues went via the landing -- two clicks
        # for a move a moderator makes constantly. The count comes along because "the other queue has
        # three waiting" is the reason to make that move.
        context['other_queues'] = [q for q in queue_summaries() if q['slug'] != self.queue_slug]
        context['breadcrumb'] = _breadcrumb({'text': self.queue_name})
        context['seo_title'] = f'{self.queue_name} - Mod Center'
        return context


class QuickTakeQueueView(_QueueView):
    template_name = 'moderation/quick_takes.html'
    queue_name = 'Quick Takes'
    queue_slug = 'quick-takes'
    status_map = {'pending': ['pending'], 'actioned': ['action_taken'],
                  'dismissed': ['dismissed', 'reviewed']}

    def get_queryset(self):
        # Everything a row renders, joined once. Without these the queue walks
        # report -> rating -> concept and report -> reporter per row, which is four queries a row
        # on a page of 25 -- the N+1 shape this project has a documented history with.
        return (BlurbReport.objects
                .select_related('rating__concept', 'rating__profile', 'reporter', 'reviewed_by')
                # The row links to the concept's game. Prefetched, so `.games.all|first` in the
                # template is served from cache instead of a query per row.
                .prefetch_related('rating__concept__games')
                .order_by('status', '-created_at'))


class GameFlagQueueView(_QueueView):
    template_name = 'moderation/game_flags.html'
    queue_name = 'Game Flags'
    queue_slug = 'game-flags'
    status_map = {'pending': ['pending'], 'actioned': ['approved'], 'dismissed': ['dismissed']}

    def get_queryset(self):
        return (GameFlag.objects
                .select_related('game__concept', 'reporter', 'reviewed_by')
                .order_by('status', '-created_at'))

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        # The row says what approving will DO, so it has to know which types write nothing and which
        # set the shovelware lock. Read from the service that writes them, never named here: the
        # template hand-listed two of the three no-ops and told a moderator an `other` flag would
        # change the game.
        context['no_op_flag_types'] = GameFlagService.NO_OP_FLAG_TYPES
        context['shovelware_flag_types'] = GameFlagService.SHOVELWARE_FLAG_TYPES
        return context


class _ActionView(ModeratorRequiredMixin, View):
    """POST-only. A moderation decision is never a GET: it mutates live data, and a GET would be
    followed by a crawler, a prefetcher, or a bookmark."""

    def post(self, request, pk):
        reason = (request.POST.get('reason') or '').strip()
        try:
            self.act(pk, request.user, reason)
        except moderation_service.ModerationError as exc:
            # The service's messages are written to be read by a moderator -- "already handled by
            # someone else", not a traceback -- so they are shown rather than swallowed.
            messages.error(request, str(exc))
        else:
            messages.success(request, self.success_message)
        return redirect(self._safe_next(request))

    def _safe_next(self, request):
        """Where to send the moderator back to, refusing anything that is not our own path.

        `next` arrives in the POST body so the mod lands back on the list they were reading rather
        than the top of `pending`. Unvalidated that is an open redirect -- a crafted form could
        bounce a signed-in moderator to another origin. `url_has_allowed_host_and_scheme` is
        Django's own check and is what `LoginView` uses for exactly this.
        """
        candidate = request.POST.get('next') or ''
        # Must look like a path. `url_has_allowed_host_and_scheme` accepts a bare querystring as
        # "relative", but `redirect()` treats a string with no slash as a VIEW NAME and raises
        # NoReverseMatch -- so the leading slash is both a correctness check and the safety one.
        if not candidate.startswith('/'):
            return self.default_redirect()
        if url_has_allowed_host_and_scheme(
                candidate, allowed_hosts={request.get_host()}, require_https=request.is_secure()):
            return candidate
        return self.default_redirect()

    def default_redirect(self):
        return reverse_lazy('mod_center')


class HideBlurbView(_ActionView):
    success_message = 'Quick take hidden.'

    def act(self, pk, user, reason):
        moderation_service.hide_blurb(get_object_or_404(BlurbReport, pk=pk), user, reason)

    def default_redirect(self):
        return reverse_lazy('mod_quick_takes')


class DismissBlurbReportView(_ActionView):
    success_message = 'Report dismissed.'

    def act(self, pk, user, reason):
        moderation_service.dismiss_blurb_report(get_object_or_404(BlurbReport, pk=pk), user, reason)

    def default_redirect(self):
        return reverse_lazy('mod_quick_takes')


class ApproveGameFlagView(_ActionView):
    success_message = 'Flag approved and applied.'

    def act(self, pk, user, reason):
        moderation_service.approve_game_flag(get_object_or_404(GameFlag, pk=pk), user, reason)

    def default_redirect(self):
        return reverse_lazy('mod_game_flags')


class DismissGameFlagView(_ActionView):
    success_message = 'Flag dismissed.'

    def act(self, pk, user, reason):
        moderation_service.dismiss_game_flag(get_object_or_404(GameFlag, pk=pk), user, reason)

    def default_redirect(self):
        return reverse_lazy('mod_game_flags')
