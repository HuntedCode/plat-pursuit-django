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
from django.views.generic import DetailView, TemplateView
from django_ratelimit.decorators import ratelimit

from challenges.models import CHALLENGE_TYPE_CHOICES, Challenge
from challenges.services import challenge_service as svc
from challenges.services import slot_render
from core.previews import previewing

#: Shared by both write doors, so create-hide-create cannot outrun one door by using the other. The cap
#: on runs is structural (one active per type) rather than numeric, but the RATE is not bounded by it:
#: hiding frees nothing and starting resumes, yet each still writes and each still takes a row lock.
CHALLENGE_WRITE_RATELIMIT_GROUP = 'challenges:write'

#: The team preview door for what a FREE hunter sees during the beta: `?preview=challenges-free`.
#:
#: Staff or moderator only, and it writes nothing -- `core.previews` enforces both, and that is the
#: whole reason to use it rather than the obvious alternative of flipping your own `user_is_premium`
#: in a shell. That mutates a real row, changes every other page you visit, and is easy to forget to
#: put back. Same slug shape as `gamelists`' `lists-free`, which previews the same thing one feature
#: over.
#:
#: IT OPENS THE WHOLE THING, which `core.previews`' own docstring insists on: the door is read by the
#: page AND by the write endpoint, so pressing the button you are previewing gives you the refusal you
#: are previewing. A preview that dimmed the button and then let the POST through would be showing you
#: something no free hunter can reach.
#:
#: Cheap by construction, per the premium-preview rule: it flips one boolean that HIDES an affordance.
#: No provider runs, no extra query, nothing reads the viewer's data differently.
PREVIEW_FREE = 'challenges-free'


def creation_is_open(request, profile):
    """Can this hunter start a run, as THIS REQUEST should be shown it?

    `challenge_service.creation_is_open_to` is the real gate and stays the single enforcement point;
    this is that answer plus the team preview door, which can only ever make it stricter.
    """
    if previewing(request, PREVIEW_FREE):
        return False
    return svc.creation_is_open_to(profile)


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
        context['creation_is_open'] = creation_is_open(self.request, profile)
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


