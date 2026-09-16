"""Tiers, Grids & Polls (2026-09).

One hunter authors a PROMPT -- a set of games plus a structure to place them in -- and other hunters
each author their own RESPONSE. The response is the point: it is a statement of taste, it is
shareable, and comparing responses is the engagement.

WHY THIS IS NOT A GAME LIST TYPE, since it was very nearly one. A list type has ONE rendering and
every viewer sees identical bytes; here the arrangement itself is authored by the viewer. Putting it
in `GameList.list_type` would have meant every read path in Lists asking "canonical, or mine?", and
the paths that forgot would have silently shown the wrong one. See
docs/design/game-list-types.md#the-test-a-type-has-to-pass.

THREE SHAPES, ONE ENGINE. A tier list is ordered labelled buckets holding many games each; a grid is
labelled slots holding one game each; a poll is one bucket holding one pick, where the game set is
the options. What actually differs between them is how many buckets there are and whether a bucket
holds one game or many -- so they are one set of tables, one editor and one browse page with a
switcher, not three features.

WHAT THE SHAPE DOES NOT DO is get derived. `shape` is a stored, validated, IMMUTABLE column. Deriving
it from "one bucket that holds one game" is how a two-option poll becomes a grid nobody asked for,
and the URL router would have to ask a question needing two joins to answer.

THE ONE IDEA WORTH READING THE REST FOR: a placement points at the POOL ROW, not at the Concept. That
single choice is what makes "the author edits a live prompt" safe, and it is the fix for a bug this
codebase already shipped once -- see `PromptPlacement`.
"""
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q

from trophies.models import Concept, Profile

#: HOW MANY PROMPTS ONE HUNTER MAY AUTHOR. Same shape as the list caps next door: everyone authors,
#: members author more. To be read ONLY by `prompt_service.max_prompts_for` when P1 lands, so the
#: tier rule has exactly one enforcement point. Nothing reads it yet -- this is an obligation on the
#: service, not a description of one.
#:
#: The free number does not bind anybody on day one, because the whole system ships behind a member +
#: staff beta gate and every hunter who can reach the author tools is already a member. It is written
#: now anyway: the gate is designed to be DELETED, and the day it goes is the day a cap that was never
#: written becomes an incident rather than a decision.
FREE_MAX_PROMPTS = 3
MEMBER_MAX_PROMPTS = 25

#: What a prompt IS, and unlike `GameList.list_type` this one is not merely presentation -- it decides
#: how many buckets are allowed, whether a bucket holds one game or many, and which of the two unique
#: constraints on a placement applies. It is therefore the single source of truth for those rules, and
#: `PromptPlacement.single_slot` is denormalized from it.
#:
#: IMMUTABLE ONCE CREATED, which the service must hold by offering no way to change it --
#: `update_prompt` is to take no `shape` argument at all. Nothing enforces this yet. Changing a
#: tier list into a grid would leave buckets holding seven games under a one-game promise, and would
#: make `single_slot` -- written at insert, on rows belonging to other people -- lie everywhere at
#: once, with no non-arbitrary way to pick which placement survives. Immutability costs nothing today
#: and is impossible to impose after launch. If switching is ever wanted, it is "duplicate as a new
#: prompt", which is an honest different thing.
SHAPE_TIER = 'tier'
SHAPE_GRID = 'grid'
SHAPE_POLL = 'poll'
SHAPE_CHOICES = [
    (SHAPE_TIER, 'Tier list'),
    (SHAPE_GRID, 'Grid'),
    (SHAPE_POLL, 'Poll'),
]
#: The service is to validate against this so an unknown shape cannot reach the column from the API.
#: The CheckConstraint below is what holds today, and it holds against every writer.
SHAPES = frozenset(value for value, _ in SHAPE_CHOICES)

#: Shapes whose buckets hold exactly ONE game. This is the rule `single_slot` carries down to the
#: database, and it lives here rather than as a `capacity` column on the bucket: a capacity column
#: would be a second source of truth that can contradict `shape` while enforcing nothing `shape` does
#: not already. (A real per-bucket capacity -- "S-tier holds at most three" -- is a different feature
#: and can add that column when somebody asks for it.)
SINGLE_SLOT_SHAPES = frozenset({SHAPE_GRID, SHAPE_POLL})

#: One line per shape, for the pickers. Beside the choices rather than in the templates that render
#: them, so a shape cannot ship with a label and no explanation, and so the create dialog and the
#: browse tabs cannot describe the same shape differently. Says what the hunter GETS.
SHAPE_BLURBS = {
    SHAPE_TIER: 'Rank them into rows, S down to D.',
    SHAPE_GRID: 'Labelled slots. One game each.',
    SHAPE_POLL: 'One question. Everybody picks one.',
}


def shape_options():
    """`(value, label, blurb)` for a picker, so a template never hardcodes the set."""
    return [(value, label, SHAPE_BLURBS[value]) for value, label in SHAPE_CHOICES]


