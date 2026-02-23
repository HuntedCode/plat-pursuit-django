from django.db import models, DatabaseError, IntegrityError, OperationalError
from django.utils import timezone
from users.models import CustomUser
from django.contrib.postgres.fields import ArrayField
from django.contrib.postgres.indexes import GinIndex
from django.core.validators import RegexValidator, MinValueValidator, MaxValueValidator
from django.db import transaction
from django.db.models import F, IntegerField, Max, Min, Q
from django.db.models.functions import Cast, Substr
import logging

logger = logging.getLogger("psn_api")
from datetime import timedelta
from tenacity import retry, stop_after_attempt, wait_fixed, retry_if_exception_type
from trophies.util_modules.language import count_unique_game_groups, calculate_trimmed_mean
from trophies.util_modules.constants import (
    TITLE_STATS_SUPPORTED_PLATFORMS, NA_REGION_CODES, EU_REGION_CODES,
    JP_REGION_CODES, AS_REGION_CODES, KR_REGION_CODES, CN_REGION_CODES,
)
from trophies.managers import (
    ProfileManager, GameManager, ProfileGameManager,
    BadgeManager, MilestoneManager, CommentManager, ChecklistManager
)
import re


# Create your models here.

class Profile(models.Model):
    user = models.OneToOneField(
        CustomUser,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="profile",
    )
    psn_username = models.CharField(
        max_length=16,
        unique=True,
        help_text="PSN Online ID (case-sensitive as per PSN)",
        validators=[
            RegexValidator(
                regex=r"^[a-zA-Z0-9_-]{3,16}$",
                message="PSN username must be 3-16 characters, using letters, numbers, hyphens or underscores.",
            )
        ],
    )
    display_psn_username = models.CharField(
        max_length=16,
        blank=True,
        null=True,
        help_text="PSN username with original capitalization (populated from PSN API)"
    )
    verification_code = models.CharField(max_length=32, blank=True, null=True, help_text="Temporary code for PSN About Me verification.")
    verification_expires_at = models.DateTimeField(blank=True, null=True, help_text="Expiration timestamp for verification code.")
    is_discord_verified = models.BooleanField(default=False, help_text="True if Discord ownership verified via code.")
    account_id = models.CharField(max_length=20, unique=True, blank=True, null=True)
    np_id = models.CharField(max_length=50, blank=True, null=True)
    avatar_url = models.URLField(blank=True, null=True)
    is_plus = models.BooleanField(default=False)
    about_me = models.TextField(blank=True)
    languages_used = models.JSONField(default=list, blank=True)
    trophy_level = models.IntegerField(default=0)
    progress = models.IntegerField(default=0)
    tier = models.IntegerField(default=0)
    earned_trophy_summary = models.JSONField(default=dict, blank=True)
    country = models.CharField(max_length=250, blank=True, null=True)
    country_code = models.CharField(max_length=5, blank=True, null=True)
    flag = models.CharField(max_length=5, blank=True, null=True)
    extra_data = models.JSONField(default=dict, blank=True)
    last_synced = models.DateTimeField(default=timezone.now)
    last_profile_health_check = models.DateTimeField(null=True)
    user_is_premium = models.BooleanField(default=False)
    sync_tier = models.CharField(
        max_length=10,
        choices=[("basic", "Basic"), ("preferred", "Preferred")],
        default="basic",
    )
    sync_status = models.CharField(
        max_length=20,
        choices=[('synced', 'Synced'), ('syncing', 'Syncing'), ('error', 'Error')],
        default='synced',
        help_text='Current sync state of the profile'
    )
    sync_progress_value = models.IntegerField(default=0, help_text='Current sync progress value')
    sync_progress_target = models.IntegerField(default=0, help_text='Current sync progress target')
    is_linked = models.BooleanField(default=False)
    psn_history_public = models.BooleanField(default=True, help_text="Flag indicating if PSN gaming history is public.")
    created_at = models.DateTimeField(auto_now_add=True)
    discord_id = models.BigIntegerField(unique=True, blank=True, null=True, help_text='Unique Discord user ID. Set on bot linking.')
    discord_linked_at = models.DateTimeField(blank=True, null=True, help_text='Timestamp when Discord was linked via bot.')
    total_trophies = models.PositiveIntegerField(default=0)
    total_unearned = models.PositiveIntegerField(default=0)
    total_bronzes = models.PositiveIntegerField(default=0)
    total_silvers = models.PositiveIntegerField(default=0)
    total_golds = models.PositiveIntegerField(default=0)
    total_plats = models.PositiveIntegerField(default=0)
    total_hiddens = models.PositiveIntegerField(default=0)
    total_games = models.PositiveIntegerField(default=0)
    total_completes = models.PositiveIntegerField(default=0)
    avg_progress = models.FloatField(default=0.0)
    recent_plat = models.ForeignKey('EarnedTrophy', on_delete=models.SET_NULL, null=True, blank=True, related_name='recent_for_profiles', help_text='Most recent earned platinum.')
    rarest_plat = models.ForeignKey('EarnedTrophy', on_delete=models.SET_NULL, null=True, blank=True, related_name='rarest_for_profiles', help_text='Rarest earned platinum by earn_rate.')
    selected_background = models.ForeignKey('Concept', on_delete=models.SET_NULL, null=True, blank=True, related_name='selected_by_profiles', help_text='Selected background concept for premium profiles.')
    selected_theme = models.CharField(max_length=50, blank=True, null=True, help_text='Selected gradient theme key for premium site-wide background.')
    hide_hiddens = models.BooleanField(default=False, help_text="If true, hide hidden/deleted games from list and totals.")
    hide_zeros = models.BooleanField(default=False, help_text="If true, hide games with no trophies earned.")
    guidelines_agreed = models.BooleanField(default=False, help_text="True if user has agreed to community guidelines for commenting.")
    guidelines_agreed_at = models.DateTimeField(null=True, blank=True, help_text="Timestamp when user agreed to community guidelines.")
    view_count = models.PositiveIntegerField(default=0, help_text="Denormalized total page view count.")

    objects = ProfileManager()

    class Meta:
        indexes = [
            models.Index(fields=["psn_username"], name="psn_username_idx"),
            models.Index(fields=["account_id"], name="account_id_idx"),
            models.Index(fields=['discord_id'], name='discord_id_idx'),
            models.Index(fields=['is_discord_verified', 'last_synced'], name='verified_synced_idx'),
            models.Index(fields=['sync_status'], name='profile_sync_status_idx'),
            models.Index(fields=['total_trophies'], name='profile_total_trophies_idx'),
            models.Index(fields=['total_unearned'], name='profile_total_unearned_idx'),
            models.Index(fields=['total_bronzes'], name='profile_total_bronzes_idx'),
            models.Index(fields=['total_silvers'], name='profile_total_silver_idx'),
            models.Index(fields=['total_golds'], name='profile_total_gold_idx'),
            models.Index(fields=['total_plats'], name='profile_total_plats_idx'),
            models.Index(fields=['total_games'], name='profile_total_games_idx'),
            models.Index(fields=['total_completes'], name='profile_total_completes_idx'),
            models.Index(fields=['avg_progress'], name='profile_avg_progress_idx'),
            models.Index(fields=['last_synced'], name='profile_last_synced_idx'),
            models.Index(fields=['created_at'], name='profile_created_at_idx'),
            models.Index(fields=['country_code'], name='profile_country_code_idx'),
            models.Index(fields=['is_linked', 'sync_tier'], name='profile_linked_tier_idx'),
            models.Index(fields=['is_discord_verified', 'discord_linked_at'], name='profile_discord_idx'),
            models.Index(fields=['user_is_premium', 'selected_background']),
        ]

    def __str__(self):
        return self.psn_username
    
    def save(self, *args, **kwargs):
        if self.psn_username:
            self.psn_username = self.psn_username.lower()
        super().save(*args, **kwargs)

    def link_to_user(self, user):
        """
        Link this PSN profile to a user account.

        Delegates to VerificationService for actual logic.
        Maintained for backward compatibility.

        Args:
            user: CustomUser instance to link to
        """
        from trophies.services.verification_service import VerificationService
        VerificationService.link_profile_to_user(self, user)

    def unlink_user(self):
        """
        Unlink this PSN profile from its user account.

        Delegates to VerificationService for actual logic.
        Maintained for backward compatibility.
        """
        from trophies.services.verification_service import VerificationService
        VerificationService.unlink_profile_from_user(self)
    
    def update_profile_premium(self, is_premium: bool):
        self.sync_tier = 'preferred' if is_premium else 'basic'
        self.user_is_premium = is_premium
        self.save(update_fields=['sync_tier', 'user_is_premium'])
    
    def get_time_since_last_sync(self) -> timedelta:
        """
        Get time elapsed since last successful sync.

        Delegates to SyncService for actual logic.
        Maintained for backward compatibility.

        Returns:
            timedelta: Time since last sync
        """
        from trophies.services.sync_service import SyncService
        return SyncService.get_time_since_last_sync(self)

    def attempt_sync(self):
        """
        Attempt to initiate profile synchronization.

        Delegates to SyncService for actual logic.
        Maintained for backward compatibility.

        Returns:
            bool: True if sync initiated, False if cooldown active
        """
        from trophies.services.sync_service import SyncService
        return SyncService.initiate_sync(self)

    def get_seconds_to_next_sync(self):
        """
        Get seconds remaining until next sync is allowed.

        Delegates to SyncService for actual logic.
        Maintained for backward compatibility.

        Returns:
            int: Seconds until next sync allowed
        """
        from trophies.services.sync_service import SyncService
        return SyncService.get_seconds_to_next_sync(self)
    
    def generate_verification_code(self):
        """
        Generate and set a secure, time-limited verification code.

        Delegates to VerificationService for actual logic.
        Maintained for backward compatibility.

        Returns:
            str: The generated verification code
        """
        from trophies.services.verification_service import VerificationService
        return VerificationService.generate_code(self)

    def verify_code(self, fetched_about_me: str) -> bool:
        """
        Verify that verification code appears in PSN About Me section.

        Delegates to VerificationService for actual logic.
        Maintained for backward compatibility.

        Args:
            fetched_about_me: About Me text from PSN API

        Returns:
            bool: True if verification successful
        """
        from trophies.services.verification_service import VerificationService
        return VerificationService.verify_code(self, fetched_about_me)

    def clear_verification_code(self):
        """
        Clear verification code and expiry from profile.

        Delegates to VerificationService for actual logic.
        Maintained for backward compatibility.
        """
        from trophies.services.verification_service import VerificationService
        VerificationService.clear_code(self)

    def get_total_trophies_from_summary(self):
        if self.earned_trophy_summary:
            return self.earned_trophy_summary.get('bronze', 0) + self.earned_trophy_summary.get('silver', 0) + self.earned_trophy_summary.get('gold', 0) + self.earned_trophy_summary.get('platinum', 0)

    def displayed_title(self):
        user_title = self.user_titles.filter(is_displayed=True).first()
        return user_title.title.name if user_title else None
    
    def get_earned_titles(self):
        return Title.objects.earned_by_user(self)

    def link_discord(self, discord_id: int):
        if self.discord_id:
            raise ValueError("Discord already linked to this profile.")
        self.discord_id = discord_id
        self.discord_linked_at = timezone.now()
        self.is_discord_verified = True
        self.save(update_fields=['discord_id', 'discord_linked_at', 'is_discord_verified'])

        # Check for Discord linking milestones
        from trophies.services.milestone_service import check_all_milestones_for_user
        check_all_milestones_for_user(self, criteria_type='discord_linked')
    
    def unlink_discord(self):
        # Collect all Discord roles to remove while discord_id is still set
        all_role_ids = set()
        should_remove_roles = self.discord_id and self.is_discord_verified

        if should_remove_roles:
            from trophies.services.badge_service import notify_bot_role_removed

            # Collect badge roles
            all_role_ids.update(
                UserBadge.objects.filter(profile=self, badge__discord_role_id__isnull=False)
                .values_list('badge__discord_role_id', flat=True)
            )

            # Collect milestone roles
            all_role_ids.update(
                UserMilestone.objects.filter(profile=self, milestone__discord_role_id__isnull=False)
                .values_list('milestone__discord_role_id', flat=True)
            )

            # Collect premium roles if applicable
            from django.conf import settings
            if self.user_is_premium:
                for role_setting in ('DISCORD_PREMIUM_ROLE', 'DISCORD_PREMIUM_PLUS_ROLE'):
                    role_id = getattr(settings, role_setting, 0)
                    if role_id:
                        all_role_ids.add(role_id)

        # Clear Discord fields immediately, defer role removal HTTP calls
        discord_id_snapshot = self.discord_id
        self.discord_id = None
        self.discord_linked_at = None
        self.is_discord_verified = False
        self.save(update_fields=['discord_id', 'discord_linked_at', 'is_discord_verified'])

        # Schedule role removal after transaction commits (avoid blocking HTTP calls)
        if should_remove_roles and all_role_ids and discord_id_snapshot:
            from django.db import transaction

            class _ProfileSnapshot:
                """Lightweight snapshot to pass discord_id to notify_bot_role_removed."""
                def __init__(self, discord_id, psn_username):
                    self.discord_id = discord_id
                    self.psn_username = psn_username

            snapshot = _ProfileSnapshot(discord_id_snapshot, self.psn_username)
            for role_id in all_role_ids:
                rid = role_id
                transaction.on_commit(lambda rid=rid: notify_bot_role_removed(snapshot, rid))
    
    def set_history_public_flag(self, value: bool):
        self.psn_history_public = value
        self.save(update_fields=['psn_history_public'])

    def set_sync_status(self, value: str) -> bool:
        """Set the sync status. Returns False if the profile was deleted (no account_id + error state)."""
        if value not in ['syncing', 'synced', 'error']:
            return True
        if value == 'error' and not self.account_id:
            logger.info(f"Deleting unlinked profile {self.id} (no account_id) after sync error.")
            self.delete()
            return False
        self.sync_status = value
        self.save(update_fields=['sync_status'])
        self.refresh_from_db(fields=['sync_status'])
        return True
    
    @retry(stop=stop_after_attempt(5), wait=wait_fixed(0.2), retry=retry_if_exception_type(OperationalError))
    def add_to_sync_target(self, value: int):
        if not value:
            return

        try:
            with transaction.atomic():
                Profile.objects.select_for_update(nowait=True).filter(id=self.id).update(
                    sync_progress_target=F('sync_progress_target') + value
                )
                self.refresh_from_db(fields=['sync_progress_target'])
        except Profile.DoesNotExist:
            logger.error(f"Profile {self.id} not found in add_to_sync_target")
        except OperationalError:
            # Re-raise so @retry can handle lock contention
            raise
        except DatabaseError as db_err:
            logger.warning(f"Database error in add_to_sync_target for profile {self.id}: {db_err}")
        except Exception as e:
            logger.error(f"Unexpected error in add_to_sync_target for profile {self.id}: {e}")
    
    def increment_sync_progress(self, value: int = 1):
        self.sync_progress_value = F('sync_progress_value') + value
        self.save(update_fields=['sync_progress_value'])
        self.refresh_from_db(fields=['sync_progress_value'])

    def reset_sync_progress(self):
        self.sync_progress_target = 0
        self.sync_progress_value = 0
        self.save(update_fields=['sync_progress_target', 'sync_progress_value'])
        self.refresh_from_db(fields=['sync_progress_target', 'sync_progress_value'])

    @property
    def sync_percentage(self):
        if self.sync_progress_target > 0:
            return self.sync_progress_value / self.sync_progress_target * 100
        return 0
    
    def update_plats(self):
        """Recalculate and update recent and rarest platinums."""
        platinums = self.earned_trophy_entries.filter(earned=True, trophy__trophy_type='platinum')

        # Single aggregate for both max date and min earn rate (saves 2 queries)
        agg = platinums.aggregate(
            max_date=Max('earned_date_time'),
            min_rate=Min('trophy__trophy_earn_rate'),
        )

        max_date = agg['max_date']
        min_rate = agg['min_rate']

        if max_date is None:
            self.recent_plat = None
            self.rarest_plat = None
        else:
            self.recent_plat = platinums.filter(earned_date_time=max_date).first()
            self.rarest_plat = (
                platinums.filter(trophy__trophy_earn_rate=min_rate)
                .order_by('trophy__trophy_earn_rate').first()
                if min_rate is not None else None
            )

        self.save(update_fields=['recent_plat', 'rarest_plat'])