class ChallengeDetailView(DetailView):
    """One run's squares. PUBLIC to read, and the owner gets the affordances.

    Public because the Hall of Fame is the point of the feature: a finished run has to be something you
    can show somebody, and a link that only works for its owner is not that. Hidden runs are the
    exception and `readable_by` handles it -- a hunter who took a run out of view gets a 404 for everybody
    else, including a crawler, and still reaches it themselves.

    NO `is_public` FIELD, deliberately, so there is nothing here to get wrong: visibility is one flag
    with one meaning, and `ChallengeQuerySet` is the only place that knows it.

    FLAT IN QUERIES, and that is the property to protect rather than a claim to make. A run is 26 squares
    and every filled one wants cover art, which is the exact shape CLAUDE.md's `raw_response` rule exists
    for -- so the covers come from `slot_render.slot_cards`, batched, and nothing here resolves one per
    square.
    """

    template_name = 'challenges/challenge_detail.html'
    pk_url_kwarg = 'challenge_id'

    def _viewer(self):
        """The reading profile, or None.

        Safe for both an `AnonymousUser` and an authenticated account with no `Profile` row, because
        `RelatedObjectDoesNotExist` subclasses `AttributeError`. Spelled once: `get_queryset` and
        `get_context_data` both want it, and the second access is free (the reverse one-to-one caches).
        """
        return getattr(self.request.user, 'profile', None)

    def get_queryset(self):
        """Delegated to `readable_by`, which is where the flag lives.

        A hidden run is readable by its owner and nobody else. `select_related('profile')` because the
        byline wants it, and without it the page pays a query to learn whose run it is looking at.
        """
        return Challenge.objects.readable_by(self._viewer()).select_related('profile')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        challenge = self.object
        viewer = self._viewer()

        context['cards'] = slot_render.slot_cards(challenge)
        context['is_owner'] = viewer is not None and viewer.id == challenge.profile_id
        # WHETHER THE OWNER MAY CHANGE THIS RUN. Two conditions beyond ownership, and a run fails either:
        # a FINISHED run has nothing left to change (completed squares lock, and every square is
        # complete), and a HIDDEN run is read-only because `start` is what brings one back
        # (`challenge_service.start`'s resume branch) -- editing one in place would mean filling squares
        # on a run that is on nobody's page and in no hub.
        #
        # NOTHING RENDERS THIS YET, and that is worth admitting rather than dressing up. An earlier
        # comment here claimed it was "asked once rather than three times in the template"; the template
        # asks zero times, because its two read-only notes name the specific reason (finished / hidden)
        # instead of the general one, which is better copy. So this is an invariant with a test and no
        # consumer until 4b's picker gates on it.
        #
        # It is kept rather than deleted for one reason: `not is_deleted` was MISSING from the first
        # version, and the bug was only visible because it made a template branch unreachable. The picker
        # would have had to re-derive the same predicate, with nothing to notice if it missed the same
        # condition again. The tests are the point; the context key is how they reach it.
        context['can_edit'] = (context['is_owner']
                               and not challenge.is_complete
                               and not challenge.is_deleted)
        context['progress'] = (
            round(challenge.completed_count / challenge.total_slots * 100)
            if challenge.total_slots else 0
        )
        # THE PUBLIC TRAIL, not the owner's. This route lives under `/community/challenges/` precisely
        # because the page is somebody's artefact rather than their working surface, and the trail has to
        # agree with that: `My Pursuit` and `My Challenges` are both login-gated, so an anonymous reader
        # following them got bounced to a login screen from a page that never asked them to sign in, and
        # a signed-in visitor was sent to THEIR OWN runs from a page about somebody else's.
        #
        # `gamelists` settled this one app over: the public list detail trails Home / Game Lists / name,
        # never My Lists.
        context['breadcrumb'] = [
            {'text': 'Home', 'url': reverse_lazy('home')},
            {'text': 'Challenges', 'url': reverse_lazy('challenges')},
            {'text': challenge.name},
        ]
        return context


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
    `gamelists.CreateListView` writes down -- though unlike that one this returns to My Challenges
    rather than to the thing it just made, because the card it returns to is what changed: its verb
    moves from Start to Continue, and the message says which branch fired.

    Three outcomes, and they deliberately do NOT look identical -- `start_reporting` reports which
    branch fired and the messages below differ. An earlier docstring said the opposite, describing the
    `svc.start` call this no longer makes.

    `ALREADY_ACTIVE` IS THE EDGE CASE AGAIN, and it is worth recording that it briefly was not. While
    the run had no page of its own, the card's primary button on an active run (labelled Continue) posted
    here, redirected back and showed nothing -- the page's deadest button, and this outcome was how it
    got there. Continue is now a link to `challenge_detail`, so the only ways to reach this branch are a
    double-submit and a stale tab, which is what the comment below treats it as.
    """

    @method_decorator(ratelimit(group=CHALLENGE_WRITE_RATELIMIT_GROUP, key='user', rate='30/m',
                                method='POST', block=True))
    def post(self, request, challenge_type):
        # THE PREVIEW REFUSES TOO, so the door opens the whole behaviour rather than half of it. Only
        # for a genuinely new run: previewing must not take away a run you already have, which is the
        # same line the beta gate itself draws.
        if previewing(request, PREVIEW_FREE) and svc.active_run(
                request.user.profile, challenge_type) is None:
            messages.error(request, 'Starting a challenge is in beta for members first. '
                                    'Everyone can start one when the beta ends.')
            return self._back(request)

        try:
            challenge, outcome = svc.start_reporting(request.user.profile, challenge_type)
        except svc.ChallengeError as exc:
            messages.error(request, str(exc))
            return self._back(request)

        # THE OUTCOME, not `filled_count`. That could not tell a fresh run from an empty resumed one,
        # and said "back where you left it" about a run that had never gone away -- the one case where
        # it was actively wrong rather than merely silent.
        if outcome == svc.RESUMED:
            messages.success(request, f'{challenge.name} is back where you left it.')
        elif outcome == svc.CREATED:
            messages.success(request, f'{challenge.name} is ready. Pick your first game.')
        # ALREADY_ACTIVE says nothing. For a double submit or a stale tab that is right; for a Continue
        # press it is merely all there is to say until the run has a page to go to.
        return self._back(request)

    @staticmethod
    def _back(request):
        """Back to the page, KEEPING the preview querystring if there is one.

        A bare `redirect('my_challenges')` drops it, so previewing the refusal would land you on the
        un-previewed page: the error message from the gated state, above live buttons. Confusing in
        exactly the way a half-open door is.
        """
        url = reverse_lazy('my_challenges')
        if previewing(request, PREVIEW_FREE):
            return redirect(f'{url}?preview={PREVIEW_FREE}')
        return redirect(url)


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
