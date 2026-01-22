from django.contrib import admin, messages
from django.contrib.admin import SimpleListFilter
from django.db import transaction
from django.db.models import Q
from datetime import timedelta
from .models import Profile, Game, Trophy, EarnedTrophy, ProfileGame, APIAuditLog, FeaturedGame, FeaturedProfile, Event, Concept, TitleID, TrophyGroup, UserTrophySelection, UserConceptRating, Badge, UserBadge, UserBadgeProgress, FeaturedGuide, Stage, PublisherBlacklist, Title, UserTitle, Milestone, UserMilestone, UserMilestoneProgress, Comment, CommentVote, CommentReport, ModerationLog, BannedWord


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
    list_display = ['name', 'tier', 'badge_type', 'series_slug', 'title', 'display_series', 'required_stages', 'requires_all', 'min_required', 'earned_count', 'most_recent_concept']
    list_filter = ['tier', 'badge_type']
    search_fields = ['name', 'series_slug']
    fields = ['name', 'series_slug', 'description', 'badge_image', 'base_badge', 'tier', 'badge_type', 'title', 'display_title', 'display_series', 'user_title', 'discord_role_id', 'requires_all', 'min_required', 'requirements', 'earned_count']

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
