from django.contrib import admin, messages
from django.contrib.admin import SimpleListFilter
from django.db import transaction
from django.db.models import Count, F, IntegerField, Q, Value
from django.db.models.functions import Cast, Coalesce
from datetime import timedelta
from .models import Profile, Game, Trophy, EarnedTrophy, ProfileGame, APIAuditLog, FeaturedGame, FeaturedProfile, Concept, TitleID, TrophyGroup, ConceptTrophyGroup, UserTrophySelection, UserConceptRating, Badge, UserBadge, UserBadgeProgress, ProfileBadgeShowcase, FeaturedGuide, Stage, PublisherBlacklist, Title, UserTitle, Milestone, UserMilestone, UserMilestoneProgress, Comment, CommentVote, CommentReport, ModerationLog, BannedWord, ProfileGamification, StatType, StageStatValue, MonthlyRecap, GameList, GameListItem, GameListLike, Challenge, AZChallengeSlot, GameFamily, GameFamilyProposal, Review, ReviewVote, ReviewReply, ReviewReport, ReviewModerationLog, DashboardConfig, StageCompletionEvent, Roadmap, RoadmapTab, RoadmapStep, RoadmapStepTrophy, TrophyGuide, Company, ConceptCompany, IGDBMatch, GameFlag, Genre, Theme, GameEngine, ScoutAccount, Franchise, ConceptFranchise


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
        "total_trophies",
        "total_unearned",
        "tour_completed_at",
        "game_detail_tour_completed_at",
        "badge_detail_tour_completed_at",
    )
    list_filter = ("is_linked", "is_plus", "sync_tier", "sync_status", "user_is_premium",)
    search_fields = ("psn_username", "account_id", "user__username__iexact", "about_me")
    raw_id_fields = ("user",)
    ordering = ("psn_username",)
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
            {"fields": ("psn_username", "display_psn_username", "account_id", "np_id", "user", "user_is_premium", "is_linked", "psn_history_public", "guidelines_agreed", "tour_completed_at", "game_detail_tour_completed_at", "badge_detail_tour_completed_at", "hide_hiddens", "discord_id", "discord_linked_at", "is_discord_verified", "verification_code")},
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
    list_filter = ("has_trophy_groups", "is_regional", RegionListFilter, 'concept_lock', 'concept_stale', 'shovelware_status', 'shovelware_lock', 'is_delisted', 'is_obtainable', "has_online_trophies", "has_buggy_trophies")
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
            new_concept = obj.concept
            super().save_model(request, obj, form, change)
            # Invalidate game page caches
            from django.core.cache import cache
            cache.delete(f"game:imageurls:{obj.np_communication_id}")
            cache.delete(f"game:trophygroups:{obj.np_communication_id}")
            # Absorb orphaned old concept
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
        "play_duration",
        "last_played_date_time",
        "last_updated_datetime",
    )
    list_select_related = ('profile', 'game')
    list_filter = ("hidden_flag",)
    search_fields = ("profile__psn_username", "game__title_name")
    raw_id_fields = ("profile", "game")
    ordering = ("-last_updated_datetime",)

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
    search_fields = ("profile__psn_username", "trophy__trophy_name", "trophy__game__title_name")
    raw_id_fields = ("profile", "trophy")
    ordering = ("-last_updated",)

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
    search_fields = ("endpoint", "profile__psn_username")
    ordering = ("-timestamp",)

@admin.register(FeaturedGame)
class FeaturedGameAdmin(admin.ModelAdmin):
    list_display = ('game', 'priority', 'reason', 'start_date', 'end_date')
    list_select_related = ('game',)
    search_fields = ('game__title_name',)
    list_filter = ('reason',)
    raw_id_fields = ('game',)

@admin.register(FeaturedProfile)
class FeaturedProfileAdmin(admin.ModelAdmin):
    list_display = ('profile', 'priority', 'reason', 'start_date', 'end_date')
    list_select_related = ('profile',)
    search_fields = ('profile__psn_username',)
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


@admin.register(Concept)
class ConceptAdmin(admin.ModelAdmin):
    list_display = ('id', 'concept_id', 'unified_title', 'release_date', 'publisher_name', 'genres')
    search_fields = ('concept_id', 'unified_title')
    actions = ['duplicate_concept', 'lock_games', 'unlock_games']
    inlines = [ConceptGameInline, ConceptCompanyInline, ConceptFranchiseInline]

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
    search_fields = ('profile__psn_username', 'concept__unified_title')
    raw_id_fields = ('profile', 'concept')

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
    search_fields = ['name', 'series_slug']
    readonly_fields = ['created_at', 'earned_count', 'view_count', 'required_stages', 'required_value']
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

@admin.register(Stage)
class StageAdmin(admin.ModelAdmin):
    list_display = ('__str__', 'series_slug', 'stage_number', 'title', 'required_tiers', 'has_online_trophies')
    list_filter = ('series_slug', 'stage_number')
    search_fields = ('title',)
    autocomplete_fields = ['concepts']