class FeaturedProfile(models.Model):
    profile = models.ForeignKey(Profile, on_delete=models.CASCADE)
    priority = models.IntegerField(default=0, help_text='Higher = preferred for display')
    start_date = models.DateField(null=True, blank=True, help_text="Feature from this date")
    end_date = models.DateField(null=True, blank=True, help_text="Feature until this date")
    reason = models.CharField(max_length=100, blank=True, choices=[('admin_pick', 'Admin Pick'), ('top_week', 'Top of Week'), ('event', 'Event')])

    class Meta:
        ordering = ['-priority']


class Game(models.Model):
    np_communication_id = models.CharField(
        max_length=50, unique=True, blank=True, null=True
    )
    np_service_name = models.CharField(max_length=50, blank=True)
    trophy_set_version = models.CharField(max_length=10, blank=True)
    title_name = models.CharField(max_length=255)
    lock_title = models.BooleanField(default=False, help_text="Admin title override - won't be automatically updated.")
    title_detail = models.TextField(blank=True, null=True)
    title_image = models.URLField(blank=True, null=True)
    title_icon_url = models.URLField(blank=True, null=True)
    force_title_icon = models.BooleanField(default=False, help_text="Force displays to use the title icon instead of title image.")
    title_platform = models.JSONField(default=list, blank=True)
    has_trophy_groups = models.BooleanField(default=False)
    defined_trophies = models.JSONField(default=dict, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    concept = models.ForeignKey('Concept', null=True, blank=True, on_delete=models.SET_NULL, related_name='games')
    region = models.JSONField(default=list, blank=True)
    title_ids = models.JSONField(default=list, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    played_count = models.PositiveIntegerField(default=0, help_text="Denormalized count of profiles that have played the game (PP-specific).")
    view_count = models.PositiveIntegerField(default=0, help_text="Denormalized total page view count.")
    is_regional = models.BooleanField(default=False)
    region_lock = models.BooleanField(default=False, help_text="Admin region override lock - won't be automatically updated.")
    concept_lock = models.BooleanField(default=False, help_text="Admin concept override lock - won't be automatically updated.")
    concept_stale = models.BooleanField(default=False, help_text="Flag for concept re-lookup on next sync.")
    shovelware_status = models.CharField(
        max_length=20,
        choices=[
            ('clean', 'Clean'),
            ('auto_flagged', 'Auto-Flagged'),
            ('manually_flagged', 'Manually Flagged'),
            ('manually_cleared', 'Manually Cleared'),
        ],
        default='clean',
        help_text="Shovelware detection status. Manual statuses are never overwritten by auto-detection."
    )
    shovelware_lock = models.BooleanField(
        default=False,
        help_text="Admin lock: prevents auto-detection from changing this game's shovelware status."
    )
    shovelware_updated_at = models.DateTimeField(null=True, blank=True)
    is_obtainable= models.BooleanField(default=True)
    is_delisted = models.BooleanField(default=False)
    has_online_trophies = models.BooleanField(default=False)

    objects = GameManager()

    class Meta:
        indexes = [
            models.Index(fields=["np_communication_id", "title_name"], name="game_idx"),
            models.Index(fields=['played_count'], name='game_played_count_idx'),
            models.Index(fields=['title_name'], name='game_title_idx'),
            GinIndex(fields=['title_platform'], name='game_platform_gin_idx'),
            models.Index(fields=['created_at'], name='game_created_idx'),
            models.Index(fields=['is_obtainable', 'title_platform'], name='game_obtainable_platform_idx'),
            models.Index(fields=['shovelware_status'], name='game_sw_status_idx'),
            models.Index(fields=['is_delisted'], name='game_delisted_idx'),
            models.Index(fields=['is_regional'], name='game_regional_idx'),
            models.Index(fields=['has_online_trophies'], name='game_online_trophies_idx'),
        ]
    
    def save(self, *args, **kwargs):
        fields_to_clean = ['title_name']
        
        for field in fields_to_clean:
            if hasattr(self, field):
                value = getattr(self, field)
                cleaned_value = re.sub(r'[™®]|(\bTM\b)|(\(R\))', '', value).strip()
                setattr(self, field, cleaned_value)
        super().save(*args, **kwargs)

    def add_concept(self, concept):
        if not concept or self.concept_lock:
            return
        if self.concept == concept:
            if self.concept_stale:
                self.concept_stale = False
                self.save(update_fields=['concept_stale'])
            return
        old_concept = self.concept
        self.concept = concept
        self.concept_stale = False
        self.save(update_fields=['concept', 'concept_stale'])
        # Invalidate game page caches since concept data changed
        from django.core.cache import cache
        cache.delete(f"game:imageurls:{self.np_communication_id}")
        cache.delete(f"game:trophygroups:{self.np_communication_id}")
        if old_concept and old_concept.games.count() == 0:
            concept.absorb(old_concept)
            old_concept.delete()
            from trophies.services.comment_service import CommentService
            from trophies.services.rating_service import RatingService
            CommentService.invalidate_cache(concept)
            RatingService.invalidate_cache(concept)
    
    def add_region(self, region: str):
        if region and not self.region_lock:
            if region in NA_REGION_CODES:
                region = 'NA'
            elif region in EU_REGION_CODES:
                region = 'EU'
            elif region in JP_REGION_CODES:
                region = 'JP'
            elif region in AS_REGION_CODES:
                region = 'AS'
            elif region in KR_REGION_CODES:
                region = 'KR'
            elif region in CN_REGION_CODES:
                region = 'CN'
            else:
                return
            if region not in self.region:
                self.region.append(region)
                self.save(update_fields=['region'])
    
    def add_title_id(self, title_id: str):
        if title_id and title_id not in self.title_ids:
            self.title_ids.append(title_id)
            self.save(update_fields=['title_ids'])
    
    @property
    def is_shovelware(self):
        """Whether this game is flagged as shovelware (auto or manual)."""
        return self.shovelware_status in ('auto_flagged', 'manually_flagged')

    def get_total_defined_trophies(self):
        return self.defined_trophies['bronze'] + self.defined_trophies['silver'] + self.defined_trophies['gold'] + self.defined_trophies['platinum']

    def get_icon_url(self):
        if self.force_title_icon or not self.title_image:
            return self.title_icon_url
        return self.title_image

    @property
    def image_url(self):
        """Alias for get_icon_url() for template convenience."""
        return self.get_icon_url()

    @property
    def platforms_display(self):
        """Format platforms for display: 'PS5, PS4' or 'PS5'."""
        if not self.title_platform:
            return 'Unknown'
        return ', '.join(self.title_platform)
    
    @property
    def regions_display(self):
        """Format regions for display"""
        if not self.is_regional:
            return 'Global'
        return ', '.join(self.region)

    @property
    def trophy_count_summary(self):
        """Format trophy counts: '32Total/8B/18S/5G/1P'."""
        if not self.defined_trophies:
            return '0 Trophies'

        total = self.get_total_defined_trophies()
        bronze = self.defined_trophies.get('bronze', 0)
        silver = self.defined_trophies.get('silver', 0)
        gold = self.defined_trophies.get('gold', 0)
        platinum = self.defined_trophies.get('platinum', 0)

        return f"{total}Total/{bronze}B/{silver}S/{gold}G/{platinum}P"

    def __str__(self):
        return self.title_name

class GameFamily(models.Model):
    """Groups related Concepts across generations/regions without merging them.

    Each Concept keeps its own comments, ratings, and checklists.
    GameFamily is a lightweight grouping layer for cross-gen unification.
    """
    canonical_name = models.CharField(max_length=255, db_index=True)
    admin_notes = models.TextField(blank=True)
    is_verified = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name_plural = "Game families"
        ordering = ['canonical_name']

    def __str__(self):
        return self.canonical_name


class GameFamilyProposal(models.Model):
    """Proposed GameFamily grouping awaiting admin review."""
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('approved', 'Approved'),
        ('rejected', 'Rejected'),
    ]

    concepts = models.ManyToManyField('Concept', related_name='family_proposals')
    proposed_name = models.CharField(max_length=255)
    confidence = models.FloatField()
    match_reason = models.TextField()
    match_signals = models.JSONField(default=dict)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='pending')
    reviewed_by = models.ForeignKey(
        'users.CustomUser', null=True, blank=True, on_delete=models.SET_NULL
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    resulting_family = models.ForeignKey(
        GameFamily, null=True, blank=True, on_delete=models.SET_NULL, related_name='proposals'
    )

    class Meta:
        ordering = ['-confidence', '-created_at']

    def __str__(self):
        return f"Proposal: {self.proposed_name} ({self.get_status_display()}, {self.confidence:.0%})"


class Concept(models.Model):
    concept_id = models.CharField(max_length=50, unique=True)
    unified_title = models.CharField(max_length=255, blank=True)
    title_ids = models.JSONField(default=list, blank=True)
    family = models.ForeignKey(
        GameFamily, null=True, blank=True, on_delete=models.SET_NULL, related_name='concepts'
    )
    publisher_name = models.CharField(max_length=255, blank=True)
    release_date = models.DateTimeField(null=True, blank=True)
    genres = models.JSONField(default=list, blank=True)
    subgenres = models.JSONField(default=list, blank=True)
    descriptions = models.JSONField(default=dict, blank=True)
    content_rating = models.JSONField(default=dict, blank=True)
    media = models.JSONField(default=dict, blank=True)
    bg_url = models.URLField(null=True, blank=True)
    concept_icon_url = models.URLField(null=True, blank=True)
    guide_slug = models.CharField(max_length=50, blank=True, null=True)
    guide_created_at = models.DateTimeField(null=True, blank=True)
    comment_count = models.PositiveIntegerField(default=0, help_text="Denormalized count of concept-level comments (excludes trophy and checklist comments)")

    class Meta:
        indexes = [
            models.Index(fields=['concept_id'], name='concept_id_idx'),
            models.Index(fields=['unified_title'], name='concept_title_idx'),
            models.Index(fields=['publisher_name'], name='content_publisher_idx'),
            models.Index(fields=['release_date'], name='concept_release_date_idx'),
        ]
    
    def save(self, *args, **kwargs):
        fields_to_clean = ['unified_title']
        
        for field in fields_to_clean:
            if hasattr(self, field):
                value = getattr(self, field)
                cleaned_value = re.sub(r'[™®]|(\bTM\b)|(\(R\))', '', value).strip()
                setattr(self, field, cleaned_value)
        super().save(*args, **kwargs)
    
    @classmethod
    def create_default_concept(cls, game):
        """Create a stub Concept for games that couldn't be looked up via PSN API.

        Uses a Redis atomic counter for unique ID generation. Falls back to
        DB-based max+1 with retries if the counter isn't initialized.
        """
        from trophies.util_modules.cache import redis_client

        platforms = ', '.join(game.title_platform) if game.title_platform else 'Unknown'
        counter_key = "pp_concept_counter"

        # Initialize counter from DB if it doesn't exist yet
        if not redis_client.exists(counter_key):
            max_counter = (
                cls.objects
                .filter(concept_id__startswith='PP_')
                .annotate(numeric_suffix=Cast(Substr('concept_id', 4), output_field=IntegerField()))
                .aggregate(max_val=Max('numeric_suffix'))
            )['max_val'] or 0
            # SET NX to avoid overwriting if another worker initialized it first
            redis_client.set(counter_key, max_counter, nx=True)

        for attempt in range(5):
            next_id = redis_client.incr(counter_key)
            try:
                return cls.objects.create(
                    concept_id=f"PP_{next_id}",
                    unified_title=f"{game.title_name} ({platforms})",
                    concept_icon_url=game.title_icon_url or None,
                )
            except IntegrityError:
                if attempt == 4:
                    raise

    def absorb(self, other):
        """Migrate all related data from another concept to this one before deletion.

        IMPORTANT: When adding new models with FK/M2M to Concept, update this method.
        See CLAUDE.md for the full list of currently handled relationships.
        """
        if other == self:
            return

        # Comments (concept-level, trophy-level, checklist-level)
        other.comments.update(concept=self)

        # Ratings: re-point non-duplicate, duplicates cascade-delete with old concept
        existing_raters = set(self.user_ratings.values_list('profile_id', flat=True))
        other.user_ratings.exclude(profile_id__in=existing_raters).update(concept=self)

        # Checklists
        other.checklists.update(concept=self)

        # Featured guides
        other.featured_entries.update(concept=self)

        # Profiles using old concept as background
        other.selected_by_profiles.update(selected_background=self)

        # Badge most_recent_concept
        other.most_recent_for_badges.update(most_recent_concept=self)

        # Stages M2M
        for stage in other.stages.all():
            stage.concepts.add(self)
            stage.concepts.remove(other)

        # GameFamilyProposal M2M
        for proposal in other.family_proposals.all():
            proposal.concepts.add(self)
            proposal.concepts.remove(other)

        # Genre challenge slots
        other.genre_challenge_slots.update(concept=self)

        # Genre bonus slots
        other.genre_bonus_slots.update(concept=self)

        # GameFamily — inherit if this concept doesn't have one
        if other.family and not self.family:
            self.family = other.family
            self.save(update_fields=['family'])

        # Merge title_ids
        for tid in other.title_ids:
            if tid not in self.title_ids:
                self.title_ids.append(tid)
        if other.title_ids:
            self.save(update_fields=['title_ids'])

        # Rebuild comment count
        self.comment_count = self.comments.filter(
            is_deleted=False, trophy_id__isnull=True, checklist_id__isnull=True
        ).count()
        self.save(update_fields=['comment_count'])

    def add_title_id(self, title_id: str):
        if title_id and title_id not in self.title_ids:
            self.title_ids.append(title_id)
            self.save(update_fields=['title_ids'])

    def check_and_mark_regional(self):
        """Check if this concept has multiple games for the same platform. If so, mark as regional. Run post sync."""
        for platform in TITLE_STATS_SUPPORTED_PLATFORMS:
                games = self.games.filter(title_platform__contains=platform, region_lock=False)
                if count_unique_game_groups(games) > 1:
                    for game in games:
                        game.is_regional = True
                        game.save(update_fields=['is_regional'])

    def update_media(self, media, icon_url, bg_url):
        if media:
            self.media = media
            self.concept_icon_url = icon_url
            self.bg_url = bg_url
            self.save(update_fields=['media', 'concept_icon_url', 'bg_url'])
    
    def update_release_date(self, date):
        if date:
            self.release_date = date
            self.save(update_fields=['release_date'])
    
    def has_user_earned_platinum(self, profile):
        platinum_trophies = Trophy.objects.filter(game__concept=self, trophy_type='platinum')
        return EarnedTrophy.objects.filter(profile=profile, trophy__in=platinum_trophies, earned=True).exists()

    def get_community_averages(self):
        """
        Calculate community rating averages for this game concept.

        Delegates to RatingService for actual logic.
        Maintained for backward compatibility.

        Returns:
            dict or None: Rating averages dictionary, or None if no ratings
        """
        from trophies.services.rating_service import RatingService
        return RatingService.get_community_averages(self)

    def __str__(self):
        return self.unified_title or self.concept_id

class TitleID(models.Model):
    title_id = models.CharField(max_length=50, unique=True)
    platform = models.CharField(max_length=10)
    region = models.CharField(max_length=10, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=['title_id'], name='title_id_idx'),
            models.Index(fields=['region'], name='title_region_idx'),
        ]

    def __str__(self):
        return self.title_id

