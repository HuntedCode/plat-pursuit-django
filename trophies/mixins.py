from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.paginator import Page, Paginator
from django.http import Http404, JsonResponse
from django.shortcuts import redirect
from django.utils.cache import patch_vary_headers
from django.utils.http import url_has_allowed_host_and_scheme


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


def is_mod_or_admin(user):
    """The moderation gate, as ONE expression.

    `is_staff` rather than `role == 'admin'` on the admin half: `CustomUser.save()` keeps the two in
    lockstep, and `is_staff` additionally covers superusers, who have no role set at all and would
    otherwise be locked out of the tools they are most likely to be asked to fix.

    A function as well as a mixin because the same question is asked from templates and from a
    context processor, and three hand-written copies of `is_staff or is_moderator` is how one of them
    ends up subtly different from the others.
    """
    return bool(
        user
        and user.is_authenticated
        # `is_active` because this is called directly from templates and services too, not only
        # behind the auth backend. In a request the backend already turns a deactivated user into
        # AnonymousUser -- but a direct call with a stale user object would otherwise still say yes,
        # and revoking access is precisely the moment that must not happen.
        and user.is_active
        # Direct attribute, not getattr-with-default: `is_moderator` is a property on CustomUser
        # that cannot raise, so the default only ever masked a rename -- silently locking out every
        # moderator with no error anywhere. If the property goes, this should break loudly.
        and (user.is_staff or user.is_moderator)
    )


class ModeratorRequiredMixin(LoginRequiredMixin):
    """Mod Center access: moderators AND admins.

    Redirects rather than 403s, matching StaffRequiredMixin above -- a hunter who finds a mod URL
    gets the home page, not confirmation that something is there.
    """
    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return self.handle_no_permission()
        if is_mod_or_admin(request.user):
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


class _ScrollPage(Page):
    """Page for the countless scroll branch below: has_next comes from the +1-row probe, never
    from a count."""

    def __init__(self, object_list, number, paginator, has_next):
        super().__init__(object_list, number, paginator)
        self._has_next = has_next

    def has_next(self):
        return self._has_next


