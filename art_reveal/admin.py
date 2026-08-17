from django.contrib import admin

from .models import ArtRevealEvent, ArtRevealItem
from .services import reconcile_event


class ArtRevealItemInline(admin.TabularInline):
    model = ArtRevealItem
    extra = 0
    # Autocompletes against BadgeSeriesAdmin, which already declares the required `search_fields`.
    # The tier-1 queryset narrowing this used to need is gone with the tiers: a reveal applies to the
    # series, and every edition inherits the released art through GroupBadge.art_layers().
    autocomplete_fields = ['series']
    fields = ['order', 'series', 'artwork', 'placeholder_label', 'released', 'released_at']
    readonly_fields = ['released', 'released_at']
    ordering = ['order']


@admin.register(ArtRevealEvent)
class ArtRevealEventAdmin(admin.ModelAdmin):
    list_display = [
        'name', 'slug', 'is_active', 'started_at', 'ended_at',
        'platinums_per_reveal', 'last_platinum_count', 'reveal_progress',
    ]
    list_filter = ['is_active', 'banner_active']
    search_fields = ['name', 'slug']
    prepopulated_fields = {'slug': ('name',)}
    readonly_fields = ['last_platinum_count', 'last_counted_at', 'created_at']
    inlines = [ArtRevealItemInline]
    actions = ['recount_and_release']

    @admin.display(description='Revealed')
    def reveal_progress(self, obj):
        return f"{obj.released_count} / {obj.total_items}"

    @admin.action(description='Recount community platinums & release now')
    def recount_and_release(self, request, queryset):
        total_released = 0
        for event in queryset:
            result = reconcile_event(event)
            total_released += len(result['released'])
        self.message_user(
            request,
            f"Reconciled {queryset.count()} event(s); released {total_released} new artwork(s).",
        )
