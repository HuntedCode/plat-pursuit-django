"""Game Lists, rebuilt (2026-09).

WHY A SEPARATE APP. The 2019-era models are still in `trophies/models.py` under the same names, and
they have to stay there: they hold every existing list, and the rebuilt system offers a per-list
importer that reads them. Renaming them to make room would have touched thirteen files of code that
is about to be deleted anyway. A new app costs one INSTALLED_APPS line and gives the rebuild its own
namespace, its own service and its own migrations -- the same shape `milestones`, `notifications` and
`fundraiser` already use. `trophies.GameList` is the old one; `gamelists.GameList` is this one.

WHAT IS ACTUALLY DIFFERENT, since "rebuilt" should mean something:

1. **A service owns every write.** The old system had none -- the writes lived inline in twelve API
   views -- which is exactly why lists are the one user-content system on the site with no
   restriction gate. `gamelists/services/game_list_service.py` is the only thing that writes here.

2. **Visibility has ONE supported read path per question.** `.visible()` is the floor, `.public()`
   is how you read somebody else's, `.readable_by()` is how a detail page asks. The old model
   exposed a bare manager and left `is_deleted=False, is_public=True` to be remembered at ~20 call
   sites. Note what this does NOT claim: `objects.all()` still returns soft-deleted rows on purpose
   (see `GameListManager`), so the floor is opt-in and one word long rather than baked into the
   default manager. `owned_by()` and the two explicit browse sorts ride partial indexes;
   `readable_by()` cannot (its OR spans two columns) and neither can a `.public()` read that leans
   on `Meta.ordering` instead of naming its sort -- so a public browse must `.order_by()`
   explicitly, and `readable_by()` should stay bounded to one list or a small page.

3. **Follows exist.** The first social relation on the site -- there is no follow/follower anything
   anywhere else, so this is a new abstraction rather than a borrowed one.

4. **`first_game_image` is gone.** It read like a field and ran a query per call, which is how the
   browse grid got to 23 queries for 20 lists. Covers come from a bounded prefetch in the view.
"""
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q

from trophies.models import Concept, Profile

#: How many LISTS you get. Everyone makes lists; members make more of them -- the same shape as the
#: shipped `sync` perk (everyone syncs, members sync more often) rather than a capability a free
#: hunter cannot reach. Read ONLY by `game_list_service.max_lists_for`, so the tier rule has exactly
#: one enforcement point.
#:
FREE_MAX_LISTS = 3
# 25, not 10: once lists come in several TYPES (collection, ranked, progress, tier), a hunter using
# the feature properly holds far more than ten -- a backlog tracker, a couple of ranked top-tens, a
# tier list per franchise. The free cap stays at 3 deliberately, as spam control rather than as a
# tease; three is enough to see what lists are for.
MEMBER_MAX_LISTS = 25