#: Field lengths, defined ONCE and read by the column, the service and the form's counter. The list
#: app's comment on this is worth not repeating in full: a live "118/120" that disagrees with what the
#: server accepts is worse than no counter, because it is confidently wrong.
#:
#: TWO OF THE THREE ARE DB-ENFORCED. `TITLE_MAX_LENGTH` and `LABEL_MAX_LENGTH` sit on CharFields and
#: become `varchar(n)`. `DESCRIPTION_MAX_LENGTH` sits on a TextField, which Django emits as a bare
#: `text` column and bounds in FORM validation only -- so a 50 KB description is insertable from the
#: shell, the admin or a data migration, which are precisely the three writers the check constraints
#: below exist to catch. House style (`GameList.description` is the same), and the service enforces
#: it; recorded because the sentence above would otherwise imply a column that bounds it.
#: Sized against where each renders. A title sits in a browse tile and line-clamps. A bucket label
#: sits in a narrow row header beside a count, at 375px, which is tighter than a list's section header
#: because a tier row's label column is a fixed strip down the left.
TITLE_MAX_LENGTH = 60
DESCRIPTION_MAX_LENGTH = 300
LABEL_MAX_LENGTH = 24

#: HOW BIG A PROMPT GETS, per shape, because the shapes differ by an order of magnitude and one number
#: would be wrong for two of them. A 200-option poll is not a poll; a 20-game tier list is a fine tier
#: list but a mean ceiling.
#:
#: FLAT WITHIN EACH SHAPE, never tiered. This is abuse prevention, and abuse prevention must not be
#: purchasable -- a spam limit somebody can pay to raise is not a spam limit. The tiering lives on
#: prompt COUNT above, where it says the honest thing.
MAX_GAMES_PER_PROMPT = {
    SHAPE_TIER: 200,
    SHAPE_GRID: 60,
    SHAPE_POLL: 20,
}
#: A bucket is a rendered row or slot with its own header. Tier lists in the wild run five to seven
#: (S/A/B/C/D plus F and a joke tier); a grid is a 3x3 or a 4x3; a poll has exactly one, created by
#: the service and never by the author.
#:
#: THE POLL ENTRY IS NOT A CAP, IT IS HALF OF A GUARANTEE. `unique(response, bucket) WHERE
#: single_slot` allows one placement PER BUCKET, so "a hunter votes once" holds only while a poll has
#: exactly one bucket. A second bucket on a poll buys a second vote with every flag set correctly.
#: The service must never let an author add one, and P1's tests must say so -- the database cannot,
#: because the count lives on the parent.
MAX_BUCKETS_PER_PROMPT = {
    SHAPE_TIER: 8,
    SHAPE_GRID: 12,
    SHAPE_POLL: 1,
}
#: The abuse bound tier's unlimited-per-bucket buckets otherwise lack. Sized to the largest pool,
#: because a response placing every game in the biggest allowed prompt is the honest ceiling.
#:
#: An earlier version of this comment said the bound followed for free from
#: `unique(response, prompt_game)`. It does not: that constraint counts DISTINCT POOL ROWS, and
#: nothing in the schema ties a placement's pool row to its response's prompt (see
#: `PromptPlacement`). The bound is a service obligation like the others here, not a consequence.
MAX_PLACEMENTS_PER_RESPONSE = MAX_GAMES_PER_PROMPT[SHAPE_TIER]

#: HOW MANY GAMES BEFORE IT CAN BE PUBLISHED, per shape. Enforced at the PUBLISH transition and never
#: at save: an author builds up to it, and a draft may sit at one game for as long as they like.
#:
#: Five for a tier list (owner's call): one per seeded row, and low enough that a six-entry franchise
#: tier list is still legal. The floor exists to stop "two games in S" reaching the browse page, not
#: to curate taste.
#:
#: Two for a poll, because a poll with one option is not a question.
#:
#: ZERO FOR A GRID, and that is the interesting one -- a grid may legitimately ship with no pool at
#: all. See `Prompt.allow_duplicates` and `PromptPlacement.concept`: an empty pool means respondents
#: search the whole catalogue for each slot, which is the shape the grid is really for. A grid's floor
#: is on its SLOTS instead, and lives in the service.
MIN_GAMES_TO_PUBLISH = {
    SHAPE_TIER: 5,
    SHAPE_GRID: 0,
    SHAPE_POLL: 2,
}

#: Bucket colours, as PALETTE SLOTS rather than free hex. The tier-list genre has its own convention
#: (S is red, F is grey) and hunters expect it, but free hex is a UGC surface that needs validating
#: and can paint unreadable text over the design tokens. The CSS maps each slot to a `--pp-*` derived
#: colour, so a theme change moves them all.
#: Blank is a real value and the default: a grid slot and a poll have no use for a tier colour.
BUCKET_COLOUR_CHOICES = [
    ('', 'None'),
    ('red', 'Red'),
    ('orange', 'Orange'),
    ('yellow', 'Yellow'),
    ('green', 'Green'),
    ('blue', 'Blue'),
    ('purple', 'Purple'),
    ('grey', 'Grey'),
]
BUCKET_COLOURS = frozenset(value for value, _ in BUCKET_COLOUR_CHOICES)

