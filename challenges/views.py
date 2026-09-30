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

from trophies.mixins import LoginRequiredAPIMixin
from trophies.models import Contract
from trophies.util_modules.constants import CHALLENGE_SLOT_JOB_XP
from django.http import JsonResponse
from django.template.loader import render_to_string
from django.shortcuts import redirect
from django.urls import reverse_lazy
from django.utils.decorators import method_decorator
from django.views import View
from django.views.generic import DetailView, TemplateView
from django_ratelimit.decorators import ratelimit

from challenges.models import (CHALLENGE_TYPE_AZ, CHALLENGE_TYPE_CHOICES, CHALLENGE_TYPE_JOBS,
                               Challenge)
from challenges.services import challenge_service as svc
from challenges.services import rewards
from challenges.services import picker
from challenges.services import slot_render
from core.previews import previewing

#: Shared by both write doors, so create-hide-create cannot outrun one door by using the other. The cap
#: on runs is structural (one active per type) rather than numeric, but the RATE is not bounded by it:
#: hiding frees nothing and starting resumes, yet each still writes and each still takes a row lock.
CHALLENGE_WRITE_RATELIMIT_GROUP = 'challenges:write'

#: THE REWARD DOORS GET THEIR OWN BUDGET, and the reason is the panel's shape: it renders one Claim button
#: per finished square, up to 25, and claiming them one at a time is what the UI invites. On the shared write
#: bucket that exhausts 30/m by itself -- and a tripped `block=True` limit raises `Ratelimited`, which
#: renders through the 403 HTML path with no `error` key, so the toast reads a generic failure AND assign,
#: clear and hide are locked out too. Claiming is also idempotent per square, so a flood cannot double-pay;
#: the limit is about lock contention on `ProfileJobXP`, not about correctness.
CHALLENGE_REDEEM_RATELIMIT_GROUP = 'challenges:redeem'