#: HOW MANY GAMES ONE LIST HOLDS, and it is 200 because that is what one page renders. Owner's call,
#: 2026-09-14, overturning the "no cap" position recorded here since the rebuild.
#:
#: The old reasoning was that the legacy system gave members unlimited games per list, so any ceiling
#: would take a perk back. That argument belonged to the membership system this one replaced, and the
#: owner's word is that no real list ever approached 200 anyway -- so the ceiling costs nobody
#: anything and closes the one surface on the feature with no bound at all.
#:
#: EQUAL TO `views.MAX_ITEMS_RENDERED`, ON PURPOSE, and that equality is the whole design rather than
#: a coincidence to tidy up later. A list longer than one render was a genuinely half-broken thing:
#: it truncated, its section counts were computed from the slice and therefore lied, and it could not
#: be reordered AT ALL (`reorder` refuses a partial ordering by design, so the page had to explain
#: why its own defining feature was unavailable). Making the cap the render bound does not improve
#: that state, it removes it -- and with it a bug family that cost two real defects in one session:
#: `can_arrange` silently inheriting the truncation clause, and the section counts quietly lying.
#:
#: FLAT, NOT TIERED, which is the other half of the decision. This is abuse prevention, and abuse
#: prevention must not be purchasable -- a spam limit somebody can pay to raise is not a spam limit.
#: The tiering lives on list COUNT above, where it says the honest thing: members get more LISTS.
#: Keeping this flat is also what keeps `cap == render bound` true for everybody.
#:
#: Read ONLY by `game_list_service.add_concept`, so the product has exactly one enforcement point.
#: WHAT THAT DOES AND DOES NOT BUY, because the first version of this comment overclaimed it: a check
#: in a service binds callers OF THAT SERVICE. It does not bind a shell doing `bulk_create`, and there
#: is no CheckConstraint behind it (a cap on a row COUNT is not something a table constraint can
#: express). A future importer is bound only if it goes through `add_concept`, which is a thing to
#: make sure of rather than a thing to assume.
#:
#: Which is why the render carries its own bound: `views.MAX_ITEMS_RENDERED` slices regardless, so a
#: row that arrived another way cannot turn a public page into an unbounded render.
#:
#: Enforced on the way IN and never by deletion: a list that is somehow already over the cap keeps
#: every row it has and simply cannot take more. A cap that removes somebody's games is the one
#: version of this worth regretting.
MAX_ITEMS_PER_LIST = 200

#: What a list IS, which here means only how it presents. The rows are identical either way -- a
#: Ranked list is a Collection whose `position` is meant rather than incidental -- so the type is one
#: CharField and switching it loses nothing and needs no migration of items.
#:
#: ONLY THE TWO THAT RENDER ARE HERE. docs/design/game-list-types.md plans five more (Top-N, Progress,
#: Sectioned, Tier, Backlog tracker) and it is tempting to declare them now; declaring a choice that
#: no template can draw is how a hunter picks "Tier" and gets a Collection with a different label.
#: Each arrives with its presentation.
LIST_TYPE_COLLECTION = 'collection'
LIST_TYPE_RANKED = 'ranked'
LIST_TYPE_CHOICES = [
    (LIST_TYPE_COLLECTION, 'Collection'),
    (LIST_TYPE_RANKED, 'Ranked'),
]
#: The service validates against this, so an unknown type cannot reach the column from the API.
LIST_TYPES = frozenset(value for value, _ in LIST_TYPE_CHOICES)

#: One line per type, for the pickers. It lives beside the choices rather than in the two templates
#: that render it, so a new type cannot ship with a label and no explanation -- and so the create
#: dialog and the detail page's switcher cannot describe the same type differently.
#: Says what the hunter GETS, not what the field stores: "sorted however you like" is the difference
#: they can act on, where "list_type=collection" is not.
LIST_TYPE_BLURBS = {
    LIST_TYPE_COLLECTION: 'A shelf. Sort it any way you like.',
    LIST_TYPE_RANKED: 'An order you choose. Drag to arrange, numbered 1 down.',
}


def list_type_options():
    """`(value, label, blurb)` for a picker, so a template never hardcodes the set."""
    return [(value, label, LIST_TYPE_BLURBS[value]) for value, label in LIST_TYPE_CHOICES]

