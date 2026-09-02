from django.contrib import admin

from .models import EarnedMilestoneTier, Milestone, MilestoneTier, UserMilestone


class MilestoneTierInline(admin.TabularInline):
    model = MilestoneTier
    extra = 0
    fields = ('index', 'threshold', 'name', 'discord_role_id', 'earned_count')
    readonly_fields = ('earned_count',)
    ordering = ('index',)


@admin.register(Milestone)
class MilestoneAdmin(admin.ModelAdmin):
    list_display = ('name', 'slug', 'metric', 'category', 'tier_count', 'is_active', 'sort_order')
    list_filter = ('is_active', 'category', 'metric')
    list_editable = ('is_active', 'sort_order')
    search_fields = ('name', 'slug', 'description')
    prepopulated_fields = {'slug': ('name',)}
    inlines = [MilestoneTierInline]

    @admin.display(description='Tiers')
    def tier_count(self, obj):
        return obj.tiers.count()


@admin.register(EarnedMilestoneTier)
class EarnedMilestoneTierAdmin(admin.ModelAdmin):
    list_display = ('profile', 'tier', 'earned_at')
    search_fields = ('profile__psn_username', 'tier__milestone__name')
    raw_id_fields = ('profile', 'tier')
    readonly_fields = ('earned_at',)


@admin.register(UserMilestone)
class UserMilestoneAdmin(admin.ModelAdmin):
    list_display = ('profile', 'milestone', 'current_value', 'highest_tier_index', 'updated_at')
    list_filter = ('milestone',)
    search_fields = ('profile__psn_username', 'milestone__name')
    raw_id_fields = ('profile', 'milestone')
    readonly_fields = ('updated_at',)
