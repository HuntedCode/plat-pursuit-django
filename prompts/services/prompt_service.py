"""The only thing that writes a prompt.

Lifted from `gamelists.services.game_list_service`, which is the house pattern and which earned every
rule it holds. The four that carry over unchanged:

1. **A write refuses before it starts**, and field-by-field where the site draws that line. A
   restriction stops new WORDS; it does not stop somebody taking their own prompt down.
2. **The lock comes before the value it protects, and the precondition is re-asserted on the row that
   came back** -- both halves, or a prompt deleted in another tab still accepts bucket writes.
3. **Denormalized counts move with the rows they count**, in the same transaction.
4. **`position` stays dense and is derived from the ROWS**, never from the counter.

WHAT THIS SERVICE OWNS THAT THE LIST ONE DOES NOT, because these are the rules the schema cannot
hold and the model comments defer to exactly here:

- **`single_slot` on every placement comes from `Prompt.is_single_slot`.** The partial unique's
  predicate is per-ROW, so one placement written with the wrong flag is not merely unconstrained --
  it is invisible to the index and will not collide with the correct row beside it. One wrong row is
  enough to make a poll accept two votes. (P2 writes placements; this module creates the buckets they
  land in and guarantees a poll has exactly one.)
- **A poll has exactly one bucket, forever.** `unique(response, bucket) WHERE single_slot` allows one
  placement PER BUCKET, so "a hunter votes once" holds only while a poll has one bucket. A second
  bucket buys a second vote with every flag set correctly. The service creates it; `create_bucket`
  refuses to add another and `delete_bucket` refuses to remove it.
- **Sub-resources are resolved WITHIN their prompt, never by bare id.** Nothing in the schema ties a
  placement's three foreign keys to the same prompt -- the same gap `GameListItem.section` ships with
  next door, answered the same way. `get_bucket` and `get_game` are the supported lookups and they
  take the prompt.
- **`shape` is immutable**, so `update_prompt` has no `shape` argument at all.
- **An answered prompt cannot be deleted or unpublished**, only closed. A response is unreadable
  without the structure it was placed into, so hiding the prompt orphans work that is not the
  author's to withdraw.
"""
from django.db import models, transaction
from django.utils import timezone

from prompts.models import (
    BUCKET_COLOURS,
    DESCRIPTION_MAX_LENGTH,
    FREE_MAX_PROMPTS,
    LABEL_MAX_LENGTH,
    MAX_BUCKETS_PER_PROMPT,
    MAX_GAMES_PER_PROMPT,
    MAX_GRID_COLUMNS,
    MEMBER_MAX_PROMPTS,
    MIN_GAMES_TO_PUBLISH,
    MIN_GRID_COLUMNS,
    SHAPE_GRID,
    SHAPE_POLL,
    SHAPE_TIER,
    SHAPES,
    TITLE_MAX_LENGTH,
    Prompt,
    PromptBucket,
    PromptGame,
    PromptResponse,
)
from trophies.models import Profile
from trophies.services.comment_service import CommentService
from users.services import restriction_service


class PromptError(Exception):
    """A refusal a caller is expected to show the hunter. The message is user-facing."""


#: What a new prompt starts with, per shape.
#:
#: A TIER LIST ARRIVES USABLE. The genre has one universal convention -- S down to D, red through grey
#: -- and a tier list with no rows is not a tier list, it is a form. Seeding them means the author's
#: first act is dragging games rather than naming five rows everybody names the same way. They are
#: ordinary buckets: rename them, recolour them, delete the ones you do not want.
#:
#: A POLL GETS ITS ONE BUCKET HERE and nowhere else. The author never sees it and can never add a
#: second; see the module docstring for why that is an integrity rule rather than a cap.
#:
#: A GRID GETS NOTHING, deliberately. Its slots ARE the question ("Best combat", "Biggest letdown"),
#: so seeding placeholders would either be wrong or would invite somebody to ship "Slot 1".
DEFAULT_BUCKETS = {
    SHAPE_TIER: (('S', 'red'), ('A', 'orange'), ('B', 'yellow'), ('C', 'green'), ('D', 'blue')),
    SHAPE_POLL: (('Your pick', ''),),
}

#: Offered as one-tap suggestions, not as the only choices -- the same line list names draw.
SUGGESTED_TITLES = (
    'Rank the platinums you actually enjoyed',
    'Hardest platinum of the generation',
    'Best game to start a backlog with',
    'The one you would replay tomorrow',
)


def max_prompts_for(profile):
    """The cap on how many prompts, in one place. Everyone authors; members author more.

    There is deliberately no matching `max_games_for`: prompt SIZE is abuse prevention, and a spam
    limit somebody can pay to raise is not a spam limit. Only the COUNT is a perk, which is the honest
    thing to sell. Size is capped per SHAPE instead (`MAX_GAMES_PER_PROMPT`), because a 200-option
    poll is not a poll.
    """
    return MEMBER_MAX_PROMPTS if profile.user_is_premium else FREE_MAX_PROMPTS