#: Field lengths, defined ONCE and read by the column, the service and the form.
#:
#: They used to be written three times each -- `max_length` on the field, a literal in
#: `_clean_text`, and a hardcoded `maxlength` in the template -- which is fine until a counter shows
#: the number to a hunter. A live "118/120" that disagrees with what the server accepts is worse
#: than no counter, because it is confidently wrong, so the display and the enforcement have to read
#: the same constant by construction rather than by somebody remembering.
#: Sized against where each one RENDERS, not inherited from the old system (which allowed 200/1000).
#: A name lives in a `.pp-gtile__name` inside a grid tile and line-clamps: at 120 it stops being a
#: title and becomes an unreadable block. 60 is two comfortable lines. A description is a subtitle
#: above the games, so 300 is two or three sentences saying why the list exists; 1000 was ~150 words,
#: which pushed the games themselves below the fold on a phone. A note is an annotation beside ONE
#: game, and a hundred of them at 500 characters is a page nobody reads.
NAME_MAX_LENGTH = 60
DESCRIPTION_MAX_LENGTH = 300
NOTE_MAX_LENGTH = 200
#: A section name is a HEADER above a row of cards, not a title -- it sits in a line with a count and
#: the owner's controls, and at 375px that line is ~343px wide. 40 is a comfortable "Currently
#: playing"; 60 (the list's own ceiling) would push the controls onto a second line.
SECTION_NAME_MAX_LENGTH = 40

#: How many sections a list may hold. A section is a rendered header with its own row, so a hundred
#: of them is a page nobody can read. Capped for the same reason list SIZE is (`MAX_ITEMS_PER_LIST`)
#: and, like it, flat for everyone rather than tiered.
#: Twenty-five covers every real shape with room over: Finished/Playing/Someday is three, a tier
#: list is five to seven, one per platform is about six, and one per release year on a long-running
#: series is the shape that wanted more than twenty. Raised from 20 on the owner's call, 2026-09.
#:
#: Raising it is the safe direction and staying under `MEMBER_MAX_LISTS` is a coincidence, not a
#: rule -- the two count different things. What makes 25 free to adopt is that nothing was built on
#: the old number: the cap is enforced in one place (`create_section`, lock-then-count from rows),
#: read from this constant by the message that reports it, and the render is bounded by the cap
#: rather than by a literal. Lowering it later would be the hard direction, because existing lists
#: would be over it.
MAX_SECTIONS_PER_LIST = 25


class GameListQuerySet(models.QuerySet):
    """Reads that cannot forget a flag.

    `visible()` is the floor: a soft-deleted list is not "a list you have to remember to filter
    out", it is not a list. `public()` adds the privacy rule and is the ONLY supported way to read
    somebody else's. Both mirror a partial index predicate exactly.
    """

    def visible(self):
        return self.filter(is_deleted=False)

    def public(self):
        return self.visible().filter(is_public=True)

    def owned_by(self, profile):
        return self.visible().filter(owner=profile)

    def readable_by(self, profile):
        """Public lists, plus your own private ones. The one read a detail page needs.

        `profile` may be None (a signed-out reader), which must not be allowed to match rows whose
        `owner_id` is somehow null -- hence the explicit branch rather than `Q(owner=profile)`.
        """
        if profile is None:
            return self.public()
        return self.visible().filter(Q(is_public=True) | Q(owner=profile))

    def featured(self):
        """Lists eligible for the browse Spotlight, most recently chosen first.

        Built on `public()` rather than `visible()` deliberately: featuring is an editorial act, but
        a list that was un-published or deleted after being featured must drop out of the Spotlight
        on its own. Stacking the predicates here means the page cannot promote a list its own browse
        grid would refuse to show.

        `text_hidden` is NOT in this predicate, and that is the one exception worth stating. A
        moderator hiding a featured list's words leaves it in the band, rendering `HIDDEN_NAME` and
        no description -- exactly as the same list renders in the grid below. Hiding words is not
        un-publishing, `public()` does not filter it anywhere else on the site, and adding it only
        here would make the band disagree with the grid about what exists. The admin refuses to
        newly feature a hidden list, which covers the case that is actually a mistake.

        Ordering lives in the vocabulary, not at the call site, because "the featured list" means
        "the one chosen most recently" everywhere -- see `featured_at`.
        """
        # `-pk` AS A TIEBREAK. Two lists featured inside the same timestamp otherwise resolve by
        # whatever order Postgres felt like, so the Spotlight could flip between page loads -- the
        # same non-determinism `covers._sort_key` documents adding a pk tiebreak to prevent. The
        # admin refuses bulk featuring so it is unlikely, and it is free: the partial index on
        # `-featured_at` still serves the ordering as a prefix.
        return self.public().filter(featured_at__isnull=False).order_by('-featured_at', '-pk')


