"""My Challenges: the hunter's own two runs, and the three writes that act on them.

ONE PAGE, NOT A HUB. `/my-challenges/` is personal and login-gated, so it sits in My Pursuit -> Tools
beside My Lists; the public browse and the Hall of Fame are a separate surface (the Community rail's
`challenges` slot, still a placeholder). Same split `gamelists` draws, and for the same reason: what
belongs to the community is the browse, not your own working page.

TWO CARDS, ALWAYS BOTH. The page renders one card per challenge type whatever state you are in, rather
than a list of what you happen to own. A hunter with no runs should see what the feature IS, and a
hunter with one active run should still see the other type existing -- a list would show them nothing
about Job Coverage until they started it.

THE CARD'S VERB IS THE WHOLE DESIGN, and it comes from the hide-and-resume rule. Three states:

  no run          -> "Start"   (or, during the beta for a free hunter, a disabled Start that explains)
  active run      -> "Continue", with its progress
  hidden run      -> "Resume",  with its progress

Without the third, a hunter who hid a run with twelve squares filled sees "Start" and pressing it
hands that run back -- correct, and a surprise. `challenge_service.resumable_run` is what the card asks;
the page never spells the hidden-run predicate itself, because the write already owns it.

EVERY RULE LIVES IN THE SERVICE. These views translate a refusal into a message or a status code and do
nothing else -- no gate is re-implemented here, including the beta gate, which the page ASKS about
(`creation_is_open_to`) so it can render a disabled button rather than discovering the refusal after a
POST.
"""
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import JsonResponse
from django.shortcuts import redirect
from django.urls import reverse_lazy
from django.utils.decorators import method_decorator
from django.views import View
from django.views.generic import TemplateView
from django_ratelimit.decorators import ratelimit

from challenges.models import CHALLENGE_TYPE_CHOICES, Challenge
from challenges.services import challenge_service as svc

#: Shared by both write doors, so create-hide-create cannot outrun one door by using the other. The cap
#: on runs is structural (one active per type) rather than numeric, but the RATE is not bounded by it:
#: hiding frees nothing and starting resumes, yet each still writes and each still takes a row lock.
CHALLENGE_WRITE_RATELIMIT_GROUP = 'challenges:write'


class _LinkedProfileRequired:
    """A challenge belongs to a PROFILE, so a signed-in account without one has nothing to show.

    Same shape and same redirect as `gamelists._LinkedProfileRequired` and `CareerView`, which are the
    other pages whose entire content hangs off a linked profile. Without it `request.user.profile` raises
    `RelatedObjectDoesNotExist` and the page 500s.
    """

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            profile = getattr(request.user, 'profile', None)
            if not profile or not profile.is_linked:
                messages.info(request, 'Link your PSN account to start a challenge.')
                return redirect('link_psn')
        return super().dispatch(request, *args, **kwargs)


class MyChallengesView(LoginRequiredMixin, _LinkedProfileRequired, TemplateView):
    """The two cards, plus this hunter's finished runs.

    Query cost is flat and small by construction: two or three reads per type for the card state (the
    third only when there is no active run to short-circuit it), plus a count and a bounded slice for
    the history. Nothing here reads `ProfileGame`, `EarnedTrophy`, `EarnedContract` or a single slot
    row, which the rest of this feature does -- so a whale's page costs what anybody's does. (It does
    touch `Profile`, for `user_is_premium`, at no extra query.)
    """

    template_name = 'challenges/my_challenges.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        profile = self.request.user.profile

        context['cards'] = [self._card(profile, key, label)
                            for key, label in CHALLENGE_TYPE_CHOICES]
        # `completed()` carries its own ordering (newest finish first) and is built on `visible()`, so a
        # run hidden after finishing correctly drops out of the hunter's own history too -- hiding means
        # "off my profile", and this page is the profile's.
        finished = Challenge.objects.completed().filter(profile=profile)
        # The TALLY counts them all; the LIST is sliced. Rendering `finished|length` for both made a
        # hunter with 30 finished runs read "24 finished".
        #
        # `my_lists` is NOT the precedent an earlier comment claimed: it PAGINATES, and its `list_count`
        # exists to render the cap ("3/25") rather than to reconcile a slice. The difference is real and
        # unresolved here -- that page gives a reader pages to reach the rest, and this one advertises a
        # total above a hard slice with no way further. `HISTORY_LIMIT` notes that the history wants its
        # own page if anybody reaches it; until then the template says when it is truncated.
        context['finished_total'] = finished.count()
        context['finished'] = list(finished[:self.HISTORY_LIMIT])
        context['creation_is_open'] = svc.creation_is_open_to(profile)
        # `text`, NOT `label`: `partials/breadcrumb.html` and `seo_tags` both read `text`, so a `label`
        # key rendered two EMPTY crumbs and two blank names in the JSON-LD. Every other caller on the
        # site passes `text` and starts at Home; this did neither.
        context['breadcrumb'] = [
            {'text': 'Home', 'url': reverse_lazy('home')},
            {'text': 'My Pursuit', 'url': reverse_lazy('career')},
            {'text': 'My Challenges'},
        ]
        return context

    #: Enough to see a career's worth without paging. Sequential runs mean a hunter accumulates these
    #: slowly, so a bound this high is a guard rather than a feature -- if anybody reaches it, the
    #: history wants its own page rather than a bigger number here.
    HISTORY_LIMIT = 24

    def _card(self, profile, challenge_type, label):
        """One type's state, resolved through the service.

        `active` is asked first and `resumable` only when there is none, which is a DISPLAY choice
        rather than something the schema guarantees: `challenge_one_active_per_type` is partial on the
        unfinished-and-visible predicate, so a visible active run and a hidden unfinished one of the
        same type can coexist. When they do, the active one is what a hunter means.
        """
        run = svc.active_run(profile, challenge_type)
        state = 'active'
        if run is None:
            run = svc.resumable_run(profile, challenge_type)
            state = 'resumable' if run is not None else 'empty'
        # Both derived HERE rather than in the template, because Django's `add` filter chains cannot do
        # arithmetic honestly -- the first version of this card computed "planned" as a string of chained
        # `add`s that produced nonsense. A number the page shows is a number the view owes it.
        planned = (run.filled_count - run.completed_count) if run else 0
        progress = round(run.completed_count / run.total_slots * 100) if run else 0
        return {
            'type': challenge_type,
            'label': label,
            'state': state,
            'run': run,
            'planned': planned,
            'progress': progress,
            # The verb IS the state, resolved here rather than in the template so the three cases are
            # visible in one place and a fourth cannot be added by accident in markup.
            'verb': {'active': 'Continue', 'resumable': 'Resume', 'empty': 'Start'}[state],
            # Keyed `visible_...` to match what it holds. Named `completed_run_count` it invited back
            # the very confusion it was added to fix -- the two functions differ on hidden runs.
            'visible_completed_count': svc.visible_completed_count(profile, challenge_type),
        }


