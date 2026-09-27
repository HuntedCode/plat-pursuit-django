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

    Query cost is flat and small by construction: two reads per type for the card state, one bounded
    queryset for the history. Nothing here scales with a hunter's library, so a whale's page costs what
    anybody's does -- worth stating because the rest of this feature reads trophy data and this page
    deliberately does not.
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
        context['finished'] = list(
            Challenge.objects.completed().filter(profile=profile)[:self.HISTORY_LIMIT]
        )
        context['creation_is_open'] = svc.creation_is_open_to(profile)
        context['breadcrumb'] = [
            {'label': 'My Pursuit', 'url': '/career/'},
            {'label': 'My Challenges'},
        ]
        return context

    #: Enough to see a career's worth without paging. Sequential runs mean a hunter accumulates these
    #: slowly, so a bound this high is a guard rather than a feature -- if anybody reaches it, the
    #: history wants its own page rather than a bigger number here.
    HISTORY_LIMIT = 24

    def _card(self, profile, challenge_type, label):
        """One type's state, resolved through the service.

        `active` is asked first and `resumable` only when there is no active run, because the two are
        mutually exclusive by the one-active-per-type constraint and asking anyway would be a query
        spent to learn something the first answer already implied.
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
            'completed_run_count': svc.completed_run_count(profile, challenge_type),
        }


class _ChallengeActionView(LoginRequiredMixin, _LinkedProfileRequired, View):
    """POST-only, answers JSON, and translates a `ChallengeError` into a status code.

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

    A navigation rather than an in-place update -- you land on the run -- and a plain form is the
    sturdier version of that: it works with no JavaScript, and the page needs no client state to offer
    it. The same reasoning `gamelists.CreateListView` writes down.

    Both outcomes look identical from here, which is the point: `svc.start` decides whether this is a
    fresh run, a resume, or a no-op on one already going, and the caller does not need to know.
    """

    @method_decorator(ratelimit(group=CHALLENGE_WRITE_RATELIMIT_GROUP, key='user', rate='30/m',
                                method='POST', block=True))
    def post(self, request, challenge_type):
        try:
            challenge = svc.start(request.user.profile, challenge_type)
        except svc.ChallengeError as exc:
            messages.error(request, str(exc))
            return redirect('my_challenges')

        if challenge.filled_count:
            messages.success(request, f'{challenge.name} is back where you left it.')
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