# ── gates ────────────────────────────────────────────────────────────────────────────────────────

def refuse_if_restricted(profile):
    """`all_ugc`, the same scope every other user-content surface uses.

    PUBLIC, because the response service shares it: both of this app's services ask the same two
    questions of a writer, so the gates belong to the app rather than to whichever module happened to
    need them first.

    A title, a description and a bucket label are user-submitted content shown to other people.
    Restricting somebody hides nothing they already published -- their prompts stay up and their
    answers stay readable, they simply cannot write more.
    """
    if restriction_service.is_restricted_from(profile, 'all_ugc'):
        raise PromptError('Your account is currently restricted from posting.')


def refuse_if_unlinked(profile):
    if profile is None or not profile.is_linked:
        raise PromptError('Link your PSN account to build one of these.')


# ── text ─────────────────────────────────────────────────────────────────────────────────────────

def _clean_text(raw, *, field, max_length):
    """Sanitize and validate one hunter-written string.

    Runs `CommentService.sanitize_text` TO A FIXPOINT, which is the part that matters: that function
    bleaches and then `html.unescape()`s its own output, so it is NOT idempotent -- `&lt;script&gt;`
    survives the bleach as text and the unescape turns it back into a live `<script>`. A single pass
    therefore stores raw markup for entity-encoded input, which is safe only under Django
    auto-escaping, and a prompt title is headed for `og:title`.

    Refuses rather than storing a partly-cleaned string, so the guarantee is one a caller can rely on.
    """
    text = raw or ''
    for _pass in range(3):
        cleaned = (CommentService.sanitize_text(text) or '').strip()
        if cleaned == text:
            break
        text = cleaned

    if '<' in text or '>' in text:
        raise PromptError(f'That {field} contains characters that are not allowed.')
    if len(text) > max_length:
        raise PromptError(f'That {field} is too long (max {max_length} characters).')
    return text


def _refuse_banned_words(text, *, field):
    if not text:
        return
    banned, _word = CommentService.check_banned_words(text)
    if banned:
        # The matched word is deliberately not echoed back: it tells somebody probing the filter
        # exactly which term tripped it, which is a list they can then work around.
        raise PromptError(f'That {field} is not allowed. Please choose another.')


def _check_title(raw):
    title = _clean_text(raw, field='title', max_length=TITLE_MAX_LENGTH)
    if not title:
        raise PromptError('It needs a title -- the question you are asking.')
    _refuse_banned_words(title, field='title')
    return title


def _check_description(raw):
    text = _clean_text(raw, field='description', max_length=DESCRIPTION_MAX_LENGTH)
    _refuse_banned_words(text, field='description')
    return text


def _check_label(raw):
    """A bucket label is PUBLIC TEXT a hunter wrote, and it is public on every response too.

    Easy to miss, because a label feels structural rather than editorial. It is not: the header renders
    under the author's byline on the prompt AND on every answer anybody gives, so one bad label taints
    N public pages at once. That reach is the argument for checking it, not against.
    """
    label = _clean_text(raw, field='label', max_length=LABEL_MAX_LENGTH)
    if not label:
        raise PromptError('A row needs a label.')
    _refuse_banned_words(label, field='label')
    return label


def _check_shape(raw):
    """Validate against the shapes that actually RENDER.

    Django's `choices` is a form and admin concern and is not enforced by Postgres. The
    `prompt_shape_valid` CheckConstraint is what holds against a shell or a data migration; this is
    what turns a bad API value into a 400 the client can display rather than an IntegrityError.
    """
    value = (raw or '').strip()
    if value not in SHAPES:
        raise PromptError('That is not one of the shapes.')
    return value


def _check_colour(raw):
    """A palette slot, never free hex.

    `colour` is interpolated into a CSS class name, and the model's own comment says the palette is
    closed -- but unlike `shape` it has no CheckConstraint, so this is the only thing standing between
    a shell-written value and a class attribute.
    """
    value = (raw or '').strip()
    if value not in BUCKET_COLOURS:
        raise PromptError('That is not one of the colours.')
    return value


def _check_grid_columns(raw):
    try:
        value = int(raw)
    except (TypeError, ValueError):
        raise PromptError('That is not a number of columns.')
    if not MIN_GRID_COLUMNS <= value <= MAX_GRID_COLUMNS:
        raise PromptError(f'A grid has between {MIN_GRID_COLUMNS} and {MAX_GRID_COLUMNS} columns.')
    return value


# ── locks and lookups ────────────────────────────────────────────────────────────────────────────

