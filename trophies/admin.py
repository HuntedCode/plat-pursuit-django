from django import forms
from django.contrib import admin, messages
from django.contrib.admin import SimpleListFilter
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Count, F, IntegerField, Prefetch, Q, Value
from django.db.models.functions import Cast, Coalesce
from django.forms.models import BaseInlineFormSet
from django.urls import reverse
from django.utils import timezone
from django.utils.html import format_html, format_html_join
from datetime import timedelta
from .models import Profile, Game, Trophy, EarnedTrophy, ProfileGame, APIAuditLog, FeaturedGame, FeaturedProfile, Concept, TitleID, TrophyGroup, ConceptTrophyGroup, UserTrophySelection, UserConceptRating, Badge, UserBadge, UserBadgeProgress, ProfileBadgeShowcase, ProfileShowcase, FeaturedGuide, Stage, ConceptBundle, DeveloperBlacklist, Title, UserTitle, Milestone, UserMilestone, UserMilestoneProgress, Comment, CommentVote, CommentReport, ModerationLog, BannedWord, ProfileGamification, StatType, StageStatValue, MonthlyRecap, GameList, GameListItem, GameListLike, Challenge, AZChallengeSlot, GameFamily, Review, ReviewVote, ReviewReply, ReviewReport, ReviewModerationLog, DashboardConfig, StageCompletionEvent, Roadmap, RoadmapStep, RoadmapStepTrophy, TrophyGuide, RoadmapEditLock, RoadmapRevision, RoadmapNote, RoadmapNoteRead, Company, ConceptCompany, IGDBMatch, ConceptJoinReview, RematchSuggestion, ConceptSplitEvent, GameFlag, Genre, ConceptGenre, Theme, ConceptTheme, GameEngine, ConceptEngine, EngineCompany, ScoutAccount, Franchise, ConceptFranchise, Checklist, ChecklistSection, ChecklistItem, ChecklistVote, UserChecklistProgress, ChecklistReport


# Register your models here.
@admin.register(Profile)
class ProfileAdmin(admin.ModelAdmin):
    list_display = (
        "psn_username",
        "id",
        "account_id",
        "user",
        "user_is_premium",
        "roadmap_role",
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
        "total_trophies",
        "total_unearned",
        "tour_completed_at",
        "game_detail_tour_completed_at",
        "badge_detail_tour_completed_at",
    )
    list_filter = (
        "is_linked",
        "is_plus",
        "sync_tier",
        "sync_status",
        "user_is_premium",
        "roadmap_role",
        "is_discord_verified",
        "psn_history_public",
    )
    search_fields = (
        "psn_username",
        "display_psn_username",
        "account_id",
        "np_id",
        "user__username__iexact",
        "user__email",
        "about_me",
    )
    raw_id_fields = ("user",)
    ordering = ("psn_username",)
    date_hierarchy = "last_synced"
    actions = [
        'subtract_10_days_and_mark_synced',
        'recheck_badges',
        'move_jobs_to_high_priority',
        'move_jobs_to_medium_priority',
        'move_jobs_to_low_priority',
        'move_jobs_to_bulk_priority',
        'add_as_scout',
    ]
    fieldsets = (
        (
            "Core Info",
            {"fields": ("psn_username", "display_psn_username", "account_id", "np_id", "user", "user_is_premium", "roadmap_role", "is_linked", "psn_history_public", "guidelines_agreed", "tour_completed_at", "game_detail_tour_completed_at", "badge_detail_tour_completed_at", "hide_hiddens", "discord_id", "discord_linked_at", "is_discord_verified", "verification_code")},
        ),
        (
            "Profile Details",
            {"fields": ("avatar_url", "about_me", "languages_used", "is_plus", "selected_background")},
        ),
        (
            "Trophy Summary",
            {"fields": ("trophy_level", "progress", "tier", "earned_trophy_summary", 'total_trophies', 'total_unearned', 'total_bronzes', 'total_silvers', 'total_golds', 'total_plats', 'total_hiddens', 'total_games', 'total_completes', 'avg_progress')},
        ),
        ("Sync Info", {"fields": ("extra_data", "last_synced", "sync_status", "sync_progress_value", "sync_progress_target", "sync_tier")}),
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

    @admin.action(description="Recheck all badges for selected profiles")
    def recheck_badges(self, request, queryset):
        """Run a full badge recheck for selected profiles."""
        import logging
        from trophies.services.badge_service import initial_badge_check

        logger = logging.getLogger("psn_api")
        success = 0
        failed_profiles = []
        for profile in queryset:
            try:
                initial_badge_check(profile, discord_notify=False)
                success += 1
            except Exception as e:
                logger.exception(f"Badge recheck failed for {profile.psn_username}")
                failed_profiles.append(profile.psn_username)

        if success:
            messages.success(
                request,
                f"Successfully rechecked badges for {success} profile(s)."
            )
        if failed_profiles:
            messages.error(
                request,
                f"Failed to recheck badges for: {', '.join(failed_profiles)}"
            )

    @admin.action(description="Add selected profiles as scout accounts")
    def add_as_scout(self, request, queryset):
        created = 0
        skipped = 0
        for profile in queryset:
            _, was_created = ScoutAccount.objects.get_or_create(
                profile=profile,
                defaults={'added_by': request.user},
            )
            if was_created:
                created += 1
            else:
                skipped += 1
        parts = []
        if created:
            parts.append(f"Created {created} scout account(s)")
        if skipped:
            parts.append(f"skipped {skipped} (already scouts)")
        messages.success(request, '. '.join(parts) + '.')

    @admin.action(description="Move queued sync jobs to HIGH priority")
    def move_jobs_to_high_priority(self, request, queryset):
        self._move_jobs_to_queue(request, queryset, 'high_priority')

    @admin.action(description="Move queued sync jobs to MEDIUM priority")
    def move_jobs_to_medium_priority(self, request, queryset):
        self._move_jobs_to_queue(request, queryset, 'medium_priority')

    @admin.action(description="Move queued sync jobs to LOW priority")
    def move_jobs_to_low_priority(self, request, queryset):
        self._move_jobs_to_queue(request, queryset, 'low_priority')

    @admin.action(description="Move queued sync jobs to BULK priority (lowest)")
    def move_jobs_to_bulk_priority(self, request, queryset):
        self._move_jobs_to_queue(request, queryset, 'bulk_priority')

    # Queues that track per-profile job counters (must match PSNManager.COUNTED_QUEUES)
    _COUNTED_QUEUES = ('low_priority', 'medium_priority', 'bulk_priority')

    def _move_jobs_to_queue(self, request, queryset, target_queue):
        """Move all queued sync jobs for selected profiles to the target priority queue."""
        import json
        import logging
        from trophies.util_modules.cache import redis_client

        logger = logging.getLogger("psn_api")
        target_queue_key = f"{target_queue}_jobs"
        source_queues = ['high_priority', 'medium_priority', 'low_priority', 'bulk_priority']
        profile_ids = {str(p.id) for p in queryset}
        profile_names = {str(p.id): p.psn_username for p in queryset}

        total_moved = 0
        per_profile_moved = {}

        for source_queue in source_queues:
            if source_queue == target_queue:
                continue

            source_queue_key = f"{source_queue}_jobs"
            all_jobs = redis_client.lrange(source_queue_key, 0, -1)

            for job_json in all_jobs:
                try:
                    job_data = json.loads(job_json)
                    job_profile_id = str(job_data.get('profile_id', ''))
                except (json.JSONDecodeError, TypeError):
                    continue

                if job_profile_id not in profile_ids:
                    continue

                removed = redis_client.lrem(source_queue_key, 1, job_json)
                if removed == 0:
                    continue

                redis_client.lpush(target_queue_key, job_json)
                total_moved += 1
                per_profile_moved[job_profile_id] = per_profile_moved.get(job_profile_id, 0) + 1

                # Update counters for counted queues
                if source_queue in self._COUNTED_QUEUES:
                    counter_key = f"profile_jobs:{job_profile_id}:{source_queue}"
                    current = int(redis_client.get(counter_key) or 0)
                    if current > 0:
                        redis_client.decr(counter_key)
                    if current <= 1:
                        redis_client.delete(counter_key)

                if target_queue in self._COUNTED_QUEUES:
                    redis_client.incr(f"profile_jobs:{job_profile_id}:{target_queue}")
                    redis_client.sadd("active_profiles", job_profile_id)

        # Clean up active_profiles for profiles with zero counted jobs
        for pid in profile_ids:
            total = sum(
                int(redis_client.get(f"profile_jobs:{pid}:{q}") or 0)
                for q in self._COUNTED_QUEUES
            )
            if total <= 0:
                redis_client.srem("active_profiles", pid)
                for q in self._COUNTED_QUEUES:
                    redis_client.delete(f"profile_jobs:{pid}:{q}")

        queue_display = target_queue.replace('_', ' ').upper()
        if total_moved > 0:
            details = ", ".join(
                f"{profile_names.get(pid, pid)}: {count}"
                for pid, count in per_profile_moved.items()
            )
            logger.info(
                f"Admin '{request.user.username}' moved {total_moved} sync job(s) "
                f"to {queue_display} queue. [{details}]"
            )
            messages.success(
                request,
                f"Moved {total_moved} job(s) to {queue_display} queue. [{details}]"
            )
        else:
            messages.info(
                request,
                f"No pending jobs found for the selected profile(s) to move to {queue_display}."
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
        "shovelware_status",
        "shovelware_lock",
        "is_delisted",
        "has_online_trophies",
        "has_buggy_trophies",
    )
    list_select_related = ('concept',)
    list_filter = (
        "has_trophy_groups",
        "is_regional",
        RegionListFilter,
        'concept_lock',
        'concept_stale',
        'lock_title',
        'force_title_icon',
        'shovelware_status',
        'shovelware_lock',
        'is_delisted',
        'is_obtainable',
        "has_online_trophies",
        "has_buggy_trophies",
    )
    search_fields = (
        "title_name",
        "np_communication_id",
        "concept__unified_title",
        "concept__concept_id",
        "concept__slug",
    )
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
                    "is_obtainable",
                    "is_delisted",
                    "has_online_trophies",
                    "has_buggy_trophies",
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
        (
            "Shovelware Detection",
            {"fields": ("shovelware_status", "shovelware_lock", "shovelware_updated_at")},
        ),
    )
    readonly_fields = ('shovelware_updated_at',)
    actions = ['toggle_is_regional', 'add_psvr_platform', 'add_psvr2_platform', 'mark_concepts_stale', 'copy_concept_icon', 'lock_concept', 'unlock_concept', 'mark_as_shovelware', 'clear_shovelware_flag', 'reset_shovelware_auto', 'mark_unobtainable', 'mark_obtainable', 'mark_has_online_trophies', 'mark_no_online_trophies', 'mark_has_buggy_trophies', 'mark_no_buggy_trophies']
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
            messages.info(request, 'No changes made. "PSVR" already present in selected games.')

    @admin.action(description='Add "PSVR2" to platforms for selected games')
    def add_psvr2_platform(self, request, queryset):
        updated_count = 0
        with transaction.atomic():
            for game in queryset:
                if 'PSVR2' not in game.title_platform:
                    game.title_platform.append('PSVR2')
                    game.save(update_fields=['title_platform'])
                    updated_count += 1
        if updated_count:
            messages.success(request, f"Added 'PSVR2' to {updated_count} game(s).")
        else:
            messages.info(request, 'No changes made. "PSVR2" already present in selected games.')

    @admin.action(description="Mark concepts as stale for selected games")
    def mark_concepts_stale(self, request, queryset):
        updated = queryset.filter(concept_stale=False).update(concept_stale=True)
        messages.success(request, f"Marked {updated} game(s) as concept_stale. Concepts will be re-looked up on next sync.")

    @admin.action(description="Copy concept icon to title_image")
    def copy_concept_icon(self, request, queryset):
        updated = 0
        skipped = 0
        for game in queryset.select_related('concept'):
            if game.concept and game.concept.concept_icon_url:
                game.title_image = game.concept.concept_icon_url
                game.save(update_fields=['title_image'])
                updated += 1
            else:
                skipped += 1
        msg = f"Updated title_image for {updated} game(s)."
        if skipped:
            msg += f" Skipped {skipped} (no concept or no concept icon)."
        messages.success(request, msg)

    @admin.action(description="Lock concept on selected games")
    def lock_concept(self, request, queryset):
        updated = queryset.filter(concept_lock=False).update(concept_lock=True)
        messages.success(request, f"Locked concept on {updated} game(s).")

    @admin.action(description="Unlock concept on selected games")
    def unlock_concept(self, request, queryset):
        updated = queryset.filter(concept_lock=True).update(concept_lock=False)
        messages.success(request, f"Unlocked concept on {updated} game(s).")

    @admin.action(description="Mark as shovelware (manual override)")
    def mark_as_shovelware(self, request, queryset):
        from django.utils import timezone as tz
        updated = queryset.update(
            shovelware_status='manually_flagged',
            shovelware_lock=True,
            shovelware_updated_at=tz.now(),
        )
        messages.success(request, f"Manually flagged {updated} game(s) as shovelware (locked).")

    @admin.action(description="Clear shovelware flag (manual override)")
    def clear_shovelware_flag(self, request, queryset):
        from django.utils import timezone as tz
        updated = queryset.update(
            shovelware_status='manually_cleared',
            shovelware_lock=True,
            shovelware_updated_at=tz.now(),
        )
        messages.success(request, f"Manually cleared shovelware flag for {updated} game(s) (locked).")

    @admin.action(description="Reset to auto-detection (unlock)")
    def reset_shovelware_auto(self, request, queryset):
        from trophies.services.shovelware_detection_service import ShovelwareDetectionService
        count = 0
        for game in queryset.select_related('concept').prefetch_related('trophies'):
            game.shovelware_lock = False
            game.save(update_fields=['shovelware_lock'])
            ShovelwareDetectionService.evaluate_game(game)
            count += 1
        messages.success(request, f"Unlocked and re-evaluated {count} game(s) with auto-detection.")

    @admin.action(description="Mark as unobtainable")
    def mark_unobtainable(self, request, queryset):
        updated = queryset.filter(is_obtainable=True).update(is_obtainable=False)
        messages.success(request, f"Marked {updated} game(s) as unobtainable.")

    @admin.action(description="Mark as obtainable")
    def mark_obtainable(self, request, queryset):
        updated = queryset.filter(is_obtainable=False).update(is_obtainable=True)
        messages.success(request, f"Marked {updated} game(s) as obtainable.")

    @admin.action(description="Mark as having online trophies")
    def mark_has_online_trophies(self, request, queryset):
        updated = queryset.filter(has_online_trophies=False).update(has_online_trophies=True)
        messages.success(request, f"Marked {updated} game(s) as having online trophies.")

    @admin.action(description="Mark as no online trophies")
    def mark_no_online_trophies(self, request, queryset):
        updated = queryset.filter(has_online_trophies=True).update(has_online_trophies=False)
        messages.success(request, f"Marked {updated} game(s) as not having online trophies.")

    @admin.action(description="Mark as having buggy trophies")
    def mark_has_buggy_trophies(self, request, queryset):
        updated = queryset.filter(has_buggy_trophies=False).update(has_buggy_trophies=True)
        messages.success(request, f"Marked {updated} game(s) as having buggy trophies.")

    @admin.action(description="Mark as no buggy trophies")
    def mark_no_buggy_trophies(self, request, queryset):
        updated = queryset.filter(has_buggy_trophies=True).update(has_buggy_trophies=False)
        messages.success(request, f"Marked {updated} game(s) as not having buggy trophies.")

    def save_model(self, request, obj, form, change):
        if change and 'concept' in form.changed_data:
            old_concept_id = form.initial.get('concept')
            old_concept = Concept.objects.filter(pk=old_concept_id).first() if old_concept_id else None
            # If staff cleared the concept entirely, mint a fresh PP_* stub so
            # the game always has a concept and orphaned old-concept data has
            # somewhere to be absorbed.
            if obj.concept is None:
                obj.concept = Concept.create_default_concept(obj)
                messages.info(
                    request,
                    f"Concept was cleared; created stub concept "
                    f"'{obj.concept.concept_id}' ({obj.concept.unified_title}) "
                    "as the new destination."
                )
            new_concept = obj.concept
            super().save_model(request, obj, form, change)
            # Invalidate game page caches
            from django.core.cache import cache
            cache.delete(f"game:imageurls:{obj.np_communication_id}")
            # Absorb orphaned old concept into the new one
            if old_concept and old_concept.games.count() == 0:
                new_concept.absorb(old_concept)
                old_concept.delete()
                from trophies.services.comment_service import CommentService
                from trophies.services.rating_service import RatingService
                CommentService.invalidate_cache(new_concept)
                RatingService.invalidate_cache(new_concept)
        else:
            super().save_model(request, obj, form, change)

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs.annotate(
            total_trophies_count=(
                Coalesce(Cast(F('defined_trophies__bronze'), IntegerField()), Value(0)) +
                Coalesce(Cast(F('defined_trophies__silver'), IntegerField()), Value(0)) +
                Coalesce(Cast(F('defined_trophies__gold'), IntegerField()), Value(0)) +
                Coalesce(Cast(F('defined_trophies__platinum'), IntegerField()), Value(0))
            )
        )

    def total_defined_trophies(self, obj):
        return getattr(obj, 'total_trophies_count', 0)

    total_defined_trophies.short_description = "Total Trophies"
    total_defined_trophies.admin_order_field = 'total_trophies_count'