class HtmxListMixin:
    """Mixin for ListViews that returns a partial template on HTMX requests.

    Subclasses must set ``partial_template_name`` to the results-only partial
    (cards + pagination). On normal requests the full page template is rendered;
    on HTMX requests only the partial is returned, enabling snappy filter
    updates without a full page reload.

    Infinite-scroll fetches (plain XHR, page >= 2) additionally get COUNTLESS pagination: the
    InfiniteScroller appends cards and stops on the ``X-Has-Next`` header (or an empty page) --
    it never reads ``paginator.count`` -- yet Django's paginator ran a full ``COUNT(*)`` over
    the queryset on every fetch. On the browse pages whose querysets carry the page-identity
    WINDOW election (Games, tag detail), that count executed the whole election a SECOND time
    per scroll page, which is what made deep scrolling feel slow on the beta. The scroll branch
    instead slices ``page_size + 1`` rows: one query, has_next from the probe row, past-end
    still 404s (the scroller's stop contract). Filter swaps (HX-Request) and full pages keep
    the real count -- their headers render it.
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

    def _is_scroll_fetch(self):
        """A plain-XHR fetch of page >= 2: the InfiniteScroller pulling the next page."""
        if self.request.headers.get('X-Requested-With') != 'XMLHttpRequest':
            return False
        try:
            return int(self.request.GET.get('page', 1)) > 1
        except (TypeError, ValueError):
            return False

    def paginate_queryset(self, queryset, page_size):
        if not self._is_scroll_fetch():
            return super().paginate_queryset(queryset, page_size)
        page_number = int(self.request.GET.get('page'))
        offset = (page_number - 1) * page_size
        rows = list(queryset[offset:offset + page_size + 1])
        if not rows:
            # Past the end: the scroller's fallback stop signal (and the pre-existing contract
            # every suite pins). Matches what Django's paginator raises through ListView.
            raise Http404(f'Empty scroll page {page_number}')
        has_next = len(rows) > page_size
        rows = rows[:page_size]
        # A local paginator over just this page's rows: len(), never a query. Its fake `count`
        # reaches the partial's data-result-count attribute (unread on a scroll append -- the
        # scroller extracts cards + the X-Has-Next header alone) and any view that copies
        # paginator.count into context (company_list's total_company_count, browse_lists'
        # total_lists -- both rendered by full-page templates only). A partial that starts
        # RENDERING a count must not trust it on this branch. num_pages is stamped honest-enough
        # (this page's number, +1 when more exist) so a future pager partial's
        # next/previous_page_number calls don't raise EmptyPage against the 1-page default.
        paginator = Paginator(rows, page_size)
        paginator.num_pages = page_number + (1 if has_next else 0)
        page = _ScrollPage(rows, page_number, paginator, has_next)
        return paginator, page, rows, True

    def render_to_response(self, context, **response_kwargs):
        # `X-Has-Next` stops the scroller one fetch EARLIER than the empty-page fallback --
        # each saved fetch is a whole queryset execution on the windowed browse pages. Free on
        # both branches: the scroll page carries the probe answer, the counted page derives it
        # from the count it already paid for. XHR only; a filter swap's response is consumed by
        # htmx, which ignores it.
        response = super().render_to_response(context, **response_kwargs)
        # The same URL serves two bodies (full page vs partial) keyed on this header, so any
        # shared cache must partition on it -- the central hook is the right place to say so
        # once for every consumer.
        patch_vary_headers(response, ('X-Requested-With',))
        if self.request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            page_obj = context.get('page_obj')
            if page_obj is not None:
                response['X-Has-Next'] = '1' if page_obj.has_next() else '0'
        return response


class BackgroundContextMixin:
    """
    Mixin for views that display page-specific game image backgrounds.

    Provides a consistent way to build the image_urls context that base.html
    uses to display background images. Page-specific backgrounds (from this mixin)
    take priority over user's premium theme (from context processor).

    Usage:
        class MyView(BackgroundContextMixin, TemplateView):
            def get_context_data(self, **kwargs):
                context = super().get_context_data(**kwargs)
                context['image_urls'] = self.get_background_context(concept=my_concept)
                return context
    """

    def get_background_context(self, concept=None):
        """
        Build image_urls dict for template context.

        Args:
            concept: A Concept model instance

        Returns:
            dict: Contains 'bg_url' with the assembled landscape image (IGDB screenshots ->
            artworks -> PSN bg_url fallback), or empty dict when there's no landscape image.
        """
        landscape = concept.get_landscape_url() if concept else None
        if landscape:
            return {'bg_url': landscape}
        return {}


class PostActionMixin:
    """POST-only, act, tell the user, go back where they came from.

    Shared by the moderation actions and the admin ones. It carries NO GATE of its own on purpose --
    each family pairs it with theirs (`ModeratorRequiredMixin` for /mod/, `StaffRequiredMixin` for
    /staff/), and a mixin that silently supplied one would make the gate invisible at the point where
    it is chosen.

    Hoisted rather than copied because of `_safe_next`. `next` arrives in the POST body so somebody
    lands back on the list they were reading; unvalidated that is an OPEN REDIRECT, and an
    open-redirect guard is precisely the code that must not exist in two places where one can be
    fixed and the other forgotten.

    Subclasses provide `act(pk, user, reason)`, `success_message`, `error_class`, and optionally
    `default_redirect()`.
    """
    #: The exception this family raises for a refusable action. Its message is SHOWN to the user, so
    #: it must be one written to be read -- "already handled by somebody else", not a traceback.
    #:
    #: No default. It was `Exception`, which would have caught AttributeError, KeyError and every
    #: other programming mistake in a subclass that forgot to set it, and rendered the traceback text
    #: to an admin as though it were an explanation. Same treatment as `default_redirect`: a subclass
    #: that does not answer this does not work.
    error_class = None

    def dispatch(self, request, *args, **kwargs):
        if self.error_class is None:
            raise NotImplementedError(
                f'{type(self).__name__} must set `error_class` to the exception its service raises.')
        return super().dispatch(request, *args, **kwargs)

    def post(self, request, pk):
        reason = (request.POST.get('reason') or '').strip()
        try:
            self.act(pk, request.user, reason)
        except self.error_class as exc:
            messages.error(request, str(exc))
        else:
            messages.success(request, self.success_message)
        return redirect(self._safe_next(request))

    def _safe_next(self, request):
        """Where to send them back to, refusing anything that is not our own path.

        Unvalidated, `next` is an open redirect: a crafted form could bounce a signed-in moderator
        or admin to another origin. `url_has_allowed_host_and_scheme` is Django's own check and is
        what `LoginView` uses for exactly this.
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
        raise NotImplementedError
