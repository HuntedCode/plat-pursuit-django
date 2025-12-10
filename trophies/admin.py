from django.contrib import admin, messages
from django.contrib.admin import SimpleListFilter
from django.db import transaction
from django.db.models import Q
from .models import Profile, Game, Trophy, EarnedTrophy, ProfileGame, APIAuditLog, FeaturedGame, FeaturedProfile, Event, Concept, TitleID, TrophyGroup, UserTrophySelection, UserConceptRating, Badge, UserBadge, UserBadgeProgress


# Register your models here.
@admin.register(Profile)
class ProfileAdmin(admin.ModelAdmin):
    list_display = (
        "psn_username",
        "id",
        "account_id",
        "user",
        "discord_id",
        "verification_code",
        "is_linked",
        "is_verified",
        "psn_history_public",
        'country_code',
        "is_plus",
        "last_synced",
        "sync_tier",
    )
    list_filter = ("is_linked", "is_plus", "sync_tier")
    search_fields = ("psn_username", "account_id", "user__username__iexact", "about_me")
    raw_id_fields = ("user",)
    ordering = ("psn_username",)
    fieldsets = (
        (
            "Core Info",
            {"fields": ("psn_username", "display_psn_username", "account_id", "np_id", "user", "is_linked", "psn_history_public", "discord_id", "discord_linked_at", "is_verified", "verification_code")},
        ),
        (
            "Profile Details",
            {"fields": ("avatar_url", "about_me", "languages_used", "is_plus")},
        ),
        (
            "Trophy Summary",
            {"fields": ("trophy_level", "progress", "tier", "earned_trophy_summary")},
        ),
        ("Sync Info", {"fields": ("extra_data", "last_synced", "sync_tier")}),
    )
    actions = ["link_to_user"]

    def link_to_user(self, request, queryset):
        for profile in queryset:
            if not profile.is_linked:
                profile.link_to_user(request.user)
                self.message_user(
                    request, f"Linked {profile.psn_username} to {request.user.username}"
                )

    link_to_user.short_description = "Link selected profiles to current user"


class RegionListFilter(SimpleListFilter):
    title = 'Region'
    parameter_name = 'region'

    def lookups(self, request, model_admin):
        return (
            ('NA', 'North America'),
            ('EU', 'Europe'),
            ('JP', 'Japan'),
            ('AS', 'Asia'),
        )
    
    def queryset(self, request, queryset):
        values = self.value()
        if not values:
            return queryset
        
        regions = values.split(',') if ',' in values else [values]

        region_filter = Q()
        for region in regions:
            region_filter |= Q(region__contains=region)

        return queryset.filter(region_filter)

@admin.register(Game)
class GameAdmin(admin.ModelAdmin):
    list_display = (
        "title_name",
        "np_communication_id",
        "title_platform",
        "concept",
        "region",
        "is_regional",
        "title_ids",
        "is_obtainable",
        "total_defined_trophies",
        "played_count",
        "is_shovelware",
    )
    list_filter = ("has_trophy_groups", "is_regional", RegionListFilter, 'is_shovelware', 'is_obtainable')
    search_fields = ("title_name", "np_communication_id")
    ordering = ("title_name",)
    fieldsets = (
        (
            "Core Info",
            {
                "fields": (
                    "np_communication_id",
                    "np_service_name",
                    "title_name",
                    "concept",
                    "region",
                    "is_regional",
                    "title_ids",
                    "title_detail",
                    "title_image",
                    "is_shovelware",
                )
            },
        ),
        (
            "Trophy Data",
            {"fields": ("trophy_set_version", "has_trophy_groups", "defined_trophies", "played_count")},
        ),
        (
            "Metadata",
            {"fields": ("title_icon_url", "title_platform", "metadata")},
        ),
    )
    actions = ['toggle_is_regional']

    @admin.action(description="Toggle is_regional for selected games")
    def toggle_is_regional(self, request, queryset):
        with transaction.atomic():
            for game in queryset:
                game.is_regional = not game.is_regional
                game.save(update_fields=['is_regional'])
            
            count = queryset.count()
            messages.success(request, f"Toggled is_regional for {count} game(s).")

    def total_defined_trophies(self, obj):
        return sum(obj.defined_trophies.values()) if obj.defined_trophies else 0

    total_defined_trophies.short_description = "Total Trophies"


@admin.register(ProfileGame)
class ProfileGameAdmin(admin.ModelAdmin):
    list_display = (
        "profile",
        "game",
        "game__played_count",
        "progress",
        "play_duration",
        "last_played_date_time",
        "last_updated_datetime",
    )
    list_filter = ("hidden_flag",)
    search_fields = ("profile__psn_username", "game__title_name")
    raw_id_fields = ("profile", "game")
    ordering = ("-last_updated_datetime",)


