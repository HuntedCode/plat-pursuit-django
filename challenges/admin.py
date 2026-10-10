"""Challenge admin: an INSPECTION desk, not a content editor.

The same posture as `gamelists.admin` and for a sharper reason. A challenge is a record of what a
hunter did, and two of its columns are rewards already paid out: `ChallengeSlot.xp_redeemed_at` marks
a row written into an APPEND-ONLY job-XP ledger, and a completed run has granted a title. Editing any
of that by hand desynchronises the run from the ledger in a direction nothing can repair -- clearing
`xp_redeemed_at` invites a second payout that the DB constraint will then refuse, which is the good
outcome; flipping `is_completed` off strands a title with nothing behind it, which is the bad one.

So every field is read-only, adding is off, and deleting is off. Registered on `admin.site`, which
`core/admin_site.py` narrows to superusers.

Unlike `GameListAdmin` there is no curation action here -- nothing about a challenge is editorial. If
a run needs surgery, it needs a management command that moves the run AND the ledger together.
"""
from django.contrib import admin

from challenges.models import Challenge, ChallengeSlot


class ChallengeSlotInline(admin.TabularInline):
    model = ChallengeSlot
    extra = 0
    can_delete = False
    # 26 is the whole A-Z run. This is DOCUMENTATION rather than a behaviour change: with `extra = 0`
    # a formset renders `initial_forms` whenever that exceeds `max_num`, so 26 and 0 do the same
    # thing here. It states the expected row count for a reader, and that is all it does -- an
    # earlier comment claimed it prevented the `max_num = 0` trap `GameListItemInline` documents,
    # which is not true when `extra` is already 0.
    max_num = 26
    fields = ('position', 'key', 'contract_name', 'contract_slug', 'assigned_at',
              'is_completed', 'completed_at', 'completed_via', 'xp_redeemed_at')
    readonly_fields = fields
    ordering = ('position',)

    def has_add_permission(self, request, obj):
        return False

    def get_queryset(self, request):
        # The frozen columns make this flat already -- nothing here reads `contract`, deliberately,
        # since the snapshot is the point. `select_related` anyway would join 26 contracts per page
        # to render nothing.
        return super().get_queryset(request).only(
            'challenge', 'position', 'key', 'contract_name', 'contract_slug', 'assigned_at',
            'is_completed', 'completed_at', 'completed_via', 'xp_redeemed_at',
        )


@admin.register(Challenge)
class ChallengeAdmin(admin.ModelAdmin):
    list_display = ('name', 'profile', 'challenge_type', 'completed_count', 'total_slots',
                    'is_complete', 'completed_at', 'is_deleted', 'updated_at')
    list_filter = ('challenge_type', 'is_complete', 'is_deleted')
    search_fields = ('name', 'profile__psn_username', 'profile__display_psn_username')
    date_hierarchy = 'created_at'
    ordering = ('-updated_at',)
    inlines = [ChallengeSlotInline]

    # `fields` and `readonly_fields` are the SAME TUPLE, which is the whole posture in one line.
    fields = ('profile', 'challenge_type', 'name', 'total_slots', 'filled_count', 'completed_count',
              'is_complete', 'completed_at', 'is_deleted', 'deleted_at',
              'created_at', 'updated_at')
    readonly_fields = fields

    def get_queryset(self, request):
        return super().get_queryset(request).select_related('profile')

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        # A challenge cascades to its slots, and a slot is the only record of which contract paid
        # which job. The ledger rows would outlive it with a `source_id` pointing at nothing.
        return False
