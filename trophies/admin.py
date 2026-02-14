from django.contrib import admin, messages
from django.contrib.admin import SimpleListFilter
from django.db import transaction
from django.db.models import Q
from datetime import timedelta
from .models import Profile, Game, Trophy, EarnedTrophy, ProfileGame, APIAuditLog, FeaturedGame, FeaturedProfile, Concept, TitleID, TrophyGroup, UserTrophySelection, UserConceptRating, Badge, UserBadge, UserBadgeProgress, FeaturedGuide, Stage, PublisherBlacklist, Title, UserTitle, Milestone, UserMilestone, UserMilestoneProgress, Comment, CommentVote, CommentReport, ModerationLog, BannedWord, Checklist, ChecklistSection, ChecklistItem, ChecklistVote, UserChecklistProgress, ChecklistReport, ProfileGamification, StatType, StageStatValue, MonthlyRecap, GameList, GameListItem, GameListLike, Challenge, AZChallengeSlot


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
    actions = ['subtract_10_days_and_mark_synced']
    fieldsets = (
        (
            "Core Info",
            {"fields": ("psn_username", "display_psn_username", "account_id", "np_id", "user", "user_is_premium", "is_linked", "psn_history_public", "guidelines_agreed", "hide_hiddens", "discord_id", "discord_linked_at", "is_discord_verified", "verification_code")},
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

    @admin.action(description="Subtract 10 days from last_synced and set sync_status to synced")
    def subtract_10_days_and_mark_synced(self, request, queryset):
        """Subtract 10 days from last_synced and set sync_status to 'synced' for selected profiles."""
        updated_count = 0
        with transaction.atomic():
            for profile in queryset:
                old_last_synced = profile.last_synced
                old_sync_status = profile.sync_status

                # Subtract 10 days from last_synced
                profile.last_synced = profile.last_synced - timedelta(days=10)
                profile.sync_status = 'synced'
                profile.save(update_fields=['last_synced', 'sync_status'])

                updated_count += 1

        messages.success(
            request,
            f"Successfully updated {updated_count} profile(s): subtracted 10 days from last_synced and set sync_status to 'synced'."
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
        "concept_lock",
        "concept_stale",
        "title_ids",
        "is_obtainable",
        "total_defined_trophies",
        "played_count",
        "is_shovelware",
        "is_delisted",
        "has_online_trophies",
    )
    list_filter = ("has_trophy_groups", "is_regional", RegionListFilter, 'concept_lock', 'concept_stale', 'is_shovelware', 'is_delisted', 'is_obtainable', "has_online_trophies")
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
                    "concept_lock",
                    "concept_stale",
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
    actions = ['toggle_is_regional', 'add_psvr_platform', 'mark_concepts_stale']
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

    @admin.action(description="Mark concepts as stale for selected games")
    def mark_concepts_stale(self, request, queryset):
        updated = queryset.filter(concept_stale=False).update(concept_stale=True)
        messages.success(request, f"Marked {updated} game(s) as concept_stale. Concepts will be re-looked up on next sync.")

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

@admin.register(TitleID)
class TitleIDAdmin(admin.ModelAdmin):
    list_display = ('title_id', 'platform', 'region')
    search_fields = ('title_id',)
    list_filter = ('region', 'platform')

@admin.register(Concept)
class ConceptAdmin(admin.ModelAdmin):
    list_display = ('id', 'concept_id', 'unified_title', 'release_date', 'publisher_name', 'genres')
    search_fields = ('concept_id', 'unified_title')
    actions = ['duplicate_concept', 'lock_games', 'unlock_games']

    @admin.action(description="Lock concept on all games using selected concepts")
    def lock_games(self, request, queryset):
        count = 0
        for concept in queryset:
            updated = concept.games.filter(concept_lock=False).update(concept_lock=True)
            count += updated
        messages.success(request, f"Locked concept on {count} game(s).")

    @admin.action(description="Unlock concept on all games using selected concepts")
    def unlock_games(self, request, queryset):
        count = 0
        for concept in queryset:
            updated = concept.games.filter(concept_lock=True).update(concept_lock=False)
            count += updated
        messages.success(request, f"Unlocked concept on {count} game(s).")

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
    list_display = ['name', 'tier', 'badge_type', 'series_slug', 'title', 'display_series', 'required_stages', 'requires_all', 'min_required', 'earned_count', 'most_recent_concept']
    list_filter = ['tier', 'badge_type']
    search_fields = ['name', 'series_slug']
    fields = ['name', 'series_slug', 'description', 'badge_image', 'base_badge', 'tier', 'badge_type', 'title', 'display_title', 'display_series', 'discord_role_id', 'requires_all', 'min_required', 'requirements', 'earned_count']

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == 'base_badge':
            kwargs['queryset'] = Badge.objects.filter(tier=1)
        return super().formfield_for_foreignkey(db_field, request, **kwargs)

@admin.register(Stage)
class StageAdmin(admin.ModelAdmin):
    list_display = ('__str__', 'series_slug', 'stage_number', 'title', 'required_tiers', 'has_online_trophies')
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


@admin.register(Comment)
class CommentAdmin(admin.ModelAdmin):
    """Admin interface for comment moderation."""
    list_display = [
        'id',
        'profile',
        'concept',
        'trophy_id',
        'body_preview',
        'upvote_count',
        'depth',
        'is_deleted',
        'created_at',
    ]
    list_filter = [
        'is_deleted',
        'is_edited',
        'created_at',
        'depth',
    ]
    search_fields = [
        'body',
        'profile__psn_username',
        'profile__user__email',
        'concept__concept_name',
    ]
    raw_id_fields = ['profile', 'parent', 'concept']
    readonly_fields = [
        'depth',
        'created_at',
        'updated_at',
        'deleted_at',
        'upvote_count',
    ]
    ordering = ['-created_at']
    date_hierarchy = 'created_at'
    actions = ['soft_delete_comments', 'restore_comments', 'bulk_delete_comments']

    fieldsets = (
        ('Content', {
            'fields': ('concept', 'trophy_id', 'profile', 'body')
        }),
        ('Threading', {
            'fields': ('parent', 'depth')
        }),
        ('Stats & Status', {
            'fields': ('upvote_count', 'is_edited', 'is_deleted', 'deleted_at')
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at')
        }),
    )

    def body_preview(self, obj):
        """Show truncated body text."""
        if obj.is_deleted:
            return '[deleted]'
        return obj.body[:100] + '...' if len(obj.body) > 100 else obj.body
    body_preview.short_description = 'Body'

    @admin.action(description='Soft delete selected comments')
    def soft_delete_comments(self, request, queryset):
        """Soft delete comments (preserves thread structure)."""
        count = 0
        for comment in queryset.filter(is_deleted=False):
            comment.soft_delete()
            count += 1
        self.message_user(
            request,
            f"Successfully soft-deleted {count} comment(s).",
            messages.SUCCESS
        )

    @admin.action(description='Restore soft-deleted comments')
    def restore_comments(self, request, queryset):
        """Restore soft-deleted comments (admin only)."""
        count = queryset.filter(is_deleted=True).update(
            is_deleted=False,
            deleted_at=None
        )
        self.message_user(
            request,
            f"Successfully restored {count} comment(s).",
            messages.SUCCESS
        )

    @admin.action(description='Permanently delete selected comments')
    def bulk_delete_comments(self, request, queryset):
        """Hard delete comments from database."""
        count = queryset.count()
        queryset.delete()
        self.message_user(
            request,
            f"Permanently deleted {count} comment(s).",
            messages.WARNING
        )


@admin.register(CommentVote)
class CommentVoteAdmin(admin.ModelAdmin):
    """Admin interface for comment votes."""
    list_display = [
        'id',
        'comment',
        'profile',
        'created_at',
    ]
    list_filter = ['created_at']
    search_fields = [
        'profile__psn_username',
        'comment__body',
    ]
    raw_id_fields = ['comment', 'profile']
    readonly_fields = ['created_at']
    ordering = ['-created_at']
    date_hierarchy = 'created_at'


class ReportStatusFilter(SimpleListFilter):
    """Filter reports by status."""
    title = 'Status'
    parameter_name = 'status'

    def lookups(self, request, model_admin):
        return CommentReport.REPORT_STATUS

    def queryset(self, request, queryset):
        if self.value():
            return queryset.filter(status=self.value())
        return queryset


@admin.register(CommentReport)
class CommentReportAdmin(admin.ModelAdmin):
    """Admin interface for comment report moderation queue."""
    list_display = [
        'id',
        'comment_preview',
        'reporter',
        'reason',
        'status',
        'created_at',
        'reviewed_by',
        'reviewed_at',
    ]
    list_filter = [
        ReportStatusFilter,
        'reason',
        'created_at',
        'reviewed_at',
    ]
    search_fields = [
        'comment__body',
        'reporter__psn_username',
        'details',
        'admin_notes',
    ]
    raw_id_fields = ['comment', 'reporter', 'reviewed_by']
    readonly_fields = ['created_at', 'reviewed_at']
    ordering = ['-created_at']
    date_hierarchy = 'created_at'
    actions = [
        'mark_as_reviewed',
        'mark_as_dismissed',
        'take_action_and_delete',
    ]

    fieldsets = (
        ('Report Info', {
            'fields': ('comment', 'reporter', 'reason', 'details')
        }),
        ('Status', {
            'fields': ('status', 'reviewed_at', 'reviewed_by', 'admin_notes')
        }),
        ('Timestamps', {
            'fields': ('created_at',)
        }),
    )

    def comment_preview(self, obj):
        """Show truncated comment body."""
        comment = obj.comment
        if comment.is_deleted:
            return '[deleted comment]'
        return comment.body[:75] + '...' if len(comment.body) > 75 else comment.body
    comment_preview.short_description = 'Comment'

    @admin.action(description='Mark selected reports as reviewed')
    def mark_as_reviewed(self, request, queryset):
        """Mark reports as reviewed without taking action."""
        from django.utils import timezone
        count = queryset.filter(status='pending').update(
            status='reviewed',
            reviewed_at=timezone.now(),
            reviewed_by=request.user
        )
        self.message_user(
            request,
            f"Marked {count} report(s) as reviewed.",
            messages.SUCCESS
        )

    @admin.action(description='Mark selected reports as dismissed')
    def mark_as_dismissed(self, request, queryset):
        """Dismiss reports as invalid."""
        from django.utils import timezone
        count = queryset.filter(status='pending').update(
            status='dismissed',
            reviewed_at=timezone.now(),
            reviewed_by=request.user
        )
        self.message_user(
            request,
            f"Dismissed {count} report(s).",
            messages.SUCCESS
        )

    @admin.action(description='Take action: Mark reviewed & soft-delete comment')
    def take_action_and_delete(self, request, queryset):
        """Mark as action taken and soft-delete the reported comment."""
        from django.utils import timezone
        count = 0
        for report in queryset.filter(status='pending'):
            # Soft delete the comment with moderator logging
            report.comment.soft_delete(
                moderator=request.user,
                reason=f"Admin action via Django admin on report #{report.id}",
                request=request
            )
            # Mark report as action taken
            report.status = 'action_taken'
            report.reviewed_at = timezone.now()
            report.reviewed_by = request.user
            report.save(update_fields=['status', 'reviewed_at', 'reviewed_by'])
            count += 1
        self.message_user(
            request,
            f"Took action on {count} report(s) and soft-deleted the comments.",
            messages.WARNING
        )


@admin.register(ModerationLog)
class ModerationLogAdmin(admin.ModelAdmin):
    """Admin interface for moderation log (read-only)."""
    list_display = [
        'timestamp',
        'moderator',
        'action_type',
        'comment_author',
        'comment_preview_short',
        'concept',
    ]
    list_filter = [
        'action_type',
        'moderator',
        'timestamp',
    ]
    search_fields = [
        'original_body',
        'comment_author__psn_username',
        'moderator__username',
        'reason',
        'internal_notes',
    ]
    readonly_fields = [
        'timestamp',
        'moderator',
        'action_type',
        'comment',
        'comment_id_snapshot',
        'comment_author',
        'original_body',
        'concept',
        'trophy_id',
        'related_report',
        'reason',
        'internal_notes',
        'ip_address',
    ]
    ordering = ['-timestamp']
    date_hierarchy = 'timestamp'

    # Make read-only (don't allow creation/deletion via admin)
    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def comment_preview_short(self, obj):
        """Show truncated original body."""
        return obj.original_body[:50] + '...' if len(obj.original_body) > 50 else obj.original_body
    comment_preview_short.short_description = 'Comment'


@admin.register(BannedWord)
class BannedWordAdmin(admin.ModelAdmin):
    """Admin interface for managing banned words."""
    list_display = [
        'word',
        'is_active',
        'use_word_boundaries',
        'added_by',
        'added_at',
    ]
    list_filter = [
        'is_active',
        'use_word_boundaries',
        'added_at',
    ]
    search_fields = [
        'word',
        'notes',
    ]
    readonly_fields = [
        'added_by',
        'added_at',
    ]
    ordering = ['word']
    date_hierarchy = 'added_at'

    def save_model(self, request, obj, form, change):
        """Automatically set added_by to current user on creation."""
        if not change:  # Only on creation
            obj.added_by = request.user
        super().save_model(request, obj, form, change)

        # Clear the banned words cache when any word is added/modified
        from django.core.cache import cache
        cache.delete('banned_words:active')

    def delete_model(self, request, obj):
        """Clear cache when deleting banned words."""
        super().delete_model(request, obj)
        from django.core.cache import cache
        cache.delete('banned_words:active')

    def delete_queryset(self, request, queryset):
        """Clear cache when bulk deleting banned words."""
        super().delete_queryset(request, queryset)
        from django.core.cache import cache
        cache.delete('banned_words:active')


# ---------- Checklist Admin ----------

@admin.register(Checklist)
class ChecklistAdmin(admin.ModelAdmin):
    """Admin interface for checklist moderation."""
    list_display = [
        'id',
        'title',
        'profile',
        'concept',
        'status',
        'upvote_count',
        'progress_save_count',
        'total_items_display',
        'is_deleted',
        'created_at',
    ]
    list_filter = [
        'status',
        'is_deleted',
        'created_at',
    ]
    search_fields = [
        'title',
        'description',
        'profile__psn_username',
        'concept__unified_title',
    ]
    raw_id_fields = ['profile', 'concept']
    readonly_fields = [
        'created_at',
        'updated_at',
        'published_at',
        'deleted_at',
        'upvote_count',
        'progress_save_count',
    ]
    ordering = ['-created_at']
    date_hierarchy = 'created_at'
    actions = ['soft_delete_checklists', 'restore_checklists']

    def total_items_display(self, obj):
        return obj.total_items
    total_items_display.short_description = 'Items'

    @admin.action(description='Soft delete selected checklists')
    def soft_delete_checklists(self, request, queryset):
        count = 0
        for checklist in queryset.filter(is_deleted=False):
            checklist.soft_delete()
            count += 1
        messages.success(request, f"Soft-deleted {count} checklist(s).")

    @admin.action(description='Restore soft-deleted checklists')
    def restore_checklists(self, request, queryset):
        count = queryset.filter(is_deleted=True).update(is_deleted=False, deleted_at=None)
        messages.success(request, f"Restored {count} checklist(s).")


@admin.register(ChecklistSection)
class ChecklistSectionAdmin(admin.ModelAdmin):
    """Admin interface for checklist sections."""
    list_display = [
        'id',
        'subtitle',
        'checklist',
        'order',
        'item_count_display',
    ]
    list_filter = [
        'created_at',
    ]
    search_fields = [
        'subtitle',
        'checklist__title',
    ]
    raw_id_fields = ['checklist']
    ordering = ['checklist', 'order']

    def item_count_display(self, obj):
        return obj.item_count
    item_count_display.short_description = 'Items'


@admin.register(ChecklistItem)
class ChecklistItemAdmin(admin.ModelAdmin):
    """Admin interface for checklist items."""
    list_display = [
        'id',
        'text_preview',
        'section',
        'trophy_id',
        'order',
    ]
    list_filter = [
        'created_at',
    ]
    search_fields = [
        'text',
        'section__subtitle',
        'section__checklist__title',
    ]
    raw_id_fields = ['section']
    ordering = ['section', 'order']

    def text_preview(self, obj):
        return obj.text[:50] + '...' if len(obj.text) > 50 else obj.text
    text_preview.short_description = 'Text'


@admin.register(ChecklistVote)
class ChecklistVoteAdmin(admin.ModelAdmin):
    """Admin interface for checklist votes (read-only tracking)."""
    list_display = [
        'id',
        'checklist',
        'profile',
        'created_at',
    ]
    list_filter = [
        'created_at',
    ]
    search_fields = [
        'profile__psn_username',
        'checklist__title',
    ]
    raw_id_fields = ['checklist', 'profile']
    readonly_fields = ['created_at']
    ordering = ['-created_at']


@admin.register(UserChecklistProgress)
class UserChecklistProgressAdmin(admin.ModelAdmin):
    """Admin interface for user checklist progress (read-only tracking)."""
    list_display = [
        'id',
        'profile',
        'checklist',
        'items_completed',
        'total_items',
        'progress_percentage_display',
        'last_activity',
    ]
    list_filter = [
        'last_activity',
    ]
    search_fields = [
        'profile__psn_username',
        'checklist__title',
    ]
    raw_id_fields = ['profile', 'checklist']
    readonly_fields = [
        'created_at',
        'updated_at',
        'last_activity',
        'completed_items',
    ]
    ordering = ['-last_activity']

    def progress_percentage_display(self, obj):
        return f"{obj.progress_percentage:.1f}%"
    progress_percentage_display.short_description = 'Progress'


@admin.register(ChecklistReport)
class ChecklistReportAdmin(admin.ModelAdmin):
    """Admin interface for checklist report moderation queue."""
    list_display = [
        'id',
        'checklist',
        'reporter',
        'reason',
        'status',
        'created_at',
        'reviewed_by',
    ]
    list_filter = [
        'status',
        'reason',
        'created_at',
    ]
    search_fields = [
        'checklist__title',
        'reporter__psn_username',
        'details',
        'admin_notes',
    ]
    raw_id_fields = ['checklist', 'reporter', 'reviewed_by']
    readonly_fields = ['created_at', 'reviewed_at']
    ordering = ['-created_at']
    date_hierarchy = 'created_at'
    actions = ['mark_as_reviewed', 'mark_as_dismissed', 'take_action_and_delete']

    @admin.action(description='Mark selected reports as reviewed')
    def mark_as_reviewed(self, request, queryset):
        from django.utils import timezone
        count = queryset.filter(status='pending').update(
            status='reviewed',
            reviewed_at=timezone.now(),
            reviewed_by=request.user
        )
        messages.success(request, f"Marked {count} report(s) as reviewed.")

    @admin.action(description='Dismiss selected reports')
    def mark_as_dismissed(self, request, queryset):
        from django.utils import timezone
        count = queryset.filter(status='pending').update(
            status='dismissed',
            reviewed_at=timezone.now(),
            reviewed_by=request.user
        )
        messages.success(request, f"Dismissed {count} report(s).")

    @admin.action(description='Take action: Soft-delete checklist and mark report')
    def take_action_and_delete(self, request, queryset):
        from django.utils import timezone
        count = 0
        for report in queryset.filter(status='pending'):
            if not report.checklist.is_deleted:
                report.checklist.soft_delete()
            report.status = 'action_taken'
            report.reviewed_at = timezone.now()
            report.reviewed_by = request.user
            report.save(update_fields=['status', 'reviewed_at', 'reviewed_by'])
            count += 1
        messages.success(request, f"Took action on {count} report(s).")


# ---------- Gamification Admin ----------

@admin.register(ProfileGamification)
class ProfileGamificationAdmin(admin.ModelAdmin):
    """Admin interface for ProfileGamification stats."""
    list_display = [
        'profile',
        'total_badge_xp',
        'total_badges_earned',
        'last_updated',
    ]
    search_fields = ['profile__psn_username']
    readonly_fields = ['last_updated']
    ordering = ['-total_badge_xp']
    actions = ['recalculate_selected']

    @admin.action(description='Recalculate XP for selected profiles')
    def recalculate_selected(self, request, queryset):
        """Recalculate gamification stats for selected profiles."""
        from trophies.services.xp_service import update_profile_gamification
        count = 0
        for gamification in queryset:
            update_profile_gamification(gamification.profile)
            count += 1
        messages.success(request, f"Recalculated XP for {count} profile(s).")


@admin.register(StatType)
class StatTypeAdmin(admin.ModelAdmin):
    """Admin interface for gamification stat types."""
    list_display = ['slug', 'name', 'icon', 'color', 'is_active', 'display_order']
    list_editable = ['is_active', 'display_order']
    search_fields = ['slug', 'name']
    ordering = ['display_order', 'name']


@admin.register(StageStatValue)
class StageStatValueAdmin(admin.ModelAdmin):
    """Admin interface for stage stat value configuration."""
    list_display = [
        'stage',
        'stat_type',
        'bronze_value',
        'silver_value',
        'gold_value',
        'platinum_value',
    ]
    list_filter = ['stat_type', 'stage__series_slug']
    search_fields = ['stage__series_slug', 'stage__title']
    raw_id_fields = ['stage']


# ---------- Monthly Recap Admin ----------

@admin.register(MonthlyRecap)
class MonthlyRecapAdmin(admin.ModelAdmin):
    """Admin interface for Monthly Recap management."""
    list_display = [
        'profile',
        'year',
        'month',
        'total_trophies_earned',
        'platinums_earned',
        'games_started',
        'games_completed',
        'is_finalized',
        'email_sent',
        'notification_sent',
        'generated_at',
        'updated_at',
    ]
    list_filter = [
        'year',
        'month',
        'is_finalized',
        'email_sent',
        'notification_sent',
        'generated_at',
        'email_sent_at',
        'notification_sent_at',
    ]
    search_fields = [
        'profile__psn_username',
    ]
    raw_id_fields = ['profile']
    readonly_fields = [
        'generated_at',
        'updated_at',
        'email_sent_at',
        'notification_sent_at',
    ]
    ordering = ['-year', '-month', '-updated_at']
    date_hierarchy = 'generated_at'
    actions = ['finalize_recaps', 'regenerate_recaps', 'send_recap_emails', 'audit_badge_xp']

    fieldsets = (
        ('Profile & Period', {
            'fields': ('profile', 'year', 'month', 'is_finalized')
        }),
        ('Email Status', {
            'fields': ('email_sent', 'email_sent_at'),
            'description': 'Email notification tracking for monthly recap availability'
        }),
        ('In-App Notification Status', {
            'fields': ('notification_sent', 'notification_sent_at'),
            'description': 'In-app notification tracking for monthly recap availability'
        }),
        ('Trophy Stats', {
            'fields': (
                'total_trophies_earned',
                'bronzes_earned',
                'silvers_earned',
                'golds_earned',
                'platinums_earned',
            )
        }),
        ('Game Stats', {
            'fields': ('games_started', 'games_completed')
        }),
        ('Highlights (JSON)', {
            'fields': (
                'platinums_data',
                'rarest_trophy_data',
                'most_active_day',
                'activity_calendar',
                'streak_data',
                'time_analysis_data',
            ),
            'classes': ('collapse',)
        }),
        ('Quiz Data (JSON)', {
            'fields': (
                'quiz_total_trophies_data',
                'quiz_rarest_trophy_data',
                'quiz_active_day_data',
                'badge_progress_quiz_data',
            ),
            'classes': ('collapse',)
        }),
        ('Badge Stats', {
            'fields': ('badge_xp_earned', 'badges_earned_count', 'badges_data'),
            'classes': ('collapse',)
        }),
        ('Comparison Data', {
            'fields': ('comparison_data',),
            'classes': ('collapse',)
        }),
        ('Timestamps', {
            'fields': ('generated_at', 'updated_at')
        }),
    )

    @admin.action(description='Mark selected recaps as finalized')
    def finalize_recaps(self, request, queryset):
        """Mark recaps as finalized (immutable)."""
        count = queryset.filter(is_finalized=False).update(is_finalized=True)
        messages.success(request, f"Finalized {count} recap(s).")

    @admin.action(description='Regenerate selected recaps')
    def regenerate_recaps(self, request, queryset):
        """Regenerate recap data for selected entries."""
        from trophies.services.monthly_recap_service import MonthlyRecapService
        count = 0
        for recap in queryset:
            if recap.is_finalized:
                continue  # Skip finalized recaps
            try:
                MonthlyRecapService.get_or_generate_recap(
                    recap.profile,
                    recap.year,
                    recap.month,
                    force_regenerate=True
                )
                count += 1
            except Exception as e:
                messages.warning(request, f"Failed to regenerate recap for {recap}: {e}")
        messages.success(request, f"Regenerated {count} recap(s).")

    @admin.action(description='Send recap emails to selected users')
    def send_recap_emails(self, request, queryset):
        """Send (or resend) recap emails for selected monthly recaps."""
        from core.services.email_service import EmailService
        from django.conf import settings
        from django.utils import timezone
        from users.services.email_preference_service import EmailPreferenceService

        # Filter to only finalized recaps with linked users
        valid_recaps = queryset.filter(
            is_finalized=True,
            profile__is_linked=True,
            profile__user__isnull=False,
            profile__user__email__isnull=False,
        ).exclude(
            profile__user__email=''
        )

        if not valid_recaps.exists():
            messages.warning(request, "No valid recaps to email (must be finalized with linked user and email).")
            return

        sent = 0
        failed = 0
        already_sent = []

        for recap in valid_recaps:
            # Warn if email was already sent
            if recap.email_sent:
                already_sent.append(f"{recap.profile.psn_username} ({recap.month_name} {recap.year})")

            try:
                user = recap.profile.user
                profile = recap.profile

                # Get active days from activity calendar or streak data
                active_days = recap.activity_calendar.get('total_active_days', 0)
                if not active_days:
                    active_days = recap.streak_data.get('total_active_days', 0)

                # Calculate trophy tier
                count = recap.total_trophies_earned
                if count == 0:
                    trophy_tier = '0'
                elif count < 10:
                    trophy_tier = str(count)
                elif count < 25:
                    trophy_tier = '10+'
                elif count < 50:
                    trophy_tier = '25+'
                elif count < 100:
                    trophy_tier = '50+'
                elif count < 250:
                    trophy_tier = '100+'
                elif count < 500:
                    trophy_tier = '250+'
                elif count < 1000:
                    trophy_tier = '500+'
                else:
                    trophy_tier = '1000+'

                # Generate preference token for email footer
                try:
                    preference_token = EmailPreferenceService.generate_preference_token(user.id)
                    preference_url = f"{settings.SITE_URL}/users/email-preferences/?token={preference_token}"
                except Exception as e:
                    # Fallback to generic preferences page (no token)
                    preference_url = f"{settings.SITE_URL}/users/email-preferences/"

                context = {
                    'username': profile.display_psn_username or profile.psn_username,
                    'month_name': recap.month_name,
                    'year': recap.year,
                    'active_days': active_days,
                    'trophy_tier': trophy_tier,
                    'games_started': recap.games_started,
                    'total_trophies': recap.total_trophies_earned,
                    'platinums_earned': recap.platinums_earned,
                    'games_completed': recap.games_completed,
                    'badges_earned': recap.badges_earned_count,
                    'has_streak': bool(recap.streak_data.get('longest_streak', 0) > 1),
                    'recap_url': f"{settings.SITE_URL}/recap/{recap.year}/{recap.month}/",
                    'site_url': settings.SITE_URL,
                    'preference_url': preference_url,
                }

                subject = f"Your {recap.month_name} Monthly Rewind is Ready! 🏆"

                sent_count = EmailService.send_html_email(
                    subject=subject,
                    to_emails=[user.email],
                    template_name='emails/monthly_recap.html',
                    context=context,
                    fail_silently=False,
                )

                if sent_count > 0:
                    recap.email_sent = True
                    recap.email_sent_at = timezone.now()
                    recap.save(update_fields=['email_sent', 'email_sent_at'])
                    sent += 1
                else:
                    failed += 1

            except Exception as e:
                failed += 1
                messages.error(request, f"Failed to send email for {recap}: {e}")

        # Build success message
        msg_parts = []
        if sent > 0:
            msg_parts.append(f"Sent {sent} email(s)")
        if failed > 0:
            msg_parts.append(f"{failed} failed")
        if already_sent:
            msg_parts.append(f"Resent to {len(already_sent)} user(s) who already received emails")

        if sent > 0:
            messages.success(request, " • ".join(msg_parts))
        else:
            messages.warning(request, " • ".join(msg_parts))

    @admin.action(description='Audit badge XP calculations')
    def audit_badge_xp(self, request, queryset):
        """Compare stored badge XP against recalculated values."""
        from trophies.services.monthly_recap_service import MonthlyRecapService

        mismatches = []
        for recap in queryset:
            try:
                recalculated = MonthlyRecapService.get_badge_stats_for_month(
                    recap.profile, recap.year, recap.month
                )

                stored_xp = recap.badge_xp_earned
                calculated_xp = recalculated['xp_earned']

                if stored_xp != calculated_xp:
                    mismatches.append({
                        'recap': f"{recap.profile.psn_username} {recap.year}/{recap.month:02d}",
                        'stored': stored_xp,
                        'calculated': calculated_xp,
                        'diff': calculated_xp - stored_xp
                    })
            except Exception as e:
                messages.error(request, f"Error auditing {recap}: {e}")

        if mismatches:
            msg = f"Found {len(mismatches)} XP discrepancy(s):\n"
            for m in mismatches[:10]:  # Show first 10
                msg += f"  {m['recap']}: stored={m['stored']:,}, calculated={m['calculated']:,} (diff={m['diff']:+,})\n"
            if len(mismatches) > 10:
                msg += f"  ... and {len(mismatches) - 10} more\n"
            messages.warning(request, msg)
        else:
            messages.success(request, f"All {queryset.count()} recap(s) have correct badge XP.")


# ---------- Game List Admin ----------

class GameListItemInline(admin.TabularInline):
    model = GameListItem
    extra = 0
    fields = ('game', 'position', 'note', 'added_at')
    readonly_fields = ('added_at',)
    raw_id_fields = ('game',)
    ordering = ('position',)


@admin.register(GameList)
class GameListAdmin(admin.ModelAdmin):
    list_display = [
        'id',
        'name',
        'profile',
        'game_count',
        'like_count',
        'is_public',
        'is_deleted',
        'created_at',
    ]
    list_filter = [
        'is_public',
        'is_deleted',
        'created_at',
    ]
    search_fields = [
        'name',
        'description',
        'profile__psn_username',
    ]
    raw_id_fields = ['profile']
    readonly_fields = [
        'game_count',
        'like_count',
        'view_count',
        'created_at',
        'updated_at',
        'deleted_at',
    ]
    ordering = ['-created_at']
    date_hierarchy = 'created_at'
    inlines = [GameListItemInline]
    actions = ['soft_delete_lists', 'restore_lists']

    @admin.action(description='Soft delete selected game lists')
    def soft_delete_lists(self, request, queryset):
        count = 0
        for game_list in queryset.filter(is_deleted=False):
            game_list.soft_delete()
            count += 1
        messages.success(request, f"Soft-deleted {count} game list(s).")

    @admin.action(description='Restore soft-deleted game lists')
    def restore_lists(self, request, queryset):
        count = queryset.filter(is_deleted=True).update(is_deleted=False, deleted_at=None)
        messages.success(request, f"Restored {count} game list(s).")


@admin.register(GameListLike)
class GameListLikeAdmin(admin.ModelAdmin):
    list_display = ['id', 'game_list', 'profile', 'created_at']
    list_filter = ['created_at']
    search_fields = ['profile__psn_username', 'game_list__name']
    raw_id_fields = ['game_list', 'profile']
    readonly_fields = ['created_at']
    ordering = ['-created_at']


class AZChallengeSlotInline(admin.TabularInline):
    model = AZChallengeSlot
    extra = 0
    fields = ['letter', 'game', 'is_completed', 'completed_at', 'assigned_at']
    readonly_fields = ['completed_at', 'assigned_at']
    raw_id_fields = ['game']
    ordering = ['letter']


@admin.register(Challenge)
class ChallengeAdmin(admin.ModelAdmin):
    list_display = ['id', 'name', 'profile', 'challenge_type', 'filled_count', 'completed_count', 'is_complete', 'is_deleted', 'created_at']
    list_filter = ['challenge_type', 'is_complete', 'is_deleted']
    search_fields = ['name', 'profile__psn_username']
    raw_id_fields = ['profile']
    readonly_fields = ['created_at', 'updated_at', 'completed_at', 'deleted_at', 'view_count']
    ordering = ['-created_at']
    inlines = [AZChallengeSlotInline]


@admin.register(AZChallengeSlot)
class AZChallengeSlotAdmin(admin.ModelAdmin):
    list_display = ['id', 'challenge', 'letter', 'game', 'is_completed', 'assigned_at']
    list_filter = ['is_completed', 'letter']
    search_fields = ['challenge__name', 'challenge__profile__psn_username', 'game__title_name']
    raw_id_fields = ['challenge', 'game']
    readonly_fields = ['completed_at', 'assigned_at']
    ordering = ['challenge', 'letter']