#: Grid geometry. Read only when `shape == SHAPE_GRID`; a tier list is always one bucket per row and a
#: poll is one bucket. Bounded in the database as well as the service because it is arithmetic the
#: template divides by.
MIN_GRID_COLUMNS = 1
MAX_GRID_COLUMNS = 6


class PromptQuerySet(models.QuerySet):
    """Reads that cannot forget a flag. Lifted from `GameListQuerySet`, deliberately unchanged.

    `visible()` is the floor: a soft-deleted prompt is not "a prompt you have to remember to filter
    out", it is not a prompt. `public()` adds the privacy rule and is the ONLY supported way to read
    somebody else's. Both mirror a partial index predicate exactly.
    """

    def visible(self):
        return self.filter(is_deleted=False)

    def public(self):
        return self.visible().filter(is_public=True)

    def owned_by(self, profile):
        return self.visible().filter(owner=profile)

    def readable_by(self, profile):
        """Public prompts, plus your own private ones. The one read a detail page needs.

        The `None` branch is kept for symmetry with `GameListQuerySet`, which is the file this one
        is lifted from, and because reading `readable_by(None)` as "the public ones" at the call site
        beats reading it as an OR against a null. It is NOT load-bearing here and the comment it
        replaces claimed it was: `owner` is NOT NULL, and `Q(owner=None)` compiles to `IS NULL`, which
        matches nothing on such a column -- so the hazard it described was unreachable twice over.
        """
        if profile is None:
            return self.public()
        return self.visible().filter(Q(is_public=True) | Q(owner=profile))

    def of_shape(self, shape):
        """One tab of the browse switcher. Pairs with `prm_public_shape_idx`."""
        return self.filter(shape=shape)


class PromptManager(models.Manager.from_queryset(PromptQuerySet)):
    """Deliberately NOT filtering in `get_queryset`, for the reasons `GameListManager` records.

    A default manager that hides soft-deleted rows looks safer and is worse: `objects` then lies about
    what is in the table, admin and the undelete path both need a second manager, and a cascade or a
    `count()` silently disagrees with the database.
    """


