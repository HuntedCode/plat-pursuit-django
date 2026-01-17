from django.contrib import admin, messages
from django.contrib.admin import SimpleListFilter
from django.db import transaction
from django.db.models import Q
from .models import Profile, Game, Trophy, EarnedTrophy, ProfileGame, APIAuditLog, FeaturedGame, FeaturedProfile, Event, Concept, TitleID, TrophyGroup, UserTrophySelection, UserConceptRating, Badge, UserBadge, UserBadgeProgress, FeaturedGuide, Stage, PublisherBlacklist, Title, UserTitle, Milestone, UserMilestone, UserMilestoneProgress, GuideTag, AuthorTrust, Guide, GuideImage, GuideSection, GuideRating


# Register your models here.
@admin.register(Profile)
class ProfileAdmin(admin.ModelAdmin):
    list_display = (
        "psn_username",
        "id",
        "account_id",
        "user",
        "user_is_premium",
        "last_synced",
        "sync_status",
        "sync_progress_value",
        "sync_progress_target",
        "sync_tier",
        "is_linked",
        "discord_id",
        "verification_code",
        "is_discord_verified",
        "psn_history_public",
        'country_code',
        "is_plus",
    )
    list_filter = ("is_linked", "is_plus", "sync_tier", "sync_status", "user_is_premium",)
    search_fields = ("psn_username", "account_id", "user__username__iexact", "about_me")
    raw_id_fields = ("user",)
    ordering = ("psn_username",)
    fieldsets = (
        (
            "Core Info",
            {"fields": ("psn_username", "display_psn_username", "account_id", "np_id", "user", "user_is_premium", "is_linked", "psn_history_public", "hide_hiddens", "discord_id", "discord_linked_at", "is_discord_verified", "verification_code")},
        ),
        (
            "Profile Details",
            {"fields": ("avatar_url", "about_me", "languages_used", "is_plus", "selected_background")},
        ),
        (
            "Trophy Summary",
            {"fields": ("trophy_level", "progress", "tier", "earned_trophy_summary", 'total_trophies', 'total_unearned', 'total_bronzes', 'total_silvers', 'total_golds', 'total_plats', 'total_hiddens', 'total_games', 'total_completes', 'avg_progress')},
        ),
        ("Sync Info", {"fields": ("extra_data", "last_synced", "last_profile_health_check", "sync_status", "sync_progress_value", "sync_progress_target", "sync_tier")}),
    )


class RegionListFilter(SimpleListFilter):
    title = 'Region'
    parameter_name = 'region'

    def lookups(self, request, model_admin):
        return (
            ('NA', 'North America'),
            ('EU', 'Europe'),
            ('JP', 'Japan'),
            ('AS', 'Asia'),
            ('KR', 'Korea'),
            ('CN', 'China'),
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
        "region_lock",
        "title_ids",
        "is_obtainable",
        "total_defined_trophies",
        "played_count",
        "is_shovelware",
        "is_delisted",
        "has_online_trophies",
    )
    list_filter = ("has_trophy_groups", "is_regional", RegionListFilter, 'is_shovelware', 'is_delisted', 'is_obtainable', "has_online_trophies")
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
                    "lock_title",
                    "concept",
                    "region",
                    "is_regional",
                    "region_lock",
                    "title_ids",
                    "title_detail",
                    "title_image",
                    "is_shovelware",
                    "is_obtainable",
                    "is_delisted",
                    "has_online_trophies",
                )
            },
        ),
        (
            "Trophy Data",
            {"fields": ("trophy_set_version", "has_trophy_groups", "defined_trophies", "played_count")},
        ),
        (
            "Metadata",
            {"fields": ("title_icon_url", "force_title_icon", "title_platform", "metadata")},
        ),
    )
    actions = ['toggle_is_regional', 'add_psvr_platform']
    autocomplete_fields=['concept']

    @admin.action(description="Toggle is_regional for selected games")
    def toggle_is_regional(self, request, queryset):
        with transaction.atomic():
            for game in queryset:
                game.is_regional = not game.is_regional
                game.save(update_fields=['is_regional'])
            
            count = queryset.count()
            messages.success(request, f"Toggled is_regional for {count} game(s).")
    
    @admin.action(description='Add "PSVR" to platforms for selected games')
    def add_psvr_platform(self, request, queryset):
        updated_count = 0
        with transaction.atomic():
            for game in queryset:
                if 'PSVR' not in game.title_platform:
                    game.title_platform.append('PSVR')
                    game.save(update_fields=['title_platform'])
                    updated_count += 1
        if updated_count:
            messages.success(request, f"Added 'PSVR' to {updated_count} game(s).")
        else:
            messages.info(request,  'No changes made. "PSVR" already present in selected games.')

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
    list_display = ('id', 'concept_id', 'unified_title', 'release_date', 'publisher_name', 'genres')
    search_fields = ('concept_id', 'unified_title')
    actions = ['duplicate_concept']

    @admin.action(description="Duplicate selected concepts")
    def duplicate_concept(self, request, queryset):
        for concept in queryset:
            new_concept = concept
            new_concept.pk = None

            original_id  = concept.concept_id
            i = 1
            while True:
                new_id = f"{original_id}-{i}"
                if not Concept.objects.filter(concept_id=new_id).exists():
                    break
                i += 1
            
            new_concept.concept_id = new_id
            new_concept.save()

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
    list_display = ('profile', 'concept', 'difficulty', 'grindiness', 'hours_to_platinum', 'fun_ranking', 'overall_rating', 'created_at', 'updated_at')
    list_filter = ('created_at', 'updated_at')
    search_fields = ('profile__psn_username', 'concept__unified_title')