class UserConceptRating(models.Model):
    profile = models.ForeignKey(Profile, on_delete=models.CASCADE, related_name='concept_ratings')
    concept = models.ForeignKey(Concept, on_delete=models.CASCADE, related_name='user_ratings')
    difficulty = models.IntegerField(validators=[MinValueValidator(1), MaxValueValidator(10)], help_text='Platinum Difficulty rating (1-10)')
    grindiness = models.IntegerField(validators=[MinValueValidator(1), MaxValueValidator(10)], help_text='Platinum grindiness rating (1-10)')
    hours_to_platinum = models.PositiveIntegerField(help_text='Estimated hours to achieve platinum.')
    fun_ranking = models.IntegerField(validators=[MinValueValidator(1), MaxValueValidator(10)], help_text='Fun ranking for the platinum (1-10)')
    overall_rating = models.FloatField(validators=[MinValueValidator(0.5), MaxValueValidator(5.0)], help_text="Overall game rating (1-5 stars)")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ['profile', 'concept']
        indexes = [
            models.Index(fields=['concept'], name='user_rating_concept_idx'),
            models.Index(fields=['profile', 'concept'], name='user_rating_unique_idx'),
        ]
        ordering = ['-updated_at']
    
    def __str__(self):
        return f"{self.profile.display_psn_username}'s rating for {self.concept.unified_title}"

class ProfileGame(models.Model):
    profile = models.ForeignKey(
        Profile, on_delete=models.CASCADE, related_name="played_games"
    )
    game = models.ForeignKey(Game, on_delete=models.CASCADE, related_name="played_by")
    play_count = models.IntegerField(default=0)
    first_played_date_time = models.DateTimeField(blank=True, null=True)
    last_played_date_time = models.DateTimeField(blank=True, null=True)
    play_duration = models.DurationField(blank=True, null=True)
    progress = models.IntegerField(default=0)
    hidden_flag = models.BooleanField(default=False)
    earned_trophies = models.JSONField(default=dict, blank=True)
    last_updated_datetime = models.DateTimeField(blank=True, null=True)
    last_sync = models.DateTimeField(auto_now=True)
    most_recent_trophy_date = models.DateTimeField(null=True, blank=True, help_text="Date of most recent trophy earned.")
    earned_trophies_count = models.PositiveIntegerField(default=0, help_text="Number of earned trophies.")
    unearned_trophies_count = models.PositiveIntegerField(default=0, help_text="Number of unearned trophies.")
    has_plat = models.BooleanField(default=False, help_text="Whether the plat has been earned.")
    user_hidden = models.BooleanField(default=False, help_text="True if user has game hidden/deleted.")

    objects = ProfileGameManager()

    class Meta:
        unique_together = ["profile", "game"]
        indexes = [
            models.Index(fields=["last_updated_datetime"], name="profilegame_updated_idx"),
            models.Index(fields=["progress"], name="profilegame_progress_idx"),
            models.Index(fields=['most_recent_trophy_date'], name='pg_recent_trophy_idx'),
            models.Index(fields=['earned_trophies_count'], name='pg_earned_count_idx'),
            models.Index(fields=['unearned_trophies_count'], name='pg_unearned_count_idx'),
            models.Index(fields=['has_plat'], name='pg_has_plat_idx'),
        ]
    
    @property
    def total_trophies(self):
        return self.earned_trophies_count + self.unearned_trophies_count


class FeaturedGame(models.Model):
    game = models.ForeignKey(Game, on_delete=models.CASCADE)
    priority = models.IntegerField(default=0, help_text="Higher = shown first")
    start_date = models.DateField(null=True, blank=True)
    end_date = models.DateField(null=True, blank=True)
    reason = models.CharField(max_length=100, blank=True, choices=[('staff_pick', 'Staff Pick'), ('event', 'Event'), ('trending', 'Trending')])

    class Meta:
        ordering = ['-priority']

class FeaturedGuide(models.Model):
    concept = models.ForeignKey('Concept', on_delete=models.CASCADE, related_name='featured_entries')
    start_date = models.DateField(null=True, blank=True, help_text='Start of feature period')
    end_date = models.DateField(null=True, blank=True, help_text='End of featured period')
    priority = models.PositiveIntegerField(default=1, help_text='Higher = preferred if multiple overlap')

    class Meta:
        ordering = ['-priority', '-start_date']
        indexes = [
            models.Index(fields=['start_date', 'end_date'], name='featured_date_idx'),
        ]
    
    def __str__(self):
        return f"Featured: {self.concept.unified_title} ({self.start_date} to {self.end_date})"

class Trophy(models.Model):
    trophy_set_version = models.CharField(max_length=10, blank=True)
    trophy_id = models.IntegerField()
    trophy_type = models.CharField(max_length=20)
    trophy_name = models.CharField(max_length=255)
    trophy_detail = models.TextField(blank=True, null=True)
    trophy_icon_url = models.URLField(blank=True, null=True)
    trophy_group_id = models.CharField(max_length=10, default="default")
    progress_target_value = models.CharField(max_length=50, blank=True, null=True)
    reward_name = models.CharField(max_length=255, blank=True, null=True)
    reward_img_url = models.URLField(blank=True, null=True)
    trophy_rarity = models.IntegerField(blank=True, null=True, help_text='Common=3, Rare=2, Very_Rare=1, Ultra_Rare=0')  # PSN global
    trophy_earn_rate = models.FloatField(default=0.0)  # PSN global
    game = models.ForeignKey(Game, on_delete=models.CASCADE, related_name="trophies")
    earned_by = models.ManyToManyField(
        Profile, through="EarnedTrophy", related_name="earned_trophies"
    )
    earned_count = models.PositiveIntegerField(default=0, help_text="Denormalized count of profiles that have earned this trophy (PP-specific).")
    earn_rate = models.FloatField(default=0.0)

    class Meta:
        unique_together = ["trophy_id", "game"]
        indexes = [
            models.Index(fields=["trophy_name", "trophy_type"], name="trophy_idx"),
            models.Index(fields=["earned_count"], name="trophy_earned_count_idx"),
            models.Index(fields=['trophy_earn_rate'], name="trophy_psn_rate_idx"),
            models.Index(fields=['earn_rate'], name='trophy_pp_rate_idx'),
        ]
    
    def save(self, *args, **kwargs):
        fields_to_clean = ['trophy_name']
        
        for field in fields_to_clean:
            if hasattr(self, field):
                value = getattr(self, field)
                cleaned_value = re.sub(r'[™®]|(\bTM\b)|(\(R\))', '', value).strip()
                setattr(self, field, cleaned_value)
        super().save(*args, **kwargs)
    
    def get_pp_rarity_tier(self):
        """Compute Plat Pursuit specific rarity tier based on earn_rate.
        
        PLACEHOLDER THRESHOLDS - Refine based on discussion.
        
        Returns a string tier (e.g., 'Common', 'Rare') for display/filtering.
        """
        rate = self.earn_rate
        # EXAMPLE THRESHOLDS - ADJUST LATER
        if rate > 0.5:
            return 'Common'
        elif rate > 0.2:
            return 'Uncommon'
        elif rate > 0.05:
            return 'Rare'
        elif rate > 0.01:
            return 'Very Rare'
        else:
            return 'Ultra Rare'

    def get_most_recent_earner(self):
        recent_entry = self.earned_trophy_entries.filter(earned=True).order_by(F('earned_date_time').desc(nulls_last=True)).first()
        if recent_entry:
            return recent_entry.profile.psn_username
        return None
    
    def get_most_recent_earned_date(self):
        recent_entry = self.earned_trophy_entries.filter(earned=True).order_by(F('earned_date_time').desc(nulls_last=True)).first()
        if recent_entry:
            return recent_entry.earned_date_time
        return None

    def __str__(self):
        return f"{self.trophy_name} ({self.game.title_name})"