class Prompt(models.Model):
    """A question somebody asks with games: a pool, a structure, and a shape.

    CLOSING IS NOT UNPUBLISHING, and the difference is the answer to "what happens to four hundred
    responses when the author changes their mind". Once a prompt carries a response from somebody
    else it can be CLOSED -- no new responses -- but it cannot be unpublished or deleted, because a
    response is unreadable without the structure it was placed into, and hiding the prompt would
    silently orphan work that is not the author's to withdraw.

    The alternative was to AND the parent's visibility into every response read. The cost is that
    the parent's flags live on ANOTHER TABLE, so that is a join predicate rather than an index
    predicate: no partial index on `PromptResponse` can carry it, and every response read pays a join
    forever in exchange for a case a refusal handles once at write time. (An earlier version of this
    comment said "a multi-column predicate cannot ride a partial index", which is simply false -- the
    partial indexes on this very model have two-column conditions. The table boundary is the problem,
    not the column count.)

    An author who has not been answered yet can still delete freely; moderation can remove anything;
    and account deletion overrides this entirely, because the rule protects responders from an
    author's change of mind, not from an author's departure.
    """

    owner = models.ForeignKey(
        Profile,
        on_delete=models.CASCADE,
        related_name='prompts',
        help_text='The hunter who set it. Prompts do not have co-authors.',
    )
    #: See SHAPE_CHOICES. Immutable after creation -- the service offers no way to change it.
    shape = models.CharField(max_length=16, choices=SHAPE_CHOICES)
    title = models.CharField(max_length=TITLE_MAX_LENGTH)
    description = models.TextField(max_length=DESCRIPTION_MAX_LENGTH, blank=True, default='')
    #: Grid only. Stored rather than derived from the bucket count because "nine slots" is a different
    #: promise laid out 3x3 than 1x9, and the author picks.
    grid_columns = models.PositiveSmallIntegerField(default=3)

    is_public = models.BooleanField(
        default=False,
        help_text='Opt-IN. A prompt is private until its author publishes it.',
    )
    #: GRID ONLY, and the one rule the shipped schema had to be reopened for.
    #:
    #: `PromptPlacement` used to carry an UNCONDITIONAL `unique(response, prompt_game)` -- one bucket
    #: per game, always -- and this file argued for refusing "Elden Ring wins Best Combat AND Best
    #: Story" on the grounds that the tray must stay derivable as `pool - placements`. The owner's
    #: call overrides that: on a grid, naming the same game twice is the point, so the constraint
    #: became conditional and the tray became "everything, minus nothing" for an open grid.
    #:
    #: A NEW GRID ALLOWS THEM, because a grid asking nine questions usually wants nine independent
    #: answers. Turning it off is a deliberate "each slot gets a different game", and the service
    #: refuses to turn it off unless the pool can actually fill every slot -- otherwise the author
    #: ships a grid nobody can complete.
    #:
    #: THE COLUMN DEFAULTS TO FALSE AND THE GRID'S DEFAULT LIVES IN `create_prompt`, which looks
    #: backwards and is not. `prompt_duplicates_grid_only` below requires this to be False on every
    #: shape that has no such setting, so a column default of True would make `Prompt.objects.create()`
    #: violate a check constraint for a tier list or a poll -- i.e. the admin, the shell and every data
    #: migration, which are exactly the writers that constraint exists for. The default belongs to the
    #: one shape that has the setting, so it lives at that shape's door.
    #:
    #: Read by nothing on a tier list or a poll, which forbid duplicates unconditionally. See
    #: `forbids_duplicates`.
    allow_duplicates = models.BooleanField(
        default=False,
        help_text='Grids only: may one game be used in more than one slot?',
    )

    #: Answered but finished with. Closing hides nothing and deletes nothing: every response stays
    #: readable, and the prompt stays on its own page. It only refuses NEW responses.
    is_closed = models.BooleanField(
        default=False,
        help_text='No new responses. Existing ones stay readable and their authors may still edit.',
    )

    #: Denormalized, maintained by the service. Never written by hand: the browse grid sorts on them,
    #: so a drifted count silently reorders the page.
    #: `response_count` is the popularity signal that matters here -- a prompt is good if people
    #: answered it -- so it is what the default browse sort reads.
    game_count = models.PositiveIntegerField(default=0)
    response_count = models.PositiveIntegerField(default=0)
    like_count = models.PositiveIntegerField(default=0)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    is_deleted = models.BooleanField(default=False)
    deleted_at = models.DateTimeField(null=True, blank=True)

    objects = PromptManager()

    class Meta:
        #: `-created_at`, NOT `-updated_at`, and the difference is the whole reason this line has a
        #: comment. `Meta.ordering` applies to every query that does not name its own sort, so it
        #: decides which index an un-ordered read can use -- and none of the indexes below contains
        #: `updated_at`. `Prompt.objects.public().of_shape(SHAPE_TIER)`, which is exactly what the
        #: browse does, would have sorted by a column no index carries: a bitmap scan plus a full
        #: external sort, on the page whose three indexes were added to prevent that, and invisible to
        #: every functional test. Aligning the default with `prm_public_new_idx` means the lazy read
        #: is the indexed one. Browse still names its sort explicitly; this is the floor, not the plan.
        ordering = ['-created_at']
        indexes = [
            # "my prompts", newest first. Partial on the visible predicate so deleted rows are not in
            # the index at all rather than being scanned and discarded. Ordered to match the default
            # above, so an un-ordered `owned_by()` rides it too.
            models.Index(fields=['owner', '-created_at'], name='prm_owner_idx',
                         condition=Q(is_deleted=False)),
            # THE TAB SWITCHER, and the reason this index leads with `shape` where the list app's
            # equivalent has no such column. Every browse read here is scoped to one shape -- the
            # three URLs are three shapes -- so a shape-blind index would be filtered after the sort
            # on every page. Fine at two hundred prompts, not at twenty thousand.
            models.Index(fields=['shape', '-response_count', '-created_at'],
                         name='prm_public_shape_idx',
                         condition=Q(is_deleted=False, is_public=True)),
            models.Index(fields=['shape', '-like_count', '-created_at'],
                         name='prm_public_liked_idx',
                         condition=Q(is_deleted=False, is_public=True)),
            models.Index(fields=['shape', '-created_at'], name='prm_public_new_idx',
                         condition=Q(is_deleted=False, is_public=True)),
        ]
        constraints = [
            # The admin, the shell and a data migration all write around the service, and an untitled
            # prompt is a question nobody asked. Catches `''` only: a whitespace-only title still
            # passes here, which is what `clean()` and the service's strip are for.
            models.CheckConstraint(condition=~Q(title=''), name='prompt_title_not_blank'),
            # `choices` is a form and admin concern; Postgres does not enforce it. A shape written
            # from a shell that no template can draw leaves the page with nothing to render AND no UI
            # path back, and because shape is immutable there is no way to correct it in product.
            models.CheckConstraint(condition=Q(shape__in=[v for v, _ in SHAPE_CHOICES]),
                                   name='prompt_shape_valid'),
            # Arithmetic the grid template divides by. Zero columns is a division by zero and a
            # hundred is a page nobody can read.
            models.CheckConstraint(
                condition=Q(grid_columns__gte=MIN_GRID_COLUMNS,
                            grid_columns__lte=MAX_GRID_COLUMNS),
                name='prompt_grid_columns_sane'),
            # GRID-ONLY, said in the database. `forbids_duplicates` short-circuits on shape, so a
            # stored `True` on a poll was inert -- but it reads backwards to anyone looking at the
            # table or an admin form ("this poll allows duplicates" is the opposite of what a poll
            # promises), and it becomes a live bug across two shapes the day somebody writes
            # `if prompt.allow_duplicates:` instead of `if not prompt.forbids_duplicates:`.
            models.CheckConstraint(
                condition=Q(shape=SHAPE_GRID) | Q(allow_duplicates=False),
                name='prompt_duplicates_grid_only'),
        ]

    def __str__(self):
        return f'{self.title} ({self.get_shape_display()})'

    def clean(self):
        if not self.title.strip():
            raise ValidationError({'title': 'A prompt needs a title.'})

    @property
    def forbids_duplicates(self):
        """May one game appear in two of this prompt's buckets, within one answer?

        THE SECOND AXIS, and it is genuinely not the same question as `is_single_slot`. That one asks
        how many games a BUCKET holds; this asks how many buckets a GAME may occupy. A grid answers
        "one" to the first and (by default) "as many as you like" to the second, which is why one
        capacity number could never have expressed both.

        Tier and poll forbid duplicates unconditionally: a game in two tiers is a contradiction and a
        poll picks one thing.
        """
        return self.shape != SHAPE_GRID or not self.allow_duplicates

    @property
    def is_single_slot(self):
        """Do this prompt's buckets hold exactly one game? Written onto every placement it takes."""
        return self.shape in SINGLE_SLOT_SHAPES