@admin.register(ProfileGame)
class ProfileGameAdmin(admin.ModelAdmin):
    list_display = (
        "profile",
        "game",
        "game_played_count",
        "progress",
        "earned_trophies_count",
        "has_plat",
        "play_duration",
        "last_played_date_time",
        "last_updated_datetime",
    )
    list_select_related = ('profile', 'game')
    list_filter = (
        "user_hidden",
        "has_plat",
        "hidden_flag",
        "game__title_platform",
    )
    search_fields = (
        "profile__psn_username",
        "profile__display_psn_username",
        "profile__account_id",
        "game__title_name",
        "game__np_communication_id",
        "game__concept__unified_title",
        "game__concept__slug",
    )
    raw_id_fields = ("profile", "game")
    ordering = ("-last_updated_datetime",)
    date_hierarchy = "last_played_date_time"

    def game_played_count(self, obj):
        return obj.game.played_count
    game_played_count.short_description = 'Played Count'
    game_played_count.admin_order_field = 'game__played_count'


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
    list_select_related = ('game',)
    list_filter = ("trophy_type", "trophy_rarity", "game__title_platform")
    search_fields = (
        "trophy_name",
        "trophy_detail",
        "game__title_name",
        "game__np_communication_id",
        "game__concept__unified_title",
    )
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



@admin.register(EarnedTrophy)
class EarnedTrophyAdmin(admin.ModelAdmin):
    list_display = (
        "profile",
        "trophy",
        "trophy_type_display",
        "earned",
        'progress',
        "trophy_earned_count",
        "earned_date_time",
        "last_updated",
    )
    list_select_related = ('profile', 'trophy')
    list_filter = ("earned", "trophy_hidden", "earned_date_time", "trophy__trophy_type")
    search_fields = (
        "profile__psn_username",
        "profile__display_psn_username",
        "trophy__trophy_name",
        "trophy__game__title_name",
        "trophy__game__np_communication_id",
    )
    raw_id_fields = ("profile", "trophy")
    ordering = ("-last_updated",)
    date_hierarchy = "earned_date_time"

    def trophy_type_display(self, obj):
        return obj.trophy.trophy_type
    trophy_type_display.short_description = 'Trophy Type'
    trophy_type_display.admin_order_field = 'trophy__trophy_type'

    def trophy_earned_count(self, obj):
        return obj.trophy.earned_count
    trophy_earned_count.short_description = 'Earned Count'
    trophy_earned_count.admin_order_field = 'trophy__earned_count'


@admin.register(APIAuditLog)
class APIAuditLogAdmin(admin.ModelAdmin):
    list_display = ("timestamp", "endpoint", "profile", "status_code", "response_time", "calls_remaining")
    list_select_related = ('profile',)
    list_filter = ("status_code", "timestamp")
    search_fields = ("endpoint", "profile__psn_username", "profile__display_psn_username")
    ordering = ("-timestamp",)
    date_hierarchy = "timestamp"

@admin.register(FeaturedGame)
class FeaturedGameAdmin(admin.ModelAdmin):
    list_display = ('game', 'priority', 'reason', 'start_date', 'end_date')
    list_select_related = ('game',)
    search_fields = ('game__title_name', 'game__np_communication_id')
    list_filter = ('reason',)
    raw_id_fields = ('game',)

@admin.register(FeaturedProfile)
class FeaturedProfileAdmin(admin.ModelAdmin):
    list_display = ('profile', 'priority', 'reason', 'start_date', 'end_date')
    list_select_related = ('profile',)
    search_fields = ('profile__psn_username', 'profile__display_psn_username')
    list_filter = ('reason',)
    raw_id_fields = ('profile',)

@admin.register(TitleID)
class TitleIDAdmin(admin.ModelAdmin):
    list_display = ('title_id', 'platform', 'region')
    search_fields = ('title_id',)
    list_filter = ('region', 'platform')

class ConceptGameInline(admin.TabularInline):
    model = Game
    extra = 0
    fields = ('np_communication_id', 'title_name', 'title_platform', 'region', 'shovelware_status')
    readonly_fields = ('np_communication_id', 'title_name', 'title_platform', 'region', 'shovelware_status')
    can_delete = False
    show_change_link = True

    def has_add_permission(self, request, obj=None):
        return False


class ConceptCompanyInline(admin.TabularInline):
    model = ConceptCompany
    extra = 0
    readonly_fields = ('company', 'is_developer', 'is_publisher', 'is_porting', 'is_supporting')
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False


class ConceptFranchiseInline(admin.TabularInline):
    """Manage franchise/collection links from inside the Concept admin page.

    Defined here (not next to FranchiseAdmin) because ConceptAdmin is declared
    before the franchise admin block in this file and Python name resolution
    needs the inline class to exist at class-body evaluation time.
    """
    model = ConceptFranchise
    fk_name = 'concept'
    extra = 0
    fields = ('franchise', 'is_main')
    autocomplete_fields = ('franchise',)
    can_delete = True

    def get_queryset(self, request):
        return super().get_queryset(request).select_related('franchise')


class ConceptAnchorStatusFilter(SimpleListFilter):
    """Filter Concepts by IGDB-anchor migration status.

    Four buckets:
      * Anchored: anchor_migration_completed_at set → at canonical identity.
      * Not yet anchored: anchor_migration_completed_at null → covers both
        the "never attempted" and "attempted but deferred" buckets below.
      * Attempted but not anchored (deferred): the migration touched this
        Concept (last_attempt_at set) but couldn't anchor — NO_MATCH,
        SPLIT, COLLISION, or fingerprint mismatch. These are the ones
        staff actually needs to investigate.
      * Never attempted: neither timestamp set → migration hasn't reached
        this Concept yet. Routine pending; no review needed.
    """

    title = 'Anchor status'
    parameter_name = 'anchor'

    def lookups(self, request, model_admin):
        return (
            ('anchored', 'Anchored (IGDB-canonical)'),
            ('pending', 'Not yet anchored'),
            ('attempted_pending', 'Attempted but not anchored (deferred)'),
            ('never_attempted', 'Never attempted'),
        )

    def queryset(self, request, queryset):
        value = self.value()
        if value == 'anchored':
            return queryset.filter(anchor_migration_completed_at__isnull=False)
        if value == 'pending':
            return queryset.filter(anchor_migration_completed_at__isnull=True)
        if value == 'attempted_pending':
            return queryset.filter(
                anchor_migration_completed_at__isnull=True,
                anchor_migration_last_attempt_at__isnull=False,
            )
        if value == 'never_attempted':
            return queryset.filter(
                anchor_migration_completed_at__isnull=True,
                anchor_migration_last_attempt_at__isnull=True,
            )
        return queryset


@admin.register(Concept)
class ConceptAdmin(admin.ModelAdmin):
    list_display = (
        'id', 'concept_id', 'unified_title',
        'anchor_migration_completed_at', 'anchor_migration_last_attempt_at',
        'title_lock', 'title_reviewed_at',
        'family_pk_display', 'family_display',
        'release_date_display', 'developers_display',
        'publishers_display', 'genres_display',
    )
    list_select_related = ('family', 'igdb_match')
    list_filter = ('family__is_verified', 'title_lock', ConceptAnchorStatusFilter)
    # Searching ``concept_companies__company__name`` matches ANY linked
    # company (developer, publisher, porter, supporter). Broader than just
    # developers, but more useful in practice — admins typing a studio name
    # find the concept regardless of the role flag.
    search_fields = (
        'concept_id', 'unified_title', 'slug', 'family__canonical_name',
        'concept_companies__company__name',
    )
    raw_id_fields = ('family',)
    readonly_fields = ('title_reviewed_at',)
    actions = [
        'manual_anchor_selected',
        'manual_anchor_games_selected',
        'duplicate_concept',
        'lock_games', 'unlock_games',
        'lock_titles', 'unlock_titles',
        'clear_title_review',
    ]
    inlines = [ConceptGameInline, ConceptCompanyInline, ConceptFranchiseInline]

    def get_queryset(self, request):
        # Prefetches keep the IGDB-derived list_display columns from N+1ing.
        # ConceptCompany is fetched twice with different filters (developers
        # vs. publishers) and attached to separate attrs so each column's
        # display method can read its slice without re-querying.
        return super().get_queryset(request).prefetch_related(
            Prefetch(
                'concept_companies',
                queryset=ConceptCompany.objects.filter(is_developer=True).select_related('company'),
                to_attr='_developer_links',
            ),
            Prefetch(
                'concept_companies',
                queryset=ConceptCompany.objects.filter(is_publisher=True).select_related('company'),
                to_attr='_publisher_links',
            ),
            Prefetch(
                'concept_genres',
                queryset=ConceptGenre.objects.select_related('genre'),
                to_attr='_genre_links',
            ),
        )

    def developers_display(self, obj):
        devs = getattr(obj, '_developer_links', None) or []
        if not devs:
            return '—'
        return ', '.join(cc.company.name for cc in devs)
    developers_display.short_description = 'Developers'

    def publishers_display(self, obj):
        """Publishers via ConceptCompany (IGDB-derived). Falls back to the
        legacy PSN `Concept.publisher_name` for un-migrated concepts.
        """
        pubs = getattr(obj, '_publisher_links', None) or []
        if pubs:
            return ', '.join(cc.company.name for cc in pubs)
        return obj.publisher_name or '—'
    publishers_display.short_description = 'Publishers'

    def genres_display(self, obj):
        """Genres via ConceptGenre (IGDB-derived). Falls back to the legacy
        PSN `Concept.genres` JSONField for un-migrated concepts.
        """
        links = getattr(obj, '_genre_links', None) or []
        if links:
            return ', '.join(cg.genre.name for cg in links)
        legacy = obj.genres or []
        if isinstance(legacy, list) and legacy:
            return ', '.join(str(g) for g in legacy)
        return '—'
    genres_display.short_description = 'Genres'

    def release_date_display(self, obj):
        """Prefer IGDB's first PS release date (Tier 1 from IGDBMatch).
        Falls back to the legacy PSN `Concept.release_date` for un-migrated
        concepts.
        """
        match = getattr(obj, 'igdb_match', None)
        igdb_date = getattr(match, 'igdb_first_release_date', None)
        if igdb_date:
            return igdb_date.strftime('%Y-%m-%d')
        if obj.release_date:
            return obj.release_date.strftime('%Y-%m-%d')
        return '—'
    release_date_display.short_description = 'Release date'
    release_date_display.admin_order_field = 'igdb_match__igdb_first_release_date'

    def family_pk_display(self, obj):
        """GameFamily primary key, exposed in the list view so staff can grab
        it for commands like `anchor_concepts --family <id>` without having
        to click into each concept."""
        return obj.family_id or '—'
    family_pk_display.short_description = 'Family ID'
    family_pk_display.admin_order_field = 'family_id'

    def family_display(self, obj):
        if not obj.family_id:
            return '—'
        label = obj.family.canonical_name
        if obj.family.igdb_id:
            label += f' (IGDB {obj.family.igdb_id})'
        return label
    family_display.short_description = 'GameFamily'
    family_display.admin_order_field = 'family__canonical_name'

    @admin.action(description='Manual anchor to IGDB id (deferred concepts)')
    def manual_anchor_selected(self, request, queryset):
        """Two-step action: render an intermediate form, then anchor on POST.

        Use case: a Concept the migration deferred (NO_MATCH) because the
        matcher couldn't find an IGDB hit on its own — but you (the staff)
        know what IGDB id is correct. Enter the id per row, submit, and the
        same vetting pipeline the migration uses (identity cross-check,
        trophy-fingerprint vs target's existing Games) runs. Clean Games
        anchor; flagged Games get a ConceptJoinReview entry like normal.
        """
        from django.contrib.admin.helpers import ACTION_CHECKBOX_NAME
        from django.shortcuts import render
        from trophies.services.concept_anchor_service import (
            anchor_concept_to_canonical,
        )

        concepts = list(queryset.select_related('family').prefetch_related('games'))

        # Step 2: POST → do the work.
        if request.POST.get('apply') == f'Anchor {len(concepts)} concept(s)':
            total_moved = 0
            total_flagged = 0
            errors = 0
            for c in concepts:
                raw_id = request.POST.get(f'igdb_id_{c.pk}', '').strip()
                if not raw_id:
                    continue  # blank input → skip this row
                try:
                    result = anchor_concept_to_canonical(
                        c, raw_id, user=request.user,
                    )
                except Exception as exc:
                    errors += 1
                    messages.error(
                        request,
                        f'Concept {c.concept_id!r}: anchor failed — {exc}',
                    )
                    continue
                if not result['ok']:
                    errors += 1
                    messages.error(
                        request,
                        f'Concept {c.concept_id!r}: {result["error"]}',
                    )
                    continue
                total_moved += result['moved_count']
                total_flagged += result['flagged_count']
                if result['flagged_games']:
                    detail = '; '.join(
                        f'game pk={g.pk} ({", ".join(flags)})'
                        for g, flags in result['flagged_games']
                    )
                    messages.warning(
                        request,
                        f'Concept {c.concept_id!r} → '
                        f'{result["target_concept"].concept_id!r}: '
                        f'{result["moved_count"]} moved, '
                        f'{result["flagged_count"]} flagged ({detail})',
                    )
                else:
                    messages.success(
                        request,
                        f'Concept {c.concept_id!r} → '
                        f'{result["target_concept"].concept_id!r}: '
                        f'{result["moved_count"]} moved cleanly',
                    )
            if total_moved or total_flagged:
                messages.info(
                    request,
                    f'Total: {total_moved} game(s) anchored, '
                    f'{total_flagged} flagged for review.',
                )
            return None  # fall through to the default changelist response

        # Step 1: render the form.
        rows = []
        for c in concepts:
            match = getattr(c, 'igdb_match', None)
            rows.append({
                'concept': c,
                'games': list(c.games.all()),
                'current_match': match,
                'family': c.family,
            })
        context = {
            **self.admin_site.each_context(request),
            'rows': rows,
            'selected_pks': [c.pk for c in concepts],
            'action_checkbox_name': ACTION_CHECKBOX_NAME,
            'title': 'Manual anchor to IGDB id',
        }
        return render(request, 'admin/trophies/concept/manual_anchor.html', context)

    @admin.action(description='Manual anchor PER GAME to IGDB id (split a concept)')
    def manual_anchor_games_selected(self, request, queryset):
        """Per-Game analogue of `manual_anchor_selected`.

        Use case: a Concept lumps Games that actually belong to different IGDB
        versions (e.g. a Remaster trophy list living inside the original-game
        Concept). Enter an IGDB id per Game; each Game is routed independently
        via `anchor_game_to_canonical`. Clean Games move; flagged Games get a
        ConceptJoinReview. Per-game vetting handles the trophy-fingerprint and
        identity cross-checks the same way the bulk anchor does.
        """
        from django.contrib.admin.helpers import ACTION_CHECKBOX_NAME
        from django.shortcuts import render
        from trophies.services.concept_anchor_service import (
            anchor_game_to_canonical,
        )

        concepts = list(queryset.select_related('family').prefetch_related('games'))

        if request.POST.get('apply', '').startswith('Anchor'):
            total_moved = 0
            total_flagged = 0
            total_already = 0
            errors = 0
            for c in concepts:
                for g in c.games.all():
                    raw_id = request.POST.get(f'igdb_id_game_{g.pk}', '').strip()
                    if not raw_id:
                        continue  # blank → skip this game
                    try:
                        result = anchor_game_to_canonical(
                            g, raw_id, user=request.user,
                        )
                    except Exception as exc:
                        errors += 1
                        messages.error(
                            request,
                            f'Game pk={g.pk}: anchor failed — {exc}',
                        )
                        continue
                    if not result['ok']:
                        errors += 1
                        messages.error(
                            request,
                            f'Game pk={g.pk}: {result["error"]}',
                        )
                        continue
                    if result['already_anchored']:
                        total_already += 1
                        messages.info(
                            request,
                            f'Game pk={g.pk}: already on '
                            f'{result["target_concept"].concept_id!r}, no-op.',
                        )
                        continue
                    if result['flagged']:
                        total_flagged += 1
                        messages.warning(
                            request,
                            f'Game pk={g.pk} → '
                            f'{result["target_concept"].concept_id!r}: flagged '
                            f'({", ".join(result["flag_reasons"])})',
                        )
                    elif result['moved']:
                        total_moved += 1
                        messages.success(
                            request,
                            f'Game pk={g.pk} → '
                            f'{result["target_concept"].concept_id!r}: moved cleanly',
                        )
            if total_moved or total_flagged or total_already:
                messages.info(
                    request,
                    f'Total: {total_moved} game(s) anchored, '
                    f'{total_flagged} flagged, {total_already} already-anchored.',
                )
            return None  # fall through to the default changelist response

        # Step 1: render the form.
        rows = []
        for c in concepts:
            match = getattr(c, 'igdb_match', None)
            games_info = []
            for g in c.games.all():
                games_info.append({
                    'game': g,
                    'defined_trophies': g.defined_trophies or {},
                })
            rows.append({
                'concept': c,
                'games': games_info,
                'current_match': match,
                'family': c.family,
            })
        context = {
            **self.admin_site.each_context(request),
            'rows': rows,
            'selected_pks': [c.pk for c in concepts],
            'action_checkbox_name': ACTION_CHECKBOX_NAME,
            'title': 'Manual anchor PER GAME to IGDB id',
        }
        return render(
            request,
            'admin/trophies/concept/manual_anchor_games.html',
            context,
        )

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

    @admin.action(description="Lock title (PSN sync won't overwrite unified_title)")
    def lock_titles(self, request, queryset):
        count = queryset.filter(title_lock=False).update(title_lock=True)
        messages.success(request, f"Locked title on {count} concept(s).")

    @admin.action(description="Unlock title (allow PSN sync to update unified_title)")
    def unlock_titles(self, request, queryset):
        count = queryset.filter(title_lock=True).update(title_lock=False)
        messages.success(request, f"Unlocked title on {count} concept(s).")

    @admin.action(description="Clear title review (re-surface in review_title_merges)")
    def clear_title_review(self, request, queryset):
        count = queryset.filter(title_reviewed_at__isnull=False).update(title_reviewed_at=None)
        messages.success(
            request,
            f"Cleared title_reviewed_at on {count} concept(s). They'll re-surface on next review_title_merges run."
        )

    @admin.action(description="Duplicate selected concepts")
    def duplicate_concept(self, request, queryset):
        for concept in queryset:
            original_id = concept.concept_id
            # Fetch a fresh copy to avoid mutating the original queryset object
            new_concept = Concept.objects.get(pk=concept.pk)
            new_concept.pk = None
            # Clear title_ids to prevent sync ambiguity (duplicate title_ids
            # would cause game lookups to match against the wrong concept)
            new_concept.title_ids = []
            new_concept.slug = ''  # Let save() regenerate with deduplication

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
    list_select_related = ('game',)
    search_fields = ('game__title_name', 'trophy_group_name')
    raw_id_fields = ('game',)