class TrophyGroup(models.Model):
    game = models.ForeignKey(Game, on_delete=models.CASCADE, related_name='trophy_groups')
    trophy_group_id = models.CharField(max_length=10)
    trophy_group_name = models.CharField(max_length=255, blank=True)
    trophy_group_detail = models.TextField(blank=True, null=True)
    trophy_group_icon_url = models.URLField(blank=True, null=True)
    defined_trophies = models.JSONField(default=dict, blank=True)

    class Meta:
        unique_together = ['game', 'trophy_group_id']
        indexes = [
            models.Index(fields=['trophy_group_id'], name='trophy_group_id_idx'),
        ]
        ordering = ['trophy_group_id']
    
    def __str__(self):
        return f"{self.trophy_group_name or self.trophy_group_id} ({self.game.title_name})"

class EarnedTrophy(models.Model):
    profile = models.ForeignKey(
        Profile, on_delete=models.CASCADE, related_name="earned_trophy_entries"
    )
    trophy = models.ForeignKey(
        Trophy, on_delete=models.CASCADE, related_name="earned_trophy_entries"
    )
    earned = models.BooleanField(default=False)
    trophy_hidden = models.BooleanField(default=False)
    progress = models.IntegerField(default=0, blank=True, null=True)
    progress_rate = models.FloatField(default=0, blank=True, null=True)
    progressed_date_time = models.DateTimeField(blank=True, null=True)
    earned_date_time = models.DateTimeField(null=True, blank=True)
    last_updated = models.DateTimeField(auto_now=True)
    user_hidden = models.BooleanField(default=False, help_text="True if user has game hidden/deleted.")

    class Meta:
        unique_together = ["profile", "trophy"]
        indexes = [
            models.Index(fields=["last_updated"], name="earned_trophy_updated_idx"),
            models.Index(fields=['earned_date_time'], name="earned_trophy_earned_time_idx"),
            # Composite index for trophy type count queries (profile + earned status)
            models.Index(fields=['profile', 'earned'], name="earnedtrophy_profileearned_idx"),
            # Composite index for timeline queries (profile + earned + date for Window functions)
            models.Index(
                fields=['profile', 'earned', 'earned_date_time'],
                condition=Q(earned=True),
                name='earnedtrophy_timeline_idx'
            ),
        ]

class UserTrophySelection(models.Model):
    profile = models.ForeignKey(Profile, on_delete=models.CASCADE, related_name='trophy_selections')
    earned_trophy = models.ForeignKey(EarnedTrophy, on_delete=models.CASCADE, related_name='selections')

    class Meta:
        unique_together = ['profile', 'earned_trophy']
    
    def save(self, *args, **kwargs):
        if not self.pk:
            with transaction.atomic():
                # Lock the profile's selections to prevent concurrent inserts exceeding the limit
                count = UserTrophySelection.objects.select_for_update().filter(
                    profile=self.profile
                ).count()
                if count >= 10:
                    raise ValueError("Maximum 10 selections allowed.")
                super().save(*args, **kwargs)
        else:
            super().save(*args, **kwargs)

class Badge(models.Model):
    TIER_CHOICES = [
        (1, 'Bronze'),
        (2, 'Silver'),
        (3, 'Gold'),
        (4, 'Platinum'),
    ]
    BADGE_TYPES = [
        ('series', 'Series'),
        ('collection', 'Collection'),
        ('megamix', 'Megamix'),
        ('misc', 'Miscellaneous'),
    ]

    name = models.CharField(max_length=255)
    series_slug = models.SlugField(max_length=100, blank=True, null=True, help_text='Groups tiers of the same series')
    description = models.TextField(blank=True)
    badge_image = models.ImageField(upload_to='badges/main/', blank=True, null=True, help_text='Main badge layer - defaults to static if blank.')
    base_badge = models.ForeignKey('self', on_delete=models.SET_NULL, null=True, blank=True, related_name='derived_badges', help_text='Reference a base (Tier 1) badge to inherit its icon')
    display_title = models.CharField(max_length=100, blank=True)
    display_series = models.CharField(max_length=100, blank=True)
    title = models.ForeignKey('Title', on_delete=models.SET_NULL, null=True, blank=True, related_name='badge_title', help_text='Title awarded to user upon earning.')
    discord_role_id = models.BigIntegerField(null=True, blank=True, help_text="Discord role ID to auto assign upon earning the badge (optional).")
    tier = models.IntegerField(choices=TIER_CHOICES, default=1)
    badge_type = models.CharField(max_length=10, choices=BADGE_TYPES, default='series')
    requires_all = models.BooleanField(default=True, help_text="If True, user must complete all qualifying Concepts. If false, only the min_required.")
    min_required = models.PositiveIntegerField(default=0, help_text="For large series (e.g. 10 out of 30)")
    requirements = models.JSONField(default=dict, blank=True, help_text="For misc badges")
    most_recent_concept = models.ForeignKey(Concept, on_delete=models.SET_NULL, null=True, blank=True, related_name='most_recent_for_badges', help_text='Concept with the latest release_date')
    funded_by = models.ForeignKey('Profile', on_delete=models.SET_NULL, null=True, blank=True, related_name='funded_badges', help_text='Profile of the donor who funded this badge artwork.')
    created_at = models.DateTimeField(auto_now_add=True)
    earned_count = models.PositiveIntegerField(default=0, help_text="Count of users who have earned this badge tier")
    view_count = models.PositiveIntegerField(default=0, help_text="Denormalized total page view count (only tracked on tier=1 badge rows).")
    required_stages = models.PositiveIntegerField(default=0, help_text="Denormalized count of required stages for series badges")
    required_value = models.PositiveIntegerField(default=0, help_text="Denormalized required value for misc badges")
    is_live = models.BooleanField(default=False, help_text="Whether this badge is visible to regular users. New badges start hidden until explicitly released.")

    objects = BadgeManager()

    class Meta:
        ordering = ['tier', 'name']
        indexes = [
            models.Index(fields=['series_slug', 'tier'], name='badge_series_tier_idx'),
            models.Index(fields=['badge_type'], name='badge_type_idx'),
            models.Index(fields=['earned_count'], name='badge_earned_count_idx'),
            models.Index(fields=['most_recent_concept'], name='badge_recent_concept_idx'),
            models.Index(fields=['tier'], name='badge_tier_idx'),
            models.Index(fields=['is_live'], name='badge_is_live_idx'),
        ]

    @property
    def effective_display_series(self):
        if self.display_series:
            return self.display_series
        elif self.base_badge and self.base_badge.display_series:
            return self.base_badge.display_series
        return None
    
    @property
    def effective_display_title(self):
        if self.display_title:
            return self.display_title
        elif self.base_badge and self.base_badge.display_title:
            return self.base_badge.display_title
        return None
    
    @property
    def effective_user_title(self):
        if self.title:
            return self.title.name
        elif self.base_badge and self.base_badge.title:
            return self.base_badge.title.name
        return None

    @property
    def effective_description(self):
        if self.description:
            return self.description
        elif self.base_badge and self.base_badge.description:
            return self.base_badge.description
        return None
    
    def get_badge_layers(self):
        """Return dict of layer URLs for backdrop, main and foreground."""

        has_custom = bool(self.badge_image or (self.base_badge and self.base_badge.badge_image))
        main_url = self.badge_image.url if self.badge_image else self.base_badge.badge_image.url if self.base_badge and self.base_badge.badge_image else 'images/badges/default.png'
        backdrop_url = f"images/badges/backdrops/{self.tier}_backdrop.png"
        if has_custom:
            foreground_url = f"images/badges/foregrounds/{self.tier}_foreground.png"
            return {
                'backdrop': backdrop_url,
                'main': main_url,
                'foreground': foreground_url,
                'has_custom_image': True,
            }
        return {
            'backdrop': backdrop_url,
            'main': main_url,
            'has_custom_image': False,
        }

    def update_most_recent_concept(self):
        concepts = Concept.objects.filter(stages__series_slug=self.series_slug).distinct()
        if not concepts.exists():
            self.most_recent_concept = None
        else:
            max_date = concepts.aggregate(Max('release_date'))['release_date__max']
            self.most_recent_concept = concepts.filter(release_date=max_date).first() if max_date else None
        self.save(update_fields=['most_recent_concept'])

    def update_required(self):
        from trophies.models import Stage
        if self.badge_type in ['series', 'collection', 'megamix']:
            stages = Stage.objects.filter(series_slug=self.series_slug)
            required_count = 0
            for stage in stages:
                if stage.stage_number == 0:
                    continue
                if stage.applies_to_tier(self.tier):
                    required_count += 1

            # For megamix badges with requires_all=False, use min_required
            # Otherwise use the total count of non-zero stages
            if self.badge_type == 'megamix' and not self.requires_all:
                self.required_stages = self.min_required
            else:
                self.required_stages = required_count

            self.save(update_fields=['required_stages'])


    def get_stage_completion(self, profile: Profile, badge_type: str) -> dict[int, bool]:
        if not profile:
            return {}

        from django.db.models import Q

        stages = Stage.objects.filter(
            Q(series_slug=self.series_slug)
            & (Q(required_tiers__len=0) | Q(required_tiers__contains=[self.tier]))
        ).prefetch_related('concepts__games')

        is_plat_check = False
        is_progress_check = False

        if badge_type in ['series', 'collection']:
            is_plat_check = self.tier in [1, 3]
            is_progress_check = self.tier in [2, 4]
        elif badge_type == 'megamix':
            is_plat_check = True
        else:
            return {}

        if is_plat_check:
            condition = Q(has_plat=True)
        elif is_progress_check:
            condition = Q(progress=100)
        else:
            return {}

        # Build a mapping of stage_number -> set of game_ids for that stage
        stage_games = {}  # {stage_number: set of game_ids}
        all_game_ids = set()
        for stage in stages:
            game_ids = set()
            for concept in stage.concepts.all():
                for game in concept.games.all():
                    game_ids.add(game.id)
            stage_games[stage.stage_number] = game_ids
            all_game_ids.update(game_ids)

        if not all_game_ids:
            return {sn: False for sn in stage_games}

        # Single query: fetch all completed game IDs for this profile
        completed_game_ids = set(
            ProfileGame.objects.filter(
                profile=profile, game_id__in=all_game_ids
            ).filter(condition).values_list('game_id', flat=True)
        )

        # Check per-stage completion in memory
        completion = {}
        for stage_number, game_ids in stage_games.items():
            if not game_ids:
                continue
            completion[stage_number] = bool(completed_game_ids & game_ids)
        return completion

    def __str__(self):
        return f"{self.name} (Tier {self.tier})"
    
class UserBadge(models.Model):
    profile = models.ForeignKey(Profile, on_delete=models.CASCADE, related_name='badges')
    badge = models.ForeignKey(Badge, on_delete=models.CASCADE, related_name='earned_by')
    earned_at = models.DateTimeField(auto_now_add=True)
    is_displayed = models.BooleanField(default=False, help_text="User's selected display badge.")

    class Meta:
        unique_together = ['profile', 'badge']
        indexes = [
            models.Index(fields=['profile', 'is_displayed'], name='userbadge_display_idx'),
            models.Index(fields=['earned_at'], name='userbadge_earned_at_idx'),
        ]

    def __str__(self):
        return f"{self.profile.psn_username} - {self.badge.name}"

class UserBadgeProgress(models.Model):
    profile = models.ForeignKey(Profile, on_delete=models.CASCADE, related_name='badge_progress')
    badge = models.ForeignKey(Badge, on_delete=models.CASCADE, related_name='progress_for')
    completed_concepts = models.PositiveIntegerField(default=0)
    progress_value = models.PositiveIntegerField(default=0)
    last_checked = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ['profile', 'badge']
        indexes = [
            models.Index(fields=['profile', 'badge'], name='userbadgeprogress_idx'),
        ]