@admin.register(Trophy)
class TrophyAdmin(admin.ModelAdmin):
    list_display = (
        "trophy_name",
        "game",
        "trophy_type",
        "trophy_rarity",
        "trophy_earn_rate",
        "earned_count",
        "earn_rate",
    )
    list_filter = ("trophy_type", "game__title_platform")
    search_fields = ("trophy_name", "trophy_detail")
    raw_id_fields = ("game",)
    ordering = ("trophy_name",)
    fieldsets = (
        (
            "Core Info",
            {"fields": ("trophy_id", "trophy_name", "trophy_type", "game", "trophy_detail")},
        ),
        (
            "Rewards",
            {
                "fields": (
                    "trophy_icon_url",
                    "reward_name",
                    "reward_img_url",
                )
            },
        ),
        (
            "Group/Progress",
            {
                "fields": (
                    "trophy_group_id",
                    "progress_target_value",
                    "trophy_set_version",
                )
            },
        ),
        (
            "Rarity/Stats",
            {"fields": ("trophy_rarity", "trophy_earn_rate", "earned_count", "earn_rate")},
        ),
    )

    def earned_by_count(self, obj):
        return obj.earned_by.count()

    earned_by_count.short_description = "Earned By"


@admin.register(EarnedTrophy)
class EarnedTrophyAdmin(admin.ModelAdmin):
    list_display = (
        "profile",
        "trophy",
        "trophy__trophy_type",
        "earned",
        'progress',
        "trophy__earned_count",
        "earned_date_time",
        "last_updated",
    )
    list_filter = ("earned", "trophy_hidden", "earned_date_time", "trophy__trophy_type")
    search_fields = ("profile__psn_username", "trophy__trophy_name", "trophy__game__title_name")
    raw_id_fields = ("profile", "trophy")
    ordering = ("-last_updated",)


@admin.register(APIAuditLog)
class APIAuditLogAdmin(admin.ModelAdmin):
    list_display = ("timestamp", "endpoint", "profile", "status_code", "response_time", "calls_remaining")
    list_filter = ("status_code", "timestamp")
    search_fields = ("endpoint", "profile__psn_username")
    ordering = ("-timestamp",)

@admin.register(FeaturedGame)
class FeaturedGameAdmin(admin.ModelAdmin):
    list_display = ('game', 'priority', 'reason', 'start_date', 'end_date')
    search_fields = ('game__title_name',)
    list_filter = ('reason',)

@admin.register(FeaturedProfile)
class FeaturedProfileAdmin(admin.ModelAdmin):
    list_display = ('profile', 'priority', 'reason', 'start_date', 'end_date')
    search_fields = ('profile__psn_username',)
    list_filter = ('reason',)

@admin.register(Event)
class EventAdmin(admin.ModelAdmin):
    list_display = ('title', 'description', 'date', 'end_date', 'color')
    search_fields = ('title', 'description')
    list_filter = ('color', 'date')
    date_hierarchy = 'date'

@admin.register(TitleID)
class TitleIDAdmin(admin.ModelAdmin):
    list_display = ('title_id', 'platform', 'region')
    search_fields = ('title_id',)
    list_filter = ('region', 'platform')

@admin.register(Concept)
class ConceptAdmin(admin.ModelAdmin):
    list_display = ('id', 'concept_id', 'unified_title', 'publisher_name', 'genres')
    search_fields = ('concept_id', 'unified_title')

@admin.register(TrophyGroup)
class TrophyGroupAdmin(admin.ModelAdmin):
    list_display = ('game', 'trophy_group_id', 'trophy_group_name')
    search_fields = ('game__title_name', 'trophy_group_name')

@admin.register(UserTrophySelection)
class UserTrophySelectionAdmin(admin.ModelAdmin):
    list_display = ('profile', 'earned_trophy__trophy__trophy_name', 'earned_trophy__trophy__game__title_name')
    search_fields = ('profile__psn_username',)

@admin.register(UserConceptRating)
class UserConceptRatingAdmin(admin.ModelAdmin):
    list_display = ('profile', 'concept', 'difficulty', 'hours_to_platinum', 'fun_ranking', 'overall_rating', 'created_at', 'updated_at')
    list_filter = ('created_at', 'updated_at')
    search_fields = ('profile__psn_username', 'concept__unified_title')

@admin.register(Badge)
class BadgeAdmin(admin.ModelAdmin):
    list_display = ['name', 'tier', 'badge_type', 'series_slug', 'requires_all', 'min_required']
    list_filter = ['tier', 'badge_type']
    search_fields = ['name', 'series_slug']
    filter_horizontal = ['concepts',]
    fields = ['name', 'series_slug', 'description', 'icon', 'base_badge', 'tier', 'badge_type', 'display_title', 'discord_role_id', 'requires_all', 'min_required', 'requirements', 'concepts']

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == 'base_badge':
            kwargs['queryset'] = Badge.objects.filter(tier=1)
        return super().formfield_for_foreignkey(db_field, request, **kwargs)

@admin.register(UserBadge)
class UserBadgeAdmin(admin.ModelAdmin):
    list_display = ['profile', 'badge', 'earned_at', 'is_displayed']
    list_filter = ['is_displayed', 'earned_at']
    search_fields = ['profile__psn_username']

@admin.register(UserBadgeProgress)
class UserBadgeProgressAdmin(admin.ModelAdmin):
    list_display = ['profile', 'badge', 'completed_concepts', 'required_concepts', 'progress_value', 'required_value', 'last_checked']
    search_fields = ['profile__psn_username']