class GameListManager(models.Manager.from_queryset(GameListQuerySet)):
    """Deliberately NOT filtering in `get_queryset`.

    A default manager that hides soft-deleted rows looks safer and is worse: `objects` then lies
    about what is in the table, admin and the undelete path both need a second manager, and a
    cascade or a `count()` silently disagrees with the database. The floor is opt-in and one word
    long (`.visible()`), and the service is the only writer, so there is one place to be careful.
    """


class GameList(models.Model):
    owner = models.ForeignKey(
        Profile,
        on_delete=models.CASCADE,
        related_name='lists',
        help_text='The hunter who made it. Lists do not have co-owners.',
    )
    name = models.CharField(max_length=NAME_MAX_LENGTH)
    description = models.TextField(max_length=DESCRIPTION_MAX_LENGTH, blank=True, default='')
    #: Presentation only -- see LIST_TYPE_CHOICES. Deliberately NOT indexed: browse does not filter
    #: on it today, and an index on a two-value column over a table this size would be read past
    #: anyway. It gets one when a type filter ships, not before.
    list_type = models.CharField(
        max_length=20,
        choices=LIST_TYPE_CHOICES,
        default=LIST_TYPE_COLLECTION,
        help_text='How the list presents. Switching is lossless: the rows and their order are the '
                  'same either way.',
    )
    is_public = models.BooleanField(
        default=False,
        help_text='Opt-IN. A list is private until its author decides otherwise.',
    )
    #: How a SECTIONED RANKED list numbers itself, and nothing else reads it: a Collection has no
    #: numerals and an unsectioned Ranked list has only one run.
    #:
    #: It is a display choice rather than a data one, which is the whole reason `position` stays
    #: global and dense -- see `GameListItem.position`. Continue-through is `position + 1`, exactly
    #: what an unsectioned ranked list already renders; restart-per-section is the item's index
    #: within its section, computed at render. Neither stores anything.
    #:
    #: Defaults to continue-through because that is what a ranked list already MEANS: adding
    #: sections to one should group it, not renumber it underneath the author.
    sections_restart_numbering = models.BooleanField(
        default=False,
        help_text='Sectioned ranked lists only: restart numbering at 1 in each section, rather '
                  'than running 1..N through the whole list.',
    )

    #: Denormalized, maintained by the service with F() expressions. Never written by hand: the
    #: counts are what the browse grid sorts on, so a drifted count silently reorders the page.
    game_count = models.PositiveIntegerField(default=0)
    like_count = models.PositiveIntegerField(default=0)
    follower_count = models.PositiveIntegerField(default=0)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    #: A MODERATOR HID THIS LIST'S WORDS. The list itself survives -- its games, its likes, its
    #: followers, its owner's curation. Only the name and description stop being shown.
    #:
    #: THE SAME CALL `blurb_hidden` MAKES, for the same reason. That field exists so that hiding
    #: words a moderator objects to does not silently rewrite a game's rating averages; here it
    #: exists so a bad TITLE does not destroy a two-hundred-game backlog somebody spent months on.
    #: A moderator who needs the whole list gone has the restriction and admin tooling for that.
    #:
    #: ONE FLAG FOR BOTH FIELDS, not one each. A moderator reviewing a reported list is judging its
    #: presentation as a whole, the report reasons do not distinguish the two, and splitting a
    #: boolean later is a cheap migration if precision is ever wanted.
    #:
    #: NEVER READ DIRECTLY BY A TEMPLATE. Read `display_name` / `display_description`, which is the
    #: `display_image_url` rule: a flag honoured in one template and forgotten in the next is the
    #: `profile_views.py:670` bug class, where a private row leaked through an HTMX path that never
    #: rendered the parent.
    text_hidden = models.BooleanField(
        default=False,
        help_text="A moderator hid this list's name and description. The list itself is untouched.",
    )

    #: CHOSEN FOR THE BROWSE SPOTLIGHT, and when. Null means not featured, which is almost every row.
    #:
    #: ONE TIMESTAMP RATHER THAN THE `Featured*` SHAPE. `trophies` already holds three curation
    #: models -- `FeaturedGuide`, `FeaturedGame`, `FeaturedProfile` -- each an FK plus `priority`
    #: plus a `start_date`/`end_date` window, and TWO of the three have no consumer outside the
    #: admin. The scheduling machinery was built three times and used once. A nullable timestamp
    #: says the same thing for the one question the page actually asks ("what is featured now?"),
    #: answers ordering with the same column, and records when the choice was last made.
    #:
    #: NOT RESTRICTED TO STAFF-OWNED LISTS, in the model or in a service guard. Curation is the act
    #: of setting this field, and the set of people who can set it is already the set of people with
    #: admin access. Encoding "staff-authored" a second time as a constraint would have to be
    #: unpicked -- along with its tests -- on the day a hunter's list deserves the slot, which is a
    #: direction this is expected to go.
    featured_at = models.DateTimeField(
        null=True, blank=True,
        help_text='Set to feature this list in the Spotlight on the Game Lists browse page. The '
                  'most recently set wins. Clear it to remove the Spotlight.',
    )

    is_deleted = models.BooleanField(default=False)
    deleted_at = models.DateTimeField(null=True, blank=True)

    objects = GameListManager()

    class Meta:
        ordering = ['-updated_at']
        indexes = [
            # "my lists", newest first. Partial on the visible predicate so deleted rows are not in
            # the index at all rather than being scanned and discarded.
            models.Index(fields=['owner', '-updated_at'], name='glst_owner_idx',
                         condition=Q(is_deleted=False)),
            # The two browse sorts. Both partial on exactly what `.public()` filters, so the manager
            # everybody is supposed to use is also the one that gets an index-only scan.
            models.Index(fields=['-like_count', '-created_at'], name='glst_public_likes_idx',
                         condition=Q(is_deleted=False, is_public=True)),
            models.Index(fields=['-created_at'], name='glst_public_new_idx',
                         condition=Q(is_deleted=False, is_public=True)),
            # `GameListSitemap.get_latest_lastmod()` and `Meta.ordering`. Without it the sitemap
            # index does a filtered scan plus a sort on every `/sitemap.xml` hit -- anonymous,
            # uncached and crawler-driven, which is the shape `core/sitemaps.py`'s header blames for
            # the May 2026 sitemap OOM. Same partial predicate as the two above, so it is only as
            # wide as the rows a crawler may see.
            models.Index(fields=['-updated_at'], name='glst_public_upd_idx',
                         condition=Q(is_deleted=False, is_public=True)),
            # The Spotlight, whose predicate is `GameListQuerySet.featured()` exactly. Partial on
            # `featured_at__isnull=False` as well as the public rule, so the index holds the handful
            # of rows ever featured rather than the whole table.
            #
            # NOT an index-only scan, despite the shape inviting that claim: the caller selects every
            # column and joins `Profile` for the byline's mark, so this is an index scan plus a heap
            # fetch plus a nested loop. What the partial index buys is that the scan walks a
            # handful-of-rows index instead of filtering the table -- which is the whole win, and is
            # worth stating accurately so the next person tuning it does not chase a plan that
            # cannot happen.
            models.Index(fields=['-featured_at'], name='glst_featured_idx',
                         condition=Q(is_deleted=False, is_public=True,
                                     featured_at__isnull=False)),
        ]
        constraints = [
            # A name is how you tell two of your own lists apart, so an EMPTY one is refused in the
            # DB and not only in the service -- the admin, the shell and the importer all write
            # around the service. Its limits, stated rather than implied: it catches `''` only, so a
            # whitespace-only name still passes here (the service strips, `clean()` catches it, and
            # neither runs for those same out-of-service writers), and nothing stops two lists
            # sharing a name -- there is no unique constraint on (owner, name) and there should not
            # be, since "Backlog" and "Backlog" are a user's problem, not a data-integrity one.
            models.CheckConstraint(condition=~Q(name=''), name='gamelist_name_not_blank'),
            # `choices` is a form and admin concern; Postgres does not enforce it. The constraint
            # above exists because "the admin, the shell and the importer all write around the
            # service", and that reasoning applies here identically -- `_check_list_type` only
            # guards the two service functions. A `list_type='tier'` written from a shell or a data
            # migration renders as a Collection AND leaves the detail page's radio group with
            # nothing checked, so the owner has no UI path back.
            models.CheckConstraint(
                condition=Q(list_type__in=[value for value, _ in LIST_TYPE_CHOICES]),
                name='gamelist_list_type_valid'),
        ]

    def __str__(self):
        return f'{self.name} ({self.owner.display_psn_username})'

    #: What a hidden list is called instead. Not "[removed]" or "[hidden by a moderator]": a reader
    #: who lands on it does not need the site's moderation history, and naming the act invites
    #: exactly the curiosity the hiding was meant to end.
    HIDDEN_NAME = 'Untitled list'

    @property
    def display_name(self):
        """THE supported way to render a list's name. See `text_hidden`.

        The stored name is kept rather than blanked, so a moderator can still see what was reported
        and the decision can be reversed. This is the only thing that decides what a READER sees.
        """
        return self.HIDDEN_NAME if self.text_hidden else self.name

    @property
    def display_description(self):
        """The same, for the description. Empty rather than a placeholder: a missing description is
        an ordinary state every list already renders, so it needs no explaining."""
        return '' if self.text_hidden else self.description

    def clean(self):
        if not self.name.strip():
            raise ValidationError({'name': 'A list needs a name.'})