class ProfileGamification(models.Model):
    """
    Denormalized gamification stats for a profile.

    Stores pre-computed totals and per-series breakdowns for badge XP.
    Updated in real-time via signals when UserBadgeProgress or UserBadge changes.
    Future stat types (power, luck, agility) will be added via migration when needed.
    """
    profile = models.OneToOneField(
        Profile,
        on_delete=models.CASCADE,
        related_name='gamification',
        primary_key=True
    )

    # Badge XP (denormalized)
    total_badge_xp = models.PositiveIntegerField(default=0, db_index=True)
    series_badge_xp = models.JSONField(default=dict, blank=True, help_text='Per-series XP breakdown: {"resident-evil": 1500, ...}')
    total_badges_earned = models.PositiveIntegerField(default=0)

    # Tracking
    last_updated = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Profile Gamification Stats'
        verbose_name_plural = 'Profile Gamification Stats'
        indexes = [
            models.Index(fields=['total_badge_xp'], name='gamification_badge_xp_idx'),
            models.Index(fields=['total_badges_earned'], name='gamification_badges_idx'),
        ]

    def __str__(self):
        return f"Gamification for {self.profile.psn_username}"


class StatType(models.Model):
    """
    Defines stat types for the gamification system.

    Initially includes 'badge_xp' but designed for future expansion
    to Power, Luck, Agility, etc.
    """
    slug = models.SlugField(max_length=50, unique=True, primary_key=True)
    name = models.CharField(max_length=100)
    description = models.TextField(blank=True)
    icon = models.CharField(max_length=50, blank=True, default='')
    color = models.CharField(max_length=20, default='#FFD700', help_text='Hex color for UI theming')
    is_active = models.BooleanField(default=True)
    display_order = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['display_order', 'name']
        verbose_name = 'Stat Type'
        verbose_name_plural = 'Stat Types'

    def __str__(self):
        return self.name


class StageStatValue(models.Model):
    """
    Defines stat values for specific stages.

    Allows different stages to grant different amounts of each stat type.
    For badge_xp, the current logic uses tier-based multipliers,
    but this model enables per-stage customization for future stats.
    """
    stage = models.ForeignKey(
        'Stage',
        on_delete=models.CASCADE,
        related_name='stat_values'
    )
    stat_type = models.ForeignKey(
        StatType,
        on_delete=models.CASCADE,
        related_name='stage_values'
    )

    # Values per tier when completing this stage
    bronze_value = models.PositiveIntegerField(default=0)
    silver_value = models.PositiveIntegerField(default=0)
    gold_value = models.PositiveIntegerField(default=0)
    platinum_value = models.PositiveIntegerField(default=0)

    class Meta:
        unique_together = ['stage', 'stat_type']
        verbose_name = 'Stage Stat Value'
        verbose_name_plural = 'Stage Stat Values'
        indexes = [
            models.Index(fields=['stage', 'stat_type'], name='stage_stat_idx'),
        ]

    def __str__(self):
        return f"{self.stage} - {self.stat_type.name}"

    def get_value_for_tier(self, tier: int) -> int:
        """Return the stat value for a specific tier."""
        tier_map = {
            1: self.bronze_value,
            2: self.silver_value,
            3: self.gold_value,
            4: self.platinum_value,
        }
        return tier_map.get(tier, 0)


class Stage(models.Model):
    series_slug = models.SlugField(max_length=100, db_index=True, help_text="Series slug this stage applies to.")
    stage_number = models.IntegerField(validators=[MinValueValidator(0)], help_text="Stage number (0 for optional/tangential concepts.)")
    title = models.CharField(max_length=255, blank=True, help_text="Optional stage title")
    stage_icon = models.URLField(null=True, blank=True)
    concepts = models.ManyToManyField(Concept, related_name='stages', blank=True, help_text='Concepts required for this stage.')
    required_tiers = ArrayField(models.IntegerField(choices=[(1, 'Bronze'), (2, 'Silver'), (3, 'Gold'), (4, 'Platinum')]), blank=True, default=list)
    has_online_trophies = models.BooleanField(default=False)

    class Meta:
        unique_together = ['series_slug', 'stage_number']
        ordering = ['stage_number']
        indexes = [
            models.Index(fields=['series_slug', 'stage_number'], name='stage_slug_number_idx'),
        ]

    def __str__(self):
        return f"{self.series_slug} - Stage {self.stage_number}"
        
    def applies_to_tier(self, tier: int) -> bool:
        return not self.required_tiers or tier in self.required_tiers

class TitleManager(models.Manager):
    """Fetch titles earned by a user, ordered alphabetically. Usage: Title.objects.earned_by_user(profile)"""
    def earned_by_user(self, profile):
        return self.filter(user_titles__profile=profile).order_by('user_titles__title__name')

class Title(models.Model):
    name = models.CharField(max_length=100, unique=True, help_text="The title text.")
    created_at = models.DateTimeField(auto_now_add=True)

    objects = TitleManager()

    class Meta:
        ordering = ['name']
        indexes = [
            models.Index(fields=['name'], name='title_name_idx'),
        ]
        verbose_name = 'Title'
        verbose_name_plural = 'Titles'
    
    def __str__(self):
        return self.name
    
class UserTitle(models.Model):
    SOURCE_CHOICES = [
        ('badge', 'Badge'),
        ('milestone', 'Milestone')
    ]

    profile = models.ForeignKey(Profile, on_delete=models.CASCADE, related_name='user_titles')
    title = models.ForeignKey(Title, on_delete=models.CASCADE, related_name='user_titles')
    source_type = models.CharField(max_length=20, choices=SOURCE_CHOICES, help_text="Source of the title")
    source_id = models.PositiveIntegerField(null=True, blank=True, help_text="ID of the granting object")
    earned_at = models.DateTimeField(auto_now_add=True)
    is_displayed = models.BooleanField(default=False, help_text="Whether this is the user's selected display title")

    class Meta:
        unique_together = ['profile', 'title']
        indexes = [
            models.Index(fields=['profile', 'is_displayed'], name='usertitle_display_idx'),
            models.Index(fields=['earned_at'], name='usertitle_earned_at_idx'),
            models.Index(fields=['source_type', 'source_id'], name='usertitle_source_idx'),
        ]
        verbose_name = 'User Title'
        verbose_name_plural = 'User Titles'

    def __str__(self):
        return f"{self.profile.psn_username} - {self.title.name}"

class Milestone(models.Model):
    CRITERIA_TYPES = [
        ('manual', 'Manual Award'),
        ('plat_count', 'Earned Plats'),
        ('psn_linked', 'PSN Profile Linked'),
        ('discord_linked', 'Discord Connected'),
        ('rating_count', 'Games Rated'),
        ('playtime_hours', 'Total Playtime (Hours)'),
        ('trophy_count', 'Total Trophies Earned'),
        ('comment_upvotes', 'Comment Upvotes Received'),
        ('checklist_upvotes', 'Checklist Upvotes Received'),
        ('badge_count', 'Badges Earned'),
        ('completion_count', 'Games 100% Completed'),
        ('stage_count', 'Badge Stages Completed'),
        ('az_progress', 'A-Z Challenge Letters'),
        ('genre_progress', 'Genre Challenge Genres'),
        ('subgenre_progress', 'Subgenre Collection'),
        ('calendar_month_jan', 'Calendar: January Complete'),
        ('calendar_month_feb', 'Calendar: February Complete'),
        ('calendar_month_mar', 'Calendar: March Complete'),
        ('calendar_month_apr', 'Calendar: April Complete'),
        ('calendar_month_may', 'Calendar: May Complete'),
        ('calendar_month_jun', 'Calendar: June Complete'),
        ('calendar_month_jul', 'Calendar: July Complete'),
        ('calendar_month_aug', 'Calendar: August Complete'),
        ('calendar_month_sep', 'Calendar: September Complete'),
        ('calendar_month_oct', 'Calendar: October Complete'),
        ('calendar_month_nov', 'Calendar: November Complete'),
        ('calendar_month_dec', 'Calendar: December Complete'),
        ('calendar_months_total', 'Calendar Months Completed'),
        ('calendar_complete', 'Calendar Challenge Complete'),
        ('is_premium', 'Premium Subscriber'),
        ('subscription_months', 'Subscription Months'),
    ]

    name = models.CharField(max_length=255, unique=True, help_text="Unique name")
    description = models.TextField(blank=True, help_text="Description for display")
    image = models.ImageField(upload_to='milestones/', blank=True, null=True, help_text='Visual icon')
    title = models.ForeignKey(Title, on_delete=models.SET_NULL, null=True, blank=True, related_name='milestones')
    discord_role_id = models.BigIntegerField(null=True, blank=True, help_text="Discord role ID to assign upon earning")
    criteria_type = models.CharField(max_length=30, choices=CRITERIA_TYPES, default='manual')
    criteria_details = models.JSONField(default=dict, blank=True, help_text="Flexible details")
    premium_only = models.BooleanField(default=False, help_text="If True, can only be earned by current premium users")
    required_value = models.PositiveIntegerField(default=0, help_text="Target for milestone")
    earned_count = models.PositiveIntegerField(default=0, help_text="Counter for user earns")
    created_at = models.DateTimeField(auto_now_add=True)

    objects = MilestoneManager()

    class Meta:
        ordering = ['name']
        indexes = [
            models.Index(fields=['criteria_type'], name='milestone_type_idx'),
            models.Index(fields=['earned_count'],  name='milestone_earned_count_idx'),
        ]
        verbose_name = 'Milestone'
        verbose_name_plural = 'Milestones'

    def save(self, *args, **kwargs):
        # Intentionally set on every save (not just creation) so required_value
        # always stays in sync with criteria_details['target']. Admin edits to
        # required_value alone will be overwritten: update criteria_details instead.
        self.required_value = self.criteria_details.get('target', 0)
        super().save(*args, **kwargs)

    def __str__(self):
        return self.name
    
class UserMilestone(models.Model):
    profile = models.ForeignKey(Profile, on_delete=models.CASCADE, related_name='user_milestones')
    milestone = models.ForeignKey(Milestone, on_delete=models.CASCADE, related_name='user_milestones')
    earned_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ['profile', 'milestone']
        indexes = [
            models.Index(fields=['earned_at'], name='usermilestone_earned_at_idx'),
        ]
        verbose_name = 'User Milestone'
        verbose_name_plural = 'User Milestones'

    def __str__(self):
        return f"{self.profile.psn_username} - {self.milestone.name}"
    
class UserMilestoneProgress(models.Model):
    profile = models.ForeignKey(Profile, on_delete=models.CASCADE, related_name='user_milestone_progress')
    milestone = models.ForeignKey(Milestone, on_delete=models.CASCADE, related_name='milestone_progress')
    progress_value = models.PositiveIntegerField(default=0, help_text="Current progress")
    last_checked = models.DateTimeField(auto_now=True, help_text="Timestamp of last progress update")

    class Meta:
        unique_together = ['profile', 'milestone']
        indexes = [
            models.Index(fields=['last_checked'], name='usermilestoneprog_checked_idx'),
        ]
        verbose_name = 'User Milestone Progress'
        verbose_name_plural = 'User Milestone Progress'
    
    def __str__(self):
        return f"{self.profile.psn_username} - {self.milestone.name} Progress"

class PublisherBlacklist(models.Model):
    name = models.CharField(max_length=255, unique=True)
    date_added = models.DateTimeField(auto_now_add=True)
    flagged_concepts = models.JSONField(
        default=list, blank=True,
        help_text="List of concept IDs whose games triggered this entry."
    )
    is_blacklisted = models.BooleanField(
        default=False,
        help_text="True when any concept is flagged. All publisher games get flagged."
    )
    notes = models.TextField(blank=True)

    class Meta:
        indexes = [
            models.Index(fields=['name'], name='blacklist_name_idx'),
        ]

    @property
    def flagged_concept_count(self):
        return len(self.flagged_concepts)

    def add_concept(self, concept_id):
        """Add a concept ID and immediately blacklist the publisher."""
        if concept_id not in self.flagged_concepts:
            self.flagged_concepts.append(concept_id)
            self.is_blacklisted = True
            self.save(update_fields=['flagged_concepts', 'is_blacklisted'])
            return True
        return False

    def remove_concept(self, concept_id):
        """Remove a concept ID. Un-blacklist only when no concepts remain."""
        if concept_id in self.flagged_concepts:
            self.flagged_concepts.remove(concept_id)
            self.is_blacklisted = bool(self.flagged_concepts)
            self.save(update_fields=['flagged_concepts', 'is_blacklisted'])
            return True
        return False

    def __str__(self):
        status = "BLACKLISTED" if self.is_blacklisted else f"{self.flagged_concept_count} concepts"
        return f"{self.name} ({status})"