class _ChallengeActionView(LoginRequiredMixin, _LinkedProfileRequired, View):
    """POST-only. What the two subclasses share is the gate, not the contract.

    `HideChallengeView` resolves a run by id and answers JSON, using all four helpers below.
    `StartChallengeView` resolves a TYPE, answers a redirect, and uses none of them -- for it this base
    is just `LoginRequiredMixin + _LinkedProfileRequired + View`. Worth saying because an earlier
    docstring promised a JSON contract both shared, and because "scoped to the hunter's own runs"
    describes `get_challenge` rather than the class.

    `get_challenge` returns None rather than raising `Http404`, deliberately: this project installs a
    GET-only `handler404`, so an `Http404` raised from a POST comes back as a 405 listing GET/HEAD/OPTIONS
    instead of a 404. `gamelists._ListActionView` documents the same workaround for the same reason.

    Scoped to the hunter's OWN runs. A challenge is public to read, but every write here is on something
    you own, so resolving through `profile=` means an id alone can never confirm somebody else's run
    exists -- and it means `svc`'s ownership check is a second line of defence rather than the only one.
    """

    def _profile(self, request):
        return getattr(request.user, 'profile', None)

    def get_challenge(self, request, challenge_id):
        return Challenge.objects.filter(
            pk=challenge_id, profile=self._profile(request)
        ).first()

    def not_found(self):
        return JsonResponse({'error': 'That challenge is not available.'}, status=404)

    def fail(self, exc, status=400):
        return JsonResponse({'error': str(exc)}, status=status)


class StartChallengeView(_ChallengeActionView):
    """Start a run, or bring back the one you hid. A form POST, not JSON.

    A navigation rather than an in-place update, and a plain form is the sturdier version of that: it
    works with no JavaScript, and the page needs no client state to offer it. The same reasoning
    `gamelists.CreateListView` writes down -- though unlike that one this returns to the SAME page,
    because the run's own page does not exist yet.

    Three outcomes, and they deliberately do NOT look identical -- `start_reporting` reports which
    branch fired and the messages below differ. An earlier docstring said the opposite, describing the
    `svc.start` call this no longer makes.

    NOTE WHAT `ALREADY_ACTIVE` ACTUALLY IS, because it is not only the double-submit case: it is the
    outcome of the card's PRIMARY button on an active run (the one labelled Continue). So Continue
    currently posts, redirects here, and shows nothing -- the page's deadest button. That is because the
    run's own page does not exist yet; when it does, Continue becomes a link to it and this outcome goes
    back to being the edge case the comment below calls it.
    """

    @method_decorator(ratelimit(group=CHALLENGE_WRITE_RATELIMIT_GROUP, key='user', rate='30/m',
                                method='POST', block=True))
    def post(self, request, challenge_type):
        try:
            challenge, outcome = svc.start_reporting(request.user.profile, challenge_type)
        except svc.ChallengeError as exc:
            messages.error(request, str(exc))
            return redirect('my_challenges')

        # THE OUTCOME, not `filled_count`. That could not tell a fresh run from an empty resumed one,
        # and said "back where you left it" about a run that had never gone away -- the one case where
        # it was actively wrong rather than merely silent.
        if outcome == svc.RESUMED:
            messages.success(request, f'{challenge.name} is back where you left it.')
        elif outcome == svc.CREATED:
            messages.success(request, f'{challenge.name} is ready. Pick your first game.')
        # ALREADY_ACTIVE says nothing. For a double submit or a stale tab that is right; for a Continue
        # press it is merely all there is to say until the run has a page to go to.
        return redirect('my_challenges')


class HideChallengeView(_ChallengeActionView):
    """Take a run off the profile and out of the hub. JSON, because the page updates in place.

    Not called Delete anywhere a hunter can see, and the client confirms first -- the run keeps every
    square it has and Start brings it back, which is exactly the thing somebody expecting a delete needs
    told BEFORE they act rather than after.
    """

    @method_decorator(ratelimit(group=CHALLENGE_WRITE_RATELIMIT_GROUP, key='user', rate='30/m',
                                method='POST', block=True))
    def post(self, request, challenge_id):
        challenge = self.get_challenge(request, challenge_id)
        if challenge is None:
            return self.not_found()
        try:
            svc.hide(challenge, request.user.profile)
        except svc.ChallengeError as exc:
            return self.fail(exc)
        return JsonResponse({'hidden': True})