#: WHAT A PUBLISHED PROMPT STOPS ACCEPTING, per shape. Owner's call, and the reasoning differs by
#: shape rather than being one blanket rule:
#:
#: * A TIER LIST stays fully live. Its rows are a ranking the author owns, and a locked decision says
#:   author edits reach existing answers -- an added game appears unplaced in everyone's, a removed
#:   one drops out. Nothing here freezes.
#: * A GRID freezes its SLOTS, because the slots are the questions: changing "Best combat" to "Worst
#:   combat" after people answer inverts what every existing answer says, silently, with no row
#:   changed. Its POOL may still GROW -- more choices is additive and breaks nothing -- but nothing
#:   may leave it, because a departing game takes real answers with it.
#: * A POLL freezes entirely. Adding an option mid-vote is the classic way to rig one, and the tally
#:   is the whole artifact.
#:
#: THE ESCAPE HATCH IS A RULE THAT ALREADY EXISTS rather than a special case: unpublishing is refused
#: only once somebody has answered, so an author who spots a typo with zero responses can unpublish,
#: fix and republish. The moment anyone answers, that door closes and the structure is fixed for good.
_FROZEN_WHILE_PUBLIC = {
    SHAPE_TIER: frozenset(),
    SHAPE_GRID: frozenset({'rows', 'pool_remove'}),
    SHAPE_POLL: frozenset({'rows', 'pool_add', 'pool_remove', 'pool_order'}),
}

_FROZEN_COPY = {
    'rows': 'The rows of a published {shape} cannot be changed.',
    'pool_add': 'A published {shape} cannot take new games.',
    'pool_remove': 'A published {shape} cannot lose games.',
    'pool_order': 'A published {shape} cannot be rearranged.',
}


def _refuse_if_frozen(locked, *, act):
    """Refuse an edit this shape does not accept while it is public."""
    if not locked.is_public or act not in _FROZEN_WHILE_PUBLIC[locked.shape]:
        return
    what = _FROZEN_COPY[act].format(shape=locked.get_shape_display().lower())
    raise PromptError(
        f'{what} Unpublish it first -- you still can, while nobody has answered.'
    )


def _refuse_if_not_publishable(locked):
    """The floor a prompt has to clear before anybody can be asked to answer it.

    AT THE PUBLISH TRANSITION AND NOWHERE ELSE. A draft may sit at one game for as long as its author
    likes; what must not happen is a half-built thing reaching the browse page, because the first page
    of a new feature is where a two-game tier list does the most damage.

    Three checks, and the third is the one that is easy to miss: a grid whose author supplied a pool
    AND turned duplicates off needs at least as many games as it has slots, or it ships a grid nobody
    can finish.
    """
    rows = PromptBucket.objects.filter(prompt=locked).count()
    if rows < 1:
        raise PromptError('Add at least one row before publishing this.')

    games = PromptGame.objects.filter(prompt=locked).count()
    floor = MIN_GAMES_TO_PUBLISH[locked.shape]
    if games < floor:
        raise PromptError(
            f'A {locked.get_shape_display().lower()} needs at least {floor} games before it goes up. '
            f'This one has {games}.'
        )

    # A grid with an EMPTY pool is the open kind: respondents search the catalogue for each slot, so
    # there is nothing to be short of. Only a supplied pool has to be big enough.
    if locked.shape == SHAPE_GRID and games and locked.forbids_duplicates and games < rows:
        raise PromptError(
            f'With duplicates off, this needs at least as many games as slots: {rows} slots, '
            f'{games} games.'
        )


def _lock_prompt(prompt):
    """Re-read FOR UPDATE and re-assert the precondition on the row that came back.

    Both halves. Checking `is_deleted` on the caller's instance and then locking leaves a window in
    which a prompt deleted in another tab still accepts pool writes.
    """
    locked = Prompt.objects.select_for_update().get(pk=prompt.pk)
    if locked.is_deleted:
        raise PromptError('That no longer exists.')
    return locked


def _lock_game(game, locked_prompt):
    """The pivot, re-read under the parent's lock and SCOPED TO THE PARENT.

    The scoping is the cross-prompt guard the schema does not carry: nothing stops a `PromptGame` id
    from another prompt being handed in, and `Model.delete()` on a row this prompt does not own would
    silently edit somebody else's question. Filtering on the parent makes the lookup answer "no" to
    exactly that.
    """
    fresh = (PromptGame.objects.select_for_update()
             .filter(pk=game.pk, prompt=locked_prompt).first())
    if fresh is None:
        raise PromptError('That game is no longer in this pool.')
    return fresh


def _lock_bucket(bucket, locked_prompt):
    """Same treatment, same reason, plus one of its own: `delete_bucket` reads `position` off the
    pivot and shifts every row above it, so a stale pivot double-shifts and leaves two buckets sharing
    a position -- `Meta.ordering` then goes non-deterministic and `create_bucket`'s `Max + 1` leaves a
    permanent hole no user action repairs.

    THE `prompt=` FILTER HERE IS NOT REACHABLE TODAY, and that is worth saying rather than leaving it
    to look load-bearing. Both callers derive the prompt FROM the bucket (`prompt = bucket.prompt`),
    so the pair can never disagree and `_require_owner` refuses somebody else's bucket before this
    runs. Mutation-checked: removing this filter kills no test, while removing the identical filter in
    `_lock_game` kills one immediately -- because `remove_concept` takes the prompt and the game
    SEPARATELY, which is the shape that can be paired wrongly.

    Kept anyway, and cheap: it is insurance against a future caller that takes both, which is exactly
    what `remove_concept` is. The supported way for a view to get here is `get_bucket(prompt, id)`.
    """
    fresh = (PromptBucket.objects.select_for_update()
             .filter(pk=bucket.pk, prompt=locked_prompt).first())
    if fresh is None:
        raise PromptError('That row is no longer part of this.')
    return fresh


