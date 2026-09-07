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
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q

from trophies.models import Concept, Profile

#: How many LISTS you get. Everyone makes lists; members make more of them -- the same shape as the
#: shipped `sync` perk (everyone syncs, members sync more often) rather than a capability a free
#: hunter cannot reach. Read ONLY by `game_list_service.max_lists_for`, so the tier rule has exactly
#: one enforcement point.
#:
#: THERE IS NO CAP ON LIST SIZE, and that absence is deliberate. The system this replaces gave
#: members unlimited games per list, so any ceiling here would take a perk back rather than add one
#: -- and the per-list importer could then refuse to bring a member's own data across. The first cut
#: shipped a flat 100-item cap for everybody and described it as a perk; it was a reduction.
FREE_MAX_LISTS = 3
# 25, not 10: once lists come in several TYPES (collection, ranked, progress, tier), a hunter using
# the feature properly holds far more than ten -- a backlog tracker, a couple of ranked top-tens, a
# tier list per franchise. The free cap stays at 3 deliberately, as spam control rather than as a
# tease; three is enough to see what lists are for.
MEMBER_MAX_LISTS = 25

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
    is_public = models.BooleanField(
        default=False,
        help_text='Opt-IN. A list is private until its author decides otherwise.',
    )
    selected_theme = models.CharField(
        max_length=50, blank=True, default='',
        help_text='Gradient theme key from trophies.themes.GRADIENT_THEMES. Members only.',
    )

    #: Denormalized, maintained by the service with F() expressions. Never written by hand: the
    #: counts are what the browse grid sorts on, so a drifted count silently reorders the page.
    game_count = models.PositiveIntegerField(default=0)
    like_count = models.PositiveIntegerField(default=0)
    follower_count = models.PositiveIntegerField(default=0)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

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
        ]

    def __str__(self):
        return f'{self.name} ({self.owner.display_psn_username})'

    def clean(self):
        if not self.name.strip():
            raise ValidationError({'name': 'A list needs a name.'})


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
    note = models.CharField(max_length=NOTE_MAX_LENGTH, blank=True, default='')

    #: 0-indexed and DENSE. The service re-compacts on removal, and that contract is load-bearing
    #: rather than tidy: the browse tile's cover prefetch bounds itself with `position__lt=4`, which
    #: silently returns fewer covers than it should the moment a gap appears.
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