class StageInline(admin.TabularInline):
    model = Stage
    extra = 1
    fields = ('stage_number', 'title', 'stage_icon', 'concepts', 'required_tiers')
    autocomplete_fields = ['concepts']

@admin.register(Badge)
class BadgeAdmin(admin.ModelAdmin):
    list_display = ['name', 'tier', 'badge_type', 'series_slug', 'display_series', 'required_stages', 'requires_all', 'min_required', 'earned_count', 'most_recent_concept']
    list_filter = ['tier', 'badge_type']
    search_fields = ['name', 'series_slug']
    fields = ['name', 'series_slug', 'description', 'badge_image', 'base_badge', 'tier', 'badge_type', 'display_title', 'display_series', 'user_title', 'discord_role_id', 'requires_all', 'min_required', 'requirements', 'earned_count']

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == 'base_badge':
            kwargs['queryset'] = Badge.objects.filter(tier=1)
        return super().formfield_for_foreignkey(db_field, request, **kwargs)

@admin.register(Stage)
class StageAdmin(admin.ModelAdmin):
    list_display = ('__str__', 'series_slug', 'stage_number', 'title', 'has_online_trophies')
    list_filter = ('series_slug', 'stage_number')
    search_fields = ('title',)
    autocomplete_fields = ['concepts']

@admin.register(UserBadge)
class UserBadgeAdmin(admin.ModelAdmin):
    list_display = ['profile', 'badge', 'earned_at', 'is_displayed']
    list_filter = ['is_displayed', 'earned_at']
    search_fields = ['profile__psn_username']

@admin.register(UserBadgeProgress)
class UserBadgeProgressAdmin(admin.ModelAdmin):
    list_display = ['profile', 'badge', 'completed_concepts', 'progress_value', 'last_checked']
    search_fields = ['profile__psn_username']
    
@admin.register(FeaturedGuide)
class FeaturedGuideAdmin(admin.ModelAdmin):
    list_display = ['concept', 'start_date', 'end_date', 'priority']
    list_filter = ['start_date', 'end_date']
    search_fields = ['concept__unified_title']

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == 'concept':
            kwargs['queryset'] = Concept.objects.exclude(Q(guide_slug__isnull=True) | Q(guide_slug=''))
        return super().formfield_for_foreignkey(db_field, request, **kwargs)

@admin.register(PublisherBlacklist)
class PublisherBlacklistAdmin(admin.ModelAdmin):
    list_display = ['name', 'date_added']

@admin.register(Title)
class TitleAdmin(admin.ModelAdmin):
    list_display = ['name', 'created_at']

@admin.register(UserTitle)
class UserTitleAdmin(admin.ModelAdmin):
    list_display = ['profile', 'title', 'source_type', 'source_id', 'earned_at', 'is_displayed']

@admin.register(Milestone)
class MilestoneAdmin(admin.ModelAdmin):
    list_display = ['name', 'title', 'discord_role_id', 'criteria_type', 'criteria_details', 'premium_only', 'required_value', 'earned_count']

@admin.register(UserMilestone)
class UserMilestoneAdmin(admin.ModelAdmin):
    list_display = ['profile', 'milestone', 'earned_at']
    search_fields = ['profile__psn_username', 'milestone__name']