class PromptGame(models.Model):
    """One game in a prompt's pool -- the set every response draws from.

    KEYED ON CONCEPT, NOT ON `Game`, for the reason the list app's item model records: the site's word
    "game" is the Concept (the work), while `Game` is one platform's trophy list. A poll offering
    "Elden Ring (PS4)" and "Elden Ring (PS5)" as separate options is a bug, not a choice.

    THE POOL IS THE AUTHOR'S, THE PLACEMENTS ARE NOT. Removing a row here drops that game out of every
    existing response, by cascade -- see `PromptPlacement`. That is the intended behaviour and it is
    why this table is the thing placements point at.
    """

    prompt = models.ForeignKey(Prompt, on_delete=models.CASCADE, related_name='games')
    concept = models.ForeignKey(Concept, on_delete=models.CASCADE, related_name='prompt_entries')
    #: 0-indexed and DENSE, compacted by the service on removal. It matters for the same reason it
    #: does on a list: the browse tile's cover mosaic is a bounded prefetch (`position__lt=4`), so a
    #: gap renders a three-cover mosaic on a four-game prompt.
    position = models.PositiveIntegerField(default=0)
    added_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['position']
        constraints = [
            # One row per game per prompt. A pool with the same game twice is a poll you can vote for
            # twice, and it makes the tray arithmetic (`pool - placements`) ambiguous.
            models.UniqueConstraint(fields=['prompt', 'concept'], name='promptgame_unique_concept'),
        ]
        indexes = [
            models.Index(fields=['prompt', 'position'], name='prmgame_position_idx'),
        ]

    def __str__(self):
        return f'{self.concept.unified_title} in {self.prompt.title}'


class PromptBucket(models.Model):
    """A tier row, a grid slot, or a poll's single answer box.

    A POLL'S BUCKET IS A REAL ROW. The reason is that every read path is then shape-blind -- buckets
    exist, placements have one, and no template, tally or editor branches on "is this the synthetic
    one". The service creates it when the poll is created, names it, and the author never sees it.

    The obvious alternative is a nullable `PromptPlacement.bucket` for polls, and it would ALSO want
    `unique(response, bucket)` to hold across nulls, which it does not by default -- Postgres treats
    NULLs as distinct in a unique index. Stated honestly, because an earlier version of this comment
    rested the whole decision on that: it is one kwarg away (`nulls_distinct=False`, Postgres 15 and
    Django 5.0+, both of which this project is on), so it is a wrinkle in the alternative rather than
    what kills it. The shape-blind reads are what kill it.
    """

    prompt = models.ForeignKey(Prompt, on_delete=models.CASCADE, related_name='buckets')
    label = models.CharField(max_length=LABEL_MAX_LENGTH)
    #: A palette slot, not free hex. See BUCKET_COLOUR_CHOICES. Blank is normal.
    colour = models.CharField(max_length=16, choices=BUCKET_COLOUR_CHOICES, blank=True, default='')
    #: 0-indexed and dense. For a TIER prompt this is a rank and carries meaning -- it is what a
    #: future consensus view would average. For a grid it is reading order and means nothing of the
    #: kind, which is worth knowing before somebody ships "average tier" across all three shapes.
    position = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['position']
        constraints = [
            # An unlabelled bucket is a header with nothing in it. Required even for a poll, because a
            # CheckConstraint cannot see the parent's `shape` and so "blank only for polls" is not
            # expressible here; the service names the poll's bucket instead.
            models.CheckConstraint(condition=~Q(label=''), name='promptbucket_label_not_blank'),
        ]
        indexes = [
            models.Index(fields=['prompt', 'position'], name='prmbucket_position_idx'),
        ]

    def __str__(self):
        return f'{self.label} in {self.prompt.title}'