class GameListSection(models.Model):
    """An author-named group of games within one list.

    ITS OWN TABLE RATHER THAN A KEY ON THE ITEM. The first sketch was a `GameListItem.group`
    CharField, and that only works if sections sort alphabetically and cannot exist while empty. Both
    fail the first real use: "Finished / Playing / Someday" wants that order and not alphabetical,
    and a hunter creates "Someday" empty and then drags into it. A CharField can express neither.

    NOT A LIST TYPE. Sections compose with `list_type` rather than being one of its values -- a
    Collection can have them and so can a Ranked list. See docs/design/list-sections.md; the short
    version is that `list_type` picks a PRESENTATION and sectioning is structure any presentation can
    carry, so making it a type would have meant `sectioned` plus `sectioned-ranked` and a
    combinatorial table.

    MEMBERS ONLY to create or rename, and that gate lives in the service. A lapsed member keeps every
    section they made and the list renders exactly as it did -- what they lose is making more. The
    alternative is a takeback, which is the same argument the list-size comment makes above.
    """

    game_list = models.ForeignKey(GameList, on_delete=models.CASCADE, related_name='sections')
    name = models.CharField(max_length=SECTION_NAME_MAX_LENGTH)
    #: 0-indexed and dense, like `GameListItem.position` and for a weaker reason: nothing bounds a
    #: prefetch on it, so a gap is untidy rather than broken. The service compacts anyway, because
    #: two orderings in one feature that behave differently is a trap for whoever reads one and
    #: assumes the other.
    position = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['position']
        constraints = [
            # Same reasoning as the list's own blank-name check: the admin, the shell and the
            # importer all write around the service, and an unnamed section is a header with nothing
            # in it. Two sections MAY share a name -- that is the author's problem, not a data one.
            models.CheckConstraint(condition=~Q(name=''), name='gamelistsection_name_not_blank'),
        ]
        indexes = [
            models.Index(fields=['game_list', 'position'], name='glstsec_position_idx'),
        ]

    def __str__(self):
        return f'{self.name} in {self.game_list.name}'