class Comment(models.Model):
    """
    User-generated comment on Concepts or Trophies within Concepts.

    Comments are unified across game stacks:
    - Concept-level comments (trophy_id=null): Discussion about the game in general
    - Trophy-level comments (trophy_id=X): Discussion about a specific trophy across all stacks

    Supports threaded replies via parent_id self-referential FK.
    """
    # Concept-based relation instead of GenericFK
    concept = models.ForeignKey(
        'Concept',
        on_delete=models.CASCADE,
        related_name='comments',
        help_text="The game concept this comment belongs to",
    )
    trophy_id = models.IntegerField(
        null=True,
        blank=True,
        help_text="Trophy position within concept (null = concept-level comment)"
    )
    checklist_id = models.IntegerField(
        null=True,
        blank=True,
        help_text="Checklist ID within concept (null = concept-level comment)"
    )

    # Author info
    profile = models.ForeignKey(
        Profile,
        on_delete=models.CASCADE,
        related_name='comments'
    )

    # Threading - self-referential for nested replies
    parent = models.ForeignKey(
        'self',
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='replies'
    )

    # Denormalized depth for query efficiency (0 = top-level, 1 = first reply, etc.)
    depth = models.PositiveIntegerField(default=0)

    # Content
    body = models.TextField(
        max_length=2000,
        help_text="Comment text, max 2000 characters"
    )

    # Vote count (denormalized for sorting efficiency)
    upvote_count = models.PositiveIntegerField(default=0)

    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    # Edit tracking
    is_edited = models.BooleanField(default=False)

    # Soft delete
    is_deleted = models.BooleanField(default=False)
    deleted_at = models.DateTimeField(null=True, blank=True)

    objects = CommentManager()

    class Meta:
        indexes = [
            # Primary query: get comments for a concept sorted by upvotes
            models.Index(fields=['concept', 'trophy_id', '-upvote_count'], name='comment_concept_votes_idx'),
            # Get replies to a comment
            models.Index(fields=['parent', '-upvote_count'], name='comment_replies_idx'),
            # Get all comments by a profile
            models.Index(fields=['profile', '-created_at'], name='comment_profile_idx'),
            # Composite for threaded display
            models.Index(fields=['concept', 'trophy_id', 'depth', '-upvote_count'], name='comment_threaded_idx'),
            # For moderation queue
            models.Index(fields=['is_deleted', 'created_at'], name='comment_moderation_idx'),
            # Checklist comment indexes
            models.Index(fields=['concept', 'checklist_id', '-upvote_count'], name='comment_checklist_votes_idx'),
            models.Index(fields=['concept', 'checklist_id', 'depth', '-upvote_count'], name='comment_checklist_threaded_idx'),
        ]
        ordering = ['-upvote_count', '-created_at']

    def __str__(self):
        if self.checklist_id is not None:
            return f"Comment by {self.profile.psn_username} on {self.concept} (Checklist {self.checklist_id})"
        if self.trophy_id is not None:
            return f"Comment by {self.profile.psn_username} on {self.concept} (Trophy {self.trophy_id})"
        return f"Comment by {self.profile.psn_username} on {self.concept}"

    def save(self, *args, **kwargs):
        # Calculate depth based on parent
        if self.parent:
            self.depth = self.parent.depth + 1
        super().save(*args, **kwargs)

    def clean(self):
        """Validate that only one of trophy_id or checklist_id is set."""
        super().clean()
        if self.trophy_id is not None and self.checklist_id is not None:
            from django.core.exceptions import ValidationError
            raise ValidationError("A comment cannot belong to both a trophy and a checklist.")

    def soft_delete(self, moderator=None, reason="", request=None):
        """
        Soft delete preserving thread structure and logging to ModerationLog.

        Args:
            moderator: CustomUser performing deletion (None = user self-delete)
            reason: Reason for deletion (for audit trail)
            request: HttpRequest object to capture IP address
        """
        # Preserve original body before deletion
        original_body = self.body

        # Mark as deleted
        self.is_deleted = True
        self.deleted_at = timezone.now()
        self.body = '[deleted]'
        self.save(update_fields=['is_deleted', 'deleted_at', 'body'])

        # Log to ModerationLog if moderator action (not user self-delete)
        if moderator:
            ip_address = None
            if request:
                # Get IP from request
                x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
                if x_forwarded_for:
                    ip_address = x_forwarded_for.split(',')[0]
                else:
                    ip_address = request.META.get('REMOTE_ADDR')

            ModerationLog.objects.create(
                moderator=moderator,
                action_type='delete',
                comment=self,
                comment_id_snapshot=self.id,
                comment_author=self.profile,
                original_body=original_body,
                concept=self.concept,
                trophy_id=self.trophy_id,
                reason=reason,
                ip_address=ip_address
            )

    @property
    def display_body(self):
        """Returns '[deleted]' if soft-deleted, else actual body."""
        return '[deleted]' if self.is_deleted else self.body


class CommentVote(models.Model):
    """
    Upvote on a comment. One vote per profile per comment.
    """
    comment = models.ForeignKey(
        Comment,
        on_delete=models.CASCADE,
        related_name='votes'
    )
    profile = models.ForeignKey(
        Profile,
        on_delete=models.CASCADE,
        related_name='comment_votes'
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ['comment', 'profile']
        indexes = [
            models.Index(fields=['comment', 'profile'], name='commentvote_unique_idx'),
            models.Index(fields=['profile'], name='commentvote_profile_idx'),
        ]

    def __str__(self):
        return f"{self.profile.psn_username} upvoted comment {self.comment.id}"


class CommentReport(models.Model):
    """
    User report for moderation review.
    """
    REPORT_REASONS = [
        ('spam', 'Spam'),
        ('harassment', 'Harassment'),
        ('inappropriate', 'Inappropriate Content'),
        ('misinformation', 'Misinformation'),
        ('other', 'Other'),
    ]
    REPORT_STATUS = [
        ('pending', 'Pending Review'),
        ('reviewed', 'Reviewed'),
        ('dismissed', 'Dismissed'),
        ('action_taken', 'Action Taken'),
    ]

    comment = models.ForeignKey(
        Comment,
        on_delete=models.CASCADE,
        related_name='reports'
    )
    reporter = models.ForeignKey(
        Profile,
        on_delete=models.CASCADE,
        related_name='submitted_reports'
    )
    reason = models.CharField(max_length=20, choices=REPORT_REASONS)
    details = models.TextField(
        max_length=500,
        blank=True,
        help_text="Additional context for the report"
    )
    status = models.CharField(
        max_length=20,
        choices=REPORT_STATUS,
        default='pending'
    )
    created_at = models.DateTimeField(auto_now_add=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)
    reviewed_by = models.ForeignKey(
        CustomUser,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='reviewed_reports'
    )
    admin_notes = models.TextField(blank=True)

    class Meta:
        unique_together = ['comment', 'reporter']
        indexes = [
            models.Index(fields=['status', '-created_at'], name='report_status_idx'),
            models.Index(fields=['comment'], name='report_comment_idx'),
        ]

    def __str__(self):
        return f"Report on comment {self.comment.id} by {self.reporter.psn_username}"

class APIAuditLog(models.Model):
    timestamp = models.DateTimeField(auto_now_add=True)
    token_id = models.CharField(max_length=64)
    ip_used = models.CharField(max_length=45, blank=True)
    endpoint = models.CharField(max_length=100)
    profile = models.ForeignKey(
        Profile, on_delete=models.SET_NULL, null=True, blank=True
    )
    status_code = models.IntegerField()
    response_time = models.FloatField()
    error_message = models.TextField(blank=True)
    calls_remaining = models.IntegerField(default=0)

    class Meta:
        indexes = [models.Index(fields=["timestamp", "status_code"])]

    def __str__(self):
        return f"{self.endpoint} at {self.timestamp}"


class ModerationLog(models.Model):
    """
    Audit trail for all comment moderation actions.

    Preserves full context including original comment text for accountability
    and appeal reviews.
    """
    ACTION_TYPES = [
        ('delete', 'Comment Deleted'),
        ('restore', 'Comment Restored'),
        ('dismiss_report', 'Report Dismissed'),
        ('approve_comment', 'Comment Approved'),
        ('warning_issued', 'Warning Issued'),
        ('bulk_delete', 'Bulk Delete'),
        ('report_reviewed', 'Report Reviewed'),
    ]

    # Core fields
    timestamp = models.DateTimeField(auto_now_add=True, db_index=True)
    moderator = models.ForeignKey(
        CustomUser,
        on_delete=models.PROTECT,  # Never delete moderator history
        related_name='moderation_actions'
    )
    action_type = models.CharField(max_length=20, choices=ACTION_TYPES, db_index=True)

    # Target comment (preserved even if comment deleted)
    comment = models.ForeignKey(
        Comment,
        on_delete=models.SET_NULL,
        null=True,
        related_name='moderation_logs'
    )
    comment_id_snapshot = models.IntegerField()  # Preserve ID if comment deleted
    comment_author = models.ForeignKey(
        Profile,
        on_delete=models.SET_NULL,
        null=True,
        related_name='moderated_comments'
    )

    # Context preservation (CRITICAL for appeals/review)
    original_body = models.TextField(
        help_text="Original comment text preserved for context"
    )
    concept = models.ForeignKey(
        'Concept',
        on_delete=models.SET_NULL,
        null=True
    )
    trophy_id = models.IntegerField(
        null=True,
        blank=True,
        help_text="Trophy ID if comment was on trophy (null = concept-level)"
    )

    # Moderation context
    related_report = models.ForeignKey(
        CommentReport,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='action_logs'
    )
    reason = models.TextField(
        help_text="Moderator's reason for action"
    )
    internal_notes = models.TextField(
        blank=True,
        help_text="Private staff notes (not shown to user)"
    )

    # IP tracking (for pattern detection)
    ip_address = models.GenericIPAddressField(
        null=True,
        blank=True,
        help_text="IP address of comment author at time of action"
    )

    class Meta:
        ordering = ['-timestamp']
        indexes = [
            models.Index(fields=['-timestamp', 'moderator']),
            models.Index(fields=['action_type', '-timestamp']),
            models.Index(fields=['comment_author', '-timestamp']),
        ]

    def __str__(self):
        return f"{self.get_action_type_display()} by {self.moderator.username} at {self.timestamp}"

    @property
    def comment_preview(self):
        """Return truncated original body for display."""
        return self.original_body[:100] + '...' if len(self.original_body) > 100 else self.original_body


class BannedWord(models.Model):
    """
    Banned words that are automatically blocked in comments.

    Staff can manage this list through the admin interface to filter
    inappropriate content without code changes.
    """
    word = models.CharField(
        max_length=100,
        unique=True,
        help_text="Word or phrase to ban (case-insensitive)"
    )
    is_active = models.BooleanField(
        default=True,
        help_text="Whether this filter is currently active"
    )
    use_word_boundaries = models.BooleanField(
        default=True,
        help_text="Match whole words only (recommended to avoid false positives)"
    )
    added_by = models.ForeignKey(
        CustomUser,
        on_delete=models.SET_NULL,
        null=True,
        related_name='banned_words_added'
    )
    added_at = models.DateTimeField(auto_now_add=True)
    notes = models.TextField(
        blank=True,
        help_text="Internal notes about why this word was banned"
    )

    class Meta:
        ordering = ['word']
        indexes = [
            models.Index(fields=['is_active', 'word']),
        ]

    def __str__(self):
        status = "Active" if self.is_active else "Inactive"
        return f"{self.word} ({status})"


class Checklist(models.Model):
    """
    User-created checklist for a game Concept.

    Checklists are tied to Concepts and apply to all Games within that concept.
    Supports draft/published states and soft deletion.
    """
    STATUS_CHOICES = [
        ('draft', 'Draft'),
        ('published', 'Published'),
    ]

    # Concept-based relation (like Comment)
    concept = models.ForeignKey(
        'Concept',
        on_delete=models.CASCADE,
        related_name='checklists',
        help_text="The game concept this checklist belongs to"
    )

    # Selected game for trophy items (optional)
    selected_game = models.ForeignKey(
        'Game',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='checklists_using_game',
        help_text="Selected game for trophy items (from this concept's games)"
    )

    # Author info
    profile = models.ForeignKey(
        Profile,
        on_delete=models.CASCADE,
        related_name='checklists'
    )

    # Content
    title = models.CharField(max_length=200, help_text="Checklist title")
    description = models.TextField(
        max_length=2000,
        blank=True,
        help_text="Checklist description/overview"
    )
    thumbnail = models.ImageField(
        upload_to='checklists/thumbnails/',
        blank=True,
        null=True,
        help_text='Main thumbnail image (recommended: 800x800px, max 5MB)'
    )

    # Status
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default='draft',
        db_index=True
    )

    # Vote count (denormalized for sorting efficiency, like Comment)
    upvote_count = models.PositiveIntegerField(default=0)

    # Usage tracking
    progress_save_count = models.PositiveIntegerField(
        default=0,
        help_text="Number of users who have saved progress on this checklist"
    )
    view_count = models.PositiveIntegerField(default=0, help_text="Denormalized total page view count.")

    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    published_at = models.DateTimeField(null=True, blank=True)

    # Soft delete
    is_deleted = models.BooleanField(default=False)
    deleted_at = models.DateTimeField(null=True, blank=True)

    objects = ChecklistManager()

    class Meta:
        indexes = [
            # Primary query: get checklists for a concept sorted by upvotes
            models.Index(fields=['concept', 'status', '-upvote_count'], name='checklist_concept_votes_idx'),
            # Get all checklists by a profile
            models.Index(fields=['profile', '-created_at'], name='checklist_profile_idx'),
            # Draft lookup for author
            models.Index(fields=['profile', 'status'], name='checklist_author_drafts_idx'),
            # For moderation queue
            models.Index(fields=['is_deleted', 'created_at'], name='checklist_moderation_idx'),
        ]
        ordering = ['-upvote_count', '-created_at']

    def __str__(self):
        return f"{self.title} by {self.profile.psn_username}"

    def soft_delete(self, moderator=None, reason="", request=None):
        """Soft delete preserving data for audit trail."""
        self.is_deleted = True
        self.deleted_at = timezone.now()
        self.save(update_fields=['is_deleted', 'deleted_at'])

    @property
    def total_items(self):
        """Total number of trackable items across all sections (excludes sub-headers)."""
        return ChecklistItem.objects.filter(section__checklist=self, item_type__in=['item', 'trophy']).count()

    @property
    def is_draft(self):
        return self.status == 'draft'

    @property
    def is_published(self):
        return self.status == 'published'