@admin.register(UserBadge)
class UserBadgeAdmin(admin.ModelAdmin):
    list_display = ['profile', 'badge', 'earned_at', 'is_displayed']
    list_select_related = ('profile', 'badge')
    list_filter = ['is_displayed', 'earned_at']
    search_fields = ['profile__psn_username']
    raw_id_fields = ('profile', 'badge')

@admin.register(ProfileBadgeShowcase)
class ProfileBadgeShowcaseAdmin(admin.ModelAdmin):
    list_display = ['profile', 'badge', 'display_order', 'created_at']
    list_select_related = ('profile', 'badge')
    list_filter = ['display_order']
    search_fields = ['profile__psn_username']
    raw_id_fields = ('profile', 'badge')

@admin.register(UserBadgeProgress)
class UserBadgeProgressAdmin(admin.ModelAdmin):
    list_display = ['profile', 'badge', 'completed_concepts', 'progress_value', 'last_checked']
    list_select_related = ('profile', 'badge')
    search_fields = ['profile__psn_username']
    raw_id_fields = ('profile', 'badge')

@admin.register(StageCompletionEvent)
class StageCompletionEventAdmin(admin.ModelAdmin):
    list_display = ['profile', 'badge', 'stage', 'concept', 'completed_at', 'created_at']
    list_select_related = ('profile', 'badge', 'stage', 'concept')
    list_filter = ['completed_at']
    search_fields = ['profile__psn_username', 'badge__name']
    raw_id_fields = ('profile', 'badge', 'stage', 'concept')
    readonly_fields = ('created_at',)
    
@admin.register(FeaturedGuide)
class FeaturedGuideAdmin(admin.ModelAdmin):
    list_display = ['concept', 'start_date', 'end_date', 'priority']
    list_select_related = ('concept',)
    list_filter = ['start_date', 'end_date']
    search_fields = ['concept__unified_title']
    raw_id_fields = ('concept',)

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == 'concept':
            kwargs['queryset'] = Concept.objects.exclude(Q(guide_slug__isnull=True) | Q(guide_slug=''))
        return super().formfield_for_foreignkey(db_field, request, **kwargs)

@admin.register(PublisherBlacklist)
class PublisherBlacklistAdmin(admin.ModelAdmin):
    list_display = ['name', 'is_blacklisted', 'concept_count', 'date_added']
    list_filter = ['is_blacklisted']
    search_fields = ['name']
    readonly_fields = ('flagged_concepts',)

    def concept_count(self, obj):
        return obj.flagged_concept_count
    concept_count.short_description = "Flagged Concepts"

@admin.register(Title)
class TitleAdmin(admin.ModelAdmin):
    list_display = ['name', 'created_at']

@admin.register(UserTitle)
class UserTitleAdmin(admin.ModelAdmin):
    list_display = ['profile', 'title', 'source_type', 'source_id', 'earned_at', 'is_displayed']
    list_select_related = ('profile', 'title')
    raw_id_fields = ('profile', 'title')

@admin.register(Milestone)
class MilestoneAdmin(admin.ModelAdmin):
    list_display = ['name', 'title', 'discord_role_id', 'criteria_type', 'criteria_details', 'premium_only', 'required_value', 'earned_count']

@admin.register(UserMilestone)
class UserMilestoneAdmin(admin.ModelAdmin):
    list_display = ['profile', 'milestone', 'earned_at']
    list_select_related = ('profile', 'milestone')
    search_fields = ['profile__psn_username', 'milestone__name']
    raw_id_fields = ('profile', 'milestone')

@admin.register(UserMilestoneProgress)
class UserMilestoneProgressAdmin(admin.ModelAdmin):
    list_display = ['profile', 'milestone', 'progress_value', 'last_checked']
    list_select_related = ('profile', 'milestone')
    search_fields = ['profile__psn_username', 'milestone__name']
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


# Checklist admin registrations removed during roadmap migration (DB tables retained)


# ---------- Roadmap Admin ----------

class RoadmapStepInline(admin.TabularInline):
    model = RoadmapStep
    extra = 0
    ordering = ['order']


class RoadmapTabInline(admin.TabularInline):
    model = RoadmapTab
    extra = 0
    show_change_link = True


@admin.register(Roadmap)
class RoadmapAdmin(admin.ModelAdmin):
    list_display = ['id', 'concept', 'status', 'created_by', 'created_at', 'updated_at']
    list_select_related = ('concept', 'created_by')
    list_filter = ['status']
    search_fields = ['concept__unified_title']
    raw_id_fields = ['concept', 'created_by']
    readonly_fields = ['created_at', 'updated_at']
    inlines = [RoadmapTabInline]


@admin.register(RoadmapTab)
class RoadmapTabAdmin(admin.ModelAdmin):
    list_display = ['id', 'roadmap', 'concept_trophy_group', 'has_tips', 'has_youtube']
    list_select_related = ('roadmap__concept', 'concept_trophy_group')
    raw_id_fields = ['roadmap', 'concept_trophy_group']
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
    list_display = ['id', 'title', 'tab', 'order']
    list_select_related = ('tab__roadmap__concept',)
    search_fields = ['title']
    raw_id_fields = ['tab']


