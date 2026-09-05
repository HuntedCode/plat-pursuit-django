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

from django.urls import reverse_lazy
from django.views.generic import TemplateView

from core.models import AdminAction
from fundraiser.models import DonationBadgeClaim
from trophies.mixins import StaffRequiredMixin
from trophies.models import ModerationAction
from trophies.services import moderation_service
from trophies.util_modules.cache import redis_client
from trophies.views.admin_views import WORKER_QUEUES

logger = logging.getLogger(__name__)

#: How many entries the landing's activity rail shows, per log and after merging.
RECENT_LIMIT = 12


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
