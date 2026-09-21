"""The rebuilt Game Lists in Django admin -- a CURATION desk, not a content editor.

The app had no admin at all until the Spotlight shipped, and the gap was invisible because
`trophies/admin.py` registers the LEGACY `trophies.GameList` under the same class name. Two models,
one word, one of them unrouted: the same collision that has already sent a reader to the wrong file.

WHY THIS IS READ-MOSTLY. A game list is a hunter's own writing. The one thing the site needs from
an administrator is the editorial choice of which list to put in the browse Spotlight, so that is
the one field left editable. Names, descriptions, ownership and membership stay read-only here --
moderation of a list's WORDS already has a purpose-built path (`text_hidden`, the report queue in
the Admin Hub, and `moderation_service`), and that path writes an audit entry with a moderator and
a reason, which a silent admin edit does not.

Registered against `admin.site`, which `core/admin_site.py` has narrowed to superusers. Featuring is
therefore the owner's lever, not the admin team's. That is the right default for an editorial slot
while the picks are staff-written; if featuring ever wants to be a moderator action it belongs in
`/staff/` alongside the report queue, where it would be logged.
"""
from django.contrib import admin, messages
from django.db.models import F
from django.utils import timezone

from .models import GameList, GameListItem


class GameListItemInline(admin.TabularInline):
    """The membership, visible but not editable: it is here so a curator can see what they are about
    to put on the front of the browse page without opening the list in a second tab."""

    model = GameListItem
    extra = 0
    can_delete = False
    fields = ('position', 'concept', 'section')
    readonly_fields = fields
    # A list runs to hundreds of games and nobody scrolls one in the admin to decide if it is a
    # good list. The first screenful answers that.
    #
    # 20, NOT 0. `max_num = 0` reads like "none extra" and does not truncate anything: Django's
    # formset only slices its queryset `if self.max_num > 0`, so zero fell through and rendered
    # every row. A `MAX_ITEMS_PER_LIST` list was several hundred inline rows on one page -- with
    # `extra = 0` already suppressing the blank forms, the setting was doing nothing at all.
    max_num = 20

    def get_queryset(self, request):
        """`concept` and `section` are readonly, so each row renders `str(obj)` -- two queries per
        item without this. Twenty rows was forty extra queries on every change form."""
        return super().get_queryset(request).select_related('concept', 'section')

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(GameList)
class GameListAdmin(admin.ModelAdmin):
    """Find a list, read it, decide whether it deserves the Spotlight."""

    list_display = ('name', 'owner', 'list_type', 'is_public', 'game_count', 'like_count',
                    'featured_at', 'text_hidden', 'is_deleted')
    list_filter = ('list_type', 'is_public', 'text_hidden', 'is_deleted',
                   ('featured_at', admin.EmptyFieldListFilter))
    search_fields = ('name', 'owner__psn_username', 'owner__display_psn_username')
    # `nulls_last`, WITHOUT WHICH THIS SORTS BACKWARDS. Postgres puts NULLs FIRST on a `DESC`
    # ordering, and `featured_at` is null on essentially every row -- so a bare `-featured_at`
    # listed the entire unfeatured table first and buried the one featured list at the very
    # bottom, which is the opposite of what a desk for finding it needs.
    ordering = (F('featured_at').desc(nulls_last=True), '-updated_at')
    list_select_related = ('owner',)
    inlines = [GameListItemInline]

    #: WHOLLY READ-ONLY, including `featured_at`.
    #:
    #: It was editable here at first, which quietly defeated the point of `feature_selected` below:
    #: that action refuses to feature a list that is private, deleted or moderated, and a superuser
    #: typing a timestamp into this form skipped every one of those checks. Two ways to set one
    #: field, one of them guarded, is not a curation desk -- it is a guard with a door next to it.
    #: The actions are the only writer, so the checks cannot be walked around.
    fields = ('name', 'description', 'owner', 'list_type', 'is_public', 'game_count', 'like_count',
              'follower_count', 'text_hidden', 'is_deleted', 'created_at', 'updated_at',
              'featured_at')
    readonly_fields = fields

    actions = ('feature_selected', 'unfeature_selected')

    def has_add_permission(self, request):
        """Lists are made by hunters, through the service that maintains their counts. A list typed
        into the admin would arrive with a zeroed `game_count` and no owner-side history."""
        return False

    def has_delete_permission(self, request, obj=None):
        """Deletion is SOFT and belongs to the author or to moderation (`is_deleted`). A hard delete
        here would cascade a hunter's list away with no audit entry and no undo."""
        return False

    @admin.action(description='Feature in the browse Spotlight')
    def feature_selected(self, request, queryset):
        """Featuring is 'most recently set wins', so featuring several at once is not a bulk state
        change -- it is a single choice with extra rows quietly losing. Refused rather than
        half-honoured, because the losers would still LOOK featured in the changelist."""
        if queryset.count() != 1:
            self.message_user(
                request,
                'Pick exactly one list. The Spotlight shows the most recently featured list, so '
                'featuring several at once would silently choose between them.',
                level=messages.ERROR)
            return

        game_list = queryset.get()
        # The Spotlight reads through `GameList.objects.featured()`, which is built on `public()`.
        # A private or hidden list can be featured here and would simply never appear, which reads
        # as the feature being broken. Say so at the point of the decision instead.
        problems = []
        if not game_list.is_public:
            problems.append('it is private')
        if game_list.is_deleted:
            problems.append('it is deleted')
        if game_list.text_hidden:
            problems.append('a moderator has hidden its name and description')
        if problems:
            self.message_user(
                request,
                f'"{game_list.name}" cannot be featured because {" and ".join(problems)}. '
                f'The Spotlight only shows lists the browse grid would show.',
                level=messages.ERROR)
            return

        game_list.featured_at = timezone.now()
        game_list.save(update_fields=['featured_at'])
        self.message_user(request, f'"{game_list.name}" is now the browse Spotlight.')

    @admin.action(description='Remove from the browse Spotlight')
    def unfeature_selected(self, request, queryset):
        """Bulk is fine in this direction: clearing is idempotent and there is no choice to lose."""
        cleared = queryset.filter(featured_at__isnull=False).update(featured_at=None)
        self.message_user(
            request,
            f'Cleared the Spotlight flag on {cleared} list{"" if cleared == 1 else "s"}. '
            'The Spotlight now shows the next most recently featured list, or nothing.')