@admin.register(RoadmapStepTrophy)
class RoadmapStepTrophyAdmin(admin.ModelAdmin):
    list_display = ['id', 'step', 'trophy_id', 'order']
    list_select_related = ('step',)
    raw_id_fields = ['step']


@admin.register(TrophyGuide)
class TrophyGuideAdmin(admin.ModelAdmin):
    list_display = ['id', 'tab', 'trophy_id', 'body_preview']
    list_select_related = ('tab__roadmap__concept',)
    raw_id_fields = ['tab']

    def body_preview(self, obj):
        return obj.body[:80] + '...' if len(obj.body) > 80 else obj.body
    body_preview.short_description = 'Body'


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
    search_fields = ['profile__psn_username']
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
    list_select_related = ('profile',)
    list_filter = ['challenge_type', 'is_complete', 'is_deleted']
    search_fields = ['name', 'profile__psn_username']
    raw_id_fields = ['profile']
    readonly_fields = ['created_at', 'updated_at', 'completed_at', 'deleted_at', 'view_count']
    ordering = ['-created_at']
    inlines = [AZChallengeSlotInline]


@admin.register(AZChallengeSlot)
class AZChallengeSlotAdmin(admin.ModelAdmin):
    list_display = ['id', 'challenge', 'letter', 'game', 'is_completed', 'assigned_at']
    list_select_related = ('challenge', 'game')
    list_filter = ['is_completed', 'letter']
    search_fields = ['challenge__name', 'challenge__profile__psn_username', 'game__title_name']
    raw_id_fields = ['challenge', 'game']
    readonly_fields = ['completed_at', 'assigned_at']
    ordering = ['challenge', 'letter']


@admin.register(GameFamily)
class GameFamilyAdmin(admin.ModelAdmin):
    list_display = ['canonical_name', 'is_verified', 'concept_count', 'created_at', 'updated_at']
    list_filter = ['is_verified']
    search_fields = ['canonical_name', 'admin_notes']
    readonly_fields = ['created_at', 'updated_at']
    ordering = ['canonical_name']

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs.annotate(_concept_count=Count('concepts'))

    def concept_count(self, obj):
        return obj._concept_count
    concept_count.short_description = 'Concepts'
    concept_count.admin_order_field = '_concept_count'


@admin.register(GameFamilyProposal)
class GameFamilyProposalAdmin(admin.ModelAdmin):
    list_display = ['proposed_name', 'confidence_pct', 'status', 'concept_count', 'reviewed_by', 'created_at']
    list_select_related = ('reviewed_by',)
    list_filter = ['status', 'created_at']
    search_fields = ['proposed_name', 'match_reason']
    raw_id_fields = ['resulting_family', 'reviewed_by']
    readonly_fields = ['created_at', 'confidence', 'match_reason', 'match_signals']
    date_hierarchy = 'created_at'
    ordering = ['-confidence', '-created_at']

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs.annotate(_concept_count=Count('concepts'))

    def confidence_pct(self, obj):
        return f"{obj.confidence:.0%}"
    confidence_pct.short_description = 'Confidence'

    def concept_count(self, obj):
        return obj._concept_count
    concept_count.short_description = 'Concepts'
    concept_count.admin_order_field = '_concept_count'


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
        'concept__unified_title',
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
    search_fields = ['profile__psn_username']
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
    search_fields = ['body', 'profile__psn_username']
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
        'moderator__username',
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
    search_fields = ['game__title_name', 'game_list__name', 'game_list__profile__psn_username']
    raw_id_fields = ['game_list', 'game']
    readonly_fields = ['added_at']
    ordering = ['game_list', 'position']

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
    search_fields = ['profile__psn_username']
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
    search_fields = ('name', 'slug')
    raw_id_fields = ('parent', 'changed_company')
    readonly_fields = ('igdb_id', 'created_at', 'updated_at')
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


@admin.register(GameEngine)
class GameEngineAdmin(admin.ModelAdmin):
    list_display = ('name', 'igdb_id', 'slug', 'game_count')
    search_fields = ('name', 'slug')
    readonly_fields = ('igdb_id',)

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(_game_count=Count('engine_concepts'))

    def game_count(self, obj):
        return obj._game_count
    game_count.short_description = 'Games'
    game_count.admin_order_field = '_game_count'


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
    )
    list_filter = ('status', 'match_method', 'game_category', PlatformCoverageFilter)
    search_fields = ('concept__unified_title', 'igdb_name')
    raw_id_fields = ('concept',)
    readonly_fields = (
        'igdb_id', 'match_confidence', 'match_method', 'raw_response',
        'created_at', 'updated_at', 'last_synced_at',
    )
    actions = ['approve_selected', 'reject_selected', 'rematch_selected']

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
        return super().get_queryset(request).select_related('concept').prefetch_related('concept__games')

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
    list_filter = ['status']
    search_fields = ['profile__psn_username', 'staff_notes']
    raw_id_fields = ['profile', 'added_by']
    readonly_fields = ['created_at', 'updated_at', 'games_discovered']
    ordering = ['-created_at']
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