class PromptResponse(models.Model):
    """One hunter's answer to one prompt. At most one per hunter, ever.

    PUBLIC BY DEFAULT, which is the opposite of `GameList.is_public` and the divergence is deliberate:
    a list is a shelf you may choose to show people, whereas a response is a REPLY to somebody's
    question, and answering is the act of publishing. Private stays available for the hunter who just
    wanted to sort these for themselves.

    NO SOFT DELETE, and no prose either -- those two facts hold each other up. A response carries zero
    user-written words: no title, no description, no per-placement note. That is what lets the whole
    response surface skip `_clean_text`, the banned-word check and the `all_ugc` restriction gate on
    placing (liking is still gated; authoring a prompt certainly is). It is the largest simplification
    in the feature and it disappears the moment one text field is added here, so: don't.

    Given no prose, a soft delete buys nothing and costs something -- `unique(prompt, profile)` would
    make re-answering an IntegrityError against an invisible row rather than an insert.
    """

    prompt = models.ForeignKey(Prompt, on_delete=models.CASCADE, related_name='responses')
    profile = models.ForeignKey(Profile, on_delete=models.CASCADE, related_name='prompt_responses')
    is_public = models.BooleanField(
        default=True,
        help_text='Answering is publishing. Unset it to keep an arrangement to yourself.',
    )

    #: THE NUMERATOR ONLY, and never a percentage. Completeness is computed at render -- against the
    #: pool for a tier list, against the bucket count for a grid, and trivially 1 for a poll -- because
    #: those denominators are the author's and they MOVE. `UserChecklistProgress` stored the
    #: percentage, the author then edited the denominator, and the stored number lied forever; a
    #: responder could exceed 100%. Recomputed from rows by the service, never nudged.
    placement_count = models.PositiveIntegerField(default=0)
    like_count = models.PositiveIntegerField(default=0)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        #: Same reasoning as `Prompt.Meta.ordering`: none of the indexes below carries `updated_at`,
        #: so defaulting to it would make every un-ordered read of a prompt's responses sort a column
        #: nothing indexes.
        ordering = ['-created_at']
        constraints = [
            # One response per hunter per prompt. The whole social model rests on this: "my answer" is
            # a thing that can be linked to, updated and compared, not a stream of attempts.
            models.UniqueConstraint(fields=['prompt', 'profile'], name='promptresponse_unique'),
        ]
        indexes = [
            # The three response-browse sorts, all scoped to one prompt and all partial on the
            # predicate the public read uses.
            models.Index(fields=['prompt', '-like_count'], name='prmresp_liked_idx',
                         condition=Q(is_public=True)),
            models.Index(fields=['prompt', '-placement_count'], name='prmresp_filled_idx',
                         condition=Q(is_public=True)),
            models.Index(fields=['prompt', '-created_at'], name='prmresp_new_idx',
                         condition=Q(is_public=True)),
            # "responses I have written", for a hunter's own page. Ordered to match the default.
            models.Index(fields=['profile', '-created_at'], name='prmresp_profile_idx'),
        ]

    def __str__(self):
        return f'{self.profile.display_psn_username} on {self.prompt.title}'