class GameListItem(models.Model):
    """One game on a list.

    KEYED ON CONCEPT, NOT ON `Game`. The site's word "game" is the Concept (the work); the `Game`
    model is one platform's TROPHY LIST -- see docs/design/games-and-trophy-lists-ia.md, which
    settled that vocabulary in 2026-08 and asked every page rebuilt after it to inherit the decision
    rather than re-litigate it. The old model FK'd `Game`, so a backlog held Elden Ring twice, once
    per stack, and lists were the last concept-blind feature on a site whose contracts, ratings,
    roadmaps, badges and grouping pages are all concept-level. Somebody who genuinely means one
    stack has the concept page's list switcher.
    """

    game_list = models.ForeignKey(GameList, on_delete=models.CASCADE, related_name='items')
    concept = models.ForeignKey(Concept, on_delete=models.CASCADE, related_name='list_entries')
    #: Null means UNGROUPED, which is a real state rather than an error: a list that gains sections
    #: has every item unassigned, and forcing assignment before the page could render would make
    #: adding sections feel like a migration. Ungrouped items render in their own bucket at the top.
    #:
    #: `SET_NULL` and not CASCADE. Deleting a section must orphan its games, never delete them --
    #: the same reasoning `Concept.absorb()`'s list branch documents, where a cascade would destroy
    #: hand-curated entries because of a structural change the hunter never saw.
    section = models.ForeignKey('GameListSection', on_delete=models.SET_NULL, null=True, blank=True,
                                related_name='items')
    note = models.CharField(max_length=NOTE_MAX_LENGTH, blank=True, default='')

    #: 0-indexed and DENSE. The service re-compacts on removal, and that contract is load-bearing
    #: rather than tidy: the browse tile's cover prefetch bounds itself with `position__lt=4`, which
    #: silently returns fewer covers than it should the moment a gap appears.
    #:
    #: GLOBAL, AND SECTIONS DID NOT CHANGE THAT. Per-section ordering is the obvious shape and it
    #: breaks the invariant above, and `reorder` with it -- that function refuses a partial ordering
    #: by design, so a per-section payload would be a rewrite rather than a tweak. Keeping this
    #: global makes a section a grouping OVERLAY, and makes both numbering modes render-time
    #: derivations of it: continue-through is `position + 1`, restart-per-section is the index within
    #: the section. Neither stores anything, so the toggle cost a boolean rather than a migration.
    position = models.PositiveIntegerField(default=0)
    added_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['position']
        constraints = [
            models.UniqueConstraint(fields=['game_list', 'concept'],
                                    name='gamelistitem_unique_concept'),
        ]
        indexes = [
            models.Index(fields=['game_list', 'position'], name='glstitem_position_idx'),
        ]

    def __str__(self):
        return f'{self.concept.unified_title} in {self.game_list.name}'


