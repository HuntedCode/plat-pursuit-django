from django.contrib import admin

from trophies.models import Badge

from .models import ArtRevealEvent, ArtRevealItem
from .services import reconcile_event


class ArtRevealItemInline(admin.TabularInline):
    model = ArtRevealItem
    extra = 0
    autocomplete_fields = ['badge']
    fields = ['order', 'badge', 'artwork', 'placeholder_label', 'released', 'released_at']
    readonly_fields = ['released', 'released_at']
    ordering = ['order']

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        # Reveals apply to the tier-1 (base) badge of a series; tiers 2-4 inherit
        # the art via base_badge fallback, so only base badges are selectable.
        if db_field.name == 'badge':
            kwargs['queryset'] = Badge.objects.filter(tier=1)
        return super().formfield_for_foreignkey(db_field, request, **kwargs)


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
