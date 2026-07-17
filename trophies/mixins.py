from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import JsonResponse
from django.shortcuts import redirect


class PremiumRequiredMixin(LoginRequiredMixin):
    """
    Mixin that requires the user to be a premium member.
    Redirects non-premium users to the beta access page.
    """
    def dispatch(self, request, *args, **kwargs):
        # First check if user is authenticated (handled by LoginRequiredMixin)
        if not request.user.is_authenticated:
            return self.handle_no_permission()

        # Check if user is premium
        if hasattr(request.user, 'profile') and request.user.profile.user_is_premium:
            return super().dispatch(request, *args, **kwargs)

        # Redirect non-premium users to beta access page
        return redirect('beta_access_required')


class StaffRequiredMixin(LoginRequiredMixin):
    """
    Mixin that requires the user to be a staff member.
    Unauthenticated users are redirected to the login page.
    Authenticated non-staff users are redirected to the home page.
    """
    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return self.handle_no_permission()

        if request.user.is_staff:
            return super().dispatch(request, *args, **kwargs)

        return redirect('home')


class LoginRequiredAPIMixin:
    """
    Mixin for non-DRF API views that require authentication.
    Returns JSON error responses instead of redirects.
    """
    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return JsonResponse({'error': 'Authentication required.'}, status=401)

        return super().dispatch(request, *args, **kwargs)


class StaffRequiredAPIMixin(LoginRequiredAPIMixin):
    """
    Mixin for non-DRF API views that require staff access.
    Returns JSON error responses instead of redirects.
    """
    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return JsonResponse({'error': 'Authentication required.'}, status=401)

        if not request.user.is_staff:
            return JsonResponse({'error': 'Staff access required.'}, status=403)

        return super().dispatch(request, *args, **kwargs)


class StaffOrRoadmapAuthorRequiredMixin(LoginRequiredMixin):
    """
    Mixin that grants access to staff OR roadmap authors (writer / editor /
    publisher). Trial-role users are rejected because the global
    `has_roadmap_role('writer')` check tops out below `writer` for them
    when no per-roadmap escalation target is supplied (this gate is global,
    not roadmap-scoped, so it never escalates).

    Used by surfaces that are primarily authoring tools but also need
    staff oversight access (e.g. the legacy-checklist viewer used to mine
    historical prose for new Roadmaps).
    """
    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return self.handle_no_permission()

        if request.user.is_staff:
            return super().dispatch(request, *args, **kwargs)

        profile = getattr(request.user, 'profile', None)
        if profile is not None and profile.has_roadmap_role('writer'):
            return super().dispatch(request, *args, **kwargs)

        return redirect('home')


class RoadmapAuthorRequiredMixin(LoginRequiredMixin):
    """
    Mixin that requires the user to have at least the writer roadmap role.
    Independent of Django staff status. Unauthenticated users hit login;
    authenticated users without a sufficient role redirect to home.

    Trial-writer support: a subclass can override
    `get_roadmap_for_permission()` to return the Roadmap the request
    targets. When the cheap global-role check fails, we call the hook
    and re-check with the roadmap so trial-role users assigned to it
    (via Roadmap.trial_writers) pass. Subclasses that don't override
    the hook get the legacy behavior (global check only).
    """
    min_roadmap_role = 'writer'

    def get_roadmap_for_permission(self):
        """Return the Roadmap this request targets, or None.

        Override on roadmap-scoped subclasses (e.g. the editor view)
        to enable per-roadmap trial-writer escalation. The hook is
        only invoked on the slow path — users who pass the global
        role check never trigger it.
        """
        return None

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return self.handle_no_permission()

        profile = getattr(request.user, 'profile', None)
        if not profile:
            return redirect('home')
        # Fast path: global role check covers writers / editors /
        # publishers without paying for a roadmap lookup.
        if profile.has_roadmap_role(self.min_roadmap_role):
            return super().dispatch(request, *args, **kwargs)
        # Slow path: maybe a trial-role user with a per-roadmap
        # assignment. Subclasses opt in by implementing the hook.
        roadmap = None
        try:
            roadmap = self.get_roadmap_for_permission()
        except Exception:
            # Hook failures (e.g. missing object, lookup errors)
            # fall through to the redirect so a bug in the hook
            # can't accidentally grant access.
            roadmap = None
        if (
            roadmap is not None
            and profile.has_roadmap_role(self.min_roadmap_role, roadmap)
        ):
            return super().dispatch(request, *args, **kwargs)

        return redirect('home')


class RecapSyncGateMixin:
    """
    Returns a gated recap_index response if the user has no linked profile or
    their profile hasn't finished syncing. Add to recap views before other mixins.
    """
    def _get_sync_gate_response(self, request):
        from django.shortcuts import render as _render
        from django.urls import reverse_lazy
        user_tz = request.user.user_timezone or 'UTC'
        # Same breadcrumb every recap-gated path renders, kept inline because
        # there are only the two callers below.
        breadcrumb = [
            {'text': 'Home', 'url': reverse_lazy('home')},
            {'text': 'Monthly Recap'},
        ]
        profile = getattr(request.user, 'profile', None)
        if not profile:
            return _render(request, 'recap/recap_index.html', {
                'sync_gate': 'no_profile',
                'user_timezone': user_tz,
                'breadcrumb': breadcrumb,
            })
        if profile.sync_status != 'synced':
            return _render(request, 'recap/recap_index.html', {
                'sync_gate': profile.sync_status,
                'profile': profile,
                'user_timezone': user_tz,
                'breadcrumb': breadcrumb,
            })
        return None


class HtmxListMixin:
    """Mixin for ListViews that returns a partial template on HTMX requests.

    Subclasses must set ``partial_template_name`` to the results-only partial
    (cards + pagination). On normal requests the full page template is rendered;
    on HTMX requests only the partial is returned, enabling snappy filter
    updates without a full page reload.
    """
    partial_template_name = None  # e.g. 'trophies/partials/game_list/browse_results.html'

    def get_template_names(self):
        # Return the rows-only partial for BOTH django-htmx filter swaps (HX-Request) and plain XHR page
        # fetches (X-Requested-With) -- the latter is how InfiniteScroller pulls the next ?page. Without the
        # XHR branch the scroller would receive the full page and never append. Harmless to the pager-based
        # grids (they only ever send HX-Request today).
        is_xhr = self.request.headers.get('X-Requested-With') == 'XMLHttpRequest'
        if (self.request.htmx or is_xhr) and self.partial_template_name:
            return [self.partial_template_name]
        return super().get_template_names()