class GameListLike(models.Model):
    """A one-tap signal, and the input the "popular" browse sort reads.

    Same shape as the four vote models already in `trophies` (target FK + profile FK + a unique
    pair, with a denormalized counter on the parent) rather than a new idea, because the rebuilt
    lists page is the first surface any of that shape has been live on for a while.
    """

    game_list = models.ForeignKey(GameList, on_delete=models.CASCADE, related_name='likes')
    profile = models.ForeignKey(Profile, on_delete=models.CASCADE, related_name='liked_game_lists')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['game_list', 'profile'], name='gamelistlike_unique'),
        ]
        indexes = [
            models.Index(fields=['profile', '-created_at'], name='glstlike_profile_idx'),
        ]

    def __str__(self):
        return f'{self.profile.display_psn_username} likes {self.game_list.name}'


class GameListFollow(models.Model):
    """The site's first follow relation, and deliberately narrow.

    It follows a LIST, not a hunter. Following a person is a different feature with different
    consequences (a feed, a notification firehose, a block model, mutual-follow semantics) and none
    of that is in scope. Keeping this on the list means "show me this when it changes" and nothing
    more, and it can be deleted without the site having opinions about people following people.
    """

    game_list = models.ForeignKey(GameList, on_delete=models.CASCADE, related_name='followers')
    profile = models.ForeignKey(Profile, on_delete=models.CASCADE, related_name='followed_game_lists')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['game_list', 'profile'], name='gamelistfollow_unique'),
        ]
        indexes = [
            # "the lists I follow", newest first -- the only read this table has.
            models.Index(fields=['profile', '-created_at'], name='glstfollow_profile_idx'),
        ]

    def __str__(self):
        return f'{self.profile.display_psn_username} follows {self.game_list.name}'