@admin.register(UserTrophySelection)
class UserTrophySelectionAdmin(admin.ModelAdmin):
    list_display = ('profile', 'trophy_name_display', 'game_name_display')
    list_select_related = ('profile', 'earned_trophy__trophy__game')
    search_fields = ('profile__psn_username',)

    def trophy_name_display(self, obj):
        return obj.earned_trophy.trophy.trophy_name
    trophy_name_display.short_description = 'Trophy Name'

    def game_name_display(self, obj):
        return obj.earned_trophy.trophy.game.title_name
    game_name_display.short_description = 'Game'

@admin.register(UserConceptRating)
class UserConceptRatingAdmin(admin.ModelAdmin):
    list_display = ('profile', 'concept', 'difficulty', 'grindiness', 'hours_to_platinum', 'fun_ranking', 'overall_rating', 'created_at', 'updated_at')
    list_select_related = ('profile', 'concept')
    list_filter = ('created_at', 'updated_at')
    search_fields = (
        'profile__psn_username',
        'profile__display_psn_username',
        'concept__unified_title',
        'concept__concept_id',
    )
    raw_id_fields = ('profile', 'concept')
    date_hierarchy = 'created_at'

class StageInline(admin.TabularInline):
    model = Stage
    extra = 1
    fields = ('stage_number', 'title', 'stage_icon', 'concepts', 'required_tiers')
    autocomplete_fields = ['concepts']

@admin.register(Badge)
class BadgeAdmin(admin.ModelAdmin):
    list_display = ['name', 'is_live', 'tier', 'badge_type', 'series_slug', 'title', 'display_series', 'required_stages', 'requires_all', 'min_required', 'earned_count', 'most_recent_concept', 'funded_by', 'submitted_by']
    list_select_related = ('most_recent_concept', 'title', 'funded_by', 'submitted_by')
    list_filter = ['is_live', 'tier', 'badge_type']
    list_editable = ['is_live']
    search_fields = ['name', 'series_slug', 'description']
    readonly_fields = ['created_at', 'earned_count', 'view_count', 'required_stages', 'required_value']
    date_hierarchy = 'created_at'
    fields = [
        'name', 'is_live', 'series_slug', 'description', 'badge_image', 'base_badge',
        'tier', 'badge_type', 'title', 'display_title', 'display_series',
        'discord_role_id', 'requires_all', 'min_required', 'requirements',
        'most_recent_concept', 'funded_by', 'submitted_by',
        'earned_count', 'view_count', 'required_stages', 'required_value',
        'created_at',
    ]
    actions = ['mark_series_live', 'mark_series_not_live']

    def mark_series_live(self, request, queryset):
        series_slugs = set(queryset.values_list('series_slug', flat=True))
        updated = Badge.objects.filter(series_slug__in=series_slugs).update(is_live=True)
        self.message_user(request, f"Marked {updated} badges across {len(series_slugs)} series as live.")
    mark_series_live.short_description = "Mark series live (all tiers)"

    def mark_series_not_live(self, request, queryset):
        series_slugs = set(queryset.values_list('series_slug', flat=True))
        updated = Badge.objects.filter(series_slug__in=series_slugs).update(is_live=False)
        self.message_user(request, f"Marked {updated} badges across {len(series_slugs)} series as not live.")
    mark_series_not_live.short_description = "Mark series not live (all tiers)"

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == 'base_badge':
            kwargs['queryset'] = Badge.objects.filter(tier=1)
        return super().formfield_for_foreignkey(db_field, request, **kwargs)

class ConceptBundleInlineFormSet(BaseInlineFormSet):
    """Validates that no Concept appears in multiple bundles on the same Stage,
    that no bundle Concept is also a standalone qualifier on the parent Stage,
    and that no bundle is empty."""

    def clean(self):
        super().clean()

        # Collect concept ids per non-deleted bundle form. Reject empty bundles.
        seen_by_concept = {}
        for form in self.forms:
            if not hasattr(form, 'cleaned_data'):
                continue
            if form.cleaned_data.get('DELETE'):
                continue
            if not form.cleaned_data:
                # Skip blank extra forms the admin renders by default
                continue
            concepts = form.cleaned_data.get('concepts')
            label = form.cleaned_data.get('label')
            if not concepts:
                # Only flag rows the user actually started filling in (has a label
                # or otherwise has data) to avoid yelling about untouched extras.
                if label:
                    raise ValidationError(
                        f"Bundle '{label}' has no Concepts. Add at least one Concept "
                        f"or delete the bundle row."
                    )
                continue
            for concept in concepts:
                if concept.id in seen_by_concept:
                    raise ValidationError(
                        f"Concept '{concept}' appears in multiple bundles on this Stage. "
                        f"A concept can belong to at most one bundle per Stage."
                    )
                seen_by_concept[concept.id] = form

        # Reject overlap with the parent Stage's standalone concepts
        stage = self.instance
        if stage and stage.pk and seen_by_concept:
            standalone_ids = set(stage.concepts.values_list('id', flat=True))
            overlap = set(seen_by_concept.keys()) & standalone_ids
            if overlap:
                raise ValidationError(
                    f"Concept id(s) {sorted(overlap)} are both standalone qualifiers on this Stage "
                    f"and members of a bundle. Remove them from Stage.concepts first."
                )


class ConceptBundleInline(admin.TabularInline):
    model = ConceptBundle
    formset = ConceptBundleInlineFormSet
    fields = ('label', 'concepts', 'sort_order')
    autocomplete_fields = ['concepts']
    extra = 0
    verbose_name = "Concept Bundle"
    verbose_name_plural = "Concept Bundles (episodic / grouped qualifiers)"


class StageAdminForm(forms.ModelForm):
    """Rejects standalone Stage.concepts that overlap with existing bundle members."""

    class Meta:
        model = Stage
        fields = '__all__'

    def clean_concepts(self):
        concepts = self.cleaned_data.get('concepts') or []
        if self.instance and self.instance.pk:
            bundle_member_ids = set(
                ConceptBundle.objects.filter(stage=self.instance)
                .values_list('concepts__id', flat=True)
            )
            bundle_member_ids.discard(None)
            overlap = {c.id for c in concepts} & bundle_member_ids
            if overlap:
                raise ValidationError(
                    f"Concept id(s) {sorted(overlap)} are bundle members on this Stage. "
                    f"Remove them from the bundle before adding as standalone qualifiers."
                )
        return concepts


@admin.register(Stage)
class StageAdmin(admin.ModelAdmin):
    form = StageAdminForm
    list_display = (
        '__str__', 'series_slug', 'stage_number', 'title',
        'concepts_display', 'bundle_concepts_display',
        'required_tiers', 'has_online_trophies',
    )
    list_filter = ('series_slug', 'stage_number', 'has_online_trophies')
    search_fields = (
        'title', 'series_slug',
        'concepts__concept_id', 'concepts__unified_title',
        'concept_bundles__concepts__concept_id',
        'concept_bundles__concepts__unified_title',
    )
    autocomplete_fields = ['concepts']
    inlines = [ConceptBundleInline]

    # Cap how many concepts we list per column on the changelist; rows
    # with more than this many qualifiers show "+N more" to keep the cell
    # scannable. The full list is always visible on the Stage's detail page.
    _LIST_CONCEPT_LIMIT = 6

    def get_queryset(self, request):
        # Prefetch both qualifier paths so concepts_display / bundle_concepts_display
        # don't N+1 across rows on the changelist. ConceptBundle members are
        # nested two layers deep.
        return super().get_queryset(request).prefetch_related(
            'concepts',
            'concept_bundles__concepts',
        )

    @staticmethod
    def _format_concept(concept):
        """Render one Concept as "Title (concept_id)" for changelist columns."""
        title = concept.unified_title or '(no title)'
        return f'{title} ({concept.concept_id})'

    def _format_concept_list(self, concepts):
        formatted = [self._format_concept(c) for c in concepts]
        if not formatted:
            return '—'
        if len(formatted) <= self._LIST_CONCEPT_LIMIT:
            return ', '.join(formatted)
        shown = ', '.join(formatted[:self._LIST_CONCEPT_LIMIT])
        return f'{shown}, +{len(formatted) - self._LIST_CONCEPT_LIMIT} more'

    def concepts_display(self, obj):
        """Standalone qualifier Concepts (Stage.concepts M2M)."""
        return self._format_concept_list(list(obj.concepts.all()))
    concepts_display.short_description = 'Standalone Concepts'

    def bundle_concepts_display(self, obj):
        """Concepts inside this Stage's ConceptBundles.

        Bundle members are grouped per-bundle so the qualifier shape stays
        legible — e.g. "[Ep 1: Title (100), Title2 (101)]" rather than a
        flat undifferentiated list.
        """
        bundles = list(obj.concept_bundles.all())
        if not bundles:
            return '—'
        parts = []
        for bundle in bundles:
            formatted = [
                self._format_concept(c) for c in bundle.concepts.all()
            ]
            label = bundle.label or f'#{bundle.pk}'
            if not formatted:
                parts.append(f'[{label}: (empty)]')
            elif len(formatted) <= self._LIST_CONCEPT_LIMIT:
                parts.append(f'[{label}: {", ".join(formatted)}]')
            else:
                shown = ', '.join(formatted[:self._LIST_CONCEPT_LIMIT])
                parts.append(
                    f'[{label}: {shown}, +{len(formatted) - self._LIST_CONCEPT_LIMIT} more]'
                )
        return ' '.join(parts)
    bundle_concepts_display.short_description = 'Bundle Concepts'

@admin.register(UserBadge)
class UserBadgeAdmin(admin.ModelAdmin):
    list_display = ['profile', 'badge', 'earned_at', 'is_displayed']
    list_select_related = ('profile', 'badge')
    list_filter = ['is_displayed', 'earned_at']
    search_fields = ['profile__psn_username', 'profile__display_psn_username', 'badge__name', 'badge__series_slug']
    raw_id_fields = ('profile', 'badge')
    date_hierarchy = 'earned_at'

@admin.register(ProfileBadgeShowcase)
class ProfileBadgeShowcaseAdmin(admin.ModelAdmin):
    list_display = ['profile', 'badge', 'display_order', 'created_at']
    list_select_related = ('profile', 'badge')
    list_filter = ['display_order']
    search_fields = ['profile__psn_username', 'profile__display_psn_username', 'badge__name']
    raw_id_fields = ('profile', 'badge')
    date_hierarchy = 'created_at'


@admin.register(ProfileShowcase)
class ProfileShowcaseAdmin(admin.ModelAdmin):
    list_display = ['profile', 'showcase_type', 'sort_order', 'is_active', 'updated_at']
    list_select_related = ('profile',)
    list_filter = ['showcase_type', 'is_active']
    search_fields = ['profile__psn_username', 'profile__display_psn_username']
    raw_id_fields = ('profile',)
    readonly_fields = ('created_at', 'updated_at')
    fieldsets = (
        (None, {
            'fields': ('profile', 'showcase_type', 'sort_order', 'is_active'),
        }),
        ('Configuration', {
            'fields': ('config',),
            'description': 'Type-specific JSON config (e.g. selected item IDs). '
                           'Leave empty for automatic showcases.',
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',),
        }),
    )

@admin.register(UserBadgeProgress)
class UserBadgeProgressAdmin(admin.ModelAdmin):
    list_display = ['profile', 'badge', 'completed_concepts', 'progress_value', 'last_checked']
    list_select_related = ('profile', 'badge')
    search_fields = ['profile__psn_username', 'profile__display_psn_username', 'badge__name']
    raw_id_fields = ('profile', 'badge')

@admin.register(StageCompletionEvent)
class StageCompletionEventAdmin(admin.ModelAdmin):
    list_display = ['profile', 'badge', 'stage', 'concept', 'completed_at', 'created_at']
    list_select_related = ('profile', 'badge', 'stage', 'concept')
    list_filter = ['completed_at']
    search_fields = [
        'profile__psn_username',
        'profile__display_psn_username',
        'badge__name',
        'concept__unified_title',
    ]
    raw_id_fields = ('profile', 'badge', 'stage', 'concept')
    readonly_fields = ('created_at',)
    date_hierarchy = 'completed_at'
    
@admin.register(FeaturedGuide)
class FeaturedGuideAdmin(admin.ModelAdmin):
    list_display = ['concept', 'start_date', 'end_date', 'priority']
    list_select_related = ('concept',)
    list_filter = ['start_date', 'end_date']
    search_fields = ['concept__unified_title', 'concept__concept_id']
    raw_id_fields = ('concept',)

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == 'concept':
            kwargs['queryset'] = Concept.objects.exclude(Q(guide_slug__isnull=True) | Q(guide_slug=''))
        return super().formfield_for_foreignkey(db_field, request, **kwargs)