def get_bucket(prompt, bucket_id):
    """THE supported way to turn a bucket id into a bucket. Takes the prompt, and means it.

    Views must never resolve a sub-resource by bare id: the schema does not tie a placement's
    foreign keys to one prompt, so a bucket from another prompt would satisfy every constraint and
    produce a row that is invisible to the editor while still being counted.
    """
    return PromptBucket.objects.filter(pk=bucket_id, prompt=prompt).first()


def get_game(prompt, game_id):
    """The same, for a pool row. See `get_bucket`."""
    return PromptGame.objects.filter(pk=game_id, prompt=prompt).first()


def _require_owner(prompt, profile):
    """Ownership, asked about the row rather than about the URL."""
    if prompt.is_deleted:
        raise PromptError('That no longer exists.')
    if prompt.owner_id != profile.id:
        raise PromptError('That is not yours.')


def _answered_by_somebody_else(prompt):
    """Has anybody but the author put their own arrangement on this?

    The author's own response does not count. Answering your own question is a normal thing to do and
    must not be what locks you out of deleting it.
    """
    return (PromptResponse.objects
            .filter(prompt=prompt)
            .exclude(profile_id=prompt.owner_id)
            .exists())


# ── prompts ──────────────────────────────────────────────────────────────────────────────────────

@transaction.atomic
def create_prompt(profile, *, shape, title, description='', grid_columns=3,
                  allow_duplicates=True):
    """Make one, with the buckets its shape starts with.

    The buckets are created HERE rather than by the author's first edit, because a poll's single
    bucket is an integrity guarantee rather than a convenience: there is no path in this service that
    creates a poll without exactly one, and none that lets a second appear.

    IT IS ALWAYS BORN PRIVATE, and there is deliberately no `is_public` argument. Every shape now has
    a floor to clear before it can be published (`_refuse_if_not_publishable`), and a brand-new prompt
    clears none of them -- so the parameter could only ever have meant "refuse this create". Publishing
    is a deliberate second act with its own affordance, which is what the design asked for anyway.
    """
    refuse_if_unlinked(profile)
    refuse_if_restricted(profile)

    shape = _check_shape(shape)
    title = _check_title(title)
    description = _check_description(description)
    columns = _check_grid_columns(grid_columns)

    # THE LOCK GOES ON THE PROFILE, not on the prompts. `@transaction.atomic` does nothing for this by
    # itself: at READ COMMITTED two requests both COUNT 2, both pass `2 >= 3`, both insert, and the
    # hunter ends up with four. `SELECT ... FOR UPDATE` locks rows that EXIST, so locking a filtered
    # prompt queryset locks nothing in the case that matters -- the account always exists.
    Profile.objects.select_for_update().filter(pk=profile.pk).first()

    cap = max_prompts_for(profile)
    if Prompt.objects.owned_by(profile).count() >= cap:
        raise PromptError(
            f'You have reached your limit of {cap}. Delete one to make room, '
            'or become a member for more.'
        )

    prompt = Prompt.objects.create(
        owner=profile, shape=shape, title=title, description=description,
        grid_columns=columns, allow_duplicates=bool(allow_duplicates))

    for position, (label, colour) in enumerate(DEFAULT_BUCKETS.get(shape, ())):
        PromptBucket.objects.create(prompt=prompt, label=label, colour=colour, position=position)
    return prompt