class PromptPlacement(models.Model):
    """One game, in one bucket, in one response.

    IT POINTS AT THE POOL ROW, NOT AT THE CONCEPT, and this is the most load-bearing line in the app.

    The integrity a response needs is against the PROMPT'S POOL, not against the catalogue. When an
    author removes a game, every placement of it must go -- in every response, including four hundred
    belonging to other people. With a `prompt_game` FK that is a cascade: the database does it, and
    there is nobody left to forget.

    A `Concept` FK would look equally "proper" and would reproduce a bug this codebase has already
    shipped. `UserChecklistProgress` stored bare item ids in a JSONField, so an author's deletion left
    dangling ids in every responder's record that still rendered and still counted, and nothing ever
    pruned them. A real FK to Concept fixes the dangling reference and NOT the actual problem, because
    the Concept is still there -- it is the author's pool row that went away.

    THAT USED TO SAVE A WHOLE BRANCH, and no longer does. This table had no Concept FK at all, so
    `Concept.absorb()` needed nothing for it -- until open grids shipped and `concept` above arrived.
    Both columns now have absorb branches, and they dedup differently: the pool one against
    `unique(response, prompt_game)`, the free pick against a `unique(response, concept)` that is
    PARTIAL on `no_duplicates`. Recorded as a correction rather than edited away, because "no branch
    needed" is exactly the kind of note that stays believed after it stops being true.

    WHAT THE SCHEMA DOES **NOT** HOLD, said here rather than discovered later. Nothing ties these
    three foreign keys to the same prompt: a row whose `response` answers prompt A, whose
    `prompt_game` belongs to prompt B and whose `bucket` belongs to prompt C satisfies every
    constraint on this table. Such a row is invisible to the editor (it groups by the prompt's own
    buckets) while still being counted by `placement_count` -- which is the checklist failure mode
    reached by a different road.

    It is a SERVICE OBLIGATION, and deliberately so: it is the same shape `GameListItem.section`
    already ships with next door (nothing stops a section of another list either), and lists answer it
    by resolving every sub-resource WITHIN its parent rather than by id. Closing it in the database
    would mean a redundant `prompt_id` column here plus composite foreign keys via RunSQL --
    inventing a pattern this codebase does not use anywhere, to guard a write path that has exactly
    one author. P1's service resolves pool rows and buckets through the prompt, never by bare id, and
    its tests say so.
    """

    response = models.ForeignKey(PromptResponse, on_delete=models.CASCADE, related_name='placements')
    prompt_game = models.ForeignKey(PromptGame, on_delete=models.CASCADE, null=True,
                                    blank=True, related_name='placements')
    #: CASCADE and NOT NULL, which looks like it contradicts `GameListItem.section = SET_NULL` and does
    #: not. SET_NULL exists there so that deleting a section never deletes GAMES. Here the game is not
    #: deleted -- it sits untouched in the pool and the card simply returns to the tray. A placement is
    #: an association, not the content. A null bucket would belong nowhere in a tally, and for a poll
    #: it would be a second unconstrained pick, because unique indexes ignore NULLs.
    bucket = models.ForeignKey(PromptBucket, on_delete=models.CASCADE, related_name='placements')
    #: THE FREE PICK, and the reason `prompt_game` had to become nullable.
    #:
    #: An OPEN GRID has no pool: the author sets nine questions and every respondent searches the
    #: whole catalogue for each slot. There is no pool row for such a pick to point at, so it points
    #: at the Concept.
    #:
    #: This does NOT reopen the `UserChecklistProgress` bug that `prompt_game` exists to close. That
    #: bug was a reference whose INTEGRITY TARGET could go away underneath it -- an author deleting a
    #: pool entry and leaving dangling ids that still counted. Here there is no pool to be integral
    #: to, and a Concept does not vanish: it is merged, and `Concept.absorb()` re-points this column
    #: exactly as it re-points `PromptGame.concept`.
    #:
    #: EXACTLY ONE OF THE TWO IS SET, enforced below. A row with both would be two different claims
    #: about the same card; a row with neither is a placement of nothing.
    concept = models.ForeignKey(Concept, on_delete=models.CASCADE, null=True, blank=True,
                                related_name='prompt_placements')
    #: Denormalized from `prompt.shape` at insert, and safe ONLY because shape is immutable. It is
    #: how "one game per bucket for a grid and a poll, unlimited for a tier row" reaches the database
    #: at all, since the rule lives on the grandparent and a CheckConstraint cannot see it.
    #:
    #: WHAT THAT DOES AND DOES NOT BUY, because the first version of this comment overclaimed it. The
    #: database enforces the rule GIVEN the flag; the service owns the flag, and the partial unique's
    #: predicate is per-ROW -- so a single placement written with `single_slot=False` is not merely
    #: unconstrained, it is invisible to the index and will not collide with a correctly-flagged row
    #: beside it. One wrong row is enough to make a poll accept two votes.
    #:
    #: Therefore: `Prompt.is_single_slot` is the ONLY supported source of this value, and the service
    #: is the only writer. Write-once -- nothing updates it, because nothing may.
    single_slot = models.BooleanField()
    #: The other denormalized flag, from `Prompt.forbids_duplicates`, and carried for exactly the same
    #: reason: the rule lives on the grandparent and a CheckConstraint cannot see it. Same warning
    #: too -- the partial uniques below have per-ROW predicates, so one placement written with this
    #: wrong does not collide with the correctly-flagged row beside it.
    #:
    #: Unlike `single_slot` this one is NOT safe by immutability, because `allow_duplicates` can be
    #: toggled. The service therefore refuses to toggle it on a published grid and rewrites every
    #: existing placement when it does toggle -- which is bounded, because an unpublished grid can
    #: only hold answers from its own author.
    #: NO DEFAULT, exactly like `single_slot` above and for the same reason. A default makes exactly
    #: one of the two flags silently writable by anything that forgets it -- and a forgotten flag here
    #: is not a loud error, it is a row the partial index cannot see. `single_slot` was written without
    #: one deliberately; this one had `default=True` only to serve its own migration's backfill, which
    #: is where a one-off default belongs.
    no_duplicates = models.BooleanField()
    #: Order within `(response, bucket)`. Dense, and deliberately NOT global the way
    #: `GameListItem.position` is: neither of the two things that forced global there -- the cover
    #: mosaic's bounded prefetch and the two render-time numbering modes -- has an analogue inside a
    #: response. Meaningful for a tier row, where leftmost reads as best and people will drag it;
    #: always 0 for a grid slot and a poll, which hold one game.
    position = models.PositiveIntegerField(default=0)
    placed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        #: Within a bucket only. No cross-bucket promise: ordering by `bucket_id` is not display
        #: order, and putting `bucket__position` here would force a join on every unordered query.
        #: The view is to fetch buckets once and group in Python over the already-fetched
        #: placements, the way `GameListDetailView._grouped` does, bounded by the caps. (No view exists
        #: yet; this says what the ordering is FOR, so P1 does not add a join to `Meta` to get it.)
        ordering = ['position']
        constraints = [
            # ONE BUCKET PER GAME -- now CONDITIONAL, and the history is worth keeping. This was
            # unconditional, and this file argued for refusing "Elden Ring wins Best Combat AND Best
            # Story" on the grounds that the tray must stay derivable as `pool - placements`. The
            # owner's call overrode it: on a grid that is the point. The prediction in the old comment
            # held exactly -- "dropping this constraint later is a one-line migration" -- and this is
            # that migration.
            #
            # TWO OF THEM, because a placement has two possible identity columns: a pool row, or a
            # free-picked Concept, each scoped by its own `isnull=False` below.
            #
            # WHAT THEY DO NOT TOGETHER FORBID, said plainly because the sentence that used to sit
            # here claimed otherwise: one response holding the same GAME once as a pool row and once
            # as a free pick. They are two different columns and neither index sees the other's rows.
            # The service keeps that state unreachable by refusing to give an answered open grid a
            # pool at all; the schema does not.
            # `isnull=False` ON EACH, so each index really does hold only the rows that use its
            # column. Without it both indexes carry the WHOLE table: Postgres stores NULLs in a btree
            # and merely treats them as mutually distinct, so every pooled row sat in the concept
            # index and every free pick in the game index, paying full write cost for entries that
            # could never collide. (The comment that used to sit here said unique indexes "ignore
            # NULLs". They do not -- this file gets that right forty lines up, about `nulls_distinct`,
            # and got it wrong here.)
            models.UniqueConstraint(fields=['response', 'prompt_game'],
                                    condition=Q(no_duplicates=True, prompt_game__isnull=False),
                                    name='promptplacement_unique_game'),
            models.UniqueConstraint(fields=['response', 'concept'],
                                    condition=Q(no_duplicates=True, concept__isnull=False),
                                    name='promptplacement_unique_concept'),
            # A placement is of ONE thing. Both set is two claims about one card; neither set is a
            # placement of nothing, which would render as a blank slot nobody can remove.
            models.CheckConstraint(
                condition=(Q(prompt_game__isnull=False, concept__isnull=True)
                           | Q(prompt_game__isnull=True, concept__isnull=False)),
                name='promptplacement_one_identity'),
            # ONE GAME PER BUCKET, for the shapes that promise it. Partial on the denormalized flag
            # because the condition lives two tables up.
            models.UniqueConstraint(fields=['response', 'bucket'], condition=Q(single_slot=True),
                                    name='promptplacement_single_slot'),
        ]
        indexes = [
            # The editor's read: every placement of one response, grouped by bucket.
            models.Index(fields=['response', 'bucket', 'position'], name='prmplace_editor_idx'),
            # The tally's GROUP BY: per bucket, per game, how many responses placed it there.
            # ONE PER IDENTITY COLUMN, and each partial on its own. An open grid's placements all
            # carry a NULL `prompt_game`, so the pooled index's second key held no information for
            # them at all -- it would have paid full write cost to serve a tally it could not answer,
            # on the shape most likely to want one (there is no pool constraining the answers, so the
            # tally is the only aggregate view an open grid has).
            models.Index(fields=['bucket', 'prompt_game'], name='prmplace_tally_idx',
                         condition=Q(prompt_game__isnull=False)),
            models.Index(fields=['bucket', 'concept'], name='prmplace_tally_free_idx',
                         condition=Q(concept__isnull=False)),
        ]

    def __str__(self):
        # BOTH IDENTITIES. `prompt_game` became nullable when open grids shipped, so the old one-liner
        # raised AttributeError on every free pick -- in the admin changelist, in any traceback's
        # repr, and in any message built from the object.
        concept = self.concept or (self.prompt_game.concept if self.prompt_game_id else None)
        name = concept.unified_title if concept else 'an unknown game'
        return f'{name} -> {self.bucket.label}'