@admin.register(UserMilestoneProgress)
class UserMilestoneProgressAdmin(admin.ModelAdmin):
    list_display = ['profile', 'milestone', 'progress_value', 'last_checked']
    search_fields = ['profile__psn_username', 'milestone__name']


# =============================================================================
# Community Guide Admin
# =============================================================================

@admin.register(GuideTag)
class GuideTagAdmin(admin.ModelAdmin):
    list_display = ['name', 'slug', 'display_order']
    search_fields = ['name', 'slug']
    ordering = ['display_order']


@admin.register(AuthorTrust)
class AuthorTrustAdmin(admin.ModelAdmin):
    list_display = ['profile', 'trust_level', 'approved_guide_count', 'total_stars_received', 'promoted_at', 'banned_at']
    list_filter = ['trust_level']
    search_fields = ['profile__psn_username']
    readonly_fields = ['approved_guide_count', 'total_stars_received', 'promoted_at', 'created_at', 'updated_at']
    raw_id_fields = ['profile']


class GuideSectionInline(admin.TabularInline):
    model = GuideSection
    extra = 1
    fields = ['section_order', 'title', 'slug', 'content', 'has_pending_edits']
    readonly_fields = ['slug']
    ordering = ['section_order']


@admin.register(Guide)
class GuideAdmin(admin.ModelAdmin):
    list_display = ['title', 'author', 'game', 'guide_type', 'status', 'section_count', 'average_rating', 'rating_count', 'view_count', 'published_at']
    list_filter = ['guide_type', 'status', 'created_at', 'published_at']
    search_fields = ['title', 'author__psn_username', 'game__title_name']
    readonly_fields = ['slug', 'rating_count', 'rating_sum', 'average_rating', 'view_count', 'created_at', 'updated_at', 'published_at', 'moderated_at']
    raw_id_fields = ['author', 'game', 'concept', 'moderated_by']
    filter_horizontal = ['tags']
    inlines = [GuideSectionInline]
    fieldsets = (
        ('Guide Content', {
            'fields': ('title', 'slug', 'summary', 'guide_type', 'tags')
        }),
        ('Relationships', {
            'fields': ('author', 'game', 'concept')
        }),
        ('Status & Moderation', {
            'fields': ('status', 'rejection_reason', 'moderated_by', 'moderated_at')
        }),
        ('Stats', {
            'fields': ('rating_count', 'rating_sum', 'average_rating', 'view_count')
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at', 'published_at')
        }),
    )

    def section_count(self, obj):
        return obj.sections.count()
    section_count.short_description = 'Sections'


@admin.register(GuideImage)
class GuideImageAdmin(admin.ModelAdmin):
    list_display = ['id', 'guide', 'alt_text', 'file_size_display', 'width', 'height', 'uploaded_at']
    list_filter = ['uploaded_at']
    search_fields = ['guide__title', 'alt_text', 'caption']
    readonly_fields = ['file_size', 'width', 'height', 'uploaded_at']
    raw_id_fields = ['guide']

    def file_size_display(self, obj):
        if obj.file_size < 1024:
            return f"{obj.file_size} B"
        elif obj.file_size < 1024 * 1024:
            return f"{obj.file_size / 1024:.1f} KB"
        else:
            return f"{obj.file_size / (1024 * 1024):.1f} MB"
    file_size_display.short_description = 'File Size'


@admin.register(GuideSection)
class GuideSectionAdmin(admin.ModelAdmin):
    list_display = ['title', 'guide', 'section_order', 'content_length', 'has_pending_edits', 'updated_at']
    list_filter = ['has_pending_edits', 'updated_at']
    search_fields = ['title', 'guide__title', 'content']
    readonly_fields = ['slug', 'created_at', 'updated_at']
    raw_id_fields = ['guide']
    ordering = ['guide', 'section_order']

    def content_length(self, obj):
        length = len(obj.content)
        if length > 8000:
            return f"{length} ⚠️"
        return length
    content_length.short_description = 'Chars'


@admin.register(GuideRating)
class GuideRatingAdmin(admin.ModelAdmin):
    list_display = ['profile', 'guide', 'stars', 'created_at', 'updated_at']
    list_filter = ['stars', 'created_at']
    search_fields = ['profile__psn_username', 'guide__title']
    readonly_fields = ['created_at', 'updated_at']
    raw_id_fields = ['profile', 'guide']