@transaction.atomic
def update_prompt(prompt, profile, *, title=None, description=None, is_public=None,
                  grid_columns=None, allow_duplicates=None):
    """Edit one you own. Every argument is optional; only what is passed is touched.

    NO `shape` ARGUMENT, and that absence is the feature. See `prompts/models.py` -- changing a tier
    list into a grid would leave buckets holding seven games under a one-game promise and make
    `single_slot` lie on rows belonging to other people, with no non-arbitrary way to choose which
    placement survives.

    The restriction gate is scoped to the acts that PUT WORDS IN FRONT OF PEOPLE, in either of the two
    ways that can happen: writing them, or making already-written ones visible. A restricted hunter can
    still un-publish and still close. Gating the whole function would trap their prompt in public,
    which is the failure `game_list_service.update_list` records from the other direction.
    """
    _require_owner(prompt, profile)

    # `bool(is_public)` and not `is not None`: None means "not passed" and False means "take it down",
    # and only the third case -- making it public -- is the one restriction speaks to.
    publishing = is_public is not None and bool(is_public)
    if title is not None or description is not None or publishing:
        refuse_if_restricted(profile)

    # LOCKED BEFORE THE PIVOT IS READ, which this function did not do and needed to.
    #
    # The unpublish refusal below is gated on the prompt ALREADY being public, and that value used to
    # come off the caller's in-memory object. An author with the prompt open in two tabs -- publish in
    # one, four hunters answer, "make private" from the other -- handed in an instance whose
    # `is_public` was still False, so the conjunct short-circuited, the answered-check never ran, and
    # four responses ended up orphaned behind a private prompt. Exactly the state this service exists
    # to prevent, reached by reading the pivot off a stale copy: rule 2, broken by the file that
    # states it.
    locked = _lock_prompt(prompt)

    # UNPUBLISHING IS REFUSED ONCE SOMEBODY HAS ANSWERED, for the reason `delete_prompt` gives: a
    # response cannot be read without the structure it was placed into. Checked before anything is
    # written, so a POST carrying a rename AND an unpublish changes neither.
    if is_public is not None and not is_public and locked.is_public:
        if _answered_by_somebody_else(locked):
            raise PromptError(
                'People have answered this, so it cannot be hidden. Close it instead -- their '
                'answers stay readable and nobody new can add one.'
            )

    # PUBLISHING CLEARS A FLOOR. Checked before anything is written, so a call carrying a rename and
    # a publish lands neither if the floor is not met.
    if is_public is not None and is_public and not locked.is_public:
        _refuse_if_not_publishable(locked)

    # THE DUPLICATES TOGGLE, and the two rules around it. It is refused on a published grid because it
    # changes what every existing answer is allowed to be; and turning it OFF against a supplied pool
    # needs that pool to be able to fill every slot, or the author ships something unfinishable.
    if allow_duplicates is not None:
        if locked.shape != SHAPE_GRID:
            raise PromptError('Only a grid has that setting.')
        if locked.is_public:
            raise PromptError(
                'A published grid cannot change that. Unpublish it first -- you still can, while '
                'nobody has answered.'
            )
        if not allow_duplicates:
            games = PromptGame.objects.filter(prompt=locked).count()
            rows = PromptBucket.objects.filter(prompt=locked).count()
            if games and games < rows:
                raise PromptError(
                    f'With duplicates off, this needs at least as many games as slots: {rows} '
                    f'slots, {games} games.'
                )

    changed = []
    if title is not None:
        locked.title = _check_title(title)
        changed.append('title')
    if description is not None:
        locked.description = _check_description(description)
        changed.append('description')
    if is_public is not None:
        locked.is_public = bool(is_public)
        changed.append('is_public')
    if grid_columns is not None:
        locked.grid_columns = _check_grid_columns(grid_columns)
        changed.append('grid_columns')
    if allow_duplicates is not None:
        locked.allow_duplicates = bool(allow_duplicates)
        changed.append('allow_duplicates')

    if changed:
        locked.save(update_fields=[*changed, 'updated_at'])

    # EVERY EXISTING PLACEMENT'S FLAG MOVES WITH IT. `no_duplicates` is denormalized onto each
    # placement because the partial uniques cannot see the grandparent, and unlike `single_slot` it is
    # not protected by immutability -- so a toggle that left old rows behind would leave a grid whose
    # answers disagree with its own rule, invisibly, because a per-row predicate does not collide with
    # anything.
    #
    # Bounded: the toggle is refused on a published grid, and an unpublished one can only carry
    # answers from its own author.
    if allow_duplicates is not None:
        from prompts.models import PromptPlacement
        PromptPlacement.objects.filter(response__prompt=locked).update(
            no_duplicates=locked.forbids_duplicates)

    return locked


@transaction.atomic
def set_closed(prompt, profile, *, closed):
    """Stop taking new answers, or start again.

    THE EXIT THAT IS NOT A DELETION. An author who is finished with a question -- or who is being
    brigaded -- needs a way out that does not destroy work belonging to other people. Closing hides
    nothing: the prompt stays on its page, every answer stays readable, and the hunters who wrote them
    may still edit their own.

    Ungated by restriction, deliberately. Closing submits no content and removes nothing.
    """
    _require_owner(prompt, profile)
    # Locked before the idempotency shortcut, which otherwise decides from the caller's copy: an
    # instance that still says "closed" after somebody reopened it short-circuits, writes nothing, and
    # hands the view back an object claiming a state the row does not have.
    locked = _lock_prompt(prompt)
    if locked.is_closed == bool(closed):
        return locked
    locked.is_closed = bool(closed)
    locked.save(update_fields=['is_closed', 'updated_at'])
    return locked