#: A SEPARATE BUCKET from the writes, and named rather than left implicit. `django_ratelimit` keys a
#: bucket on the group AND the rate, so reusing the write group at a different rate would give the
#: reads their own bucket anyway -- by accident. The reads need a looser limit because the picker's
#: search runs while somebody is typing, and a shared 30/m would cut them off mid-word.
CHALLENGE_READ_RATELIMIT_GROUP = 'challenges:read'

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

        # ONE QUERY FOR BOTH CARDS, aggregated in the database. Asked here rather than inside `_card` so
        # the page reads the owed table once however many types there are.
        owed = rewards.owed_runs(profile)
        context['cards'] = [self._card(profile, key, label, owed)
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

    def _card(self, profile, challenge_type, label, owed_runs):
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
        # BOTH COUNTS IN ONE QUERY. The card needs the visible tally for "N finished before" and the true
        # tally (hidden runs included) for the reward line's ordinal -- two different questions over one
        # table, so `completed_counts` answers them in a single scan rather than two per card.
        counts = svc.completed_counts(profile, challenge_type)
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
            'visible_completed_count': counts['visible'],
            # WHAT FINISHING IT IS WORTH, on the one surface a hunter sees before committing.
            #
            # THE ORDINAL IS THE NEXT COMPLETION'S, not this run's: a hunter reading this card is asking
            # what they are playing for. `completed_run_count` counts finished runs including hidden ones
            # (the right population for a rule), so +1 is the ordinal the next finish earns -- and
            # `title_for` returns None past the second, which correctly leaves a third-run card with no
            # title line rather than a wrong one.
            #
            # NO QUERY OF ITS OWN beyond that count: the XP figure is the shared constant, so this cannot
            # quote a different number from the run page's panel.
            'reward': self._reward(challenge_type, counts['all']),
            # WHAT IS WAITING TO BE CLAIMED, which is not NECESSARILY about the run the card is showing --
            # `same_run` is the field that says which. See `_owed`.
            'owed': self._owed(challenge_type, owed_runs, run),
        }

    @staticmethod
    def _owed(challenge_type, owed_runs, card_run):
        """The chip's data for unclaimed job XP of this type, or None.

        `{xp, squares, run_id, hidden, more, same_run}` -- and EVERY FIGURE IS ABOUT `run_id`, the run the chip
        links to. `xp` was a sum across every owing run while the link went to one of them, so the chip read
        "+300,000 waiting" and landed the hunter on a panel offering 150,000. `hidden` was `any(...)` the same
        way, so the label could call the linked run hidden when it was not. A chip that describes one run and
        links to another is the failure this feature was built to fix, one level in.

        ACROSS EVERY RUN OF THE TYPE, not just the card's. The card shows the ACTIVE run, or a resumable one,
        or nothing -- and a FINISHED run with unclaimed squares is none of those, so a card would read "Start"
        while the run holding the XP went unmentioned. That is precisely the report this was built from ("the XP
        badge seems stuck"): the nav pill is profile-wide and the page was not, so the page could not explain
        the pill.

        `run_id` IS WHERE TO SEND THEM -- the newest owing run. An arbitrary but STABLE choice, and NOT "the
        run they were last on": nothing tracks per-run activity, and `owed_runs` retracts that exact phrasing in
        its own docstring. The chip is a LINK because when the owed run is not the one the card is offering, the
        card's own button does not lead to it. (It is the only
        route at all when that run is hidden, which `hidden` reports.)
        """
        mine = [row for row in owed_runs if row['challenge_type'] == challenge_type]
        if not mine:
            return None
        first = mine[0]
        return {
            'xp': first['xp'],
            'squares': first['owed'],
            'run_id': first['challenge_id'],
            # A HIDDEN run has no route from this page EXCEPT this link -- unless it is the card's own
            # resumable run, which `resumable_run` deliberately returns and the card renders with a "Hidden" chip
            # and a Resume button. So `hidden` and `same_run` can both be true, which is why the copy branches on
            # `same_run` first. Read off the LINKED run, not `any(...)`: the label describes where the link goes.
            'hidden': first['is_hidden'],
            # HOW MANY OTHERS ALSO OWE, carried in the ACCESSIBLE NAME only -- the visible line stays "+X
            # waiting" and names one run. Deliberate: the chip points at one run at a time and re-renders at the
            # next one after a claim, with the nav pill lit meanwhile. Saying "the chip" here overstated it.
            'more': len(mine) - 1,
            # WHETHER THE XP IS ON THE RUN THIS CARD IS OFFERING. When it is not, the card's own button leads
            # somewhere else -- and on an empty card that button says "Start", which would otherwise read as
            # the route to the XP.
            'same_run': card_run is not None and card_run.pk == first['challenge_id'],
        }

    @staticmethod
    def _reward(challenge_type, completed):
        """`{title_name, per_square}` for the next completion of this type, or None if it earns no title.

        TAKES THE COUNT rather than fetching one, so the card's single `completed_counts` query serves this
        too. `completed` is the TRUE count including hidden finished runs, because that is the population
        the title rule uses -- hiding a finished run must not hand its title out again.

        A-Z REPORTS NO XP AT ALL rather than zero, and the template shows the line only when there is a
        figure: a letter is not a job, so there is nothing for job XP to land in (owner, 2026-09-28).
        Rendering "0 XP" would answer a question nobody asked and read as a bug.
        """
        # NOT None WHEN THERE IS NO TITLE, which is the bug this replaced. Returning None dropped the
        # whole reward line, so a hunter with two completed Job Coverage runs saw a card advertising NOTHING
        # while 6,000 XP a square was still being paid -- and the run page, which gates on `per_square`,
        # said otherwise. Two surfaces disagreeing is the exact failure the one-reader rule exists to stop.
        name = rewards.title_for(challenge_type, completed + 1)
        per_square = CHALLENGE_SLOT_JOB_XP if challenge_type == CHALLENGE_TYPE_JOBS else 0
        if name is None and not per_square:
            return None
        return {
            'title_name': name,
            'per_square': per_square,
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

        # GROUPS, not a flat list. A-Z comes back as one unlabelled group so its page is unchanged;
        # Job Coverage comes back as five, one per discipline, which is the structure the flat grid
        # was hiding by laying five groups of five out seven across.
        context['groups'] = slot_render.slot_groups(challenge)
        context['is_owner'] = viewer is not None and viewer.id == challenge.profile_id
        # WHETHER THE OWNER MAY CHANGE THIS RUN. Two conditions beyond ownership, and a run fails either:
        # a FINISHED run has nothing left to change (completed squares lock, and every square is
        # complete), and a HIDDEN run is read-only because `start` is what brings one back
        # (`challenge_service.start`'s resume branch) -- editing one in place would mean filling squares
        # on a run that is on nobody's page and in no hub.
        #
        # FOUR READERS NOW, and this comment has been wrong in both directions. It first claimed the
        # template asked "once rather than three times"; then, correctly, that it asked zero times and the
        # key was an invariant waiting for 4b's picker. Both are out of date: the template gates the square's
        # tag, the dialog, the script tag and the square's screen-reader line on it, and the picker's four
        # doors ask the same three conditions of the run through `_EditableRunMixin`.
        #
        # That the doors ask SEPARATELY is deliberate rather than duplication: this key decides what to
        # RENDER, and the mixin decides what to SERVE. A page rendered a second ago is not evidence about
        # the run's state now -- a background sync can complete the final square between the two -- so the
        # door has to re-ask, exactly as `assign` re-checks everything the picker offered.
        context['can_edit'] = (context['is_owner']
                               and not challenge.is_complete
                               and not challenge.is_deleted)
        context['progress'] = (
            round(challenge.completed_count / challenge.total_slots * 100)
            if challenge.total_slots else 0
        )
        # WHETHER TO OFFER THE HISTORY IMPORTER AT ALL, and the order is the cost.
        #
        # `can_edit` first, so a visitor's page pays NOTHING: they cannot use the importer, and once the Hall
        # of Fame exists most reads of this page are visitors. `is_linked` second because the door requires it
        # and this used to omit it -- an owner whose profile is unlinked was shown the button and got a 403
        # rendered as a generic "that did not load", which is the flag and the door disagreeing.
        #
        # THE TYPE CHECK IS GONE FROM HERE, because `importer_is_available` already makes it as its own first
        # term and short-circuits on it. Having it twice saved nothing -- not a query, not a row -- and made
        # the test named for it vacuous: deleting the duplicate changed no behaviour at all.
        #
        # DELIBERATELY NOT "is anything importable?", which is the expensive question: that needs the date read
        # over trophy data (`importable_dates`, the tables the whale rule protects) and would run on every view
        # of the page. So the button may open an empty panel, which says plainly which kind of empty it is --
        # the cheap honest answer rather than the costly certain one.
        context['history_is_open'] = (
            context['can_edit']
            and challenge.profile.is_linked
            and svc.importer_is_available(challenge.profile, challenge.challenge_type)
        )
        # THE PUBLIC TRAIL, not the owner's. This route lives under `/community/challenges/` precisely
        # because the page is somebody's artefact rather than their working surface, and the trail has to
        # agree with that: `My Pursuit` and `My Challenges` are both login-gated, so an anonymous reader
        # following them got bounced to a login screen from a page that never asked them to sign in, and
        # a signed-in visitor was sent to THEIR OWN runs from a page about somebody else's.
        #
        # `gamelists` settled this one app over: the public list detail trails Home / Game Lists / name,
        # never My Lists.
        #
        # WHAT THIS RUN PAYS. One reader for the panel here and for the fragment a redeem returns, so a
        # claim cannot make the page and the reply disagree about what is owed.
        #
        # PAID FOR BY EVERY READER, visitor included, which is deliberate: "what is this worth" is the
        # question a Hall of Fame visitor is asking, and the cost is one COUNT plus the owed rows (<= 25),
        # bounded by the run rather than by the hunter's library. Compare `history_is_open` above, which is
        # gated on `can_edit` precisely because ITS question is expensive.
        context['rewards'] = rewards.summary(challenge)
        context['breadcrumb'] = [
            {'text': 'Home', 'url': reverse_lazy('home')},
            {'text': 'Challenges', 'url': reverse_lazy('challenges')},
            {'text': challenge.name},
        ]
        return context


class _LinkedProfileJson(_LinkedProfileRequired):
    """`_LinkedProfileRequired`'s check, answered in JSON.

    The parent redirects to `link_psn`, which is right for a PAGE and wrong for a fetch -- see
    `_ChallengeJsonView` below for what a redirect does to an API caller.
    """

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            profile = getattr(request.user, 'profile', None)
            if not profile or not profile.is_linked:
                return JsonResponse(
                    {'error': 'Link your PSN account to start a challenge.'}, status=403)
        # Skip the parent's redirecting branch; the gate above has already answered for this case.
        return super(_LinkedProfileRequired, self).dispatch(request, *args, **kwargs)


class _ChallengeActionView(View):
    """The shared plumbing for every challenge write: whose run it is, and how to refuse.

    NO AUTH MIXIN HERE, and that is the fix for a real bug rather than a tidy-up. This class used to carry
    `LoginRequiredMixin`, which REDIRECTS an anonymous request to the login page -- correct for
    `StartChallengeView`, which is a form POST answering with a redirect anyway, and actively dangerous
    for the JSON endpoints. So the mixins moved to the two subclasses that need different answers, and
    `_ChallengeJsonView` records what the redirect actually did.

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


class _ChallengeJsonView(LoginRequiredAPIMixin, _LinkedProfileJson, _ChallengeActionView):
    """Every endpoint a fetch calls. Answers 401/403 JSON, NEVER a redirect.

    WHAT THE REDIRECT DID, because "JSON endpoints must not redirect" sounds like a style rule and is not.
    `HideChallengeView` shipped in chunk 3 on a `LoginRequiredMixin` base, so an anonymous or
    session-expired POST got a 302 to `/accounts/login/`. `fetch` follows redirects by default, so the
    browser then received the LOGIN PAGE with status **200**. `PlatPursuit.API.request` checks
    `response.ok` -- true -- and, seeing `Content-Type: text/html`, returns `response.text()`. The caller's
    SUCCESS path therefore ran, with login-page HTML as its result: the toast said it worked and the card
    updated, while nothing had been written.

    The most likely way to hit it is not an anonymous visitor but a session expiring on an open tab, which
    is ordinary rather than exotic.

    `LoginRequiredAPIMixin` (`trophies/mixins.py`) exists for exactly this and predates the feature; not
    using it was the mistake, not a missing abstraction.
    """


class _EditableRunMixin:
    """Every picker door acts on a run the hunter can still CHANGE, so they all resolve the same way.

    A FINISHED OR HIDDEN RUN IS NOT ONE. `_lock_challenge` refuses every write on either, so without this
    the read doors answered 200 with a complete offer list whose every click could only 400 -- after doing
    the whole pool scan and, on a thin slot, the five-query trophy read. The writes were already safe; the
    reads were doing expensive work to build a lie.

    THIS IS THE CONSUMER `can_edit` WAS WAITING FOR. `ChallengeDetailView` kept that predicate with a
    comment saying 4b's picker would gate on it, and the first version of 4b did not -- so the key was dead
    and the justification for keeping it was false. Same three conditions, asked here of the run rather than
    of the page.
    """

    def editable_run(self, request, challenge_id):
        challenge = self.get_challenge(request, challenge_id)
        if challenge is None or challenge.is_complete or challenge.is_deleted:
            # 404, not 403: "you cannot edit this" and "this is not yours" must be indistinguishable from
            # outside, and the client's handling for both is the same -- reload and see the truth.
            return None
        return challenge


class _SlotView(_EditableRunMixin, _ChallengeJsonView):
    """Resolves an editable run AND one of its slots, which the three slot-scoped doors need.

    THE KEY COMES FROM THE URL, so it is untrusted: this answers 404 rather than validating the string,
    because "not a slot of this run" and "not this hunter's run" should be indistinguishable from outside.

    ONE QUERY FOR THE SLOT, not two. An earlier version asked `slots.filter(key=key).exists()` here and
    `slot_panel` then asked `slots.filter(key=key).first()` for the same row. This hands the slot forward.
    """

    def resolve(self, request, challenge_id, key):
        challenge = self.editable_run(request, challenge_id)
        if challenge is None:
            return None, None
        slot = challenge.slots.filter(key=key).first()
        if slot is None:
            return None, None
        return challenge, slot


class SlotPickerView(_SlotView):
    """What can fill this square. GET, because it reads.

    RATE LIMITED despite being a read: it runs an `icontains` over an unindexed column when a term is
    given, and one open panel per keystroke is the shape that turns a search box into a scan loop.

    A TRIPPED LIMIT ANSWERS 403, NOT 429, and saying otherwise was wrong. `Ratelimited` subclasses
    `PermissionDenied`; `RatelimitMiddleware` is not installed, and `handler429` is an allauth convention
    that nothing in Django dispatches to -- so the refusal renders through the 403 path, as HTML, without
    the `error` key the client reads. `api/game_flag_views.py` already records this and this docstring
    re-asserted the comfortable version. The client still fails correctly (`response.ok` is false) but says
    something generic; making these doors answer JSON on a tripped limit needs a `handler403` or the
    middleware, and belongs with the other 22 `method='GET'` limiters rather than in this chunk.
    """

    @method_decorator(ratelimit(group=CHALLENGE_READ_RATELIMIT_GROUP, key='user', rate='90/m',
                                method='GET', block=True))
    def get(self, request, challenge_id, key):
        challenge, slot = self.resolve(request, challenge_id, key)
        if challenge is None:
            return self.not_found()
        panel = picker.slot_panel(request.user.profile, challenge, key,
                                  query=request.GET.get('q', ''), slot=slot)
        return JsonResponse(_panel_json(panel))


class HistoryPickerView(_EditableRunMixin, _ChallengeJsonView):
    """What this hunter has already finished that a square will still accept. GET, and the third read.

    `_EditableRunMixin` FOR THE SAME REASON THE OTHER TWO USE IT: every row here is an offer, and an offer on a
    finished or hidden run is one nothing can act on. The panel's own gates are about the IMPORTER (A-Z, first
    run, a join date to measure against); this one is about the RUN.

    THE PANEL ANSWERS ITS OWN CLOSED CASE rather than this view translating it into a status code. A closed
    importer is not an error -- it is a thing to explain, and the reasons differ ("this is Job Coverage", "you
    have already finished one"), so the client needs the reason rather than a 403. Same argument the disabled
    Start button makes on My Challenges: render the refusal, do not redirect to it.
    """

    @method_decorator(ratelimit(group=CHALLENGE_READ_RATELIMIT_GROUP, key='user', rate='90/m',
                                method='GET', block=True))
    def get(self, request, challenge_id):
        challenge = self.editable_run(request, challenge_id)
        if challenge is None:
            return self.not_found()
        panel = picker.history_panel(request.user.profile, challenge,
                                    query=request.GET.get('q', ''))
        return JsonResponse({
            'open': panel['open'],
            'closed_reason': panel['closed_reason'],
            # ISO, so the client formats it with `TimeFormatter` like every other date in this sheet rather
            # than receiving a string it cannot re-style.
            'joined_at': panel['joined_at'].isoformat() if panel['joined_at'] else None,
            'query': panel['query'],
            'showing': panel['showing'],
            'more': panel['more'],
            # WHETHER "NOTHING" MEANS NOTHING. Computed by the panel and, until now, dropped here -- so
            # three comments claimed the client explains which kind of empty it is while the client was
            # never told. The data fix is worthless without the delivery.
            'scan_truncated': panel['scan_truncated'],
            'rows': [{
                'slug': r['slug'],
                'name': r['name'],
                'cover': _cover_url(r['cover']),
                'key': r['key'],
                'key_label': r['key_label'],
                'completed_at': r['completed_at'].isoformat() if r['completed_at'] else None,
                'occupant': r['occupant'],
            } for r in panel['rows']],
        })


class SearchPickerView(_EditableRunMixin, _ChallengeJsonView):
    """Which squares this game could fill. GET, and the contract-first half of the picker."""

    @method_decorator(ratelimit(group=CHALLENGE_READ_RATELIMIT_GROUP, key='user', rate='90/m',
                                method='GET', block=True))
    def get(self, request, challenge_id):
        challenge = self.editable_run(request, challenge_id)
        if challenge is None:
            return self.not_found()
        panel = picker.search_panel(request.user.profile, challenge, request.GET.get('q', ''))
        return JsonResponse({
            'query': panel['query'],
            'total': panel['total'],
            'showing': panel['showing'],
            'too_short': panel['too_short'],
            'rows': [{
                'slug': r['slug'],
                'name': r['name'],
                'cover': _cover_url(r['cover']),
                'keys': r['keys'],
                'already_in_run': r['already_in_run'],
                'is_completed_by_you': r['is_completed_by_you'],
            } for r in panel['rows']],
            # THE THREE RUN-LEVEL MAPS, all keyed on a square. They describe the run's squares, not any one
            # result, so they are sent once instead of on each of 24 rows: what each square is called, the
            # glyph and discipline it wears, and the game currently in it.
            'key_labels': panel['key_labels'],
            'key_atoms': panel['key_atoms'],
            'filled': panel['filled'],
        })


class AssignSlotView(_SlotView):
    """Put a game in a square.

    TWO-STEP FOR A COMPLETING PLACEMENT, and the second step is the server's rule rather than the dialog's.
    `svc.assign` raises `ConfirmationRequired` unless `confirm` was sent, and this answers **409** with the
    details the confirmation needs. 409 rather than 400 because nothing is wrong with the request -- the
    same request, repeated with `confirm`, succeeds.
    """

    @method_decorator(ratelimit(group=CHALLENGE_WRITE_RATELIMIT_GROUP, key='user', rate='30/m',
                                method='POST', block=True))
    def post(self, request, challenge_id, key):
        challenge, _slot = self.resolve(request, challenge_id, key)
        if challenge is None:
            return self.not_found()

        slug = (request.POST.get('contract') or '').strip()
        contract = Contract.objects.filter(slug=slug).first() if slug else None
        if contract is None:
            # NOT a 404: the run and the square exist, and the hunter is being told about the GAME. A 404
            # here would read to the client as "your run is gone".
            return self.fail(svc.ChallengeError('That game is not on the Job Board.'))

        confirm = request.POST.get('confirm') in ('1', 'true', 'True', 'on', 'yes')
        try:
            slot = svc.assign(challenge, request.user.profile, key, contract,
                              acknowledge_lock=confirm)
        except svc.ConfirmationRequired as exc:
            return JsonResponse({
                'needs_confirmation': True,
                'error': str(exc),
                'via': exc.via,
                'contract_name': exc.contract_name,
            }, status=409)
        except svc.ChallengeError as exc:
            return self.fail(exc)

        return JsonResponse(_slot_json(challenge, slot))


class ClearSlotView(_SlotView):
    """Empty an unfinished square. A finished one is refused by the service, not by this."""

    @method_decorator(ratelimit(group=CHALLENGE_WRITE_RATELIMIT_GROUP, key='user', rate='30/m',
                                method='POST', block=True))
    def post(self, request, challenge_id, key):
        challenge, _slot = self.resolve(request, challenge_id, key)
        if challenge is None:
            return self.not_found()
        try:
            slot = svc.clear(challenge, request.user.profile, key)
        except svc.ChallengeError as exc:
            return self.fail(exc)
        return JsonResponse(_slot_json(challenge, slot))


def _cover_url(game):
    """A cover's URL or None. The client needs a string, not a `Game`."""
    return game.display_image_url if game is not None else None


def _square_body_html(card):
    """One square's body from a card that is already built. The template half of `_square_html`.

    SPLIT OUT so a batch of squares can share one cover map and one catalogue read -- see `_redeem_json`
    for what looping the single-slot version cost.
    """
    return render_to_string('challenges/partials/_square_body.html',
                            {'card': card, 'can_edit': True})


def _square_html(slot):
    """The one square, re-rendered by the template that draws every other square.

    THIS IS WHAT REPLACED THE PAGE RELOAD. Filling a square used to navigate, so a hunter filling all 26
    watched the page flash, the entrance cascade replay and the scroll jump to the top, 26 times. The reply
    now carries the markup for the square that changed.

    RENDERED SERVER-SIDE ON PURPOSE. The alternative was for the client to patch in cover art, a check
    glyph and a screen-reader line it would have to compose itself -- a second renderer for squares, free
    to drift from `_square_body.html`. This way the template stays the only thing that knows what a square
    looks like.

    `can_edit=True` because only an editable run reaches a write door at all (`_EditableRunMixin`), and
    `card_for` stamps `index = 0`, so the swapped-in square is never lazy -- which is right for one image
    arriving alone. That used to read "no `forloop`", which stopped being how the partial decides when the
    squares were grouped into shelves and the index moved onto the card.
    """
    return _square_body_html(slot_render.card_for(slot))


def _rewards_html(challenge):
    """The reward panel, re-rendered by the template that draws it on page load.

    THE SAME ARGUMENT AS `_square_html`, one surface over: a redeem changes the panel's headline, its owed
    rows and its Claim-all count at once, and having the client patch four things means a second renderer
    free to drift from the partial. The reply carries the markup instead.

    `can_edit` IS NOT THE GATE HERE, unlike every other write on this page. A finished run is exactly when
    a hunter has the most XP to claim, and `_EditableRunMixin` would 404 it -- so the panel renders for the
    OWNER of any run, finished or hidden, and `is_owner` is what it asks.
    """
    return render_to_string('challenges/partials/_rewards_panel.html',
                            {'challenge': challenge, 'rewards': rewards.summary(challenge),
                             'is_owner': True})


def _redeem_json(challenge, slots, granted, ceremony, profile):
    """What both redeem doors answer with.

    ONE SHAPE FOR ONE AND FOR ALL, because the client's handling is identical: swap the squares that
    changed, swap the panel, announce the XP. `squares` is a list even when one square was paid, so the
    caller has no branch either.

    BATCHED, and the unbatched version was a real cost rather than a theoretical one. `card_for` resolves
    one slot's cover AND re-reads the job catalogue, which its docstring correctly says does not scale with
    the run -- true of the function, false of a caller looping it over 25 paid squares. That was ~125
    queries for one press of Claim all. `slot_render.cards_for` shares one cover map and one catalogue read
    across the batch.

    NO `pending_xp`: it was a duplicate COUNT of a figure already inside `rewards_html`, and nothing in the
    client ever read it.

    `claim` CARRIES THE CEREMONY, under the key the contract claim already answers with, because it is
    played by the same module from the same payload. Null when nothing was paid; the player's own guard
    covers that, and the client checks anyway so the toast fallback still fires.

    `profile` IS PASSED IN RATHER THAN READ OFF `challenge`, for one query. `get_challenge` resolves the run
    with `filter(pk=..., profile=...)`, and Django does not populate the FK cache from a filter kwarg -- so
    `challenge.profile` is a fresh SELECT, on a reply that both doors already have `request.user.profile` in
    hand for. Same object, one query fewer, on the request that just held three row locks.

    `xp_pending` IS FOR THE NAV, and it is here because the nav is the one thing a claim changes that this
    reply otherwise would not carry. The navbar and the tab bar were rendered before the claim; the service
    clears their cached answer, but this page does not reload -- so the accent XP pill would sit on My Pursuit
    with nothing left to claim until the hunter navigated away. The Career page gets that for free by
    reloading after its ceremony. It is PROFILE-WIDE, not this run: another run may still owe XP, and only the
    server can answer that. One `EXISTS`, the same query the marker itself runs.

    NO `refresh_from_db` EITHER, which was here with a false reason attached ("the panel's paid total is read
    off the slots and the instance in hand was loaded before the write"). `summary` re-queries the slots
    itself, so nothing it reports comes off this instance except `is_complete` and `completed_at` -- neither
    of which a redeem touches. It was a query buying nothing.
    """
    return {
        'granted': granted,
        'claim': ceremony,
        'xp_pending': rewards.has_unclaimed_xp(profile),
        'squares': [{'key': card['key'], 'html': _square_body_html(card)}
                    for card in slot_render.cards_for(slots)],
        'rewards_html': _rewards_html(challenge),
    }


class RedeemSlotView(_ChallengeJsonView):
    """Claim one completed square's job XP.

    NOT ON `_EditableRunMixin`, and that is the whole reason this class does its own resolving: that mixin
    404s a finished run, and a finished run is the normal case for a payout -- a hunter who has just filled
    their twenty-fifth square has twenty-five squares to claim. Every other precondition belongs to the
    service, which re-asserts them under the run's row lock.

    RATE LIMITED like the other writes. The service is idempotent per square, so a flood cannot double-pay;
    the limit is there because each call is a transaction that locks `ProfileJobXP` rows, and a held key
    should not be able to queue those behind each other.
    """

    @method_decorator(ratelimit(group=CHALLENGE_REDEEM_RATELIMIT_GROUP, key='user', rate='60/m',
                                method='POST', block=True))
    def post(self, request, challenge_id, key):
        challenge = self.get_challenge(request, challenge_id)
        if challenge is None:
            return self.not_found()
        try:
            slot, granted, ceremony = rewards.redeem_slot(challenge, request.user.profile, key)
        except svc.ChallengeError as exc:
            # `RewardError` subclasses `ChallengeError`, so this one clause covers both -- which is why it
            # is a subclass. A sibling exception would have 500d every refusal on this page.
            return self.fail(exc)
        return JsonResponse(_redeem_json(challenge, [slot], granted, ceremony, request.user.profile))


class RedeemAllView(_ChallengeJsonView):
    """Claim every completed square that is still owed, in one transaction.

    ONE CALL RATHER THAN A LOOP FROM THE CLIENT: a full run is twenty-five payouts, and twenty-five
    sequential POSTs would take the `ProfileJobXP` lock twenty-five times, run twenty-five levelling passes
    and twenty-five career-standing recomputes for one press of one button.

    NOTHING OWED IS NOT AN ERROR. The service returns `([], 0)` and this answers 200 with `granted: 0`, so
    a hunter double-pressing sees nothing alarming -- and neither does one whose sync completed a square in
    another tab.
    """

    @method_decorator(ratelimit(group=CHALLENGE_REDEEM_RATELIMIT_GROUP, key='user', rate='60/m',
                                method='POST', block=True))
    def post(self, request, challenge_id):
        challenge = self.get_challenge(request, challenge_id)
        if challenge is None:
            return self.not_found()
        try:
            slots, granted, ceremony = rewards.redeem_all(challenge, request.user.profile)
        except svc.ChallengeError as exc:
            return self.fail(exc)
        return JsonResponse(_redeem_json(challenge, slots, granted, ceremony, request.user.profile))


def _slot_json(challenge, slot):
    """One square's state after a write, plus the run's counters and the square's new markup.

    The counters travel with it because every write moves them and the page shows them in two places (the
    tally and the Horizon). Returning them here means the client never has to guess or re-fetch.
    """
    challenge.refresh_from_db()
    return {
        'html': _square_html(slot),
        'key': slot.key,
        'is_filled': slot.is_filled,
        'is_completed': slot.is_completed,
        'completed_via': slot.completed_via,
        'game_name': slot.contract_name,
        'filled_count': challenge.filled_count,
        'completed_count': challenge.completed_count,
        'total_slots': challenge.total_slots,
        'is_complete': challenge.is_complete,
    }


def _panel_json(panel):
    """`slot_panel`'s payload, with `Game` objects reduced to URLs."""
    return {
        'key': panel['key'],
        'label': panel['label'],
        'job': panel['job'],
        'query': panel['query'],
        'slot_is_filled': panel['slot_is_filled'],
        'slot_is_completed': panel['slot_is_completed'],
        'current_name': panel['current_name'],
        'total': panel['total'],
        'showing': panel['showing'],
        # `locked` and `catchup_more` travel because the client cannot derive either: a locked square looks
        # like an ordinary one with no offers, and a truncated catch-up list looks like a complete one.
        # Both ARE read -- `challenge-detail.js` renders a locked panel differently and says when the
        # catch-up list is cut short. An earlier version of this comment claimed a consumer that did not
        # exist yet, which is how a field ships and then quietly means nothing.
        'locked': panel['locked'],
        'catchup_more': panel['catchup_more'],
        'rows': [{'slug': r['slug'], 'name': r['name'], 'cover': _cover_url(r['cover'])}
                 for r in panel['rows']],
        'catchup': [{
            'slug': r['slug'],
            'name': r['name'],
            'cover': _cover_url(r['cover']),
            'via': r['via'],
            # ISO 8601 so the client can format it with `TimeFormatter` rather than being handed a
            # server-rendered string in the server's idea of a locale.
            'completed_at': r['completed_at'].isoformat() if r['completed_at'] else None,
        } for r in panel['catchup']],
    }

class StartChallengeView(LoginRequiredMixin, _LinkedProfileRequired, _ChallengeActionView):
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


class HideChallengeView(_ChallengeJsonView):
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