class ChecklistSection(models.Model):
    """
    A section within a checklist containing grouped items.

    Sections have subtitles and contain ordered items.
    No arbitrary limits on number of sections per checklist.
    """
    checklist = models.ForeignKey(
        Checklist,
        on_delete=models.CASCADE,
        related_name='sections'
    )

    subtitle = models.CharField(max_length=200, help_text="Section subtitle/header")
    description = models.TextField(
        max_length=1000,
        blank=True,
        help_text="Optional section description"
    )
    thumbnail = models.ImageField(
        upload_to='checklists/sections/',
        blank=True,
        null=True,
        help_text='Section thumbnail (recommended: 400x400px, max 2MB)'
    )

    # Ordering
    order = models.PositiveIntegerField(default=0, help_text="Display order within checklist")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['order', 'id']
        indexes = [
            models.Index(fields=['checklist', 'order'], name='section_checklist_order_idx'),
        ]

    def __str__(self):
        return f"{self.subtitle} ({self.checklist.title})"

    @property
    def item_count(self):
        """Total count of trackable items (excludes sub-headers)."""
        return self.items.filter(item_type__in=['item', 'trophy']).count()

    @property
    def total_entry_count(self):
        """Total count of all entries (items + sub-headers)."""
        return self.items.count()


class ChecklistItem(models.Model):
    """
    Individual item within a checklist section.

    Items can be checked off by users to track progress.
    Sub-headers are visual separators that cannot be checked off.
    No arbitrary limits on number of items per section.
    """
    ITEM_TYPE_CHOICES = [
        ('item', 'Item'),
        ('sub_header', 'Sub-Header'),
        ('image', 'Image'),
        ('text_area', 'Text Area'),
        ('trophy', 'Trophy'),
    ]

    section = models.ForeignKey(
        ChecklistSection,
        on_delete=models.CASCADE,
        related_name='items'
    )

    text = models.CharField(max_length=2000, help_text="Item description/task")

    item_type = models.CharField(
        max_length=20,
        choices=ITEM_TYPE_CHOICES,
        default='item',
        help_text='Type of checklist entry'
    )

    # Optional: link to specific trophy (for future use)
    trophy_id = models.IntegerField(
        null=True,
        blank=True,
        help_text="Optional trophy_id if this item relates to a specific trophy"
    )

    # Image for image-type items
    image = models.ImageField(
        upload_to='checklists/items/',
        blank=True,
        null=True,
        help_text='Image for item_type=image (premium only, max 2MB)'
    )

    # Ordering
    order = models.PositiveIntegerField(default=0, help_text="Display order within section")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['order', 'id']
        indexes = [
            models.Index(fields=['section', 'order'], name='item_section_order_idx'),
            models.Index(fields=['section', 'item_type'], name='item_section_type_idx'),
        ]

    @property
    def is_sub_header(self):
        """Check if this is a sub-header."""
        return self.item_type == 'sub_header'

    def __str__(self):
        if self.item_type == 'image':
            return f"[Image] ({self.section.subtitle})"
        elif self.item_type == 'text_area':
            return f"[Text Area] ({self.section.subtitle})"
        elif self.item_type == 'trophy':
            return f"[Trophy] {self.text} ({self.section.subtitle})"
        prefix = "[Header] " if self.item_type == 'sub_header' else ""
        truncated = f"{self.text[:50]}..." if len(self.text) > 50 else self.text
        return f"{prefix}{truncated} ({self.section.subtitle})"

    def clean(self):
        """Validate item data."""
        pass

    def save(self, *args, **kwargs):
        """Validate image, text_area, and trophy items before saving."""
        from django.core.exceptions import ValidationError

        # Validate image items
        if self.item_type == 'image':
            if not self.image:
                raise ValidationError("Image items must have an image.")
            # Allow optional caption in text field
        elif self.image:
            # Clear image field if not image type
            self.image = None

        # Validate text_area items
        if self.item_type == 'text_area':
            if not self.text or not self.text.strip():
                raise ValidationError("Text area items must have content.")
            if len(self.text) > 2000:
                raise ValidationError("Text area content too long (max 2000 characters).")
            # Clear image field for text areas
            if self.image:
                self.image = None

        # Validate trophy items
        if self.item_type == 'trophy':
            if not self.trophy_id:
                raise ValidationError("Trophy items must have a trophy_id.")
            # Clear image field for trophies
            if self.image:
                self.image = None

        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        """Delete associated image file when item deleted."""
        if self.image:
            self.image.delete(save=False)
        super().delete(*args, **kwargs)


class ChecklistVote(models.Model):
    """
    Upvote on a checklist. One vote per profile per checklist.
    Following CommentVote pattern.
    """
    checklist = models.ForeignKey(
        Checklist,
        on_delete=models.CASCADE,
        related_name='votes'
    )
    profile = models.ForeignKey(
        Profile,
        on_delete=models.CASCADE,
        related_name='checklist_votes'
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ['checklist', 'profile']
        indexes = [
            models.Index(fields=['checklist', 'profile'], name='checklistvote_unique_idx'),
            models.Index(fields=['profile'], name='checklistvote_profile_idx'),
        ]

    def __str__(self):
        return f"{self.profile.psn_username} upvoted {self.checklist.title}"


class UserChecklistProgress(models.Model):
    """
    Tracks a user's progress on a checklist.

    Premium users can save progress on any checklist.
    Non-premium users can only save progress on their OWN checklists.
    """
    profile = models.ForeignKey(
        Profile,
        on_delete=models.CASCADE,
        related_name='checklist_progress'
    )
    checklist = models.ForeignKey(
        Checklist,
        on_delete=models.CASCADE,
        related_name='user_progress'
    )

    # Track completed items as JSON array of item IDs
    completed_items = models.JSONField(
        default=list,
        help_text="List of completed ChecklistItem IDs"
    )

    # Denormalized progress tracking
    items_completed = models.PositiveIntegerField(default=0)
    total_items = models.PositiveIntegerField(default=0)
    progress_percentage = models.FloatField(default=0.0)

    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    last_activity = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ['profile', 'checklist']
        indexes = [
            models.Index(fields=['profile', 'checklist'], name='userprogress_unique_idx'),
            models.Index(fields=['profile', '-last_activity'], name='userprogress_activity_idx'),
            models.Index(fields=['checklist', 'progress_percentage'], name='userprogress_completion_idx'),
        ]

    def __str__(self):
        return f"{self.profile.psn_username}'s progress on {self.checklist.title}"

    def update_progress(self):
        """Recalculate progress statistics."""
        total = self.checklist.total_items
        completed = len(self.completed_items)
        self.items_completed = completed
        self.total_items = total
        self.progress_percentage = (completed / total * 100) if total > 0 else 0.0
        self.save(update_fields=['completed_items', 'items_completed', 'total_items', 'progress_percentage', 'updated_at', 'last_activity'])

    def mark_item_complete(self, item_id):
        """Mark an item as complete."""
        if item_id not in self.completed_items:
            self.completed_items.append(item_id)
            self.update_progress()

    def mark_item_incomplete(self, item_id):
        """Mark an item as incomplete."""
        if item_id in self.completed_items:
            self.completed_items.remove(item_id)
            self.update_progress()


class ChecklistReport(models.Model):
    """
    User report for checklist moderation review.
    Following CommentReport pattern.
    """
    REPORT_REASONS = [
        ('spam', 'Spam'),
        ('inappropriate', 'Inappropriate Content'),
        ('misinformation', 'Misinformation/Inaccurate'),
        ('plagiarism', 'Plagiarism'),
        ('other', 'Other'),
    ]
    REPORT_STATUS = [
        ('pending', 'Pending Review'),
        ('reviewed', 'Reviewed'),
        ('dismissed', 'Dismissed'),
        ('action_taken', 'Action Taken'),
    ]

    checklist = models.ForeignKey(
        Checklist,
        on_delete=models.CASCADE,
        related_name='reports'
    )
    reporter = models.ForeignKey(
        Profile,
        on_delete=models.CASCADE,
        related_name='submitted_checklist_reports'
    )
    reason = models.CharField(max_length=20, choices=REPORT_REASONS)
    details = models.TextField(
        max_length=500,
        blank=True,
        help_text="Additional context for the report"
    )
    status = models.CharField(
        max_length=20,
        choices=REPORT_STATUS,
        default='pending'
    )
    created_at = models.DateTimeField(auto_now_add=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)
    reviewed_by = models.ForeignKey(
        CustomUser,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='reviewed_checklist_reports'
    )
    admin_notes = models.TextField(blank=True)

    class Meta:
        unique_together = ['checklist', 'reporter']
        indexes = [
            models.Index(fields=['status', '-created_at'], name='checklist_report_status_idx'),
            models.Index(fields=['checklist'], name='checklist_report_checklist_idx'),
        ]

    def __str__(self):
        return f"Report on {self.checklist.title} by {self.reporter.psn_username}"


class MonthlyRecap(models.Model):
    """
    Stores cached monthly recap data for a profile.

    Follows the same denormalization pattern as ProfileGamification - pre-computing
    stats to avoid expensive aggregation queries on every view. Recaps become
    immutable once the month ends (is_finalized=True).

    Access control:
    - All users can view their current month's recap
    - Premium users can access previous months' recaps
    """
    profile = models.ForeignKey(
        Profile,
        on_delete=models.CASCADE,
        related_name='monthly_recaps'
    )
    year = models.PositiveIntegerField()
    month = models.PositiveIntegerField(
        validators=[MinValueValidator(1), MaxValueValidator(12)]
    )

    # Trophy aggregates
    total_trophies_earned = models.PositiveIntegerField(default=0)
    bronzes_earned = models.PositiveIntegerField(default=0)
    silvers_earned = models.PositiveIntegerField(default=0)
    golds_earned = models.PositiveIntegerField(default=0)
    platinums_earned = models.PositiveIntegerField(default=0)

    # Game stats
    games_started = models.PositiveIntegerField(default=0)
    games_completed = models.PositiveIntegerField(default=0)

    # Highlight data (JSON for flexibility)
    platinums_data = models.JSONField(
        default=list,
        blank=True,
        help_text='List of platinum dicts: [{game_name, game_image, earned_date, earn_rate}]'
    )
    rarest_trophy_data = models.JSONField(
        default=dict,
        blank=True,
        help_text='Rarest trophy: {name, game, earn_rate, icon_url, trophy_type}'
    )
    most_active_day = models.JSONField(
        default=dict,
        blank=True,
        help_text='Most active day: {date, day_name, trophy_count}'
    )
    activity_calendar = models.JSONField(
        default=dict,
        blank=True,
        help_text='Activity calendar: {days: [{day, count, level}], max_count, total_active_days, first_day_weekday, days_in_month}'
    )
    streak_data = models.JSONField(
        default=dict,
        blank=True,
        help_text='Streak data: {longest_streak, streak_start, streak_end, total_active_days}'
    )
    time_analysis_data = models.JSONField(
        default=dict,
        blank=True,
        help_text='Time of day analysis: {periods: {morning: N, afternoon: N, evening: N, night: N}, most_active_period, most_active_count}'
    )

    # Quiz data (denormalized for historical accuracy)
    quiz_total_trophies_data = models.JSONField(
        default=dict,
        blank=True,
        help_text='Total trophies quiz data: {correct_value, options: [numbers]}'
    )
    quiz_rarest_trophy_data = models.JSONField(
        default=dict,
        blank=True,
        help_text='Rarest trophy quiz data: {correct_trophy_id, options: [{id, name, icon_url, game, trophy_type}]}'
    )
    quiz_active_day_data = models.JSONField(
        default=dict,
        blank=True,
        help_text='Active day quiz data: {correct_day, correct_day_name, correct_count, day_counts, day_names}'
    )

    # Badge/XP stats
    badge_xp_earned = models.PositiveIntegerField(default=0)
    badges_earned_count = models.PositiveIntegerField(default=0)
    badges_data = models.JSONField(
        default=list,
        blank=True,
        help_text='List of badges earned: [{name, tier, series_slug, image_url}]'
    )
    badge_progress_quiz_data = models.JSONField(
        default=dict,
        blank=True,
        help_text='Badge progress quiz data: {correct_badge_id, correct_badge_name, correct_progress_pct, correct_completed, correct_required, options: [{id, name, series, icon_url}]}'
    )

    # Comparison data
    comparison_data = models.JSONField(
        default=dict,
        blank=True,
        help_text='Comparison stats: {vs_prev_month_pct, personal_bests: []}'
    )

    # Status
    is_finalized = models.BooleanField(
        default=False,
        help_text='True once the month ends and stats are locked'
    )
    email_sent = models.BooleanField(
        default=False,
        help_text='True if recap notification email has been sent to user'
    )
    email_sent_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text='Timestamp when recap email was sent'
    )
    notification_sent = models.BooleanField(
        default=False,
        help_text='True if recap in-app notification has been sent to user'
    )
    notification_sent_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text='Timestamp when recap notification was sent'
    )
    generated_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ['profile', 'year', 'month']
        ordering = ['-year', '-month']
        indexes = [
            models.Index(fields=['profile', 'year', 'month'], name='monthlyrecap_profile_ym_idx'),
            models.Index(fields=['year', 'month', 'is_finalized'], name='monthlyrecap_ym_final_idx'),
            models.Index(fields=['profile', 'is_finalized'], name='monthlyrecap_profile_final_idx'),
        ]
        verbose_name = 'Monthly Recap'
        verbose_name_plural = 'Monthly Recaps'

    def __str__(self):
        return f"{self.profile.psn_username} - {self.year}/{self.month:02d}"

    @property
    def month_name(self):
        """Return the full month name (e.g., 'January')"""
        import calendar
        return calendar.month_name[self.month]

    @property
    def short_month_name(self):
        """Return abbreviated month name (e.g., 'Jan')"""
        import calendar
        return calendar.month_abbr[self.month]