@transaction.atomic
def delete_prompt(prompt, profile):
    """Soft delete, idempotent, and REFUSED once somebody else has answered.

    A response is an arrangement of this prompt's games into this prompt's buckets; without them it is
    not a thing that can be rendered at all. So deleting an answered prompt does not remove one
    hunter's content, it removes everybody's -- which is why the rule is a refusal at the boundary
    rather than a cascade with an apology.

    Account deletion overrides this, and should: the rule protects responders from an author's change
    of mind, not from an author's departure. See `users/views.py`.
    """
    if prompt.owner_id != profile.id:
        raise PromptError('That is not yours.')
    if prompt.is_deleted:
        return prompt

    # LOCKED, because the refusal below is otherwise advisory. Without it the check and the write are
    # not serialized against a response arriving between them: the author deletes a prompt with zero
    # answers, the first responder commits theirs a millisecond later, and that answer ends up hanging
    # off a row `visible()` filters out of every read -- unreadable, uncountable, and with no undelete
    # in this service.
    #
    # THE OTHER HALF IS AN OBLIGATION ON THE RESPONSE SERVICE, and is discharged there: creating a
    # response re-reads this same row FOR UPDATE, so the two acts queue rather than interleave. A lock
    # only one side takes serializes nothing.
    # NOT `_lock_prompt`, which REFUSES an already-deleted row -- that helper exists for writers where
    # a vanished prompt is an error, and here it is the success case. Deleting twice must stay a no-op:
    # a second click should not answer "that no longer exists" about a prompt somebody just removed.
    locked = Prompt.objects.select_for_update().filter(pk=prompt.pk).first()
    if locked is None or locked.is_deleted:
        return locked or prompt

    if _answered_by_somebody_else(locked):
        raise PromptError(
            'People have answered this, so it cannot be deleted. Close it instead -- their answers '
            'stay readable and nobody new can add one.'
        )

    locked.is_deleted = True
    locked.deleted_at = timezone.now()
    locked.save(update_fields=['is_deleted', 'deleted_at', 'updated_at'])
    return locked


# ── the pool ─────────────────────────────────────────────────────────────────────────────────────

@transaction.atomic
def add_concept(prompt, profile, concept):
    """Append a game to the pool, up to this SHAPE's cap.

    No `note` argument, and there will not be one. The response side carries no prose at all, and the
    pool is rendered inside every response -- so a note here would be authored text appearing on other
    people's pages, which is a moderation surface this feature does not otherwise have.
    """
    # UNLINKED FIRST, because `_require_owner` dereferences `profile.id`. A view using this
    # codebase's own `getattr(request.user, 'profile', None)` idiom and forgetting the None
    # check would otherwise get an AttributeError -- a 500 past every `except PromptError`,
    # where a 400 was designed.
    refuse_if_unlinked(profile)
    _require_owner(prompt, profile)
    refuse_if_restricted(profile)

    locked = _lock_prompt(prompt)
    _refuse_if_frozen(locked, act='pool_add')
    if PromptGame.objects.filter(prompt=locked, concept=concept).exists():
        raise PromptError('That game is already in here.')

    # COUNTED UNDER THE LOCK and from the ROWS, not from `game_count`. `@transaction.atomic` alone
    # does not stop two requests both counting 199 and both inserting; and the counter drifts HIGH
    # when a Concept is deleted (CASCADE, no service involved), which would lock a hunter out of a
    # prompt that has room, permanently, with no action that clears it.
    cap = MAX_GAMES_PER_PROMPT[locked.shape]
    if PromptGame.objects.filter(prompt=locked).count() >= cap:
        raise PromptError(f'This holds {cap} games. Remove one to make room.')

    highest = PromptGame.objects.filter(prompt=locked).aggregate(
        top=models.Max('position'))['top']
    game = PromptGame.objects.create(
        prompt=locked, concept=concept, position=0 if highest is None else highest + 1)

    _recount(locked)
    return game


@transaction.atomic
def remove_concept(prompt, profile, game):
    """Take a game out of the pool and CLOSE THE GAP.

    This is the write with the widest blast radius in the feature: the row's placements cascade out of
    every response, including responses belonging to people the author has never met. That is the
    intended behaviour -- an author editing a live prompt is the locked design -- and it is the reason
    `PromptPlacement` points at the pool row rather than at the Concept.

    `placement_count` is repaired for the responses that lost something -- NARROWED to those, not
    bounded. The distinction matters and an earlier version of this comment blurred it: nothing caps
    how many responses a prompt has, so removing a game every one of fifty thousand answers had placed
    still row-locks fifty thousand rows in one statement. What the narrowing buys is proportionality to
    the EDIT rather than to the prompt's popularity. `Concept.absorb()` needed the same narrowing and
    says the same thing honestly.
    """
    _require_owner(prompt, profile)

    locked = _lock_prompt(prompt)
    _refuse_if_frozen(locked, act='pool_remove')
    fresh = _lock_game(game, locked)

    # Collected BEFORE the delete, because afterwards there is no way to find them.
    from prompts.models import PromptPlacement
    touched = set(
        PromptPlacement.objects.filter(prompt_game=fresh).values_list('response_id', flat=True)
    )

    removed_position = fresh.position
    fresh.delete()
    PromptGame.objects.filter(prompt=locked, position__gt=removed_position).update(
        position=models.F('position') - 1)
    _recount(locked)
    _recount_placements(touched)