class GameListReport(models.Model):
    """A hunter reporting a list's name or description (reactive moderation).

    MIRRORS `BlurbReport`, deliberately and to the field. That is the fifth table of this shape in
    the codebase and the duplication is real -- `CommentReport`, `ReviewReport`, `ChecklistReport`
    and `BlurbReport` differ only in which row they point at. Three of those four are dead systems
    (comments legacy, reviews archived, checklists replaced by Roadmaps), so the live pattern is
    `BlurbReport` + its queue, and matching it exactly is what lets this drop into the existing Mod
    Center with a `_QueueView` subclass and nothing else. A generic reports table is a worthwhile
    refactor and a bad thing to attempt while wiring a new queue onto live moderation tooling.

    IT LIVES IN `gamelists`, not `trophies`, because it is about a `GameList` -- the app owns its
    own rows. `trophies.services.moderation_service` imports it, which is the direction that
    dependency already runs (`Concept.absorb` reaches into `gamelists`).

    NO `absorb()` BRANCH NEEDED. It FKs the LIST, not a `Concept`, so it follows its list through
    everything -- the same reasoning `BlurbReport`'s own docstring gives for hanging off the rating.

    `unique(game_list, reporter)` so one hunter is one report. Without it a single objector can
    inflate a queue count that a moderator reads as consensus.
    """

    REPORT_REASONS = [
        ('spam', 'Spam'),
        ('harassment', 'Harassment'),
        ('inappropriate', 'Inappropriate Content'),
        ('other', 'Other'),
    ]
    REPORT_STATUS = [
        ('pending', 'Pending Review'),
        ('reviewed', 'Reviewed'),
        ('dismissed', 'Dismissed'),
        ('action_taken', 'Action Taken'),
    ]

    game_list = models.ForeignKey(GameList, on_delete=models.CASCADE, related_name='reports')
    reporter = models.ForeignKey(Profile, on_delete=models.CASCADE,
                                 related_name='submitted_list_reports')
    reason = models.CharField(max_length=20, choices=REPORT_REASONS)
    details = models.TextField(max_length=500, blank=True,
                               help_text='Additional context for the report')
    status = models.CharField(max_length=20, choices=REPORT_STATUS, default='pending')
    created_at = models.DateTimeField(auto_now_add=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)
    # `settings.AUTH_USER_MODEL`, not a hardcoded label: the user model lives in `users`, and the
    # other report tables reach it through `trophies.models`' own import rather than by name.
    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True,
                                    blank=True, related_name='reviewed_list_reports')
    admin_notes = models.TextField(blank=True)

    class Meta:
        unique_together = ['game_list', 'reporter']
        indexes = [
            # The queue reads pending-first, newest-first, and nothing else.
            models.Index(fields=['status', '-created_at'], name='glistreport_status_idx'),
            models.Index(fields=['game_list'], name='glistreport_list_idx'),
        ]

    def __str__(self):
        return f'Report on list {self.game_list_id} by {self.reporter.psn_username}'