# ---------- Game Lists ----------

# Tier limits for game lists
GAME_LIST_FREE_MAX_LISTS = 3
GAME_LIST_FREE_MAX_ITEMS = 100


class GameList(models.Model):
    """
    User-created game collection. Users can organize games into named lists
    (e.g., "My Backlog", "Favorites", "Best Platinums").

    Free users: up to 3 private lists, 100 games each.
    Premium users: unlimited lists/games, public visibility, notes on items.
    """
    profile = models.ForeignKey(
        Profile,
        on_delete=models.CASCADE,
        related_name='game_lists'
    )
    name = models.CharField(max_length=200)
    description = models.TextField(max_length=1000, blank=True)
    is_public = models.BooleanField(default=False)
    selected_theme = models.CharField(
        max_length=50, blank=True, default='',
        help_text='Selected gradient theme key for list background (premium only).'
    )

    # Denormalized counts
    game_count = models.PositiveIntegerField(default=0)
    like_count = models.PositiveIntegerField(default=0)
    view_count = models.PositiveIntegerField(default=0)

    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    # Soft delete
    is_deleted = models.BooleanField(default=False)
    deleted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=['profile', 'is_deleted'], name='gl_profile_deleted_idx'),
            models.Index(fields=['is_public', 'is_deleted', '-like_count'], name='gl_public_likes_idx'),
            models.Index(fields=['is_public', 'is_deleted', '-created_at'], name='gl_public_created_idx'),
        ]
        ordering = ['-updated_at']

    def __str__(self):
        return f"{self.name} by {self.profile.psn_username}"

    def soft_delete(self):
        self.is_deleted = True
        self.deleted_at = timezone.now()
        self.save(update_fields=['is_deleted', 'deleted_at'])

    @property
    def first_game_image(self):
        """Return the title_image of the first game in the list (for auto-thumbnail)."""
        first_item = self.items.select_related('game').order_by('position').first()
        if first_item and first_item.game.title_image:
            return first_item.game.title_image
        return None


class GameListItem(models.Model):
    """
    A game entry within a GameList. Tracks position for custom ordering
    and optional personal notes (premium only).
    """
    game_list = models.ForeignKey(
        GameList,
        on_delete=models.CASCADE,
        related_name='items'
    )
    game = models.ForeignKey(
        Game,
        on_delete=models.CASCADE,
        related_name='list_appearances'
    )
    note = models.CharField(max_length=500, blank=True)
    position = models.PositiveIntegerField(default=0)
    added_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ['game_list', 'game']
        indexes = [
            models.Index(fields=['game_list', 'position'], name='gli_list_position_idx'),
        ]
        ordering = ['position']

    def __str__(self):
        return f"{self.game.title_name} in {self.game_list.name}"


class GameListLike(models.Model):
    """Tracks likes on public game lists (one per user per list)."""
    game_list = models.ForeignKey(
        GameList,
        on_delete=models.CASCADE,
        related_name='likes'
    )
    profile = models.ForeignKey(
        Profile,
        on_delete=models.CASCADE,
        related_name='liked_lists'
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ['game_list', 'profile']

    def __str__(self):
        return f"{self.profile.psn_username} likes {self.game_list.name}"


# ─── Challenge System ───────────────────────────────────────────────────────────

class Challenge(models.Model):
    """
    Base challenge model. Houses shared fields for all challenge types.
    challenge_type determines which related data (slots, tasks, etc.) applies.
    """
    CHALLENGE_TYPES = [
        ('az', 'A-Z Platinum Challenge'),
        ('calendar', 'Platinum Calendar'),
        ('genre', 'Genre Challenge'),
    ]

    profile = models.ForeignKey(Profile, on_delete=models.CASCADE, related_name='challenges')
    challenge_type = models.CharField(max_length=30, choices=CHALLENGE_TYPES, db_index=True)
    name = models.CharField(max_length=75)
    description = models.TextField(blank=True, default='')

    # Progress (meaning varies by type — for AZ: X/26)
    total_items = models.PositiveIntegerField(default=0)
    filled_count = models.PositiveIntegerField(default=0)
    completed_count = models.PositiveIntegerField(default=0)

    # Stats
    view_count = models.PositiveIntegerField(default=0)

    # Display
    cover_letter = models.CharField(max_length=1, blank=True, default='')
    cover_genre = models.CharField(max_length=50, blank=True, default='')

    # Genre challenge: unique subgenres collected from assigned concepts
    subgenre_count = models.PositiveIntegerField(default=0)
    platted_subgenre_count = models.PositiveIntegerField(default=0)
    bonus_count = models.PositiveIntegerField(default=0)

    # Status
    is_complete = models.BooleanField(default=False)
    completed_at = models.DateTimeField(null=True, blank=True)

    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    # Soft delete
    is_deleted = models.BooleanField(default=False)
    deleted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=['profile', 'is_deleted', 'challenge_type'], name='challenge_profile_idx'),
            models.Index(fields=['challenge_type', 'is_deleted', 'is_complete'], name='challenge_type_status_idx'),
            models.Index(fields=['challenge_type', 'is_deleted', '-completed_count'], name='challenge_type_progress_idx'),
        ]
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.name} ({self.get_challenge_type_display()}) by {self.profile.psn_username}"

    def soft_delete(self):
        self.is_deleted = True
        self.deleted_at = timezone.now()
        self.save(update_fields=['is_deleted', 'deleted_at'])

    @property
    def progress_percentage(self):
        if self.total_items == 0:
            return 0
        return int((self.completed_count / self.total_items) * 100)


class AZChallengeSlot(models.Model):
    """One of the 26 letter slots (A-Z) in an A-Z Challenge."""
    challenge = models.ForeignKey(Challenge, on_delete=models.CASCADE, related_name='az_slots')
    letter = models.CharField(max_length=1, db_index=True)
    game = models.ForeignKey(
        Game, on_delete=models.SET_NULL, null=True, blank=True, related_name='az_slots'
    )

    # Progress
    is_completed = models.BooleanField(default=False)
    completed_at = models.DateTimeField(null=True, blank=True)

    # Timestamps
    assigned_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        unique_together = ['challenge', 'letter']
        ordering = ['letter']

    def __str__(self):
        status = 'completed' if self.is_completed else ('assigned' if self.game else 'empty')
        game_name = self.game.title_name if self.game else 'empty'
        return f"{self.letter}: {game_name} ({status})"


# Non-leap-year day counts per month (Feb 29 excluded from calendar challenge)
CALENDAR_DAYS_PER_MONTH = {
    1: 31, 2: 28, 3: 31, 4: 30, 5: 31, 6: 30,
    7: 31, 8: 31, 9: 30, 10: 31, 11: 30, 12: 31,
}


class CalendarChallengeDay(models.Model):
    """
    One of 365 day slots (Jan 1 - Dec 31, no Feb 29) in a Platinum Calendar Challenge.
    Filled automatically when the user earns a platinum on a matching calendar day.
    """
    challenge = models.ForeignKey(
        Challenge, on_delete=models.CASCADE, related_name='calendar_days'
    )
    month = models.PositiveSmallIntegerField()   # 1-12
    day = models.PositiveSmallIntegerField()      # 1-31

    # The first game whose platinum filled this day
    game = models.ForeignKey(
        Game, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='calendar_day_slots'
    )

    # Fill status
    is_filled = models.BooleanField(default=False)
    filled_at = models.DateTimeField(null=True, blank=True)

    # The actual earned_date_time of the platinum that filled this day
    platinum_earned_at = models.DateTimeField(null=True, blank=True)

    # Total platinums earned on this calendar day (month/day) across all years
    plat_count = models.PositiveSmallIntegerField(default=0)

    class Meta:
        unique_together = ['challenge', 'month', 'day']
        ordering = ['month', 'day']
        indexes = [
            models.Index(
                fields=['challenge', 'is_filled'],
                name='calday_challenge_filled_idx'
            ),
        ]

    def __str__(self):
        status = 'filled' if self.is_filled else 'empty'
        game_name = self.game.title_name if self.game else 'none'
        return f"{self.month:02d}/{self.day:02d}: {game_name} ({status})"


class GenreChallengeSlot(models.Model):
    """One genre slot in a Genre Challenge. Points to a Concept, not a Game."""
    challenge = models.ForeignKey(
        Challenge, on_delete=models.CASCADE, related_name='genre_slots'
    )
    genre = models.CharField(max_length=50, db_index=True)
    genre_display = models.CharField(max_length=100, blank=True, default='')
    concept = models.ForeignKey(
        'Concept', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='genre_challenge_slots'
    )

    # Progress
    is_completed = models.BooleanField(default=False)
    completed_at = models.DateTimeField(null=True, blank=True)

    # Timestamps
    assigned_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        unique_together = ['challenge', 'genre']
        ordering = ['genre']
        indexes = [
            models.Index(
                fields=['challenge', 'is_completed'],
                name='genreslot_chal_completed_idx'
            ),
        ]

    def __str__(self):
        concept_name = self.concept.unified_title if self.concept else 'empty'
        status = 'done' if self.is_completed else 'pending'
        return f"{self.genre_display}: {concept_name} ({status})"


class GenreBonusSlot(models.Model):
    """Bonus game slot for subgenre hunting in a Genre Challenge (no genre restriction)."""
    challenge = models.ForeignKey(
        Challenge, on_delete=models.CASCADE, related_name='bonus_slots'
    )
    concept = models.ForeignKey(
        'Concept', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='genre_bonus_slots'
    )

    # Progress
    is_completed = models.BooleanField(default=False)
    completed_at = models.DateTimeField(null=True, blank=True)

    # Timestamps
    assigned_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ['challenge', 'concept']
        ordering = ['assigned_at']
        indexes = [
            models.Index(
                fields=['challenge', 'is_completed'],
                name='bonusslot_chal_completed_idx'
            ),
        ]

    def __str__(self):
        concept_name = self.concept.unified_title if self.concept else 'empty'
        status = 'done' if self.is_completed else 'pending'
        return f"Bonus: {concept_name} ({status})"


class DashboardConfig(models.Model):
    """
    Per-user dashboard preferences: visible modules, ordering, and per-module settings.

    module_order: ordered list of module slugs (premium-only to customize).
        ["trophy_summary", "active_challenges", "recent_platinums", ...]

    hidden_modules: slugs the user has toggled off (free users: max 3).
        ["community_engagement", "quick_links"]

    module_settings: per-module config overrides (premium-only).
        {"games_in_progress": {"limit": 10}, "recent_platinums": {"limit": 5}}
    """
    profile = models.OneToOneField(
        Profile,
        on_delete=models.CASCADE,
        related_name='dashboard_config',
        primary_key=True,
    )
    module_order = models.JSONField(default=list, blank=True)
    hidden_modules = models.JSONField(default=list, blank=True)
    module_settings = models.JSONField(default=dict, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Dashboard Config'
        verbose_name_plural = 'Dashboard Configs'

    def __str__(self):
        return f"DashboardConfig for {self.profile.psn_username}"