@transaction.atomic
def reorder_games(prompt, profile, game_ids):
    """Set the pool's order to exactly `game_ids`.

    THE COUNTERPART `reorder_buckets` SHIPPED WITHOUT, and its absence was not cosmetic. Pool
    `position` is dense and the browse tile's cover mosaic is a bounded prefetch over the first four
    rows, so those four games are what represents this prompt to everybody scrolling past it -- and
    the author had no way to choose them. The only workaround was remove-then-add, which appends to
    the end AND cascades that game out of every existing response: the most destructive write in the
    feature, as the remedy for wanting a different cover.

    Refuses a partial list, like every reorder here. Ungated by restriction -- arranging your own rows
    submits no content -- and it disturbs no answer, because placements point at the pool ROW, not at
    its position.
    """
    _require_owner(prompt, profile)
    locked = _lock_prompt(prompt)
    _refuse_if_frozen(locked, act='pool_order')

    try:
        wanted = [int(value) for value in game_ids]
    except (TypeError, ValueError):
        raise PromptError('That is not a valid order.')

    existing = list(PromptGame.objects.filter(prompt=locked).values_list('pk', flat=True))
    if sorted(wanted) != sorted(existing):
        raise PromptError('That order does not match the games that are here.')

    by_id = {game.pk: game for game in PromptGame.objects.filter(prompt=locked)}
    for position, game_id in enumerate(wanted):
        by_id[game_id].position = position
    PromptGame.objects.bulk_update(by_id.values(), ['position'])
    _touch(locked)


# ── buckets ──────────────────────────────────────────────────────────────────────────────────────

@transaction.atomic
def create_bucket(prompt, profile, *, label, colour=''):
    """Add a row or a slot.

    REFUSED ON A POLL, and not because one is enough. `unique(response, bucket) WHERE single_slot`
    permits one placement per BUCKET, so a poll's one-vote guarantee is a statement about its bucket
    count -- a second bucket buys every hunter a second vote with every flag set correctly, and
    nothing in the database would notice. `MAX_BUCKETS_PER_PROMPT[SHAPE_POLL]` is 1, so the cap below
    already refuses it; this is here so the refusal SAYS something true, and so deleting that cap
    cannot quietly open the hole.
    """
    # UNLINKED FIRST, because `_require_owner` dereferences `profile.id`. A view using this
    # codebase's own `getattr(request.user, 'profile', None)` idiom and forgetting the None
    # check would otherwise get an AttributeError -- a 500 past every `except PromptError`,
    # where a 400 was designed.
    refuse_if_unlinked(profile)
    _require_owner(prompt, profile)
    refuse_if_restricted(profile)

    label = _check_label(label)
    colour = _check_colour(colour)

    locked = _lock_prompt(prompt)
    _refuse_if_frozen(locked, act='rows')
    if locked.shape == SHAPE_POLL:
        raise PromptError('A poll asks one question. Its answer box is the only row it has.')

    cap = MAX_BUCKETS_PER_PROMPT[locked.shape]
    if PromptBucket.objects.filter(prompt=locked).count() >= cap:
        raise PromptError(f'This holds {cap} rows. Remove one to make room.')

    highest = PromptBucket.objects.filter(prompt=locked).aggregate(
        top=models.Max('position'))['top']
    bucket = PromptBucket.objects.create(
        prompt=locked, label=label, colour=colour,
        position=0 if highest is None else highest + 1)
    _touch(locked)
    return bucket


@transaction.atomic
def update_bucket(bucket, profile, *, label=None, colour=None):
    """Rename or recolour. Both are cosmetic and both are retroactive across every answer given.

    Renaming is gated by restriction because it IS writing public text -- and it reaches further than
    most, since the label renders on every response as well as on the prompt. Recolouring is not: a
    palette slot submits no words.
    """
    prompt = bucket.prompt
    _require_owner(prompt, profile)
    if label is not None:
        refuse_if_restricted(profile)

    locked = _lock_prompt(prompt)
    _refuse_if_frozen(locked, act='rows')
    fresh = _lock_bucket(bucket, locked)

    changed = []
    if label is not None:
        fresh.label = _check_label(label)
        changed.append('label')
    if colour is not None:
        fresh.colour = _check_colour(colour)
        changed.append('colour')

    if changed:
        fresh.save(update_fields=changed)
        _touch(locked)
    return fresh