@admin.register(DeveloperBlacklist)
class DeveloperBlacklistAdmin(admin.ModelAdmin):
    list_display = ['company', 'is_blacklisted', 'concept_count', 'date_added']
    list_filter = ['is_blacklisted']
    search_fields = ['company__name']
    raw_id_fields = ('company',)

    def get_queryset(self, request):
        return super().get_queryset(request).select_related('company')

    def concept_count(self, obj):
        return obj.flagged_concept_count
    concept_count.short_description = "Flagged Concepts"

@admin.register(Title)
class TitleAdmin(admin.ModelAdmin):
    list_display = ['name', 'created_at']
    search_fields = ['name']
    date_hierarchy = 'created_at'

@admin.register(UserTitle)
class UserTitleAdmin(admin.ModelAdmin):
    list_display = ['profile', 'title', 'source_type', 'source_id', 'earned_at', 'is_displayed']
    list_select_related = ('profile', 'title')
    list_filter = ['source_type', 'is_displayed']
    search_fields = ['profile__psn_username', 'profile__display_psn_username', 'title__name']
    raw_id_fields = ('profile', 'title')
    date_hierarchy = 'earned_at'

@admin.register(Milestone)
class MilestoneAdmin(admin.ModelAdmin):
    list_display = ['name', 'title', 'discord_role_id', 'criteria_type', 'criteria_details', 'premium_only', 'required_value', 'earned_count']
    list_filter = ['premium_only', 'criteria_type']
    search_fields = ['name']

@admin.register(UserMilestone)
class UserMilestoneAdmin(admin.ModelAdmin):
    list_display = ['profile', 'milestone', 'earned_at']
    list_select_related = ('profile', 'milestone')
    search_fields = ['profile__psn_username', 'profile__display_psn_username', 'milestone__name']
    raw_id_fields = ('profile', 'milestone')
    date_hierarchy = 'earned_at'