class PromptLike(models.Model):
    """A like on the QUESTION. Same shape as `GameListLike`, which is the site's house pattern for a
    vote: target FK, profile FK, a unique pair, and a denormalized counter on the target.

    TWO TABLES RATHER THAN ONE WITH TWO NULLABLE FKS, because likes land on both a prompt and a
    response and those are different acts. There is no GenericForeignKey anywhere in this codebase and
    this is not the place to introduce one; two nullable FKs plus a check constraint is the same
    mistake with more syntax.
    """

    prompt = models.ForeignKey(Prompt, on_delete=models.CASCADE, related_name='likes')
    profile = models.ForeignKey(Profile, on_delete=models.CASCADE, related_name='prompt_likes')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['prompt', 'profile'], name='promptlike_unique'),
        ]
        indexes = [
            models.Index(fields=['profile', '-created_at'], name='prmlike_profile_idx'),
        ]

    def __str__(self):
        return f'{self.profile.display_psn_username} likes {self.prompt.title}'


class PromptResponseLike(models.Model):
    """A like on somebody's ANSWER -- on a site like this, the thing most worth applauding."""

    response = models.ForeignKey(PromptResponse, on_delete=models.CASCADE, related_name='likes')
    profile = models.ForeignKey(Profile, on_delete=models.CASCADE,
                                related_name='prompt_response_likes')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['response', 'profile'],
                                    name='promptresponselike_unique'),
        ]
        indexes = [
            models.Index(fields=['profile', '-created_at'], name='prmresplike_profile_idx'),
        ]

    def __str__(self):
        return f'{self.profile.display_psn_username} likes a response'
