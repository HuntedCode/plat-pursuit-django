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
#: members author more. Read ONLY by `prompt_service.max_prompts_for`, so there is one enforcement
#: point.
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
#: IMMUTABLE ONCE CREATED. `prompt_service.update_prompt` takes no `shape` argument at all. Changing a
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
#: The service validates against this, so an unknown shape cannot reach the column from the API.
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
MAX_BUCKETS_PER_PROMPT = {
    SHAPE_TIER: 8,
    SHAPE_GRID: 12,
    SHAPE_POLL: 1,
}
#: The abuse bound tier's unlimited-per-bucket buckets otherwise lack. A response cannot hold more
#: placements than the pool holds games in any case (`unique(response, prompt_game)` sees to that), so
#: this is the same number as the largest pool rather than an independent policy.
MAX_PLACEMENTS_PER_RESPONSE = MAX_GAMES_PER_PROMPT[SHAPE_TIER]

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

        `profile` may be None (a signed-out reader), which must not be allowed to match rows whose
        `owner_id` is somehow null -- hence the explicit branch rather than `Q(owner=profile)`.
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

    The alternative was to AND the parent's visibility into every response read. That is a
    multi-column predicate which cannot ride a partial index, so it would have made every response
    read slower forever in exchange for a case that a refusal handles at write time. An author who has
    not been answered yet can still delete freely; moderation can remove anything.
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
        ordering = ['-updated_at']
        indexes = [
            # "my prompts", newest first. Partial on the visible predicate so deleted rows are not in
            # the index at all rather than being scanned and discarded.
            models.Index(fields=['owner', '-updated_at'], name='prm_owner_idx',
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
        ]

    def __str__(self):
        return f'{self.title} ({self.get_shape_display()})'

    def clean(self):
        if not self.title.strip():
            raise ValidationError({'title': 'A prompt needs a title.'})

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

    A POLL'S BUCKET IS A REAL ROW, and that is a database decision rather than a tidiness one. The
    obvious alternative is a nullable `PromptPlacement.bucket` for polls -- but Postgres treats NULLs
    as DISTINCT in a unique index, so `unique(response, bucket)` would not constrain two null-bucket
    rows and the one-pick guarantee would evaporate at exactly the shape that needs it most. The
    service creates the bucket when the poll is created, names it, and the author never sees it.

    Every read path is then shape-blind: buckets exist, placements have one, and no template branches
    on "is this the synthetic one".
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
        ordering = ['-updated_at']
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
            # "responses I have written", for a hunter's own page.
            models.Index(fields=['profile', '-updated_at'], name='prmresp_profile_idx'),
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

    Corollary worth stating because it saves a whole branch: this table has no Concept FK, so
    `Concept.absorb()` needs no branch for it. Only `PromptGame.concept` touches Concept.
    """

    response = models.ForeignKey(PromptResponse, on_delete=models.CASCADE, related_name='placements')
    prompt_game = models.ForeignKey(PromptGame, on_delete=models.CASCADE, related_name='placements')
    #: CASCADE and NOT NULL, which looks like it contradicts `GameListItem.section = SET_NULL` and does
    #: not. SET_NULL exists there so that deleting a section never deletes GAMES. Here the game is not
    #: deleted -- it sits untouched in the pool and the card simply returns to the tray. A placement is
    #: an association, not the content. A null bucket would belong nowhere in a tally, and for a poll
    #: it would be a second unconstrained pick, because unique indexes ignore NULLs.
    bucket = models.ForeignKey(PromptBucket, on_delete=models.CASCADE, related_name='placements')
    #: Denormalized from `prompt.shape` at insert, and safe ONLY because shape is immutable. It is the
    #: only way to say "one game per bucket for a grid and a poll, unlimited for a tier row" as a
    #: database constraint, since the rule lives on the grandparent. Write-once: the service sets it
    #: and nothing updates it.
    single_slot = models.BooleanField()
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
        #: The view fetches buckets once and groups in Python over the already-fetched placements,
        #: the way `GameListDetailView._grouped` does, bounded by the caps.
        ordering = ['position']
        constraints = [
            # ONE BUCKET PER GAME, always. Obvious for a tier list. For a grid it is a real product
            # call -- "Elden Ring wins Best Combat AND Best Story" -- and it is refused, because the
            # tray is defined as `pool - placements`: if a game can sit in two slots then a card is
            # simultaneously placed and unplaced, the tray stops being derivable, and the drag needs a
            # copy affordance. That is a different editor. The asymmetry decides it: dropping this
            # constraint later is a one-line migration, adding it after four hundred people have
            # answered is not.
            models.UniqueConstraint(fields=['response', 'prompt_game'],
                                    name='promptplacement_unique_game'),
            # ONE GAME PER BUCKET, for the shapes that promise it. Partial on the denormalized flag
            # because the condition lives two tables up.
            models.UniqueConstraint(fields=['response', 'bucket'], condition=Q(single_slot=True),
                                    name='promptplacement_single_slot'),
        ]
        indexes = [
            # The editor's read: every placement of one response, grouped by bucket.
            models.Index(fields=['response', 'bucket', 'position'], name='prmplace_editor_idx'),
            # The tally's GROUP BY: per bucket, per game, how many responses placed it there.
            models.Index(fields=['bucket', 'prompt_game'], name='prmplace_tally_idx'),
        ]

    def __str__(self):
        return f'{self.prompt_game.concept.unified_title} -> {self.bucket.label}'


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