@admin.register(UserMilestoneProgress)
class UserMilestoneProgressAdmin(admin.ModelAdmin):
    list_display = ['profile', 'milestone', 'progress_value', 'last_checked']
    list_select_related = ('profile', 'milestone')
    search_fields = ['profile__psn_username', 'profile__display_psn_username', 'milestone__name']
    raw_id_fields = ('profile', 'milestone')


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
    list_select_related = ('profile', 'concept')
    list_filter = [
        'is_deleted',
        'is_edited',
        'created_at',
        'depth',
    ]
    search_fields = [
        'body',
        'profile__psn_username',
        'profile__display_psn_username',
        'profile__user__email',
        'concept__unified_title',
        'concept__concept_id',
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

    @admin.action(description='Permanently delete selected comments (must be soft-deleted first)')
    def bulk_delete_comments(self, request, queryset):
        """Hard delete comments from database. Only operates on already soft-deleted comments."""
        not_soft_deleted = queryset.filter(is_deleted=False).count()
        if not_soft_deleted:
            self.message_user(
                request,
                f"{not_soft_deleted} comment(s) are not soft-deleted. Soft-delete them first before permanently deleting.",
                messages.ERROR
            )
            return
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
    list_select_related = ('comment', 'profile')
    list_filter = ['created_at']
    search_fields = [
        'profile__psn_username',
        'profile__display_psn_username',
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
    list_select_related = ('comment', 'reporter', 'reviewed_by')
    list_filter = [
        ReportStatusFilter,
        'reason',
        'created_at',
        'reviewed_at',
    ]
    search_fields = [
        'comment__body',
        'reporter__psn_username',
        'reporter__display_psn_username',
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
    list_select_related = ('moderator', 'comment_author', 'concept')
    list_filter = [
        'action_type',
        'moderator',
        'timestamp',
    ]
    search_fields = [
        'original_body',
        'comment_author__psn_username',
        'comment_author__display_psn_username',
        'moderator__username',
        'moderator__email',
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
    list_select_related = ('added_by',)
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


# ---------- Deprecated Checklist Admin (historical data viewer) ----------
# The Checklist system was replaced by the Roadmap system. These tables are
# retained for historical data retrieval only. Admins are read-only to prevent
# accidental edits to frozen data. `verbose_name_plural` is mutated at import
# time to cluster these entries under a [Deprecated] prefix in the admin index.
# (Runtime mutation of _meta is safe here: makemigrations reads the declared
# Meta class on the model, not the runtime Options object.)

Checklist._meta.verbose_name_plural = '[Deprecated] Checklists'
ChecklistSection._meta.verbose_name_plural = '[Deprecated] Checklist sections'
ChecklistItem._meta.verbose_name_plural = '[Deprecated] Checklist items'
ChecklistVote._meta.verbose_name_plural = '[Deprecated] Checklist votes'
UserChecklistProgress._meta.verbose_name_plural = '[Deprecated] Checklist user progress'
ChecklistReport._meta.verbose_name_plural = '[Deprecated] Checklist reports'


class _DeprecatedReadOnlyAdmin(admin.ModelAdmin):
    """Read-only admin base for deprecated models kept for historical data."""

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def get_readonly_fields(self, request, obj=None):
        return [f.name for f in self.model._meta.fields]


class ChecklistSectionInline(admin.TabularInline):
    model = ChecklistSection
    extra = 0
    can_delete = False
    show_change_link = True
    fields = ('order', 'subtitle', 'created_at')
    readonly_fields = ('order', 'subtitle', 'created_at')
    ordering = ('order',)

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(Checklist)
class ChecklistAdmin(_DeprecatedReadOnlyAdmin):
    list_display = ('id', 'title', 'profile', 'concept', 'status', 'upvote_count', 'progress_save_count', 'view_count', 'is_deleted', 'created_at')
    list_select_related = ('profile', 'concept')
    list_filter = ('status', 'is_deleted')
    search_fields = ('title', 'description', 'profile__psn_username', 'concept__unified_title')
    raw_id_fields = ('concept', 'selected_game', 'profile')
    ordering = ('-created_at',)
    date_hierarchy = 'created_at'
    inlines = [ChecklistSectionInline]


@admin.register(ChecklistSection)
class ChecklistSectionAdmin(_DeprecatedReadOnlyAdmin):
    list_display = ('id', 'subtitle', 'checklist', 'order', 'created_at')
    list_select_related = ('checklist',)
    search_fields = ('subtitle', 'description', 'checklist__title')
    raw_id_fields = ('checklist',)
    ordering = ('checklist', 'order')


@admin.register(ChecklistItem)
class ChecklistItemAdmin(_DeprecatedReadOnlyAdmin):
    list_display = ('id', 'text_preview', 'item_type', 'section', 'order', 'trophy_id', 'created_at')
    list_select_related = ('section__checklist',)
    list_filter = ('item_type',)
    search_fields = ('text', 'section__subtitle', 'section__checklist__title')
    raw_id_fields = ('section',)
    ordering = ('section', 'order')

    def text_preview(self, obj):
        if not obj.text:
            return f'[{obj.item_type}]'
        return obj.text[:60] + ('...' if len(obj.text) > 60 else '')
    text_preview.short_description = 'Text'


@admin.register(ChecklistVote)
class ChecklistVoteAdmin(_DeprecatedReadOnlyAdmin):
    list_display = ('id', 'checklist', 'profile', 'created_at')
    list_select_related = ('checklist', 'profile')
    search_fields = ('checklist__title', 'profile__psn_username')
    raw_id_fields = ('checklist', 'profile')
    ordering = ('-created_at',)
    date_hierarchy = 'created_at'


@admin.register(UserChecklistProgress)
class UserChecklistProgressAdmin(_DeprecatedReadOnlyAdmin):
    list_display = ('id', 'profile', 'checklist', 'items_completed', 'total_items', 'progress_percentage', 'last_activity')
    list_select_related = ('profile', 'checklist')
    search_fields = ('profile__psn_username', 'checklist__title')
    raw_id_fields = ('profile', 'checklist')
    ordering = ('-last_activity',)
    date_hierarchy = 'last_activity'


@admin.register(ChecklistReport)
class ChecklistReportAdmin(_DeprecatedReadOnlyAdmin):
    list_display = ('id', 'checklist', 'reporter', 'reason', 'status', 'created_at')
    list_select_related = ('checklist', 'reporter')
    list_filter = ('reason', 'status')
    search_fields = ('checklist__title', 'reporter__psn_username', 'details')
    raw_id_fields = ('checklist', 'reporter')
    ordering = ('-created_at',)
    date_hierarchy = 'created_at'


# ---------- Roadmap Admin ----------

class RoadmapStepInline(admin.TabularInline):
    model = RoadmapStep
    extra = 0
    ordering = ['order']
    raw_id_fields = ['created_by', 'last_edited_by']


@admin.register(Roadmap)
class RoadmapAdmin(admin.ModelAdmin):
    list_display = [
        'id', 'concept', 'concept_trophy_group', 'status',
        'has_tips', 'has_youtube', 'created_by', 'updated_at',
    ]
    list_select_related = ('concept', 'concept_trophy_group', 'created_by')
    list_filter = ['status']
    search_fields = [
        'concept__unified_title', 'concept_trophy_group__display_name',
    ]
    raw_id_fields = ['concept', 'concept_trophy_group', 'created_by', 'last_edited_by']
    readonly_fields = ['created_at', 'updated_at']
    inlines = [RoadmapStepInline]

    def has_tips(self, obj):
        return bool(obj.general_tips)
    has_tips.boolean = True
    has_tips.short_description = 'Tips'

    def has_youtube(self, obj):
        return bool(obj.youtube_url)
    has_youtube.boolean = True
    has_youtube.short_description = 'YouTube'


@admin.register(RoadmapStep)
class RoadmapStepAdmin(admin.ModelAdmin):
    list_display = ['id', 'title', 'roadmap', 'order']
    list_select_related = ('roadmap__concept',)
    search_fields = ['title', 'description', 'roadmap__concept__unified_title']
    raw_id_fields = ['roadmap', 'created_by', 'last_edited_by']


@admin.register(RoadmapStepTrophy)
class RoadmapStepTrophyAdmin(admin.ModelAdmin):
    list_display = ['id', 'step', 'trophy_id', 'order']
    list_select_related = ('step',)
    raw_id_fields = ['step']


@admin.register(TrophyGuide)
class TrophyGuideAdmin(admin.ModelAdmin):
    list_display = ['id', 'roadmap', 'trophy_id', 'body_preview', 'created_by']
    list_select_related = ('roadmap__concept', 'created_by')
    list_filter = ['is_missable', 'is_online', 'is_unobtainable', 'phase']
    search_fields = ['body', 'roadmap__concept__unified_title', 'created_by__psn_username']
    raw_id_fields = ['roadmap', 'created_by', 'last_edited_by']

    def body_preview(self, obj):
        return obj.body[:80] + '...' if len(obj.body) > 80 else obj.body
    body_preview.short_description = 'Body'


@admin.register(RoadmapEditLock)
class RoadmapEditLockAdmin(admin.ModelAdmin):
    list_display = ['id', 'roadmap', 'holder', 'acquired_at', 'last_heartbeat', 'expires_at', 'is_expired_flag']
    list_select_related = ('roadmap__concept', 'holder')
    search_fields = ['roadmap__concept__unified_title', 'holder__psn_username', 'holder__display_psn_username']
    raw_id_fields = ['roadmap', 'holder']
    readonly_fields = ['acquired_at', 'last_heartbeat', 'expires_at', 'branch_payload']
    date_hierarchy = 'acquired_at'
    actions = ['force_release']

    def is_expired_flag(self, obj):
        return obj.is_expired()
    is_expired_flag.boolean = True
    is_expired_flag.short_description = 'Expired?'

    @admin.action(description='Force-release selected locks (admin override)')
    def force_release(self, request, queryset):
        count = queryset.count()
        queryset.delete()
        self.message_user(request, f'{count} lock(s) released.', messages.SUCCESS)


@admin.register(RoadmapRevision)
class RoadmapRevisionAdmin(admin.ModelAdmin):
    list_display = ['id', 'roadmap', 'author', 'action_type', 'summary', 'created_at']
    list_select_related = ('roadmap__concept', 'author')
    list_filter = ['action_type']
    raw_id_fields = ['roadmap', 'author']
    readonly_fields = ['created_at', 'snapshot']
    search_fields = [
        'roadmap__concept__unified_title',
        'author__psn_username',
        'author__display_psn_username',
        'summary',
    ]
    date_hierarchy = 'created_at'
    actions = ['restore_selected_revision']

    @admin.action(description='Restore live roadmap to selected revision (only works on a single selection)')
    def restore_selected_revision(self, request, queryset):
        from trophies.services.roadmap_merge_service import MergeError, restore_revision
        if queryset.count() != 1:
            self.message_user(
                request, 'Select exactly one revision to restore.', messages.ERROR
            )
            return
        revision = queryset.first()
        actor = getattr(request.user, 'profile', None)
        if actor is None:
            self.message_user(request, 'Acting user has no Profile.', messages.ERROR)
            return
        try:
            new_rev = restore_revision(revision, actor)
        except MergeError as e:
            self.message_user(request, str(e), messages.ERROR)
            return
        self.message_user(
            request,
            f'Restored revision #{revision.id}. New revision #{new_rev.id} logs the restore.',
            messages.SUCCESS,
        )


@admin.register(RoadmapNote)
class RoadmapNoteAdmin(admin.ModelAdmin):
    list_display = ['id', 'roadmap', 'target_kind', 'author', 'status', 'body_preview', 'created_at']
    list_select_related = ('roadmap__concept', 'author', 'resolved_by')
    list_filter = ['target_kind', 'status']
    raw_id_fields = ['roadmap', 'target_step', 'target_trophy_guide', 'author', 'resolved_by']
    readonly_fields = ['created_at', 'updated_at', 'resolved_at']
    search_fields = [
        'roadmap__concept__unified_title',
        'body',
        'author__psn_username',
        'author__display_psn_username',
    ]
    date_hierarchy = 'created_at'

    def body_preview(self, obj):
        return obj.body[:80] + '...' if len(obj.body) > 80 else obj.body
    body_preview.short_description = 'Body'


@admin.register(RoadmapNoteRead)
class RoadmapNoteReadAdmin(admin.ModelAdmin):
    list_display = ['id', 'profile', 'roadmap', 'last_read_at']
    list_select_related = ('profile', 'roadmap__concept')
    raw_id_fields = ['profile', 'roadmap']
    readonly_fields = ['last_read_at']
    search_fields = [
        'profile__psn_username',
        'profile__display_psn_username',
        'roadmap__concept__unified_title',
    ]


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
    list_select_related = ('profile',)
    search_fields = ['profile__psn_username', 'profile__display_psn_username']
    raw_id_fields = ('profile',)
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
    list_select_related = ('stage', 'stat_type')
    list_filter = ['stat_type', 'stage__series_slug']
    search_fields = ['stage__series_slug', 'stage__title']
    raw_id_fields = ('stage', 'stat_type')


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
    list_select_related = ('profile',)
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
        'profile__display_psn_username',
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
                    log_email_type='monthly_recap',
                    log_user=user,
                    log_triggered_by='admin_manual',
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
    list_select_related = ('profile',)
    list_filter = [
        'is_public',
        'is_deleted',
        'created_at',
    ]
    search_fields = [
        'name',
        'description',
        'profile__psn_username',
        'profile__display_psn_username',
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
    list_select_related = ('game_list', 'profile')
    list_filter = ['created_at']
    search_fields = ['profile__psn_username', 'profile__display_psn_username', 'game_list__name']
    raw_id_fields = ['game_list', 'profile']
    readonly_fields = ['created_at']
    ordering = ['-created_at']
    date_hierarchy = 'created_at'


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
    list_select_related = ('profile',)
    list_filter = ['challenge_type', 'is_complete', 'is_deleted']
    search_fields = ['name', 'profile__psn_username', 'profile__display_psn_username']
    raw_id_fields = ['profile']
    readonly_fields = ['created_at', 'updated_at', 'completed_at', 'deleted_at', 'view_count']
    ordering = ['-created_at']
    date_hierarchy = 'created_at'
    inlines = [AZChallengeSlotInline]


@admin.register(AZChallengeSlot)
class AZChallengeSlotAdmin(admin.ModelAdmin):
    list_display = ['id', 'challenge', 'letter', 'game', 'is_completed', 'assigned_at']
    list_select_related = ('challenge', 'game')
    list_filter = ['is_completed', 'letter']
    search_fields = [
        'challenge__name',
        'challenge__profile__psn_username',
        'challenge__profile__display_psn_username',
        'game__title_name',
        'game__np_communication_id',
    ]
    raw_id_fields = ['challenge', 'game']
    readonly_fields = ['completed_at', 'assigned_at']
    ordering = ['challenge', 'letter']


@admin.register(GameFamily)
class GameFamilyAdmin(admin.ModelAdmin):
    list_display = ['canonical_name', 'igdb_id', 'is_verified', 'concept_count', 'created_at', 'updated_at']
    list_filter = ['is_verified']
    search_fields = ['canonical_name', 'admin_notes', 'igdb_id']
    readonly_fields = ['created_at', 'updated_at', 'member_concepts']
    ordering = ['canonical_name']
    fieldsets = (
        (None, {
            'fields': ('canonical_name', 'igdb_id', 'is_verified', 'admin_notes'),
        }),
        ('Members', {
            'fields': ('member_concepts',),
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at'),
        }),
    )

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs.annotate(_concept_count=Count('concepts'))

    def concept_count(self, obj):
        return obj._concept_count
    concept_count.short_description = 'Concepts'
    concept_count.admin_order_field = '_concept_count'

    def member_concepts(self, obj):
        if not obj.pk:
            return '(save the family first to see members)'
        rows = list(
            obj.concepts.only('id', 'concept_id', 'unified_title').order_by('unified_title')
        )
        if not rows:
            return '(no concepts linked to this family)'
        items = format_html_join(
            '',
            '<li><a href="{}">{}</a> <span style="color:#888;">({})</span></li>',
            (
                (
                    reverse('admin:trophies_concept_change', args=[c.pk]),
                    c.unified_title or '(no title)',
                    c.concept_id,
                )
                for c in rows
            ),
        )
        return format_html('<ul style="margin:0;padding-left:1.25em;">{}</ul>', items)
    member_concepts.short_description = 'Member concepts'


# ---------- Review System Admin ----------

@admin.register(Review)
class ReviewAdmin(admin.ModelAdmin):
    """Admin interface for review moderation."""
    list_display = [
        'id',
        'profile',
        'concept',
        'concept_trophy_group',
        'recommended',
        'body_preview',
        'helpful_count',
        'funny_count',
        'reply_count',
        'is_deleted',
        'created_at',
    ]
    list_select_related = ('profile', 'concept', 'concept_trophy_group')
    list_filter = [
        'recommended',
        'is_deleted',
        'is_edited',
        'created_at',
    ]
    search_fields = [
        'body',
        'profile__psn_username',
        'profile__display_psn_username',
        'concept__unified_title',
        'concept__concept_id',
    ]
    raw_id_fields = ['profile', 'concept', 'concept_trophy_group']
    readonly_fields = [
        'created_at',
        'updated_at',
        'deleted_at',
        'helpful_count',
        'funny_count',
        'reply_count',
        'word_count',
    ]
    ordering = ['-created_at']
    date_hierarchy = 'created_at'
    actions = ['soft_delete_reviews', 'restore_reviews']

    fieldsets = (
        ('Content', {
            'fields': ('concept', 'concept_trophy_group', 'profile', 'body', 'recommended')
        }),
        ('Stats', {
            'fields': ('helpful_count', 'funny_count', 'reply_count', 'word_count')
        }),
        ('Status', {
            'fields': ('is_edited', 'is_deleted', 'deleted_at')
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at')
        }),
    )

    def body_preview(self, obj):
        if obj.is_deleted:
            return '[deleted]'
        return obj.body[:100] + '...' if len(obj.body) > 100 else obj.body
    body_preview.short_description = 'Body'

    @admin.action(description='Soft delete selected reviews')
    def soft_delete_reviews(self, request, queryset):
        count = 0
        for review in queryset.filter(is_deleted=False):
            review.soft_delete(moderator=request.user, reason="Admin bulk action")
            count += 1
        self.message_user(request, f"Soft-deleted {count} review(s).", messages.SUCCESS)

    @admin.action(description='Restore soft-deleted reviews')
    def restore_reviews(self, request, queryset):
        count = queryset.filter(is_deleted=True).update(
            is_deleted=False,
            deleted_at=None
        )
        self.message_user(request, f"Restored {count} review(s).", messages.SUCCESS)


@admin.register(ReviewVote)
class ReviewVoteAdmin(admin.ModelAdmin):
    """Admin interface for review votes."""
    list_display = ['id', 'review', 'profile', 'vote_type', 'created_at']
    list_select_related = ('review', 'profile')
    list_filter = ['vote_type', 'created_at']
    search_fields = ['profile__psn_username', 'profile__display_psn_username']
    raw_id_fields = ['review', 'profile']
    readonly_fields = ['created_at']
    ordering = ['-created_at']
    date_hierarchy = 'created_at'


@admin.register(ReviewReply)
class ReviewReplyAdmin(admin.ModelAdmin):
    """Admin interface for review replies."""
    list_display = ['id', 'review', 'profile', 'body_preview', 'is_deleted', 'created_at']
    list_select_related = ('review', 'profile')
    list_filter = ['is_deleted', 'is_edited', 'created_at']
    search_fields = [
        'body',
        'profile__psn_username',
        'profile__display_psn_username',
        'review__concept__unified_title',
    ]
    raw_id_fields = ['review', 'profile']
    readonly_fields = ['created_at', 'updated_at', 'deleted_at']
    ordering = ['-created_at']
    date_hierarchy = 'created_at'

    def body_preview(self, obj):
        if obj.is_deleted:
            return '[deleted]'
        return obj.body[:100] + '...' if len(obj.body) > 100 else obj.body
    body_preview.short_description = 'Body'


class ReviewReportStatusFilter(SimpleListFilter):
    """Filter review reports by status."""
    title = 'Status'
    parameter_name = 'status'

    def lookups(self, request, model_admin):
        return ReviewReport.REPORT_STATUS

    def queryset(self, request, queryset):
        if self.value():
            return queryset.filter(status=self.value())
        return queryset


@admin.register(ReviewReport)
class ReviewReportAdmin(admin.ModelAdmin):
    """Admin interface for review report moderation queue."""
    list_display = [
        'id',
        'review_preview',
        'reporter',
        'reason',
        'status',
        'created_at',
        'reviewed_by',
        'reviewed_at',
    ]
    list_select_related = ('review', 'reporter', 'reviewed_by')
    list_filter = [
        ReviewReportStatusFilter,
        'reason',
        'created_at',
        'reviewed_at',
    ]
    search_fields = [
        'review__body',
        'reporter__psn_username',
        'reporter__display_psn_username',
        'details',
        'admin_notes',
    ]
    raw_id_fields = ['review', 'reporter', 'reviewed_by']
    readonly_fields = ['created_at', 'reviewed_at']
    ordering = ['-created_at']
    date_hierarchy = 'created_at'
    actions = ['mark_as_reviewed', 'mark_as_dismissed', 'take_action_and_delete']

    fieldsets = (
        ('Report Info', {
            'fields': ('review', 'reporter', 'reason', 'details')
        }),
        ('Status', {
            'fields': ('status', 'reviewed_at', 'reviewed_by', 'admin_notes')
        }),
        ('Timestamps', {
            'fields': ('created_at',)
        }),
    )

    def review_preview(self, obj):
        review = obj.review
        if review.is_deleted:
            return '[deleted review]'
        return review.body[:75] + '...' if len(review.body) > 75 else review.body
    review_preview.short_description = 'Review'

    @admin.action(description='Mark selected reports as reviewed')
    def mark_as_reviewed(self, request, queryset):
        from django.utils import timezone
        count = queryset.filter(status='pending').update(
            status='reviewed',
            reviewed_at=timezone.now(),
            reviewed_by=request.user
        )
        self.message_user(request, f"Marked {count} report(s) as reviewed.", messages.SUCCESS)

    @admin.action(description='Mark selected reports as dismissed')
    def mark_as_dismissed(self, request, queryset):
        from django.utils import timezone
        count = queryset.filter(status='pending').update(
            status='dismissed',
            reviewed_at=timezone.now(),
            reviewed_by=request.user
        )
        self.message_user(request, f"Dismissed {count} report(s).", messages.SUCCESS)

    @admin.action(description='Take action: Mark reviewed & soft-delete review')
    def take_action_and_delete(self, request, queryset):
        from django.utils import timezone
        count = 0
        for report in queryset.filter(status='pending'):
            report.review.soft_delete(
                moderator=request.user,
                reason=f"Admin action via Django admin on report #{report.id}",
                request=request
            )
            report.status = 'action_taken'
            report.reviewed_at = timezone.now()
            report.reviewed_by = request.user
            report.save(update_fields=['status', 'reviewed_at', 'reviewed_by'])
            count += 1
        self.message_user(
            request,
            f"Took action on {count} report(s) and soft-deleted the reviews.",
            messages.WARNING
        )


@admin.register(ReviewModerationLog)
class ReviewModerationLogAdmin(admin.ModelAdmin):
    """Admin interface for review moderation log (read-only)."""
    list_display = [
        'timestamp',
        'moderator',
        'action_type',
        'review_author',
        'review_preview_short',
        'concept',
    ]
    list_select_related = ('moderator', 'review_author', 'concept')
    list_filter = [
        'action_type',
        'moderator',
        'timestamp',
    ]
    search_fields = [
        'original_body',
        'review_author__psn_username',
        'review_author__display_psn_username',
        'moderator__username',
        'moderator__email',
        'reason',
        'internal_notes',
    ]
    readonly_fields = [
        'timestamp',
        'moderator',
        'action_type',
        'review',
        'review_id_snapshot',
        'review_author',
        'original_body',
        'concept',
        'related_report',
        'reason',
        'internal_notes',
        'ip_address',
    ]
    ordering = ['-timestamp']
    date_hierarchy = 'timestamp'

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def review_preview_short(self, obj):
        return obj.original_body[:50] + '...' if len(obj.original_body) > 50 else obj.original_body
    review_preview_short.short_description = 'Review'


# ---------- Other Missing Admin ----------

@admin.register(ConceptTrophyGroup)
class ConceptTrophyGroupAdmin(admin.ModelAdmin):
    """Admin interface for concept-level trophy groups."""
    list_display = ['id', 'concept', 'trophy_group_id', 'display_name', 'sort_order']
    list_select_related = ('concept',)
    list_filter = ['trophy_group_id']
    search_fields = ['concept__unified_title', 'display_name']
    raw_id_fields = ['concept']
    ordering = ['concept', 'sort_order']


@admin.register(GameListItem)
class GameListItemAdmin(admin.ModelAdmin):
    """Admin interface for game list items."""
    list_display = ['id', 'game_list', 'game', 'position', 'note_preview', 'added_at']
    list_select_related = ('game_list', 'game')
    search_fields = [
        'game__title_name',
        'game__np_communication_id',
        'game_list__name',
        'game_list__profile__psn_username',
        'game_list__profile__display_psn_username',
    ]
    raw_id_fields = ['game_list', 'game']
    readonly_fields = ['added_at']
    ordering = ['game_list', 'position']
    date_hierarchy = 'added_at'

    def note_preview(self, obj):
        if not obj.note:
            return '-'
        return obj.note[:50] + '...' if len(obj.note) > 50 else obj.note
    note_preview.short_description = 'Note'


@admin.register(DashboardConfig)
class DashboardConfigAdmin(admin.ModelAdmin):
    """Admin interface for dashboard configurations."""
    list_display = ['profile', 'module_count', 'hidden_count', 'updated_at']
    list_select_related = ('profile',)
    search_fields = ['profile__psn_username', 'profile__display_psn_username']
    raw_id_fields = ['profile']
    readonly_fields = ['updated_at']
    ordering = ['-updated_at']

    def module_count(self, obj):
        return len(obj.module_order) if obj.module_order else 0
    module_count.short_description = 'Modules'

    def hidden_count(self, obj):
        return len(obj.hidden_modules) if obj.hidden_modules else 0
    hidden_count.short_description = 'Hidden'


# ---------------------------------------------------------------------------
# IGDB Integration Admin
# ---------------------------------------------------------------------------

class CompanyConceptInline(admin.TabularInline):
    model = ConceptCompany
    extra = 0
    readonly_fields = ('concept', 'is_developer', 'is_publisher', 'is_porting', 'is_supporting')
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(Company)
class CompanyAdmin(admin.ModelAdmin):
    list_display = ('name', 'igdb_id', 'country_column', 'parent', 'company_size_display', 'concept_count')
    list_filter = ('company_size',)
    search_fields = ('name', 'slug', 'parent__name')
    raw_id_fields = ('parent', 'changed_company')
    readonly_fields = ('igdb_id', 'created_at', 'updated_at')
    date_hierarchy = 'created_at'
    inlines = [CompanyConceptInline]

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(
            _concept_count=Count('company_concepts')
        )

    def concept_count(self, obj):
        return obj._concept_count
    concept_count.short_description = 'Games'
    concept_count.admin_order_field = '_concept_count'

    def country_column(self, obj):
        # Fall back to raw numeric code when the ISO mapping doesn't recognise
        # it (unknown/new country) so admin still sees SOMETHING useful.
        return obj.country_display or (str(obj.country) if obj.country else '-')
    country_column.short_description = 'Country'
    country_column.admin_order_field = 'country'

    def company_size_display(self, obj):
        return obj.get_company_size_display() if obj.company_size else '-'
    company_size_display.short_description = 'Size'


@admin.register(Genre)
class GenreAdmin(admin.ModelAdmin):
    list_display = ('name', 'igdb_id', 'slug', 'game_count')
    search_fields = ('name', 'slug')
    readonly_fields = ('igdb_id',)

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(_game_count=Count('genre_concepts'))

    def game_count(self, obj):
        return obj._game_count
    game_count.short_description = 'Games'
    game_count.admin_order_field = '_game_count'


@admin.register(Theme)
class ThemeAdmin(admin.ModelAdmin):
    list_display = ('name', 'igdb_id', 'slug', 'game_count')
    search_fields = ('name', 'slug')
    readonly_fields = ('igdb_id',)

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(_game_count=Count('theme_concepts'))

    def game_count(self, obj):
        return obj._game_count
    game_count.short_description = 'Games'
    game_count.admin_order_field = '_game_count'


class EngineCompanyInline(admin.TabularInline):
    model = EngineCompany
    extra = 0
    autocomplete_fields = ('company',)


@admin.register(GameEngine)
class GameEngineAdmin(admin.ModelAdmin):
    list_display = ('name', 'igdb_id', 'slug', 'has_logo', 'game_count')
    search_fields = ('name', 'slug')
    readonly_fields = ('igdb_id',)
    inlines = [EngineCompanyInline]
    fieldsets = (
        (None, {'fields': ('igdb_id', 'name', 'slug')}),
        ('Metadata', {'fields': ('description', 'logo_image_id')}),
    )

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(_game_count=Count('engine_concepts'))

    def game_count(self, obj):
        return obj._game_count
    game_count.short_description = 'Games'
    game_count.admin_order_field = '_game_count'

    def has_logo(self, obj):
        return bool(obj.logo_image_id)
    has_logo.boolean = True
    has_logo.short_description = 'Logo'


# ---------------------------------------------------------------------------
# Franchise admin
# ---------------------------------------------------------------------------
#
# Franchises and collections (both stored in the same table, distinguished by
# source_type) need light editorial controls so staff can rename, fix slugs,
# or split bad merges. Surfaced via three admins:
#   - FranchiseAdmin: the franchise/collection rows themselves
#   - ConceptFranchiseAdmin: the through-table for raw link inspection
#   - FranchiseConceptInline: shown inside FranchiseAdmin so you can see
#     which concepts are attached without leaving the page
# A second inline (ConceptFranchiseInline) is wired into ConceptAdmin
# further up so you can manage franchises from the Concept side too.

class FranchiseConceptInline(admin.TabularInline):
    """Shows the concepts linked to a Franchise from the Franchise admin page."""
    model = ConceptFranchise
    fk_name = 'franchise'
    extra = 0
    fields = ('concept', 'is_main')
    autocomplete_fields = ('concept',)
    can_delete = True

    def get_queryset(self, request):
        return super().get_queryset(request).select_related('concept')


@admin.register(Franchise)
class FranchiseAdmin(admin.ModelAdmin):
    list_display = ('name', 'source_type', 'igdb_id', 'slug', 'concept_count', 'main_count')
    list_filter = ('source_type',)
    search_fields = ('name', 'slug')
    # igdb_id and source_type compose the unique constraint — staff editing
    # them by hand would risk colliding with an enrichment-managed row.
    # Rename via `name` is fine; everything else is read-only.
    readonly_fields = ('igdb_id', 'source_type')
    inlines = [FranchiseConceptInline]

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(
            _concept_count=Count('franchise_concepts', distinct=True),
            _main_count=Count(
                'franchise_concepts',
                filter=Q(franchise_concepts__is_main=True),
                distinct=True,
            ),
        )

    def concept_count(self, obj):
        return obj._concept_count
    concept_count.short_description = 'Concepts'
    concept_count.admin_order_field = '_concept_count'

    def main_count(self, obj):
        return obj._main_count
    main_count.short_description = 'As main'
    main_count.admin_order_field = '_main_count'


@admin.register(ConceptFranchise)
class ConceptFranchiseAdmin(admin.ModelAdmin):
    """Standalone listing of every concept↔franchise link.

    Useful when triaging mis-linked games (e.g. cross-namespace ID
    collisions) — list_filter on is_main + source_type lets you find
    suspicious patterns quickly.
    """
    list_display = ('concept', 'franchise', 'franchise_source_type', 'is_main')
    list_filter = ('is_main', 'franchise__source_type')
    search_fields = ('concept__unified_title', 'concept__concept_id', 'franchise__name')
    autocomplete_fields = ('concept', 'franchise')
    list_select_related = ('concept', 'franchise')

    def franchise_source_type(self, obj):
        return obj.franchise.source_type
    franchise_source_type.short_description = 'Type'
    franchise_source_type.admin_order_field = 'franchise__source_type'


class SplittableCompilationFilter(SimpleListFilter):
    """Intersection filter: IGDB-classified bundle AND PSN has 2+ Games AND not dismissed.

    `is_likely_compilation` alone is IGDB-side informational (any game_type=3/13
    entry). The concepts actually worth splitting are the ones where PSN also
    ships multiple trophy lists (i.e. `concept.games.count() >= 2`). Admins
    can mark reviewed-but-not-splittable rows via `compilation_review_dismissed`
    so they stop reappearing in the triage queue.
    """
    title = 'splittable compilation'
    parameter_name = 'splittable'

    def lookups(self, request, model_admin):
        return [
            ('yes', 'IGDB bundle AND 2+ Games (unreviewed)'),
            ('dismissed', 'Dismissed as not splittable'),
        ]

    def queryset(self, request, queryset):
        value = self.value()
        if value == 'yes':
            return queryset.filter(
                is_likely_compilation=True,
                _games_count__gte=2,
                compilation_review_dismissed=False,
            )
        if value == 'dismissed':
            return queryset.filter(compilation_review_dismissed=True)
        return queryset


class PlatformCoverageFilter(SimpleListFilter):
    """Filter IGDBMatches by whether the concept's most modern platform is covered by IGDB."""
    title = 'platform coverage'
    parameter_name = 'platform_coverage'

    # Priority order: most modern first
    PLATFORM_PRIORITY = [
        ('PSVR2', 390), ('PSVR', 165), ('PS5', 167), ('PS4', 48),
        ('PS3', 9), ('PSVITA', 46), ('PSP', 38), ('PS2', 8), ('PS1', 7),
    ]

    def lookups(self, request, model_admin):
        return [
            ('covered', 'Top platform covered by IGDB'),
            ('missing', 'Top platform NOT in IGDB'),
        ]

    def queryset(self, request, queryset):
        if self.value() not in ('covered', 'missing'):
            return queryset

        covered_ids = []
        missing_ids = []

        for match in queryset.select_related('concept').prefetch_related('concept__games'):
            # Find the concept's most modern platform
            concept_platforms = set()
            for game in match.concept.games.all():
                for p in (game.title_platform or []):
                    concept_platforms.add(p)

            top_platform_igdb_id = None
            for plat_name, igdb_id in self.PLATFORM_PRIORITY:
                if plat_name in concept_platforms:
                    top_platform_igdb_id = igdb_id
                    break

            if top_platform_igdb_id is None:
                missing_ids.append(match.pk)
                continue

            # Check if IGDB response includes that platform
            igdb_platforms = match.raw_response.get('platforms', []) if match.raw_response else []
            igdb_plat_ids = set()
            for p in igdb_platforms:
                pid = p if isinstance(p, int) else p.get('id') if isinstance(p, dict) else None
                if pid:
                    igdb_plat_ids.add(pid)

            if top_platform_igdb_id in igdb_plat_ids:
                covered_ids.append(match.pk)
            else:
                missing_ids.append(match.pk)

        if self.value() == 'covered':
            return queryset.filter(pk__in=covered_ids)
        return queryset.filter(pk__in=missing_ids)


@admin.register(IGDBMatch)
class IGDBMatchAdmin(admin.ModelAdmin):
    list_display = (
        'concept_title', 'psn_platforms', 'igdb_name', 'igdb_platforms_display',
        'confidence_display', 'status', 'match_method', 'game_category_display',
        'compilation_display', 'games_count_display', 'updated_at',
    )
    list_filter = (
        'status', 'match_method', 'game_category', 'is_likely_compilation',
        SplittableCompilationFilter, PlatformCoverageFilter,
    )
    search_fields = ('concept__unified_title', 'concept__concept_id', 'igdb_name')
    raw_id_fields = ('concept',)
    date_hierarchy = 'updated_at'
    readonly_fields = (
        'igdb_id', 'match_confidence', 'match_method', 'raw_response',
        'created_at', 'updated_at', 'last_synced_at',
    )
    actions = [
        'approve_selected', 'reject_selected', 'rematch_selected',
        'split_selected_compilations',
        'dismiss_compilation_review', 'undismiss_compilation_review',
    ]

    def concept_title(self, obj):
        return obj.concept.unified_title
    concept_title.short_description = 'PSN Title'
    concept_title.admin_order_field = 'concept__unified_title'

    def confidence_display(self, obj):
        if obj.match_confidence is None:
            return '-'
        pct = f'{obj.match_confidence:.0%}'
        if obj.match_confidence >= 0.85:
            return f'{pct}'
        elif obj.match_confidence >= 0.50:
            return f'{pct}'
        return f'{pct}'
    confidence_display.short_description = 'Confidence'
    confidence_display.admin_order_field = 'match_confidence'

    def game_category_display(self, obj):
        return obj.get_game_category_display() if obj.game_category is not None else '-'
    game_category_display.short_description = 'Category'

    def compilation_display(self, obj):
        return 'Yes' if obj.is_likely_compilation else ''
    compilation_display.short_description = 'Compilation'
    compilation_display.admin_order_field = 'is_likely_compilation'
    compilation_display.boolean = False  # render as text, not icon, so empty stays blank

    def games_count_display(self, obj):
        # Annotated by get_queryset for sortability; falls back to a live count
        # if accessed outside the admin queryset (e.g. inline).
        return getattr(obj, '_games_count', obj.concept.games.count())
    games_count_display.short_description = 'Games'
    games_count_display.admin_order_field = '_games_count'

    def psn_platforms(self, obj):
        platforms = set()
        for game in obj.concept.games.all():
            for p in (game.title_platform or []):
                platforms.add(p)
        return ', '.join(sorted(platforms)) or '-'
    psn_platforms.short_description = 'PSN Platforms'

    def igdb_platforms_display(self, obj):
        from trophies.services.igdb_service import IGDB_PLATFORM_NAMES, PS_PLATFORM_IDS
        igdb_platforms = obj.raw_response.get('platforms', []) if obj.raw_response else []
        names = []
        for p in igdb_platforms:
            pid = p if isinstance(p, int) else p.get('id') if isinstance(p, dict) else None
            if pid in PS_PLATFORM_IDS:
                names.append(IGDB_PLATFORM_NAMES.get(pid, str(pid)))
        return ', '.join(names) or 'None'
    igdb_platforms_display.short_description = 'IGDB PS Platforms'

    def get_queryset(self, request):
        from django.db.models import Count
        return (
            super()
            .get_queryset(request)
            .select_related('concept')
            .prefetch_related('concept__games')
            .annotate(_games_count=Count('concept__games', distinct=True))
        )

    @admin.action(description='Approve selected matches')
    def approve_selected(self, request, queryset):
        from trophies.services.igdb_service import IGDBService
        count = 0
        for match in queryset.filter(status='pending_review'):
            IGDBService.approve_match(match)
            count += 1
        messages.success(request, f'Approved {count} match(es) and applied enrichment.')

    @admin.action(description='Reject and delete selected matches')
    def reject_selected(self, request, queryset):
        from trophies.services.igdb_service import IGDBService
        count = queryset.count()
        for match in queryset:
            IGDBService.reject_match(match)
        messages.success(request, f'Deleted {count} match(es). Run enrich_from_igdb to re-match them.')

    @admin.action(description='Re-match selected (delete and re-run)')
    def rematch_selected(self, request, queryset):
        from trophies.services.igdb_service import IGDBService
        count = 0
        errors = 0
        for match in queryset:
            try:
                IGDBService.rematch_concept(match.concept)
                count += 1
            except Exception as e:
                errors += 1
        msg = f'Re-matched {count} concept(s).'
        if errors:
            msg += f' {errors} error(s) occurred.'
        messages.success(request, msg)

    @admin.action(description='Split compilation concept(s) into parent + child concepts')
    def split_selected_compilations(self, request, queryset):
        from django.contrib.admin.helpers import ACTION_CHECKBOX_NAME
        from django.shortcuts import render
        from trophies.services.concept_split_service import (
            preview_split, split_compilation, ConceptSplitError,
        )

        matches = list(queryset.select_related('concept').prefetch_related('concept__games'))
        previews = [preview_split(m.concept) for m in matches]

        if request.POST.get('apply') == f'Execute {len(previews)} split(s)':
            applied = 0
            errors = 0
            for preview in previews:
                if preview['issues']:
                    continue
                try:
                    split_compilation(concept=preview['concept'], user=request.user)
                    applied += 1
                except ConceptSplitError as exc:
                    errors += 1
                    messages.error(
                        request,
                        f'Split failed for {preview["concept"].concept_id}: {exc}',
                    )
                except Exception as exc:
                    errors += 1
                    messages.error(
                        request,
                        f'Unexpected error splitting {preview["concept"].concept_id}: {exc}',
                    )
            if applied:
                messages.success(request, f'Split {applied} concept(s). Enrichment ran on parent + children.')
            if errors and not applied:
                messages.error(request, f'{errors} split(s) failed, none applied.')
            return None  # fall through to the default changelist response

        has_blocked = any(p['issues'] for p in previews)
        has_any_executable = any(not p['issues'] for p in previews)

        context = {
            **self.admin_site.each_context(request),
            'previews': previews,
            'selected_pks': [m.pk for m in matches],
            'action_checkbox_name': ACTION_CHECKBOX_NAME,
            'has_blocked': has_blocked,
            'has_any_executable': has_any_executable,
            'title': 'Confirm compilation split',
        }
        return render(request, 'admin/trophies/igdbmatch/confirm_split.html', context)

    @admin.action(description='Mark as reviewed — does not need splitting')
    def dismiss_compilation_review(self, request, queryset):
        count = queryset.update(compilation_review_dismissed=True)
        messages.success(
            request,
            f'Marked {count} match(es) as reviewed. They will no longer appear '
            f'in the "splittable compilation" triage filter.',
        )

    @admin.action(description='Undo "reviewed" (return to splittable queue)')
    def undismiss_compilation_review(self, request, queryset):
        count = queryset.update(compilation_review_dismissed=False)
        messages.success(
            request,
            f'Cleared the reviewed flag on {count} match(es). Splittable '
            f'candidates will reappear in the triage filter.',
        )


@admin.register(RematchSuggestion)
class RematchSuggestionAdmin(admin.ModelAdmin):
    """Triage queue for rematch_auto_accepted proposals.

    Each row is a "the matcher would pick a different IGDB id today" suggestion
    that didn't clear the auto-apply bar. Approve to swap the IGDBMatch; dismiss
    to mark reviewed and keep the existing match.
    """

    list_display = (
        'concept_title',
        'old_match_display',
        'proposed_match_display',
        'confidence_delta_display',
        'status',
        'created_at',
        'reviewed_at',
    )
    list_filter = ('status', 'proposed_match_method', 'old_match_method')
    search_fields = (
        'concept__concept_id',
        'concept__unified_title',
        'old_igdb_name',
        'proposed_igdb_name',
        'old_igdb_id',
        'proposed_igdb_id',
    )
    raw_id_fields = ('concept', 'reviewed_by')
    readonly_fields = (
        'concept',
        'old_igdb_id', 'old_igdb_name', 'old_confidence', 'old_match_method',
        'proposed_igdb_id', 'proposed_igdb_name', 'proposed_confidence',
        'proposed_match_method', 'proposed_raw_response',
        'created_at', 'reviewed_at', 'reviewed_by',
    )
    actions = ('apply_suggestions', 'dismiss_suggestions')
    ordering = ('-created_at',)
    date_hierarchy = 'created_at'

    fieldsets = (
        (None, {'fields': ('concept', 'status')}),
        ('Current match', {'fields': (
            'old_igdb_id', 'old_igdb_name', 'old_confidence', 'old_match_method',
        )}),
        ('Proposed match', {'fields': (
            'proposed_igdb_id', 'proposed_igdb_name', 'proposed_confidence',
            'proposed_match_method', 'proposed_raw_response',
        )}),
        ('Review', {'fields': ('created_at', 'reviewed_at', 'reviewed_by')}),
    )

    def get_queryset(self, request):
        return super().get_queryset(request).select_related('concept', 'reviewed_by')

    def concept_title(self, obj):
        return f'{obj.concept.unified_title} ({obj.concept.concept_id})'
    concept_title.short_description = 'Concept'
    concept_title.admin_order_field = 'concept__unified_title'

    def old_match_display(self, obj):
        if obj.old_igdb_id is None:
            return '-'
        conf = f' ({obj.old_confidence:.0%})' if obj.old_confidence is not None else ''
        return f'#{obj.old_igdb_id} {obj.old_igdb_name}{conf}'
    old_match_display.short_description = 'Current'

    def proposed_match_display(self, obj):
        conf = f' ({obj.proposed_confidence:.0%})'
        return f'#{obj.proposed_igdb_id} {obj.proposed_igdb_name}{conf}'
    proposed_match_display.short_description = 'Proposed'

    def confidence_delta_display(self, obj):
        delta = obj.confidence_delta
        sign = '+' if delta >= 0 else ''
        return f'{sign}{delta:.2f}'
    confidence_delta_display.short_description = 'Δ'

    @admin.action(description='Apply proposed match (updates IGDBMatch)')
    def apply_suggestions(self, request, queryset):
        from trophies.services.igdb_service import IGDBService
        applied = 0
        skipped = 0
        errors = 0
        for suggestion in queryset.filter(status='pending').select_related('concept'):
            try:
                IGDBService.process_match(
                    suggestion.concept,
                    suggestion.proposed_raw_response,
                    suggestion.proposed_confidence,
                    suggestion.proposed_match_method or 'manual',
                )
                suggestion.status = 'approved'
                suggestion.reviewed_at = timezone.now()
                suggestion.reviewed_by = request.user
                suggestion.save(update_fields=['status', 'reviewed_at', 'reviewed_by'])
                applied += 1
            except Exception as exc:
                errors += 1
                messages.error(
                    request,
                    f'Error applying suggestion for {suggestion.concept.concept_id}: {exc}',
                )
        non_pending = queryset.exclude(status='pending').count()
        if non_pending:
            skipped += non_pending
            messages.warning(
                request,
                f'Skipped {non_pending} already-reviewed suggestion(s).',
            )
        if applied:
            messages.success(
                request,
                f'Applied {applied} suggestion(s). IGDBMatch rows updated and enrichment re-run.',
            )
        if errors and not applied:
            messages.error(request, f'{errors} error(s) occurred with no successful applies.')

    @admin.action(description='Dismiss suggestions (keep existing match)')
    def dismiss_suggestions(self, request, queryset):
        pending = queryset.filter(status='pending')
        count = pending.update(
            status='dismissed',
            reviewed_at=timezone.now(),
            reviewed_by=request.user,
        )
        if count:
            messages.success(request, f'Dismissed {count} suggestion(s).')
        non_pending = queryset.exclude(status='pending').count()
        if non_pending:
            messages.warning(
                request,
                f'Skipped {non_pending} already-reviewed suggestion(s).',
            )


class ConceptJoinReviewFlagFilter(SimpleListFilter):
    """Filter ConceptJoinReview rows by a flag_reason present in the JSONField list."""

    title = 'Flag reason'
    parameter_name = 'flag'

    def lookups(self, request, model_admin):
        return tuple((fr, fr.replace('_', ' ').title()) for fr in ConceptJoinReview.FLAG_REASON_CHOICES)

    def queryset(self, request, queryset):
        value = self.value()
        if not value:
            return queryset
        return queryset.filter(flag_reasons__contains=[value])


class _TrophyMismatchSkip(Exception):
    """Raised inside _apply_approval to abort a join when the game's trophy
    structure diverges from the target Concept's existing game(s). Carries the
    divergence flags so approve_selected can re-flag the review for resolution
    via "Approve as separate Concept" instead of silently mis-grouping."""

    def __init__(self, flags):
        self.flags = list(flags)
        super().__init__('trophy structure mismatch: ' + ', '.join(self.flags))


@admin.register(ConceptJoinReview)
class ConceptJoinReviewAdmin(admin.ModelAdmin):
    """Staff review queue for Games whose IGDB-anchored placement couldn't be auto-resolved.

    Written by the anchor_concepts migration command (and steady-state sync once
    that integration ships) when a Game's proposed canonical IGDB id can't be
    auto-joined because trophy-metric homogeneity, identity cross-check, or
    concept_id collision detection fired a flag.

    Actions:
      * Approve — moves the Game to the proposed canonical Concept (creating
        and enriching the target if it doesn't exist).
      * Reject — Game stays in its current Concept; resolved status set so the
        migration command won't re-flag it on re-runs.
      * Defer — resolved status set but no move; will be re-evaluated when
        IGDB data improves or staff manually retries.
    """

    list_display = (
        'game_display', 'current_concept_display', 'proposed_canonical_igdb_id',
        'proposed_concept_display', 'flag_reasons_display', 'status', 'created_at',
    )
    list_filter = ('status', ConceptJoinReviewFlagFilter, 'created_at')
    # proposed_canonical_igdb_id is an IntegerField; including it in
    # search_fields would generate an invalid __icontains lookup and 500 on
    # search. Use the list filter or the changelist URL params (e.g.
    # ?proposed_canonical_igdb_id=19564) to find by id.
    search_fields = ('game__title_name', 'game__np_communication_id')
    raw_id_fields = ('game', 'proposed_concept', 'resolved_by')
    readonly_fields = (
        'game', 'proposed_canonical_igdb_id', 'trophy_fingerprint',
        'identity_check_data', 'flag_reasons',
        'created_at', 'resolved_at', 'resolved_by',
    )
    date_hierarchy = 'created_at'
    ordering = ('-created_at',)
    actions = (
        'approve_selected', 'approve_as_separate_concept',
        'reject_selected', 'defer_selected',
    )

    fieldsets = (
        (None, {'fields': (
            'game', 'status', 'proposed_canonical_igdb_id', 'proposed_concept',
        )}),
        ('Diagnostic', {'fields': (
            'flag_reasons', 'trophy_fingerprint', 'identity_check_data',
        )}),
        ('Review', {'fields': (
            'notes', 'created_at', 'resolved_at', 'resolved_by',
        )}),
    )

    def get_queryset(self, request):
        return (
            super().get_queryset(request)
            .select_related('game', 'game__concept', 'proposed_concept', 'resolved_by')
        )

    def game_display(self, obj):
        title = obj.game.title_name or obj.game.np_communication_id
        return f'{title} (game pk={obj.game.pk})'
    game_display.short_description = 'Game'
    game_display.admin_order_field = 'game__title_name'

    def current_concept_display(self, obj):
        c = obj.game.concept
        if c is None:
            return '-'
        title = c.unified_title or '(no title)'
        return f'{title} ({c.concept_id})'
    current_concept_display.short_description = 'Currently in'

    def proposed_concept_display(self, obj):
        c = obj.proposed_concept
        if c is None:
            return '(would be created)'
        title = c.unified_title or '(no title)'
        return f'{title} ({c.concept_id})'
    proposed_concept_display.short_description = 'Proposed target'

    def flag_reasons_display(self, obj):
        return ', '.join(obj.flag_reasons or [])
    flag_reasons_display.short_description = 'Flags'

    @admin.action(description='Approve selected (anchor Game at proposed canonical Concept)')
    def approve_selected(self, request, queryset):
        approved = 0
        errors = 0
        mismatched = 0
        for review in queryset.filter(status='pending'):
            try:
                self._apply_approval(review, request.user)
                approved += 1
            except _TrophyMismatchSkip as skip:
                # Re-flag (this write is outside the rolled-back approval txn)
                # and force the review back to pending so it resurfaces in the
                # queue for "Approve as separate Concept". Resetting status +
                # clearing the resolved_* fields makes this a true re-open even
                # if the row had somehow been marked resolved — otherwise a
                # re-flagged review could stay 'approved' and hide the mismatch.
                review.flag_reasons = sorted(
                    set(review.flag_reasons or []) | set(skip.flags)
                )
                review.status = 'pending'
                review.resolved_at = None
                review.resolved_by = None
                review.save(update_fields=[
                    'flag_reasons', 'status', 'resolved_at', 'resolved_by',
                ])
                mismatched += 1
                messages.warning(
                    request,
                    f'Skipped game pk={review.game_id}: trophy structure differs '
                    f'from the target Concept ({", ".join(skip.flags)}). Use '
                    f'"Approve as separate Concept" if it is the same game with a '
                    f'different trophy list.',
                )
            except Exception as exc:
                errors += 1
                messages.error(
                    request,
                    f'Approve failed for game pk={review.game_id}: {exc}',
                )
        if approved:
            messages.success(request, f'Approved {approved} review(s) and anchored the games.')
        if mismatched:
            messages.warning(
                request,
                f'{mismatched} review(s) skipped due to trophy-structure mismatch '
                f'and re-flagged for separate-Concept resolution.',
            )
        non_pending = queryset.exclude(status='pending').count()
        if non_pending:
            messages.warning(
                request,
                f'Skipped {non_pending} already-resolved review(s).',
            )

    def _apply_approval(self, review, user):
        """Anchor the Game at its proposed Concept.

        Refreshes target's IGDBMatch with RAW (version-specific) IGDB data so
        the Concept's metadata reflects the actual version it represents
        (PS3 original Concept gets PS3 metadata, Remastered Concept gets
        Remastered metadata). Raw IGDB id source priority:
          1. review.proposed_raw_igdb_id (set by migration / manual-anchor)
          2. target.igdb_match.igdb_id (already-anchored Concept)
          3. review.proposed_canonical_igdb_id (legacy fallback)

        Raises _TrophyMismatchSkip (caught by approve_selected) when the game's
        trophy structure diverges from a game already in the target Concept, so
        a bulk-approve can't silently mis-group different games.
        """
        from trophies.services.igdb_service import IGDBService
        from trophies.services.concept_anchor_service import compare_trophy_metrics

        canonical_id = review.proposed_canonical_igdb_id
        if not canonical_id:
            raise ValueError('Review has no proposed canonical IGDB id')

        with transaction.atomic():
            target = review.proposed_concept

            if target is not None:
                # Verify the proposed Concept is still in the right family.
                match = getattr(target, 'igdb_match', None)
                if match and match.igdb_id:
                    existing_canonical = IGDBService._resolve_canonical_igdb_id(
                        match.raw_response or {}, match.igdb_id
                    )
                    if existing_canonical != canonical_id:
                        raise ValueError(
                            f'Proposed Concept {target.concept_id!r} no longer '
                            f'resolves to canonical {canonical_id} '
                            f'(resolves to {existing_canonical}). Resolve manually.'
                        )
            else:
                # Legacy path: derive target from canonical. Primary slot.
                concept_id = str(canonical_id)
                target = Concept.objects.filter(concept_id=concept_id).first()
                if target:
                    match = getattr(target, 'igdb_match', None)
                    if match and match.igdb_id:
                        existing_canonical = IGDBService._resolve_canonical_igdb_id(
                            match.raw_response or {}, match.igdb_id
                        )
                        if existing_canonical != canonical_id:
                            raise ValueError(
                                f'Concept with concept_id={concept_id!r} exists but its '
                                f'IGDBMatch resolves to canonical {existing_canonical}, '
                                f'not {canonical_id}. Manual cleanup needed before this '
                                f'review can be approved.'
                            )
                else:
                    # Use raw if available, else canonical, for the initial create.
                    raw_for_create = review.proposed_raw_igdb_id or canonical_id
                    initial_data = IGDBService.fetch_full_game_data(raw_for_create)
                    if not initial_data:
                        raise ValueError(
                            f'IGDB returned no data for id {raw_for_create}; '
                            f'cannot anchor this Game.'
                        )
                    target = Concept.objects.create(
                        concept_id=concept_id,
                        unified_title=initial_data.get('name', ''),
                    )

            # Determine raw IGDB id for the refresh — prefer the field, fall
            # back to target's existing match, then to canonical.
            target_match = getattr(target, 'igdb_match', None)
            raw_id_for_refresh = (
                review.proposed_raw_igdb_id
                or (target_match.igdb_id if target_match and target_match.igdb_id else None)
                or canonical_id
            )
            raw_data = IGDBService.fetch_full_game_data(raw_id_for_refresh)
            if raw_data:
                IGDBService.process_match(
                    target, raw_data, confidence=1.0, method='manual',
                )

            # Trophy-consistency guard. A Concept groups one game's trophy list
            # across regions/platforms, so games joining it should share the
            # same trophy structure. When the target already holds a game
            # (including one anchored earlier in this same bulk-approve, since
            # each approval commits in its own transaction), compare against it;
            # a divergence means these are likely NOT the same game. Abort the
            # silent join so approve_selected re-flags it for "Approve as
            # separate Concept". The first game into an empty target has nothing
            # to compare against and passes — it becomes the reference for the
            # rest of the batch.
            reference_game = target.games.exclude(pk=review.game_id).first()
            if reference_game is not None:
                trophy_flags = compare_trophy_metrics(
                    review.game, reference_game
                )['flag_reasons']
                if trophy_flags:
                    raise _TrophyMismatchSkip(trophy_flags)

            review.game.add_concept(target, force=True)

            target.anchor_migration_completed_at = timezone.now()
            target.save(update_fields=['anchor_migration_completed_at'])

            review.status = 'approved'
            review.resolved_at = timezone.now()
            review.resolved_by = user
            review.proposed_concept = target
            review.save(update_fields=[
                'status', 'resolved_at', 'resolved_by', 'proposed_concept',
            ])

    # Flag reasons for which "approve as separate Concept" is the correct
    # resolution. Other flag reasons (low_match_confidence, identity_title_-
    # dissimilar, concept_id_collision, etc.) indicate the IGDB match itself
    # is suspect, in which case creating a sibling would propagate the bad
    # match. Staff should use the regular Approve / Reject paths there.
    _SEPARATE_CONCEPT_TROPHY_FLAGS = frozenset({
        'trophy_count_mismatch',
        'platinum_status_diverged',
        'trophy_group_count_diff',
        'region_split_suspected_japan',
    })

    @admin.action(
        description=(
            'Approve as separate Concept in same family '
            '(use when trophy lists differ but the game is the same)'
        )
    )
    def approve_as_separate_concept(self, request, queryset):
        """Resolve trophy-fingerprint-mismatch reviews by creating a sibling.

        Use case: two trophy lists for what IGDB considers one game (e.g.,
        Vita vs PS4 versions with different trophy counts). Both Concepts
        end up in the same GameFamily because their IGDBMatches canonical-
        resolve to the same id, but they stay as separate Concepts so
        their ratings/comments/badges don't cross-contaminate.

        The primary Concept (the one created first at concept_id =
        str(canonical_id)) is untouched. This action creates a sibling at
        concept_id = "{canonical_id}-N" (next free suffix), enriches it
        against canonical IGDB data, and moves the Game in.

        Gated to reviews whose flag_reasons include at least one trophy-
        related flag, to avoid propagating a bad IGDB match via siblings.
        """
        approved = 0
        bulk_resolved = 0
        errors = 0
        wrong_flags = 0
        already_resolved_in_loop = set()
        for review in queryset.filter(status='pending'):
            if review.pk in already_resolved_in_loop:
                # This review was bulk-resolved by a sibling created earlier
                # in this same action invocation (fingerprint-cluster merge).
                continue
            flags = set(review.flag_reasons or [])
            if not (flags & self._SEPARATE_CONCEPT_TROPHY_FLAGS):
                wrong_flags += 1
                continue
            try:
                cluster_resolved_pks = self._apply_approval_as_separate(
                    review, request.user,
                )
                approved += 1
                bulk_resolved += len(cluster_resolved_pks)
                already_resolved_in_loop.update(cluster_resolved_pks)
            except Exception as exc:
                errors += 1
                messages.error(
                    request,
                    f'Approve-as-separate failed for game pk={review.game_id}: {exc}',
                )
        if approved:
            if bulk_resolved:
                messages.success(
                    request,
                    f'Created {approved} sibling Concept(s); resolved '
                    f'{approved + bulk_resolved} review(s) total '
                    f'({approved} selected + {bulk_resolved} auto-merged by '
                    f'matching trophy fingerprint).',
                )
            else:
                messages.success(
                    request,
                    f'Created {approved} sibling Concept(s) and anchored the games.',
                )
        if wrong_flags:
            messages.warning(
                request,
                f'Skipped {wrong_flags} review(s) without trophy-related '
                f'flags. Approve-as-separate is for trophy-list-divergence '
                f'cases only; use the regular Approve or Reject actions for '
                f'identity / confidence / collision flags.',
            )
        non_pending = queryset.exclude(status='pending').count()
        if non_pending:
            messages.warning(
                request,
                f'Skipped {non_pending} already-resolved review(s).',
            )

    def _apply_approval_as_separate(self, review, user):
        """Anchor the Game at a new sibling Concept in the same canonical Family.

        Retries the sibling-Concept create() up to 3 times to absorb the
        rare race where two admins approve siblings of the same canonical id
        simultaneously. Each create() runs in its own savepoint so an
        IntegrityError rolls back just that attempt rather than poisoning
        the outer transaction.

        After moving the selected review's Game, scans for OTHER pending
        reviews with the same canonical_id AND the same trophy_fingerprint
        and bulk-resolves them: their Games join the same sibling, their
        reviews are marked approved. This handles the
        "migration flagged Vita-A AND Vita-B against PS4, both should be
        ONE sibling" case in a single click.

        Returns:
            set[int]: pks of OTHER reviews that were bulk-resolved by this
            call (the caller can use these to skip those rows on subsequent
            iterations of the same action).
        """
        from django.db import IntegrityError
        from trophies.services.concept_anchor_service import (
            allocate_sibling_concept_id,
        )
        from trophies.services.igdb_service import IGDBService

        canonical_id = review.proposed_canonical_igdb_id
        if not canonical_id:
            raise ValueError('Review has no proposed canonical IGDB id')

        bulk_resolved_pks = set()

        with transaction.atomic():
            # Use RAW (version-specific) IGDB data so this sibling represents
            # the actual version of the game. Fall back to canonical for
            # legacy reviews that pre-date the proposed_raw_igdb_id field.
            raw_id = review.proposed_raw_igdb_id or canonical_id
            raw_data = IGDBService.fetch_full_game_data(raw_id)
            if not raw_data:
                raise ValueError(
                    f'IGDB returned no data for id {raw_id}'
                )

            # ALWAYS create a new Concept for this approval — the action's
            # whole point is "make a separate sibling because the existing
            # version has a different trophy fingerprint." Earlier behavior
            # peeked at build_family_raw_igdb_map and reused any Concept
            # already anchored at this raw_id, which silently dropped the
            # game into the existing variant's Concept (the bug Jeffrey
            # hit on the same-raw-id-different-trophies case). Within-batch
            # duplicate prevention is still handled by the cluster
            # auto-resolve below, which lands peer reviews (same canonical
            # AND same fingerprint) into the SAME new sibling.
            #
            # Slot allocation: natural slot is str(raw_id). If it's free,
            # use it; if taken, allocate a str(raw)-N suffix. IntegrityError
            # under race rolls back the inner savepoint and forces the
            # next attempt onto a suffix slot.
            target = None
            last_error = None
            preferred_slot = str(raw_id)
            slot_taken = Concept.objects.filter(concept_id=preferred_slot).exists()
            for _ in range(3):
                sibling_id = (
                    allocate_sibling_concept_id(raw_id) if slot_taken
                    else preferred_slot
                )
                try:
                    with transaction.atomic():
                        target = Concept.objects.create(
                            concept_id=sibling_id,
                            unified_title=raw_data.get('name', ''),
                        )
                    break
                except IntegrityError as exc:
                    last_error = exc
                    slot_taken = True  # force suffix on retry
                    continue
            if target is None:
                raise ValueError(
                    f'Could not allocate concept_id slot for raw IGDB '
                    f'{raw_id} after 3 attempts: {last_error}'
                )

            IGDBService.process_match(
                target, raw_data, confidence=1.0, method='manual',
            )

            target.anchor_migration_completed_at = timezone.now()
            target.save(update_fields=['anchor_migration_completed_at'])

            review.game.add_concept(target, force=True)

            review.status = 'approved'
            review.resolved_at = timezone.now()
            review.resolved_by = user
            review.proposed_concept = target
            review.save(update_fields=[
                'status', 'resolved_at', 'resolved_by', 'proposed_concept',
            ])

            # Fingerprint-cluster auto-resolve: find OTHER pending reviews
            # with the same canonical_id and matching trophy_fingerprint,
            # land their Games at the same new sibling, mark approved.
            # Same fingerprint within the same canonical Family = same
            # trophy-list variant (per the design we landed in d276712).
            if review.trophy_fingerprint:
                cluster_reviews = ConceptJoinReview.objects.filter(
                    status='pending',
                    proposed_canonical_igdb_id=canonical_id,
                    trophy_fingerprint=review.trophy_fingerprint,
                ).exclude(pk=review.pk).select_related('game')
                for sibling_review in cluster_reviews:
                    # Verify the clustered review's flag_reasons are also
                    # trophy-fingerprint-class — don't auto-merge a review
                    # that was flagged for identity/confidence reasons.
                    sibling_flags = set(sibling_review.flag_reasons or [])
                    if not (sibling_flags & self._SEPARATE_CONCEPT_TROPHY_FLAGS):
                        continue
                    sibling_review.game.add_concept(target, force=True)
                    sibling_review.status = 'approved'
                    sibling_review.resolved_at = timezone.now()
                    sibling_review.resolved_by = user
                    sibling_review.proposed_concept = target
                    sibling_review.notes = (
                        (sibling_review.notes or '')
                        + f'\n[auto-merged into sibling via fingerprint cluster '
                          f'when review pk={review.pk} was approved]'
                    ).strip()
                    sibling_review.save(update_fields=[
                        'status', 'resolved_at', 'resolved_by',
                        'proposed_concept', 'notes',
                    ])
                    bulk_resolved_pks.add(sibling_review.pk)

        return bulk_resolved_pks

    @admin.action(description='Reject selected (Game stays in current Concept)')
    def reject_selected(self, request, queryset):
        count = queryset.filter(status='pending').update(
            status='rejected',
            resolved_at=timezone.now(),
            resolved_by=request.user,
        )
        if count:
            messages.success(request, f'Rejected {count} review(s).')
        non_pending = queryset.exclude(status='pending').count()
        if non_pending:
            messages.warning(
                request,
                f'Skipped {non_pending} already-resolved review(s).',
            )

    @admin.action(description='Defer selected (re-evaluate on next migration run)')
    def defer_selected(self, request, queryset):
        count = queryset.filter(status='pending').update(
            status='deferred',
            resolved_at=timezone.now(),
            resolved_by=request.user,
        )
        if count:
            messages.success(request, f'Deferred {count} review(s).')
        non_pending = queryset.exclude(status='pending').count()
        if non_pending:
            messages.warning(
                request,
                f'Skipped {non_pending} already-resolved review(s).',
            )


@admin.register(ConceptSplitEvent)
class ConceptSplitEventAdmin(admin.ModelAdmin):
    """Audit trail for Phase 5 compilation splits, with a reverse action."""

    list_display = (
        'id', 'parent_display', 'child_count', 'parent_original_title',
        'is_reversed', 'created_at', 'created_by',
    )
    list_filter = ('is_reversed',)
    search_fields = (
        'parent_concept__concept_id',
        'parent_concept__unified_title',
        'parent_original_title',
    )
    raw_id_fields = ('parent_concept', 'child_concepts', 'created_by', 'reversed_by')
    readonly_fields = (
        'parent_concept', 'child_concepts',
        'parent_original_title', 'parent_original_igdb_id', 'parent_original_igdb_name',
        'kept_game_id', 'is_reversed', 'reversed_at', 'reversed_by',
        'created_at', 'created_by',
    )
    ordering = ('-created_at',)
    date_hierarchy = 'created_at'
    actions = ('reverse_selected_splits',)

    def get_queryset(self, request):
        return (
            super()
            .get_queryset(request)
            .select_related('parent_concept', 'created_by', 'reversed_by')
            .prefetch_related('child_concepts')
        )

    def parent_display(self, obj):
        if not obj.parent_concept:
            return '(deleted)'
        return f'{obj.parent_concept.unified_title} ({obj.parent_concept.concept_id})'
    parent_display.short_description = 'Parent'

    def child_count(self, obj):
        return obj.child_concepts.count()
    child_count.short_description = 'Children'

    @admin.action(description='Reverse split (merge children back into parent)')
    def reverse_selected_splits(self, request, queryset):
        from django.contrib.admin.helpers import ACTION_CHECKBOX_NAME
        from django.shortcuts import render
        from trophies.services.concept_split_service import (
            reverse_split, ConceptSplitError,
        )

        events = list(
            queryset
            .select_related('parent_concept')
            .prefetch_related('child_concepts__games')
        )

        if request.POST.get('apply') == f'Reverse {len(events)} split(s)':
            reversed_count = 0
            errors = 0
            for event in events:
                try:
                    reverse_split(event=event, user=request.user)
                    reversed_count += 1
                except ConceptSplitError as exc:
                    errors += 1
                    messages.error(request, f'Reverse failed for event #{event.pk}: {exc}')
                except Exception as exc:
                    errors += 1
                    messages.error(request, f'Unexpected error reversing event #{event.pk}: {exc}')
            if reversed_count:
                messages.success(
                    request,
                    f'Reversed {reversed_count} split(s). Parent concept(s) re-enriched against original title.',
                )
            if errors and not reversed_count:
                messages.error(request, f'{errors} reversal(s) failed, none applied.')
            return None

        rows = []
        has_any_executable = False
        for event in events:
            issues = []
            if event.is_reversed:
                issues.append('Split already reversed.')
            if not event.parent_concept:
                issues.append('Parent concept no longer exists.')
            children = list(event.child_concepts.all())
            if not children and not event.is_reversed:
                issues.append('No child concepts linked to this event.')
            if not issues:
                has_any_executable = True
            rows.append({'event': event, 'children': children, 'issues': issues})

        context = {
            **self.admin_site.each_context(request),
            'events': rows,
            'selected_pks': [e.pk for e in events],
            'action_checkbox_name': ACTION_CHECKBOX_NAME,
            'has_any_executable': has_any_executable,
            'title': 'Confirm split reversal',
        }
        return render(request, 'admin/trophies/conceptsplitevent/confirm_reverse.html', context)


class GameFlagStatusFilter(SimpleListFilter):
    title = 'Status'
    parameter_name = 'status'

    def lookups(self, request, model_admin):
        return GameFlag.FLAG_STATUS

    def queryset(self, request, queryset):
        if self.value():
            return queryset.filter(status=self.value())
        return queryset


@admin.register(GameFlag)
class GameFlagAdmin(admin.ModelAdmin):
    list_display = [
        'id', 'game_name', 'flag_type', 'reporter_psn', 'status',
        'details_preview', 'created_at', 'reviewed_by',
    ]
    list_select_related = ('game', 'reporter', 'reviewed_by')
    list_filter = [GameFlagStatusFilter, 'flag_type', 'created_at']
    search_fields = ['game__title_name', 'reporter__psn_username', 'details', 'admin_notes']
    raw_id_fields = ['game', 'reporter', 'reviewed_by']
    readonly_fields = ['created_at', 'reviewed_at']
    ordering = ['-created_at']
    date_hierarchy = 'created_at'
    actions = ['approve_selected', 'dismiss_selected']

    fieldsets = (
        ('Flag Info', {'fields': ('game', 'reporter', 'flag_type', 'details')}),
        ('Status', {'fields': ('status', 'reviewed_at', 'reviewed_by', 'admin_notes')}),
        ('Timestamps', {'fields': ('created_at',)}),
    )

    def game_name(self, obj):
        return obj.game.title_name
    game_name.short_description = 'Game'

    def reporter_psn(self, obj):
        return obj.reporter.psn_username
    reporter_psn.short_description = 'Reporter'

    def details_preview(self, obj):
        if not obj.details:
            return '-'
        return obj.details[:80] + ('...' if len(obj.details) > 80 else '')
    details_preview.short_description = 'Details'

    @admin.action(description='Approve selected flags (apply game changes)')
    def approve_selected(self, request, queryset):
        from trophies.services.game_flag_service import GameFlagService
        count = 0
        with transaction.atomic():
            for flag in queryset.filter(status='pending'):
                GameFlagService.approve_flag(flag, request.user)
                count += 1
        messages.success(request, f'Approved {count} flag(s) and applied game changes.')

    @admin.action(description='Dismiss selected flags')
    def dismiss_selected(self, request, queryset):
        from trophies.services.game_flag_service import GameFlagService
        count = 0
        with transaction.atomic():
            for flag in queryset.filter(status='pending'):
                GameFlagService.dismiss_flag(flag, request.user)
                count += 1
        messages.success(request, f'Dismissed {count} flag(s).')


@admin.register(ScoutAccount)
class ScoutAccountAdmin(admin.ModelAdmin):
    list_display = [
        'profile_psn', 'status', 'games_discovered',
        'refresh_frequency_hours', 'last_synced', 'added_by', 'created_at',
    ]
    list_select_related = ('profile', 'added_by')
    list_filter = ['status']
    search_fields = ['profile__psn_username', 'profile__display_psn_username', 'staff_notes']
    raw_id_fields = ['profile', 'added_by']
    readonly_fields = ['created_at', 'updated_at', 'games_discovered']
    ordering = ['-created_at']
    date_hierarchy = 'created_at'
    actions = ['activate_selected', 'pause_selected', 'retire_selected', 'trigger_refresh']

    fieldsets = (
        ('Scout Info', {'fields': ('profile', 'status', 'added_by')}),
        ('Configuration', {'fields': ('refresh_frequency_hours',)}),
        ('Stats', {'fields': ('games_discovered',)}),
        ('Notes', {'fields': ('staff_notes',)}),
        ('Timestamps', {'fields': ('created_at', 'updated_at')}),
    )

    @admin.display(description='PSN Username')
    def profile_psn(self, obj):
        return obj.profile.psn_username

    @admin.display(description='Last Synced')
    def last_synced(self, obj):
        return obj.profile.last_synced

    @admin.action(description='Activate selected scouts')
    def activate_selected(self, request, queryset):
        updated = queryset.exclude(status='active').update(status='active')
        messages.success(request, f'Activated {updated} scout(s).')

    @admin.action(description='Pause selected scouts')
    def pause_selected(self, request, queryset):
        updated = queryset.exclude(status='paused').update(status='paused')
        messages.success(request, f'Paused {updated} scout(s).')

    @admin.action(description='Retire selected scouts')
    def retire_selected(self, request, queryset):
        updated = queryset.exclude(status='retired').update(status='retired')
        messages.success(request, f'Retired {updated} scout(s).')

    @admin.action(description='Trigger refresh now')
    def trigger_refresh(self, request, queryset):
        from trophies.psn_manager import PSNManager
        count = 0
        for scout in queryset.filter(status='active').select_related('profile'):
            PSNManager.profile_refresh(scout.profile)
            count += 1
        messages.success(request, f'Queued {count} scout(s) for refresh.')


# ---------- IGDB through-table admins ----------
# Minimal registrations so the doubled-up-enrichment debugging surface is
# fully visible. Master tables (Company, Genre, Theme, GameEngine, Franchise)
# already have their own admins above.

@admin.register(ConceptCompany)
class ConceptCompanyAdmin(admin.ModelAdmin):
    list_display = ('concept', 'company', 'is_developer', 'is_publisher', 'is_porting', 'is_supporting')
    list_select_related = ('concept', 'company')
    list_filter = ('is_developer', 'is_publisher', 'is_porting', 'is_supporting')
    search_fields = ('concept__unified_title', 'concept__concept_id', 'company__name')
    raw_id_fields = ('concept', 'company')


@admin.register(ConceptGenre)
class ConceptGenreAdmin(admin.ModelAdmin):
    list_display = ('concept', 'genre')
    list_select_related = ('concept', 'genre')
    list_filter = ('genre',)
    search_fields = ('concept__unified_title', 'concept__concept_id', 'genre__name')
    raw_id_fields = ('concept', 'genre')


@admin.register(ConceptTheme)
class ConceptThemeAdmin(admin.ModelAdmin):
    list_display = ('concept', 'theme')
    list_select_related = ('concept', 'theme')
    list_filter = ('theme',)
    search_fields = ('concept__unified_title', 'concept__concept_id', 'theme__name')
    raw_id_fields = ('concept', 'theme')


@admin.register(ConceptEngine)
class ConceptEngineAdmin(admin.ModelAdmin):
    list_display = ('concept', 'engine')
    list_select_related = ('concept', 'engine')
    list_filter = ('engine',)
    search_fields = ('concept__unified_title', 'concept__concept_id', 'engine__name')
    raw_id_fields = ('concept', 'engine')


@admin.register(EngineCompany)
class EngineCompanyAdmin(admin.ModelAdmin):
    list_display = ('engine', 'company')
    list_select_related = ('engine', 'company')
    search_fields = ('engine__name', 'company__name')
    raw_id_fields = ('engine', 'company')


# ---------- Admin index grouping ----------
# Split the trophies app's model list on the admin index page into a
# dedicated "IGDB & Enrichment" subsection. Models still live under the
# real app internally — this is display-only.

_IGDB_ADMIN_OBJECT_NAMES = frozenset({
    'IGDBMatch', 'RematchSuggestion', 'ConceptSplitEvent', 'ConceptJoinReview',
    'GameFamily',
    'Company', 'ConceptCompany',
    'Franchise', 'ConceptFranchise',
    'Genre', 'ConceptGenre',
    'Theme', 'ConceptTheme',
    'GameEngine', 'ConceptEngine', 'EngineCompany',
})

_ROADMAP_ADMIN_OBJECT_NAMES = frozenset({
    'Roadmap', 'RoadmapStep', 'RoadmapStepTrophy', 'TrophyGuide',
    'RoadmapEditLock', 'RoadmapRevision',
    'RoadmapNote', 'RoadmapNoteRead',
})

_original_get_app_list = admin.site.get_app_list


def _platpursuit_get_app_list(request, app_label=None):
    """Return the admin index app list, splitting trophies into sections.

    Trophies gets carved into:
      - "Roadmap System": authoring + lock/session + history models
      - "IGDB & Enrichment": matching + normalized metadata models
      - "Trophies": everything else
    Other apps are passed through unchanged. Internally all these models
    still belong to the `trophies` app_label — we only reshape the index
    display.
    """
    app_list = _original_get_app_list(request, app_label)
    result = []
    for app in app_list:
        if app['app_label'] != 'trophies':
            result.append(app)
            continue
        roadmap_models = [m for m in app['models'] if m['object_name'] in _ROADMAP_ADMIN_OBJECT_NAMES]
        igdb_models = [m for m in app['models'] if m['object_name'] in _IGDB_ADMIN_OBJECT_NAMES]
        other_models = [
            m for m in app['models']
            if m['object_name'] not in _ROADMAP_ADMIN_OBJECT_NAMES
            and m['object_name'] not in _IGDB_ADMIN_OBJECT_NAMES
        ]
        if roadmap_models:
            # Order roadmap models semantically: authoring → lock → history.
            authoring_order = [
                'Roadmap', 'RoadmapStep', 'TrophyGuide',
                'RoadmapStepTrophy',
                'RoadmapNote', 'RoadmapNoteRead',
                'RoadmapEditLock', 'RoadmapRevision',
            ]
            order_idx = {name: i for i, name in enumerate(authoring_order)}
            roadmap_models.sort(key=lambda m: order_idx.get(m['object_name'], 99))
            result.append({
                **app,
                'name': 'Roadmap System',
                'models': roadmap_models,
            })
        if igdb_models:
            result.append({
                **app,
                'name': 'IGDB & Enrichment',
                'models': igdb_models,
            })
        if other_models:
            result.append({**app, 'models': other_models})
    return result


admin.site.get_app_list = _platpursuit_get_app_list