@transaction.atomic
def delete_bucket(bucket, profile):
    """Remove a row, CLOSE THE GAP, and refuse to remove the last one.

    THE SAME FAN-OUT `remove_concept` CARRIES, and worse in practice: the S row of a popular tier
    list holds a placement from nearly every answer, so deleting one row can rewrite `placement_count`
    across every response there is. One statement, N rows, N unbounded.

    The games do not go: a placement is an association, so its cards simply return to every
    responder's tray. But a prompt with no buckets cannot be answered at all -- and on a poll, the one
    bucket IS the question, so removing it would delete every vote by cascade while leaving the poll
    standing. One refusal covers both.
    """
    prompt = bucket.prompt
    _require_owner(prompt, profile)

    locked = _lock_prompt(prompt)
    _refuse_if_frozen(locked, act='rows')
    fresh = _lock_bucket(bucket, locked)

    if PromptBucket.objects.filter(prompt=locked).count() <= 1:
        raise PromptError('Something needs somewhere to put the games. Add another row first.')

    from prompts.models import PromptPlacement
    touched = set(
        PromptPlacement.objects.filter(bucket=fresh).values_list('response_id', flat=True)
    )

    removed_position = fresh.position
    fresh.delete()
    PromptBucket.objects.filter(prompt=locked, position__gt=removed_position).update(
        position=models.F('position') - 1)
    _touch(locked)
    _recount_placements(touched)


@transaction.atomic
def reorder_buckets(prompt, profile, bucket_ids):
    """Set the order to exactly `bucket_ids`.

    REFUSES A PARTIAL LIST rather than accepting one, the same contract `game_list_service.reorder`
    holds: a drag that posts a subset means the client and the server disagree about what exists, and
    applying it would renumber rows the client forgot around ones it remembered.

    Ungated by restriction -- arranging your own rows submits no content. Note what reordering does
    NOT do: placements point at the bucket, not at its position, so every answer already given is
    untouched. On a tier list the order is a RANK, so this changes what those answers mean; that is
    the author's prerogative and the reason the control exists.
    """
    _require_owner(prompt, profile)
    locked = _lock_prompt(prompt)
    _refuse_if_frozen(locked, act='rows')

    try:
        wanted = [int(value) for value in bucket_ids]
    except (TypeError, ValueError):
        raise PromptError('That is not a valid order.')

    existing = list(PromptBucket.objects.filter(prompt=locked).values_list('pk', flat=True))
    if sorted(wanted) != sorted(existing):
        raise PromptError('That order does not match the rows that are here.')

    by_id = {bucket.pk: bucket for bucket in PromptBucket.objects.filter(prompt=locked)}
    for position, bucket_id in enumerate(wanted):
        by_id[bucket_id].position = position
    PromptBucket.objects.bulk_update(by_id.values(), ['position'])
    _touch(locked)


# ── counters ─────────────────────────────────────────────────────────────────────────────────────

def _touch(locked):
    """Bump the prompt's `updated_at` for an edit that changed no field ON the prompt.

    The three bucket writers used to skip this, which was a divergence introduced while lifting from
    `game_list_service` rather than a decision -- `delete_section` bumps it. Nothing SORTS on
    `updated_at` here (`Meta.ordering` is `-created_at` and no index carries it, deliberately), so the
    cost of skipping it was never a wrong page: it was a prompt whose five row labels had all been
    rewritten still reporting itself unmodified to an "edited N ago" line, or to any future ETag.

    `.update()` rather than `save()`, because `auto_now` only fires for a field named in
    `update_fields`, and there is no field to name.
    """
    Prompt.objects.filter(pk=locked.pk).update(updated_at=timezone.now())


def _recount(locked):
    """Set `game_count` from the rows rather than nudging it.

    Recomputed, so an existing error is corrected instead of carried forward.
    `PositiveIntegerField` is a DB CHECK on Postgres, so a counter that drifted high would eventually
    raise IntegrityError out of a `-1` instead of the PromptError a caller is catching.

    NOT self-healing in general: it only runs from inside the two pool writers, so a Concept deleted
    by cascade with no service involved still leaves this high until the next add or remove. Same
    honest limit `game_list_service._recount` records.
    """
    Prompt.objects.filter(pk=locked.pk).update(
        game_count=PromptGame.objects.filter(prompt=locked).count(),
        updated_at=timezone.now(),
    )


def _recount_placements(response_ids):
    """Repair `placement_count` on responses that lost placements to a cascade.

    ONE STATEMENT, narrowed to the responses actually affected -- which is not the same as bounded,
    and calling it bounded is how somebody stops measuring. Nothing caps responses per prompt, so this
    still touches N rows for N affected answers; a query-count test sees one query either way and
    cannot tell the two apart. The narrowing makes the cost proportional to the edit instead of to the
    prompt's popularity, and that is all it does.
    """
    if not response_ids:
        return
    from django.db.models import Count, OuterRef, Subquery
    from django.db.models.functions import Coalesce

    from prompts.models import PromptPlacement

    PromptResponse.objects.filter(pk__in=response_ids).update(
        placement_count=Coalesce(
            Subquery(
                PromptPlacement.objects.filter(response=OuterRef('pk'))
                .values('response').annotate(c=Count('pk')).values('c')[:1]
            ),
            0,
        )
    )
